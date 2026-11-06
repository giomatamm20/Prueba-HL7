"""Exercise the basic editor against inferred schemas and the real HL7 writer."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from app.hl7_contract import infer_hl7_contract, validate_hl7_contract
from app.hl7_orders import hl7_path_value
from app.profile_builder import build_profile_message
from app.profile_parser import parse_with_profile
from app.profile_validation import validate_profile
from test_catalog_reception import ORM


ORU = (
    "MSH|^~\\&|SIM|LAB|HIS|LAB|20261105163000||ORU^R01|EXAMPLE-RESPONSE|T|2.5.1\r"
    "PID|||SYNTHETIC||PRUEBA^SINTETICA\rORC|RE|OLD-ORDER|OLD-FILLER\r"
    "OBR|1|OLD-ORDER|OLD-FILLER|LAB_X6ED9^General de orina\r"
    "OBX|1|NM|PH^pH||6||||||F\r"
)


@unittest.skipUnless(shutil.which("node"), "Node is required for editor tests")
class BasicHL7EditorTests(unittest.TestCase):
    def run_editor(self, values=None, edited=(), mode="apply"):
        static = Path(__file__).resolve().parents[1] / "app/static"
        html = (static / "index.html").read_text(encoding="utf-8")
        conversion = html[html.index("    function structuredSchemaToOutboundSegments("):html.index("    function applyCanvasPopup(")]
        script = "const fs=require('fs');const input=JSON.parse(fs.readFileSync(0,'utf8'));global.window={};global.document={getElementById:()=>null};\n"
        script += (static / "basic-hl7.js").read_text(encoding="utf-8") + "\n" + conversion
        script += """
try {
  const profile=input.profile;
  profile.outbound.events={RESULT_FINAL:{messageType:'ORU^R01',segments:structuredSchemaToOutboundSegments(profile.outbound.schema)}};
  if(input.mode==='read')console.log(JSON.stringify(window.basicHL7.readExample(input.raw,input.locations)));
  else console.log(JSON.stringify(window.basicHL7.applyMappings(profile,input.values,input.edited)));
} catch(error) {console.log(JSON.stringify({error:error.message}));}
"""
        profile = {"name": "Basic fields", "inbound": {"schema": infer_hl7_contract(ORM)},
                   "outbound": {"schema": infer_hl7_contract(ORU)}}
        values = {"patient.id": {"from": "PID-3.1", "to": "PID-3"},
                  "order.id": {"from": "ORC-2.1", "to": "ORC-2, OBR-2"}, **(values or {})}
        data = {"profile": profile, "values": values, "edited": edited, "mode": mode,
                "raw": ORM, "locations": "ORC-3.1, ORC-4.1"}
        result = subprocess.run([shutil.which("node"), "-e", script], input=json.dumps(data),
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_custom_field_from_inferred_base_is_used_in_response_and_schema(self):
        profile = self.run_editor({
            "order.id": {"from": "ORC-4.1", "to": "ORC-4.1, OBR-2", "required": True},
            "order.fillerId": {"from": "ORC-4.1", "to": "ORC-3, OBR-3"},
        }, ["order.id", "order.fillerId"])
        self.assertNotIn("error", profile, profile)
        profile = validate_profile(profile)
        order = {**parse_with_profile(ORM, profile["inbound"]), "id": "persisted-order"}
        self.assertEqual(order["orderId"], "TEST-2611050002")
        message, _ = build_profile_message(profile, {"id": "sim"}, order,
            [{"code": "PH", "value": 6}], "RESULT_FINAL", 1)
        self.assertEqual(hl7_path_value(message, "ORC-4.1"), "TEST-2611050002")
        self.assertEqual(hl7_path_value(message, "OBR-2"), "TEST-2611050002")
        self.assertEqual(hl7_path_value(message, "ORC-3"), "TEST-2611050002")
        validate_hl7_contract(message, profile["outbound"]["schema"])
        recovered = json.loads(json.dumps(profile))
        self.assertEqual(parse_with_profile(ORM, recovered["inbound"])["orderId"], "TEST-2611050002")

    def test_empty_output_removes_mapping_and_its_required_schema_rule(self):
        profile = self.run_editor({"order.fillerId": {"from": "", "to": ""}}, ["order.fillerId"])
        self.assertNotIn("error", profile, profile)
        parsed = parse_with_profile(ORM, profile["inbound"])
        message, _ = build_profile_message(profile, {"id": "sim"}, {**parsed, "id": "stored"},
            [{"code": "PH", "value": 6}], "RESULT_FINAL", 1)
        self.assertFalse(hl7_path_value(message, "ORC-3"))
        self.assertFalse(hl7_path_value(message, "OBR-3"))
        validate_hl7_contract(message, profile["outbound"]["schema"])

    def test_bad_paths_conflicts_and_missing_required_source_have_clear_errors(self):
        for values in (
            {"order.id": {"from": "4.1"}},
            {"order.id": {"from": "ORC-4.1", "to": "ORC-4"}, "test.code": {"to": "ORC-4.1"}},
            {"order.id": {"from": "", "required": True}},
        ):
            with self.subTest(values=values):
                self.assertIn("error", self.run_editor(values))

    def test_sample_value_uses_alternatives_and_component_not_the_exam_code(self):
        self.assertEqual(self.run_editor(mode="read"), "TEST-2611050002")
