"""One persisted equipment revision governs reception, catalog and real output."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main
from app.engine import SimulationEngine
from app.equipment_profile import apply_example
from app.hl7_orders import hl7_path_value
from app.hl7_contract import infer_hl7_contract
from support import EngineCase, OML, receiver, outgoing


ORU = ('MSH|^~\\&|QA|LAB|HIS|LAB|20261106120000||ORU^R01|QA-MESSAGE|T|2.5.1\r'
       'PID|||QA-PATIENT||QA^SYNTHETIC\rORC|RE|QA-ORDER\r'
       'OBR|1|QA-ORDER||CBC^Hemograma\rOBX|1|NM|PH^pH||6|u|||||F\r'
       'OBX|2|ST|COLOR^Color||EJEMPLO||||||F\r')


def equipment(value=7):
    return {'name': 'Equipo unificado QA', 'inbound': {'receptionMode': 'PERMISSIVE', 'acceptUnknownTests': True,
        'mappings': [{'target': 'order.id', 'source': 'ORC-4.1', 'alternatives': ['ORC-2.1']}]},
        'outbound': outgoing(), 'catalog': {'studies': [{'code': 'CBC', 'name': 'Hemograma QA', 'parameters': [
            {'code': 'QA-VALUE', 'name': 'Valor QA', 'valueType': 'NM', 'unit': 'u',
             'generator': {'kind': 'fixed', 'value': value}}]}]}}


class EquipmentProfileTests(EngineCase):
    def test_moving_the_received_order_path_does_not_keep_the_old_requirement(self):
        self.add(contract=False)
        definition = equipment()
        definition['inbound'] = {'receptionMode': 'CONTRACT', 'strictValidation': True,
            'schema': infer_hl7_contract(OML), 'mappings': [{'target': 'order.id', 'source': 'ORC-4.1', 'required': True}]}
        self.engine.save_equipment_profile('test-analyzer', definition, 0)
        raw = OML.replace('ORC|NW|ORDER-1', 'ORC|NW|||ORDER-1')
        ack = self.engine.receive_hl7_order(raw, 'test-analyzer', '127.0.0.1')
        self.assertIn('MSA|AA', ack)
        self.assertEqual(self.engine.orders[-1]['orderId'], 'ORDER-1')

    def test_disabled_analitos_stay_in_the_profile_but_are_not_generated(self):
        self.add(contract=False)
        definition = equipment(5)
        definition['catalog']['studies'][0]['parameters'].append({'code': 'DISABLED', 'name': 'Desactivado',
            'valueType': 'ST', 'enabled': False, 'generator': None})
        saved = self.engine.save_equipment_profile('test-analyzer', definition, 0)
        preview = self.engine.preview_equipment_profile('test-analyzer', saved)
        self.assertEqual([r['code'] for r in preview['results']], ['QA-VALUE'])
        self.assertFalse(saved['catalog']['studies'][0]['parameters'][1]['enabled'])

    def test_catalog_binding_keeps_custom_output_routes_and_other_events(self):
        self.add(contract=False)
        definition = equipment()
        definition['outbound']['events']['CANCEL'] = copy.deepcopy(outgoing()['events']['RESULT_FINAL'])
        definition['outbound']['events']['RESULT_FINAL']['segments'].insert(2, {'name': 'ZDS', 'fields': [
            {'target': 'ZDS-1', 'source': 'customFields.reference'}]})
        saved = self.engine.save_equipment_profile('test-analyzer', definition, 0)
        self.assertEqual(saved['outbound']['events']['CANCEL'], definition['outbound']['events']['CANCEL'])
        zds = next(s for s in saved['outbound']['events']['RESULT_FINAL']['segments'] if s['name'] == 'ZDS')
        self.assertEqual(zds['fields'][0]['source'], 'customFields.reference')

    def test_save_and_restart_keep_one_complete_revision_without_catalog_assignment(self):
        self.add(contract=False)
        catalogs = copy.deepcopy(self.engine.store.list_catalogs())
        first = self.engine.save_equipment_profile('test-analyzer', equipment(), 0)
        second = self.engine.save_equipment_profile('test-analyzer', equipment(9), first['version'])
        restarted = SimulationEngine()
        loaded = restarted.equipment_profile('test-analyzer')
        self.assertEqual(loaded['id'], second['id'])
        self.assertEqual(loaded['revisionOf'], first['id'])
        self.assertEqual(loaded['catalog']['studies'][0]['parameters'][0]['generator']['value'], 9)
        self.assertEqual(loaded['inbound']['mappings'][0]['source'], 'ORC-4.1')
        self.assertEqual(restarted.store.list_catalogs(), catalogs)
        self.assertIsNone(restarted.analyzers['test-analyzer'].get('catalogRevisionId'))
        versions = restarted.list_protocol_profiles('test-analyzer')
        self.assertEqual(sum(p['status'] == 'ACTIVE' for p in versions), 1)
        archived = next(p for p in versions if p['id'] == first['id'])
        self.assertEqual(archived['catalog']['studies'][0]['parameters'][0]['generator']['value'], 7)

    def test_legacy_catalog_is_imported_on_open_without_mutating_saved_configuration(self):
        self.add()
        catalog = self.engine.save_catalog({'name': 'Anterior', 'studies': equipment()['catalog']['studies']})
        self.engine.assign_catalog('test-analyzer', catalog['id'])
        before = copy.deepcopy(self.engine.list_protocol_profiles('test-analyzer'))
        opened = self.engine.equipment_profile('test-analyzer')
        self.assertTrue(opened['legacyCatalogImported'])
        self.assertEqual(opened['catalog']['studies'], catalog['studies'])
        self.assertEqual(before, self.engine.list_protocol_profiles('test-analyzer'))

    def test_preview_uses_the_unsaved_catalog_and_its_modified_output_route(self):
        self.add(contract=False)
        definition = equipment(13)
        orc = {'name': 'ORC', 'fields': [{'target': 'ORC-4.1', 'source': 'order.id'}]}
        definition['outbound']['events']['RESULT_FINAL']['segments'].insert(2, orc)
        preview = self.engine.preview_equipment_profile('test-analyzer', definition)
        self.assertEqual(preview['results'][0]['value'], 13)
        self.assertEqual(hl7_path_value(preview['message'], 'ORC-4.1'), 'ORD-TEST-001')
        self.assertEqual(self.engine.list_protocol_profiles('test-analyzer'), [])

    def test_inference_merges_analitos_into_the_same_profile_without_copying_example_values(self):
        base = equipment(9)
        base['catalog']['studies'][0]['parameters'] = [{'code': 'PH', 'name': 'pH personalizado', 'valueType': 'NM',
            'generator': {'kind': 'fixed', 'value': 9}}]
        result = apply_example(base, ORU.replace('\r', '/r'))
        profile = result['profile']
        parameters = profile['catalog']['studies'][0]['parameters']
        self.assertEqual(parameters[0]['generator']['value'], 9)
        self.assertEqual(parameters[0]['name'], 'pH personalizado')
        self.assertIsNone(parameters[1]['generator'])
        self.assertNotIn('EJEMPLO', json.dumps(profile['catalog']))
        self.assertNotIn('QA-PATIENT', json.dumps(profile['catalog']))
        segments = profile['outbound']['events']['RESULT_FINAL']['segments']
        group = next(s for s in segments if s.get('repeatBy') == 'studies')
        self.assertEqual(sum(s['name'] == 'OBX' for s in group['children']), 1)

    def test_received_pending_study_can_be_completed_by_saving_the_same_equipment_profile(self):
        self.add(contract=False)
        inferred = apply_example(equipment(), OML, 'inbound')['profile']
        inferred['catalog']['studies'][0]['parameters'][0]['generator'] = None
        first = self.engine.save_equipment_profile('test-analyzer', inferred, 0)
        ack = self.engine.receive_hl7_order(OML, 'test-analyzer', '127.0.0.1')
        self.assertIn('MSA|AA', ack)
        order = self.engine.orders[-1]
        self.engine.load_sample(order['id'], order['barcode'])
        self.assertEqual(order['pendingCatalogStudies'], ['CBC'])
        with self.assertRaisesRegex(ValueError, 'pendientes'):
            self.engine.process_order(order['id'], False)
        first['catalog']['studies'][0]['parameters'][0]['generator'] = {'kind': 'fixed', 'value': 8}
        saved = self.engine.save_equipment_profile('test-analyzer', first, first['version'])
        processed = self.engine.process_order(order['id'], False)
        self.assertEqual(processed['results'][0]['value'], 8)
        self.assertEqual(processed['catalogProfileId'], saved['id'])

    def test_real_send_uses_embedded_catalog_and_old_order_keeps_its_results(self):
        with receiver() as peer:
            self.add(contract=False, resultDestinationPort=peer.server_address[1])
            original = equipment(7)
            original['outbound']['events']['RESULT_FINAL']['segments'].append(
                {'name': 'ZQA', 'fields': [{'target': 'ZQA-1', 'sourceType': 'fixed', 'value': 'ORIGINAL'}]})
            saved = self.engine.save_equipment_profile('test-analyzer', original, 0)
            self.assertIn('MSA|AA', self.engine.receive_hl7_order(OML, 'test-analyzer', '127.0.0.1'))
            received = self.engine.orders[-1]
            self.engine.load_sample(received['id'], received['barcode'])
            order = self.engine.process_order(received['id'], False)
            replacement = equipment(99)
            replacement['outbound']['events']['RESULT_FINAL']['segments'].append(
                {'name': 'ZQA', 'fields': [{'target': 'ZQA-1', 'sourceType': 'fixed', 'value': 'OTHER'}]})
            self.engine.save_equipment_profile('test-analyzer', replacement, saved['version'])
            sent = self.engine.send_result(order['id'])
            self.assertEqual(sent['results'][0]['value'], 7)
            self.assertEqual(hl7_path_value(peer.messages[0], 'OBX-5'), '7')
            self.assertEqual(sent['catalogProfileId'], saved['id'])
            self.assertEqual(hl7_path_value(peer.messages[0], 'ZQA-1'), 'ORIGINAL')
            self.assertEqual(sent['lastMessage']['profileId'], saved['id'])
            # Retiring a revision hides it from configuration history, but
            # orders and retries still retain their original complete profile.
            self.engine.delete_protocol_profile('test-analyzer', saved['id'])
            resent = self.engine.send_result(order['id'])
            self.assertEqual(hl7_path_value(peer.messages[1], 'ZQA-1'), 'ORIGINAL')
            self.assertEqual(resent['lastMessage']['profileId'], saved['id'])

    def test_optimistic_save_and_invalid_catalog_do_not_change_any_configuration(self):
        self.add(contract=False)
        saved = self.engine.save_equipment_profile('test-analyzer', equipment(), 0)
        before = copy.deepcopy(self.engine.list_protocol_profiles('test-analyzer'))
        with self.assertRaisesRegex(ValueError, 'otra sesión'):
            self.engine.save_equipment_profile('test-analyzer', equipment(10), 0)
        bad = equipment()
        bad['catalog']['studies'][0]['parameters'][0]['generator'] = {'kind': 'script', 'value': 'forbidden'}
        with self.assertRaises(ValueError):
            self.engine.save_equipment_profile('test-analyzer', bad, saved['version'])
        self.assertEqual(before, self.engine.list_protocol_profiles('test-analyzer'))

    def test_learning_preserves_the_embedded_catalog(self):
        self.add(contract=False)
        definition = equipment(5)
        definition['inbound']['receptionMode'] = 'LEARN'
        saved = self.engine.save_equipment_profile('test-analyzer', definition, 0)
        self.assertIn('MSA|AA', self.engine.receive_hl7_order(OML, 'test-analyzer', '127.0.0.1'))
        learned = self.engine.equipment_profile('test-analyzer')
        self.assertEqual(learned['catalog'], saved['catalog'])
        self.assertEqual(learned['inbound']['receptionMode'], 'PERMISSIVE')

    def test_api_round_trip_includes_catalog_and_conflict_status(self):
        self.add(contract=False)
        with patch.object(main, 'engine', self.engine), patch.object(main, 'listener_manager', self.manager):
            client = TestClient(main.app)
            response = client.put('/api/analyzers/test-analyzer/equipment-profile', json={**equipment(), 'expectedVersion': 0})
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()
            self.assertEqual(saved['catalog']['studies'][0]['code'], 'CBC')
            response = client.put('/api/analyzers/test-analyzer/equipment-profile', json={**equipment(9), 'expectedVersion': 0})
            self.assertEqual(response.status_code, 409)
            preview = client.post('/api/analyzers/test-analyzer/equipment-profile/preview', json={'profile': equipment(21)})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(preview.json()['results'][0]['value'], 21)

    def test_legacy_assignment_and_connection_updates_do_not_detach_embedded_studies(self):
        self.add(contract=False)
        saved = self.engine.save_equipment_profile('test-analyzer', equipment(), 0)
        self.engine.update_analyzer('test-analyzer', {'supportedTests': ['OTHER'], 'acceptUnknownTests': False,
                                                    'resultDestinationPort': 7001})
        self.assertEqual(self.engine.analyzers['test-analyzer']['supportedTests'], ['CBC'])
        self.assertTrue(self.engine.analyzers['test-analyzer']['acceptUnknownTests'])
        self.assertEqual(self.engine.analyzers['test-analyzer']['resultDestinationPort'], 7001)
        with self.assertRaisesRegex(ValueError, 'perfil del equipo'):
            self.engine.assign_catalog('test-analyzer', 'builtin-v1')
        legacy_update = self.engine.update_protocol_profile('test-analyzer', saved['id'], {
            'name': 'Nombre actualizado', 'inbound': saved['inbound'], 'outbound': saved['outbound']})
        self.assertEqual(legacy_update['catalog'], saved['catalog'])

    def test_model_keeps_late_responses_from_overwriting_drafts(self):
        if not shutil.which('node'):
            self.skipTest('Node is required')
        model = (Path(__file__).resolve().parents[1] / 'app/static/equipment-profile-model.js').read_text(encoding='utf-8')
        js = 'global.window={};\n' + model + """
let pending=[];const request=()=>new Promise(resolve=>pending.push(resolve));
const draft=new window.EquipmentProfileModel.EquipmentDraft(request);
(async()=>{const load=draft.load('qa');pending.shift()({name:'QA',version:1,inbound:{},outbound:{},catalog:{studies:[]}});await load;
 const preview=draft.preview();draft.change(p=>p.name='Typed');pending.shift()({message:'OLD'});const ignored=await preview;
 const save=draft.save();draft.change(p=>p.catalog.studies.push({code:'AFTER'}));pending.shift()({id:'v2',name:'Typed',version:2,inbound:{},outbound:{},catalog:{studies:[]}});await save;
 console.log(JSON.stringify({ignored,dirty:draft.dirty,name:draft.profile.name,study:draft.profile.catalog.studies[0].code,version:draft.version}));})();
"""
        run = subprocess.run([shutil.which('node'), '-e', js], capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout), {'ignored': None, 'dirty': True, 'name': 'Typed', 'study': 'AFTER', 'version': 2})
