"""Run the actual browser loaders with deterministic asynchronous API responses."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node is required for browser loader regressions")
class ConfigurationLoadingTests(unittest.TestCase):
    def run_js(self, code):
        result = subprocess.run([shutil.which("node"), "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_catalog_lists_anapath_as_a_laboratory_analyzer(self):
        js = (Path(__file__).resolve().parents[1] / "app/static/catalog.js").read_text(encoding="utf-8")
        loader = js[js.index("async function refreshAnalyzers()"):js.index("function bind(id,")]
        script = """
const nodes={analyzer:{value:'ana',innerHTML:''}};const $=id=>nodes[id];let analyzers=[];
const escapeHTML=s=>s;function renderAssignment(){};
async function request(){return [{id:'ana',name:'AnaPath LIS',profile:'ANAPATH',protocol:'HL7'},
 {id:'generic',name:'Genérico',profile:'GENERIC',protocol:'HL7'},
 {id:'pacs',name:'PACS',profile:'PACS',protocol:'HL7'}, {id:'astm',protocol:'ASTM'}];}
""" + loader + "\nrefreshAnalyzers().then(()=>console.log(JSON.stringify({ids:analyzers.map(a=>a.id),html:nodes.analyzer.innerHTML})));"
        result = self.run_js(script)
        self.assertEqual(result["ids"], ["ana", "generic"])
        self.assertIn("AnaPath LIS", result["html"])

    def test_designer_loads_saved_active_or_draft_once_and_keeps_unsaved_edits(self):
        html = (Path(__file__).resolve().parents[1] / "app/static/index.html").read_text(encoding="utf-8")
        loader = html[html.index("    function ensureProtocolProfileLoaded()"):html.index("    async function createSample()")]
        script = """
global.window={};let loadedProtocolAnalyzerId=null,loadingProtocolAnalyzerId=null,protocolRequestSequence=0,protocolEditorDirty=false,protocolEditorGeneration=0,protocolLearningPending=false;const analyzerList=[];
const nodes={profileAnalyzer:{value:'alpha'},profileStatus:{},saveProfileButton:{},profileVersions:{}};
const el=id=>nodes[id];let loaded=[];const renderProtocolProfileCard=p=>p.id;
const hydrateProtocolProfile=p=>{loaded.push(p.id);nodes.orderSource=p.inbound.mappings[0].source;};
const resetProtocolProfile=()=>{nodes.orderSource='DEFAULT';};
const fixtures={alpha:[{id:'active',name:'Saved',version:3,status:'ACTIVE',inbound:{mappings:[{source:'ORC-4.1'}]}}],
 beta:[{id:'old-draft',version:1,status:'DRAFT',inbound:{mappings:[{source:'ORC-2'}]}},
       {id:'new-draft',name:'Draft',version:2,status:'DRAFT',inbound:{mappings:[{source:'OBR-2.1'}]}}]};
async function get(path){return fixtures[path.split('/')[3]];}
""" + loader + """
(async()=>{await ensureProtocolProfileLoaded();const active=nodes.orderSource;nodes.orderSource='UNSAVED';
 await ensureProtocolProfileLoaded();const retained=nodes.orderSource;nodes.profileAnalyzer.value='beta';
 await ensureProtocolProfileLoaded();console.log(JSON.stringify({active,retained,draft:nodes.orderSource,loaded,
 editing:window.editingProfileId,status:nodes.profileStatus.textContent}));})().catch(e=>{console.error(e);process.exit(1)});
"""
        result = self.run_js(script)
        self.assertEqual(result["active"], "ORC-4.1")
        self.assertEqual(result["retained"], "UNSAVED")
        self.assertEqual(result["draft"], "OBR-2.1")
        self.assertEqual(result["loaded"], ["active", "new-draft"])
        self.assertEqual(result["editing"], "new-draft")
        self.assertIn("Borrador guardado", result["status"])

    def test_pending_profile_load_does_not_replace_name_being_edited(self):
        html = (Path(__file__).resolve().parents[1] / "app/static/index.html").read_text(encoding="utf-8")
        loader = html[html.index("    function ensureProtocolProfileLoaded()"):html.index("    async function createSample()")]
        result = self.run_js("""
global.window={};let loadedProtocolAnalyzerId=null,loadingProtocolAnalyzerId=null,protocolRequestSequence=0;
let protocolEditorDirty=false,protocolEditorGeneration=0,protocolLearningPending=false;const analyzerList=[];
const nodes={profileAnalyzer:{value:'alpha'},profileName:{value:'Original'},profileStatus:{},saveProfileButton:{},profileVersions:{}};
const el=id=>nodes[id];let hydrateCalls=0,requests=0,resolve;
function hydrateProtocolProfile(p){hydrateCalls++;nodes.profileName.value=p.name;}
const resetProtocolProfile=()=>{};const renderProtocolProfileCard=p=>p.name;
function get(){requests++;return new Promise(done=>resolve=done);}
""" + loader + """
(async()=>{const loading=ensureProtocolProfileLoaded();nodes.profileName.value='Mi contrato de orina';
 protocolEditorDirty=true;protocolEditorGeneration++;
 resolve([{id:'saved',name:'Nombre anterior',version:1,status:'ACTIVE'},
 {id:'deleted',name:'Eliminado',version:2,status:'RETIRED'}]);await loading;await ensureProtocolProfileLoaded();
 console.log(JSON.stringify({name:nodes.profileName.value,hydrateCalls,requests,visible:window.cachedProfiles.map(p=>p.id)}));
})().catch(e=>{console.error(e);process.exit(1)});
""")
        self.assertEqual(result["name"], "Mi contrato de orina")
        self.assertEqual(result["hydrateCalls"], 0)
        self.assertEqual(result["requests"], 1)
        self.assertEqual(result["visible"], ["saved"])
