"""Exercise the production equipment draft with out-of-order API responses."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is required for browser loader regressions')
class ConfigurationLoadingTests(unittest.TestCase):
    def run_js(self, code):
        model = Path(__file__).resolve().parents[1] / 'app/static/equipment-profile-model.js'
        code = 'global.window={};\n' + model.read_text(encoding='utf-8') + '\n' + code
        result = subprocess.run([shutil.which('node'), '-e', code], capture_output=True, text=True,
                                encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_anapath_and_other_hl7_equipment_share_the_profile_editor(self):
        result = self.run_js("""
const choices=window.EquipmentProfileModel.equipmentChoices([
 {id:'ana',profile:'ANAPATH',protocol:'HL7'},{id:'generic',protocol:'HL7'},
 {id:'pacs',profile:'PACS',protocol:'HL7'},{id:'astm',protocol:'ASTM'}]);
console.log(JSON.stringify(choices.map(a=>a.id)));
""")
        self.assertEqual(result, ['ana', 'generic', 'pacs'])

    def test_loads_once_and_keeps_unsaved_edits_when_reopened(self):
        result = self.run_js("""
let requests=0;const draft=new window.EquipmentProfileModel.EquipmentDraft(async()=>{
 requests++;return {name:'Guardado',version:3,inbound:{mappings:[{source:'ORC-4.1'}]},outbound:{},catalog:{studies:[]}};});
(async()=>{await draft.load('alpha');await draft.load('alpha');draft.change(p=>p.name='Sin guardar');
 await draft.load('alpha');let blocked=false;try{await draft.load('beta');}catch{blocked=true;}
 console.log(JSON.stringify({requests,name:draft.profile.name,source:draft.profile.inbound.mappings[0].source,blocked}));})();
""")
        self.assertEqual(result, {'requests': 1, 'name': 'Sin guardar', 'source': 'ORC-4.1', 'blocked': True})

    def test_pending_reload_does_not_replace_name_or_catalog_being_edited(self):
        result = self.run_js("""
let pending;const draft=new window.EquipmentProfileModel.EquipmentDraft(()=>new Promise(resolve=>pending=resolve));
(async()=>{const initial=draft.load('alpha');pending({name:'Original',version:1,inbound:{},outbound:{},catalog:{studies:[]}});await initial;
 const reloading=draft.load('alpha',{force:true});draft.change(p=>{p.name='Escrito';p.catalog.studies.push({code:'NUEVO'});});
 pending({name:'Antiguo',version:2,inbound:{},outbound:{},catalog:{studies:[]}});const applied=await reloading;
 console.log(JSON.stringify({applied,name:draft.profile.name,study:draft.profile.catalog.studies[0].code}));})();
""")
        self.assertEqual(result, {'applied': False, 'name': 'Escrito', 'study': 'NUEVO'})

    def test_preview_ignores_responses_for_an_old_edit_or_another_analyzer(self):
        result = self.run_js("""
let pending=[];const draft=new window.EquipmentProfileModel.EquipmentDraft(()=>new Promise(resolve=>pending.push(resolve)));
(async()=>{const loading=draft.load('alpha');pending.shift()({name:'QA',version:1,inbound:{},outbound:{},catalog:{studies:[]}});await loading;
 const first=draft.preview();draft.change(p=>p.name='Actual');const second=draft.preview();
 pending[1]({message:'CURRENT'});const current=await second;pending[0]({message:'OLD'});const old=await first;
 pending=[];const third=draft.preview();draft.analyzerId='beta';pending[0]({message:'ALPHA'});const switched=await third;
 console.log(JSON.stringify({current:current.message,old,switched}));})();
""")
        self.assertEqual(result, {'current': 'CURRENT', 'old': None, 'switched': None})
