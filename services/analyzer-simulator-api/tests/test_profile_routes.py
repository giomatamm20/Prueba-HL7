"""Visual route changes must keep the actual outbound contract in sync."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is required for visual route regressions')
class ProfileRouteTests(unittest.TestCase):
    def run_model(self, script):
        model = Path(__file__).resolve().parents[1] / 'app/static/profile-routes.js'
        source = 'global.window={};\n' + model.read_text(encoding='utf-8') + '\n' + script
        result = subprocess.run([shutil.which('node'), '-e', source], capture_output=True,
                                text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_route_component_change_removes_obsolete_required_rule(self):
        result = self.run_model("""
const schema={formatVersion:2,allowUnexpectedFields:false,segments:[{name:'ORC',min:1,max:1,fields:{
 '3':{required:true,repetitions:[{index:1,components:{'1':{required:true},'2':{required:true,const:'OLD'}}}]}}}]};
window.HL7WorkshopModel.syncSchema(schema,[{name:'ORC',fields:[{target:'ORC-4[2].1.2',source:'order.id'}]}]);
console.log(JSON.stringify(schema));
""")
        fields = result['segments'][0]['fields']
        self.assertNotIn('3', fields)
        self.assertEqual(fields['4']['maxRepetitions'], 2)
        rule = fields['4']['repetitions'][0]['components']['1']['subcomponents']['2']
        self.assertEqual(rule['source'], 'order.id')
        self.assertFalse(rule['required'])

    def test_save_includes_typed_routes_without_requiring_blur(self):
        result = self.run_model("""
const def={inbound:{mappings:[{target:'order.id',source:'ORC-4.1'}]},outbound:{events:{
 RESULT_FINAL:{segments:[{name:'ORC',fields:[{target:'ORC-2.1',source:'order.id'}]}]},
 CANCEL:{segments:[{name:'ORC',fields:[{target:'ORC-1',sourceType:'fixed',value:'CA'}]}]}}}};
const typed=[{event:'RESULT_FINAL',key:'0',index:0,target:'ORC-4.1'}];
window.HL7WorkshopModel.applyRouteEdits(def,typed);console.log(JSON.stringify(def));
""")
        events = result['outbound']['events']
        self.assertEqual(events['RESULT_FINAL']['segments'][0]['fields'][0]['target'], 'ORC-4.1')
        self.assertEqual(events['CANCEL']['segments'][0]['fields'][0]['value'], 'CA')
        self.assertEqual(result['inbound']['mappings'][0]['source'], 'ORC-4.1')

    def test_nested_group_reorder_and_private_segment_keep_other_events(self):
        result = self.run_model("""
const original={formatVersion:2,segments:[{name:'MSH',min:1,max:1,fields:{
 '2':{required:true,repetitions:[{index:1,components:{'1':{const:'^~\\\\&'}}}]},
 '12':{required:true,minRepetitions:1,repetitions:[{index:1,components:{'1':{required:true,const:'2.5.1'}}}]}}},
 {name:'ORC',fields:{'5':{required:true}}},{name:'OBR',fields:{}},{name:'OBX',fields:{}}]};
const def={inbound:{receptionMode:'LEARN',mappings:[{target:'order.id',source:'ORC-4.1'}]},outbound:{schema:original,events:{
 RESULT_FINAL:{segments:[{name:'MSH',fields:[{target:'MSH-12',sourceType:'fixed',value:'2.5.1'}]},
 {repeatBy:'studies',children:[{name:'OBR',fields:[]},{name:'ZDS',fields:[{target:'ZDS-1',source:'customFields.reference'}]},
 {name:'OBX',repeatBy:'results',fields:[{target:'OBX-5',source:'result.hl7Value'}]}]}]},
 CANCEL:{messageType:'ORM^O01',segments:[{name:'ORC',fields:[{target:'ORC-1',sourceType:'fixed',value:'CA'}]}]}}}};
window.HL7WorkshopModel.syncEventSchemas(def,'RESULT_FINAL');console.log(JSON.stringify(def));
""")
        outbound = result['outbound']
        self.assertEqual(outbound['schema']['segments'][1]['name'], 'ORC')
        updated = outbound['events']['RESULT_FINAL']['schema']['segments']
        self.assertEqual([s['name'] for s in updated], ['MSH', 'OBR', 'ZDS', 'OBX'])
        self.assertEqual(updated[-1]['max'], '*')
        self.assertEqual(updated[2]['fields']['1']['repetitions'][0]['components']['1']['source'],
                         'customFields.reference')
        self.assertEqual(result['inbound']['mappings'][0]['source'], 'ORC-4.1')
        self.assertEqual(outbound['events']['CANCEL']['segments'][0]['fields'][0]['value'], 'CA')
        self.assertTrue(updated[0]['fields']['12']['required'])
        self.assertTrue(updated[0]['fields']['12']['repetitions'][0]['components']['1']['required'])
        self.assertIn('2', updated[0]['fields'])

    def test_const_to_source_does_not_retain_example_or_old_constant(self):
        result = self.run_model("""
const schema={formatVersion:2,segments:[{name:'PID',fields:{'5':{repetitions:[{index:1,components:{
 '1':{const:'EXAMPLE',example:'PERSON',source:'old',transform:'component2',default:'OLD'}}}]}}}]};
window.HL7WorkshopModel.syncSchema(schema,[{name:'PID',fields:[{target:'PID-5.1',source:'patient.name',transform:'component1'}]}]);
console.log(JSON.stringify(schema.segments[0].fields['5'].repetitions[0].components['1']));
""")
        self.assertEqual(result, {'required': False, 'source': 'patient.name', 'transform': 'component1'})
