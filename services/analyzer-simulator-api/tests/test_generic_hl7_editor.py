"""Exercise the actual editor with multiple message shapes and profile events."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from app.hl7_contract import infer_hl7_contract
from app.hl7_orders import hl7_path_value
from app.profile_builder import build_profile_message
from app.profile_parser import parse_with_profile
from app.profile_validation import validate_profile
from app.study_catalog import dynamic_outbound
from support import OML, outgoing


HARNESS = r"""
const fs=require('fs'),input=JSON.parse(fs.readFileSync(0,'utf8'));
const html=fs.readFileSync(input.html,'utf8');
const nodes={};const decode=s=>String(s).replace(/&quot;/g,'"').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
function parseElements(markup){
 for(const m of markup.matchAll(/<(input|select|textarea|small|p|tbody)[^>]*\bid="([A-Za-z0-9]+)"[^>]*>/g)){
  const id=m[2],n=nodes[id] || makeNode();n.value=decode(/\bvalue="([^"]*)"/.exec(m[0])?.[1] || '');n.checked=/\schecked(?:\s|>)/.test(m[0]);nodes[id]=n;
 }
}
function makeNode(){return {value:'',checked:false,textContent:'',listeners:{},addEventListener(event,fn){this.listeners[event]=fn;},
 set innerHTML(value){this.markup=value;parseElements(value);},get innerHTML(){return this.markup || '';}};}
