"""Exercise the existing dynamic editor's conversion, using Node when present."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from app.hl7_contract import infer_hl7_contract, validate_hl7_contract
from app.hl7_orders import hl7_path_value
from app.profile_builder import build_profile_message


@unittest.skipUnless(shutil.which("node"), "Node is required only for browser-editor conversion tests")
class EditorTests(unittest.TestCase):
    def test_visual_fixed_value_edit_changes_saved_contract_and_generated_message(self):
        raw = "MSH|^~\\&|SIM|LAB|HIS|LAB|202610151200||ORU^R01|EXAMPLE|P|2.5.1\rPID|||PATIENT\rZXX|ORIGINAL\r"
        schema = infer_hl7_contract(raw)
        html = (Path(__file__).resolve().parents[1] / "app/static/index.html").read_text(encoding="utf-8")
        functions = html[html.index("    function structuredSchemaToOutboundSegments("):html.index("    function applyCanvasPopup(")]
        script = "const schema=JSON.parse(require('fs').readFileSync(0,'utf8')); global.window={savedOutboundSchema:schema};\n" + functions
        script += "\nupdateSavedOutboundRule('ZXX-1',null,'CORRECTED'); console.log(JSON.stringify({schema,segments:structuredSchemaToOutboundSegments(schema)}));"
        result = subprocess.run([shutil.which("node"), "-e", script], input=json.dumps(schema),
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        edited = json.loads(result.stdout)
        profile = {"outbound": {"schema": edited["schema"], "events": {"RESULT_FINAL": {
            "messageType": "ORU^R01", "segments": edited["segments"]}}}}
        message, _ = build_profile_message(profile, {"id": "configured"}, {"patientId": "PATIENT"}, [], "RESULT_FINAL", 1)
        self.assertEqual(hl7_path_value(message, "ZXX-1"), "CORRECTED")
        validate_hl7_contract(message, edited["schema"])
