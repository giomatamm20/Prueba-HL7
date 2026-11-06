"""Semantic changes use the same converter as the unified profile import."""
import unittest

from app.hl7_contract import infer_hl7_contract, validate_hl7_contract
from app.hl7_orders import hl7_path_value
from app.profile_builder import build_profile_message
from app.equipment_profile import schema_routes


class EditorTests(unittest.TestCase):
    def test_semantic_json_edit_changes_saved_contract_and_generated_message(self):
        raw = "MSH|^~\\&|SIM|LAB|HIS|LAB|202610151200||ORU^R01|EXAMPLE|P|2.5.1\rPID|||PATIENT\rZXX|ORIGINAL\r"
        schema = infer_hl7_contract(raw)
        schema['segments'][-1]['fields']['1']['repetitions'][0]['components']['1']['const'] = 'CORRECTED'
        profile = {"outbound": {"schema": schema, "events": {"RESULT_FINAL": {
            "messageType": "ORU^R01", "segments": schema_routes(schema)}}}}
        message, _ = build_profile_message(profile, {"id": "configured"}, {"patientId": "PATIENT", "sourceRawMessage": raw}, [], "RESULT_FINAL", 1)
        self.assertEqual(hl7_path_value(message, "ZXX-1"), "CORRECTED")
        validate_hl7_contract(message, schema)