parseElements(html);
global.document={getElementById:id=>nodes[id] || null};global.window={};
const el=id=>nodes[id];function markProtocolEdited(){}
eval(fs.readFileSync(input.basic,'utf8'));
eval(html.slice(html.indexOf('    function structuredSchemaToOutboundSegments('),html.indexOf('    function triggerAutoPreview(')));
eval(html.slice(html.indexOf('    function profileDefinition()'),html.indexOf('    async function saveProtocolProfile()')));
let protocolSegmentOrder=['MSH','PID','PV1','ORC','OBR','SPM','OBX'];
const profile=input.profile;
if(input.convert){profile.outbound.events={RESULT_FINAL:{messageType:'ORU^R01',segments:structuredSchemaToOutboundSegments(profile.outbound.schema)}};}
window.savedProtocolDefinition=structuredClone(profile);
window.savedInboundSchema=profile.inbound?.schema || null;window.savedInboundRawHL7=profile.inbound?.rawHL7 || null;
window.savedOutboundSchema=profile.outbound?.schema || null;window.savedOutboundRawHL7=profile.outbound?.rawHL7 || null;
el('profileName').value=profile.name;el('profileReceptionMode').value='PERMISSIVE';el('profileStrictValidation').checked=false;
el('profileInboundType').value=profile.inbound?.messageType || 'ORM^O01';el('profileMessageType').value='ORU^R01';el('profileVersion').value='2.5.1';
window.basicHL7.load(profile);
for(const action of input.actions || []){
 if(action.type==='event')window.basicHL7.selectEvent(action.name);
 if(action.type==='edit'){
  const field=window.basicHL7.fields.find(f=>f.source===action.source),id=action.direction==='from'?field.input:field.output;
  nodes[id].value=action.value;nodes[id].listeners.input?.();
 }
 if(action.type==='add'){
  el('basicHL7NewSource').value=action.source;el('basicHL7NewKey').value=action.key || '';el('basicHL7NewLabel').value=action.label || '';
  window.basicHL7.addFieldFromForm();
 }
}
const definition=profileDefinition();
console.log(JSON.stringify({definition,fields:window.basicHL7.fields.map(f=>({source:f.source,label:f.label,from:f.input?nodes[f.input].value:null,to:nodes[f.output].value})),
 status:el('basicHL7FieldStatus').textContent}));
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for editor integration")
class GenericHL7EditorTests(unittest.TestCase):
    def editor(self, profile, actions=(), convert=False):
        static = Path(__file__).resolve().parents[1] / 'app/static'
        result = subprocess.run([shutil.which('node'), '-e', HARNESS], input=json.dumps({
            'html': str(Path(__file__).parent / 'fixtures/profile_editor_v2.html'), 'basic': str(static / 'basic-hl7.js'), 'profile': profile, 'actions': actions, 'convert': convert}),
            capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def profile(self):
        outbound = outgoing()
        outbound['events']['IMAGE_READY'] = copy.deepcopy(outbound['events']['RESULT_FINAL'])
        outbound['events']['IMAGE_READY']['messageType'] = 'ORU^R40'
        outbound['events']['IMAGE_READY']['segments'].append({'name': 'ZQA', 'fields': [
            {'target': 'ZQA-1', 'sourceType': 'fixed', 'value': 'CONFIGURACION_PROPIA'}]})
        return {'name': 'Equipo genérico QA', 'inbound': {'messageType': 'OML^O21', 'mappings': [
            {'target': 'patient.id', 'source': 'PID-3.1'}, {'target': 'patient.name', 'source': 'PID-5'},
            {'target': 'order.id', 'source': 'OBR-2.1'}]}, 'outbound': outbound}

    def test_loading_and_saving_keeps_all_events_and_fixed_rules_exactly(self):
        profile = self.profile()
        # No RESULT_FINAL event: opening this profile must not create one.
        del profile['outbound']['events']['RESULT_FINAL']
        result = self.editor(profile)
        self.assertEqual(result['definition']['outbound'], profile['outbound'])
        validate_profile(result['definition'])
        self.assertEqual(next(f for f in result['fields'] if f['source'] == 'sample.id')['from'], '')

    def test_event_specific_edits_do_not_overwrite_another_event(self):
        profile = self.profile()
        result = self.editor(profile, [
            {'type': 'edit', 'source': 'patient.id', 'direction': 'to', 'value': 'PID-2'},
            {'type': 'event', 'name': 'IMAGE_READY'},
            {'type': 'edit', 'source': 'order.id', 'direction': 'to', 'value': 'ORC-4.1'},
        ])['definition']
        final = result['outbound']['events']['RESULT_FINAL']
        image = result['outbound']['events']['IMAGE_READY']
        self.assertEqual(next(f for s in final['segments'] for f in s['fields'] if f.get('source')=='patient.id')['target'], 'PID-2')
        self.assertEqual(next(s for s in image['segments'] if s['name'] == 'PID')['fields'][0]['target'], 'PID-3')
        self.assertEqual(image['segments'][-1], profile['outbound']['events']['IMAGE_READY']['segments'][-1])
        self.assertEqual([f['target'] for s in image['segments'] for f in s['fields'] if f.get('source') == 'order.id'], ['ORC-4.1'])

    def test_custom_field_and_demographics_are_saved_reloaded_and_use_each_actual_message(self):
        profile = self.profile()
        result = self.editor(profile, [
            {'type': 'add', 'source': 'custom', 'key': 'reference', 'label': 'Referencia propia'},
            {'type': 'edit', 'source': 'customFields.reference', 'direction': 'from', 'value': 'ZIN-2.1'},
            {'type': 'edit', 'source': 'customFields.reference', 'direction': 'to', 'value': 'ZOU-7'},
            {'type': 'add', 'source': 'patient.birthDate'},
            {'type': 'edit', 'source': 'patient.birthDate', 'direction': 'from', 'value': 'ZIN-3'},
            {'type': 'edit', 'source': 'patient.birthDate', 'direction': 'to', 'value': 'PID-7'},
        ])['definition']
        validate_profile(result)
        loaded = self.editor(result)
        field = next(f for f in loaded['fields'] if f['source'] == 'customFields.reference')
        self.assertEqual((field['label'], field['from'], field['to']), ('Referencia propia', 'ZIN-2.1', 'ZOU-7'))
        for reference, birth in [('REF-A', '19900101'), ('REF-B', '20000202')]:
            raw = OML + f'ZIN|1|{reference}^QA|{birth}\r'
            order = {**parse_with_profile(raw, result['inbound']), 'id': reference, 'sourceRawMessage': raw}
            self.assertEqual(order['customFields']['reference'], reference)
            message, _ = build_profile_message(result, {'id': 'qa'}, order, [], 'RESULT_FINAL', 1)
            self.assertEqual(hl7_path_value(message, 'ZOU-7'), reference)
            self.assertEqual(hl7_path_value(message, 'PID-7'), birth)

    def test_name_components_keep_transforms_when_destinations_are_edited(self):
        profile = self.profile()
        pid = profile['outbound']['events']['RESULT_FINAL']['segments'][1]
        pid['fields'] = [f for f in pid['fields'] if f['source'] != 'patient.name'] + [
            {'target': 'PID-5.1', 'source': 'patient.name', 'transform': 'component1'},
            {'target': 'PID-5.2', 'source': 'patient.name', 'transform': 'component2'}]
        result = self.editor(profile, [{'type': 'edit', 'source': 'patient.name', 'direction': 'to', 'value': 'PID-5.1, PID-5.2'}])['definition']
        message, _ = build_profile_message(result, {'id': 'qa'}, {'patientId':'QA-PATIENT','patientName': 'APELLIDO^NOMBRE'}, [], 'RESULT_FINAL', 1)
        self.assertEqual(hl7_path_value(message, 'PID-5'), 'APELLIDO^NOMBRE')

    def test_nested_study_groups_and_aliases_survive_editor_round_trip(self):
        profile = self.profile()
        profile['outbound'] = dynamic_outbound()
        result = self.editor(profile)['definition']
        self.assertEqual(result['outbound'], profile['outbound'])
        validate_profile(result)

    def test_inferred_output_does_not_freeze_example_values_or_duplicate_patient_ids(self):
        sample = ('MSH|^~\\&|EXAMPLE-SIM|EXAMPLE-LAB|EXAMPLE-HIS|EXAMPLE-FAC|202611061200||ORU^R01|EXAMPLE-CONTROL|T|2.5.1\r'
                  'PID|||EXAMPLE-A^^^EXAMPLE-AUTH^MR~EXAMPLE-B^^^EXAMPLE-AUTH^PI||EXAMPLE-FAMILY^EXAMPLE-GIVEN||19700101|M\r'
                  'OBR|1|EXAMPLE-ORDER||EXAMPLE-TEST^EXAMPLE-TESTNAME\r'
                  'OBX|1|ST|EXAMPLE-RESULT^EXAMPLE-RESULTNAME||EXAMPLE-VALUE||||||F\rNTE|1||EXAMPLE-NOTE\r')
        profile = self.profile()
        profile['outbound'] = {'schema': infer_hl7_contract(sample)}
        definition = self.editor(profile, convert=True)['definition']
        validate_profile(definition)
        for suffix, family, name, birthday in [('A', 'FAMILIA-A', 'NOMBRE-A', '19900101'), ('B', 'FAMILIA-B', 'NOMBRE-B', '20000202')]:
            raw = (f'MSH|^~\\&|HIS-{suffix}|FAC-{suffix}|SIM|LAB|202611061200||OML^O21|MSG-{suffix}|T|2.5.1\r'
                   f'PID|||PAT-{suffix}-1^^^AUTH-{suffix}^MR~PAT-{suffix}-2^^^AUTH-{suffix}^PI||{family}^{name}||{birthday}|F\r'
                   f'ORC|NW|ORD-{suffix}\rOBR|1|ORD-{suffix}|SMP-{suffix}|TEST-{suffix}^NOMBRE-TEST-{suffix}\rNTE|1||NOTA-{suffix}\r')
            order = {**parse_with_profile(raw, definition['inbound']), 'id': suffix, 'sourceRawMessage': raw}
            message, _ = build_profile_message(definition, {'id': 'qa', 'vendor': 'QA-LAB'}, order, [{
                'code': f'RES-{suffix}', 'name': f'RESULTADO-{suffix}', 'value': f'TEXTO-{suffix}', 'valueType': 'ST'}], 'RESULT_FINAL', 1)
            self.assertNotIn('EXAMPLE-', message)
            self.assertEqual(hl7_path_value(message, 'PID-3[1].1'), f'PAT-{suffix}-1')
            self.assertEqual(hl7_path_value(message, 'PID-3[2].1'), f'PAT-{suffix}-2')
            self.assertEqual(hl7_path_value(message, 'PID-5'), f'{family}^{name}')
            self.assertEqual(hl7_path_value(message, 'PID-7'), birthday)
            self.assertEqual(hl7_path_value(message, 'OBR-4.2'), f'NOMBRE-TEST-{suffix}')
            self.assertEqual(hl7_path_value(message, 'OBX-3.2'), f'RESULTADO-{suffix}')
            self.assertEqual(hl7_path_value(message, 'NTE-3'), f'NOTA-{suffix}')
