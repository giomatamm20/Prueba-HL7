"""Regression for sample-only requirements and linked catalog/protocol setup."""
import copy
import json
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient
from app import main
from app.engine import SimulationEngine
from app.hl7_contract import infer_hl7_contract, flexible_contract, hl7_contract_issues, validate_hl7_contract
from app.hl7_orders import hl7_path_value
from app.profile_builder import build_profile_message
from app.profile_validation import flexible_profile, validate_outbound
from app.study_catalog import dynamic_outbound, infer_studies
from support import EngineCase, OML, outgoing, receiver
from test_catalog_reception import ORM, urine_catalog


def segment(name, values):
    fields=['']*(max(values)+1);fields[0]=name
    for number,value in values.items():fields[number]=value
    return '|'.join(fields)


SAMPLE_HEADER='MSH|^~\\&|QA|LAB|HIS|FAC|202611061200||ORU^R01|SAMPLE|T|2.5.1'
SAMPLE='\r'.join([SAMPLE_HEADER,'PID|||QA-PATIENT||PRUEBA^SINTETICA',
    segment('ORC',{1:'RE',2:'QA-ORDER',3:'QA-FILLER^LAB',5:'CM'}),
    segment('OBR',{1:'1',2:'QA-ORDER',3:'QA-SAMPLE^LAB',4:'QA-TEST^Examen',7:'202611061200',25:'F'}),
    segment('OBX',{1:'1',2:'NM',3:'QA-VALUE^Valor^L',5:'6',14:'202611061200'}),
    segment('OBX',{1:'2',2:'NM',3:'QA-VALUE2^Valor2^L',5:'7',14:'202611061200'})])+'\r'


class FlexibleContractTests(unittest.TestCase):
    def test_reported_missing_fields_are_optional_and_utf8_header_is_accepted(self):
        generated='\r'.join([SAMPLE_HEADER.replace('|SAMPLE|','|GENERATED|')+'||||||UNICODE UTF-8',
            'PID|||OTHER||OTRO^PACIENTE','ORC|RE|ORDER|FILLER','OBR|1|ORDER|SAMPLE|QA-TEST^Examen',
            'OBX|1|NM|QA-VALUE^Valor||6','OBX|2|NM|QA-VALUE2^Valor2||7'])+'\r'
        strict=infer_hl7_contract(SAMPLE)
        issues=hl7_contract_issues(generated, strict)
        for path in ['ORC[1]-3[1].2','ORC[1]-5','OBR[1]-3[1].2','OBR[1]-7','OBR[1]-25','OBX[1]-3[1].3','OBX[1]-14','OBX[2]-14']:
            self.assertTrue(any(path in issue for issue in issues), path)
        original=copy.deepcopy(strict)
        flexible=flexible_contract(strict)
        self.assertEqual(strict, original)
        self.assertEqual(hl7_contract_issues(generated, flexible), [])
        validate_outbound(generated, {'outbound':{'schema':flexible}}, 'RESULT_FINAL', 'GENERATED')
        self.assertTrue(hl7_contract_issues(generated.replace('|GENERATED|','||'), flexible))

    def test_optional_extra_segments_and_repetitions_keep_core_validation(self):
        schema=infer_hl7_contract(OML, strict=False)
        changed=OML.replace('PAT-SYNTHETIC','PAT-A~PAT-B~PAT-C').replace('PV1|1|O\r','ZQA|EXTRA\r')
        validate_hl7_contract(changed, schema)
        self.assertEqual(schema['inferenceMode'], 'FLEXIBLE')

    def test_utf8_declaration_does_not_break_an_otherwise_strict_profile(self):
        profile={'outbound':outgoing()}
        base={'patientId':'QA','patientName':'PRUEBA^SINTETICA'}
        sample,_=build_profile_message(profile,{'id':'qa'},base,[], 'RESULT_FINAL',1)
        schema=infer_hl7_contract(sample)
        message,control=build_profile_message(profile,{'id':'qa'},{**base,'patientName':'PRUEBA^NIÑO'},[], 'RESULT_FINAL',2)
        validate_outbound(message,{'outbound':{'schema':schema}},'RESULT_FINAL',control)


class LinkedConfigurationTests(EngineCase):
    def test_catalog_first_creates_learning_profile_and_generates_compatible_results(self):
        self.add(contract=False)
        catalog=self.engine.save_catalog(urine_catalog())
        self.engine.assign_catalog('test-analyzer',catalog['id'],align_outbound=True)
        summary=self.engine.configuration_summary('test-analyzer')
        self.assertTrue(summary['compatible'])
        self.assertEqual(summary['profile']['receptionMode'],'LEARN')
        self.assertIn('MSA|AA|',self.engine.receive_hl7_order(ORM,'test-analyzer','127.0.0.1'))
        order=self.engine.list_orders()[0]
        self.engine.load_sample(order['id'],order['barcode'])
        with patch('app.engine.time.sleep'):self.engine.process_order(order['id'],False)
        with receiver() as target:
            self.engine.update_analyzer('test-analyzer',{'resultDestinationPort':target.server_address[1]})
            result=self.engine.send_result(order['id'])
            self.assertEqual(result['status'],'COMPLETED')
            self.assertEqual(hl7_path_value(target.messages[0],'OBX-5'),'6')
        restarted=SimulationEngine()
        self.assertEqual(restarted.configuration_summary('test-analyzer')['catalog']['id'],catalog['id'])
        self.assertTrue(restarted.configuration_summary('test-analyzer')['compatible'])

    def test_catalog_alignment_preserves_input_and_other_events_and_skips_compatible_profiles(self):
        self.add(contract=False)
        original=self.engine.save_protocol_profile('test-analyzer',{'name':'Configuración propia','inbound':{
            'schema':infer_hl7_contract(OML),'mappings':[{'target':'order.id','source':'ORC-2.1'}]},
            'outbound':{'events':{'IMAGE_READY':outgoing()['events']['RESULT_FINAL']}}})
        catalog=self.engine.save_catalog(urine_catalog())
        self.engine.assign_catalog('test-analyzer',catalog['id'],align_outbound=True)
        linked=self.engine.store.get_active_protocol_profile('test-analyzer')
        self.assertEqual(linked['inbound'],original['inbound'])
        self.assertEqual(linked['outbound']['events']['IMAGE_READY'],original['outbound']['events']['IMAGE_READY'])
        self.assertNotEqual(linked['id'],original['id'])
        self.engine.assign_catalog('test-analyzer',catalog['id'],align_outbound=True)
        self.assertEqual(self.engine.store.get_active_protocol_profile('test-analyzer')['id'],linked['id'])

    def test_alignment_keeps_custom_identifiers_and_result_destinations(self):
        self.add(contract=False)
        outbound=outgoing()
        outbound['events']['RESULT_FINAL']['segments'].append({'name':'ORC','fields':[{'target':'ORC-4.1','source':'order.id'}]})
        profile=self.engine.save_protocol_profile('test-analyzer',{'name':'Mapeos propios','inbound':{'schema':infer_hl7_contract(OML)},'outbound':outbound})
        definition=urine_catalog()
        definition['studies'].append({'code':'SECOND','name':'Otro estudio','parameters':[{
            'code':'TEXT','name':'Texto','valueType':'ST','generator':{'kind':'fixed','value':'QA'}}]})
        catalog=self.engine.save_catalog(definition)
        self.engine.assign_catalog('test-analyzer',catalog['id'],align_outbound=True)
        active=self.engine.store.get_active_protocol_profile('test-analyzer')
        event=active['outbound']['events']['RESULT_FINAL']
        self.assertEqual(next(s for s in event['segments'] if s['name']=='ORC')['fields'],outbound['events']['RESULT_FINAL']['segments'][-1]['fields'])
        group=next(s for s in event['segments'] if s.get('repeatBy')=='studies')
        obx=next(s for s in group['children'] if s['name']=='OBX')
        self.assertIn({'target':'OBX-5','source':'result.value','required':True},obx['fields'])
        self.assertTrue(self.engine.configuration_summary('test-analyzer')['compatible'])

    def test_api_infers_flexible_by_default_and_keeps_strict_option(self):
        self.add()
        with patch.object(main,'engine',self.engine),patch.object(main,'listener_manager',self.manager):
            client=TestClient(main.app)
            response=client.post('/api/analyzers/test-analyzer/protocol-profiles/infer',json={'rawMessage':SAMPLE})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['schema']['inferenceMode'],'FLEXIBLE')
            response=client.post('/api/analyzers/test-analyzer/protocol-profiles/infer',json={'rawMessage':SAMPLE,'strictValidation':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['schema']['segments'][1]['fields']['3']['required'])
            original=self.engine.store.get_active_protocol_profile('test-analyzer')
            payload={**original,'profileId':original['id']}
            response=client.post('/api/analyzers/test-analyzer/protocol-profiles/flexible',json=payload)
            self.assertEqual(response.status_code,201,response.text)
            self.assertNotEqual(response.json()['id'],original['id'])
            self.assertFalse(response.json()['inbound']['strictValidation'])
            self.assertEqual(next(p for p in self.engine.list_protocol_profiles('test-analyzer') if p['id']==original['id'])['status'],'ARCHIVED')

    def test_preview_runs_the_same_validation_as_send_and_flexible_revision_fixes_sample_requirements(self):
        self.add()
        definition={'name':'Ejemplo estricto','inbound':{'schema':infer_hl7_contract(OML)},'outbound':outgoing()}
        definition['outbound']['schema']=infer_hl7_contract(SAMPLE)
        with self.assertRaisesRegex(ValueError,'Contrato HL7'):
            self.engine.preview_protocol_profile('test-analyzer',definition)
        preview=self.engine.preview_protocol_profile('test-analyzer',flexible_profile(definition))
        self.assertIn('OBX|',preview['message'])

    def test_preview_uses_selected_event_instead_of_forcing_result_final(self):
        self.add()
        definition={'name':'Otro evento','inbound':{'schema':infer_hl7_contract(OML)},
                    'outbound':{'events':{'IMAGE_READY':outgoing()['events']['RESULT_FINAL']}}}
        with patch.object(main,'engine',self.engine):
            response=TestClient(main.app).post('/api/analyzers/test-analyzer/protocol-profiles/preview',json={**definition,'eventType':'IMAGE_READY'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('IMAGE_READY',response.json()['controlId'])

    def test_catalog_import_and_preview_are_linked_without_copying_patient_or_result_values(self):
        self.add(contract=False)
        active=self.engine.save_protocol_profile('test-analyzer',{'name':'Catálogo conectado','inbound':{'receptionMode':'LEARN'},'outbound':dynamic_outbound()})
        suggestions=infer_studies(SAMPLE,allow_orders=True)
        serialized=json.dumps(suggestions)
        self.assertNotIn('QA-PATIENT',serialized)
        self.assertIsNone(suggestions['studies'][0]['parameters'][0]['generator'])
        order_only=infer_studies(ORM,allow_orders=True)
        self.assertEqual(order_only['studies'][0]['code'],'LAB_X6ED9')
        self.assertEqual(order_only['studies'][0]['parameters'],[])
        with patch.object(main,'engine',self.engine),patch.object(main,'listener_manager',self.manager):
            client=TestClient(main.app)
            response=client.post('/api/catalogs/preview',json={'catalog':urine_catalog(),'analyzerId':'test-analyzer'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['usesConfiguredProfile'])
            self.assertEqual(response.json()['configurationWarnings'],[])
