import copy
import os
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.engine import SimulationEngine
from app.hl7_contract import infer_hl7_contract
from app.hl7_orders import hl7_path_value
from app.profile_builder import build_profile_message
from support import EngineCase, OML, ASTM, outgoing, outbound_contract


class StabilizationTests(EngineCase):
    def profile(self, strict=True, **changes):
        return self.engine.save_protocol_profile("test-analyzer", {
            "name": "Synthetic contract", "inbound": {"schema": infer_hl7_contract(OML), "strictValidation": strict},
            "outbound": outbound_contract(), **changes})

    def test_strict_rejection_and_explicit_warning_mode(self):
        self.add()
        self.profile()
        invalid = OML.replace("19800101", "19801301")
        self.assertIn("MSA|AE|MSG-1", self.engine.receive_hl7_order(invalid, "test-analyzer", "127.0.0.1"))
        self.assertEqual(self.engine.list_orders(), [])
        self.profile(False)
        ack = self.engine.receive_hl7_order(invalid, "test-analyzer", "127.0.0.1")
        self.assertIn("MSA|AA|MSG-1", ack)
        self.assertIn("|W|||", ack)
        self.assertTrue(self.engine.list_messages()[0]["warnings"])

    def test_contract_does_not_disable_business_rules(self):
        self.add(behavior={"requiredFields": ["PID-3", "ZZZ-1"]})
        self.profile()
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1"))
        self.assertEqual(self.engine.list_orders(), [])

    def test_required_mapping_failure_returns_persisted_negative_ack(self):
        self.add()
        self.profile(inbound={"schema": infer_hl7_contract(OML), "messageType": "OML^O21", "mappings": [
            {"target": "patient.id", "source": "ZZZ-1", "required": True}]})
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1"))
        self.assertIn("Profile requires", self.engine.list_messages()[0]["error"])

    def test_concurrent_identical_retransmission_and_restart(self):
        self.add(behavior={"ackSuccessText": "AUTO_SEQUENCE"})
        with ThreadPoolExecutor(max_workers=8) as executor:
            replies = list(executor.map(lambda _: self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1"), range(16)))
        self.assertTrue(all(reply == replies[0] for reply in replies))
        self.assertIn("HIS-SEQ-", replies[0])
        self.assertEqual(len(self.engine.list_orders()), 1)
        rebooted = SimulationEngine()
        self.assertEqual(rebooted.receive_hl7_order(OML, "test-analyzer", "127.0.0.1"), replies[0])
        self.assertEqual(len(rebooted.list_orders()), 1)

    def test_new_message_ids_cannot_race_the_same_sample(self):
        self.add()
        with ThreadPoolExecutor(max_workers=8) as executor:
            replies = list(executor.map(lambda n: self.engine.receive_hl7_order(OML.replace("MSG-1", f"MSG-{n}"), "test-analyzer", "127.0.0.1"), range(16)))
        self.assertEqual(sum("MSA|AA|" in reply for reply in replies), 1)
        self.assertEqual(len(self.engine.store.load_orders()), 1)

    def test_reused_message_id_with_changed_content_is_rejected(self):
        self.add()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        ack = self.engine.receive_hl7_order(OML.replace("CBC^Hemograma", "GLU^Glucosa"), "test-analyzer", "127.0.0.1")
        self.assertIn("MSA|AE|", ack)
        self.assertEqual(self.engine.list_orders()[0]["requestedTests"], ["CBC"])

    def test_explicit_update_and_cancel_preserve_identity_and_are_idempotent(self):
        self.add(behavior={"allowedOrderControls": ["NW", "XO", "CA"]})
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        original_id = self.engine.list_orders()[0]["id"]
        update = OML.replace("MSG-1", "MSG-UPDATE").replace("ORC|NW", "ORC|XO").replace("CBC^Hemograma", "GLU^Glucosa")
        ack = self.engine.receive_hl7_order(update, "test-analyzer", "127.0.0.1")
        self.assertIn("MSA|AA|", ack)
        order = self.engine.list_orders()[0]
        self.assertEqual(order["id"], original_id)
        self.assertEqual(order["requestedTests"], ["GLU"])
        self.assertEqual(self.engine.receive_hl7_order(update, "test-analyzer", "127.0.0.1"), ack)
        wrong = update.replace("MSG-UPDATE", "MSG-WRONG").replace("PAT-SYNTHETIC", "OTHER-PATIENT")
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order(wrong, "test-analyzer", "127.0.0.1"))
        cancel = OML.replace("MSG-1", "MSG-CANCEL").replace("ORC|NW", "ORC|CA")
        ack = self.engine.receive_hl7_order(cancel, "test-analyzer", "127.0.0.1")
        self.assertEqual(self.engine.list_orders()[0]["status"], "CANCELLED")
        self.assertEqual(self.engine.receive_hl7_order(cancel, "test-analyzer", "127.0.0.1"), ack)

    def test_order_update_requires_explicit_configuration(self):
        self.add()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        update = OML.replace("MSG-1", "MSG-UPDATE").replace("ORC|NW", "ORC|XO")
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order(update, "test-analyzer", "127.0.0.1"))

    def test_transaction_failure_rolls_back_memory_order_and_receipt(self):
        self.add()
        with patch.object(self.engine.store, "save_order", side_effect=sqlite3.OperationalError("synthetic disk failure")):
            ack = self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        self.assertIn("MSA|AE|", ack)
        self.assertEqual(self.engine.list_orders(), [])
        self.assertEqual(self.engine.store.load_orders(), [])
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1"))

    def test_revision_history_isolated_and_pinned_to_original_order(self):
        self.add()
        self.add("other", contract=False)
        original = self.profile()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        revised = self.engine.update_protocol_profile("test-analyzer", original["id"], {**original, "name": "Revised", "activate": True})
        self.assertNotEqual(original["id"], revised["id"])
        self.assertGreater(revised["version"], original["version"])
        self.assertEqual(self.engine.list_orders()[0]["inboundProfileId"], original["id"])
        self.assertIsNone(self.engine.store.get_active_protocol_profile("other"))
        old = next(p for p in self.engine.list_protocol_profiles("test-analyzer") if p["id"] == original["id"])
        self.assertEqual(old["name"], "Synthetic contract")
        self.assertEqual(old["status"], "ARCHIVED")
        self.engine.delete_protocol_profile("test-analyzer", revised["id"])
        self.assertFalse(self.engine.communication_ready("test-analyzer"))
        self.engine.delete_protocol_profile("test-analyzer", original["id"])
        self.assertIn(original["id"], [p["id"] for p in self.engine.list_protocol_profiles("test-analyzer")])

    def test_invalid_profile_definitions_leave_active_revision_unchanged(self):
        self.add()
        active = self.profile()
        invalids = [
            {"inbound": {"mappings": [{"target": "patient.id", "source": "PID-0"}]}},
            {"inbound": {"schema": {"formatVersion": 2, "segments": []}}},
            {"inbound": {"validationMode": "IGNORE"}},
            {"outbound": {"events": {"RESULT_FINAL": {"messageType": "ORU^R01", "segments": []}}}},
            {"outbound": {"events": {"RESULT_FINAL": 7}}},
            {"outbound": {"events": {"RESULT_FINAL": {"segments": [{"name": "PID"}]}}}},
        ]
        for invalid in invalids:
            with self.subTest(definition=invalid), self.assertRaises(ValueError):
                self.profile(**invalid)
        self.assertEqual(self.engine.store.get_active_protocol_profile("test-analyzer")["id"], active["id"])

    def test_missing_required_outbound_mapping_and_undefined_event_are_controlled(self):
        profile = {"outbound": outgoing()}
        profile["outbound"]["events"]["RESULT_FINAL"]["segments"][1]["fields"][0]["source"] = "customFields.missing"
        with self.assertRaisesRegex(ValueError, "requires"):
            build_profile_message(profile, {"id": "a"}, {}, [], "RESULT_FINAL", 1)
        with self.assertRaisesRegex(ValueError, "does not define"):
            build_profile_message(profile, {"id": "a"}, {}, [], "DELETE_SAMPLE", 1)

    def test_negotiated_separators_and_three_component_message_type(self):
        self.add()
        special = OML.translate(str.maketrans("|^~\\&", "!$%?@"))
        ack = self.engine.receive_hl7_order(special, "test-analyzer", "127.0.0.1")
        self.assertIn("MSA!AA!MSG-1", ack)
        self.assertEqual(self.engine.list_orders()[0]["patientName"], "PRUEBA^SINTETICA")
        other = OML.replace("OML^O21", "OML^O21^OML_O21").replace("MSG-1", "MSG-2").replace("SAMPLE-1", "SAMPLE-2")
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(other, "test-analyzer", "127.0.0.1"))

    def test_malformed_hl7_and_invalid_separators_do_not_create_orders(self):
        self.add()
        for raw in ("not HL7", OML.replace("^~\\&", "^^^^"), OML.replace("|MSG-1|", "||"), OML.replace("PAT-SYNTHETIC", "\x00")):
            with self.subTest(raw=raw[:20]):
                self.assertIn("MSA|AE|", self.engine.receive_hl7_order(raw, "test-analyzer", "127.0.0.1"))
        self.assertEqual(self.engine.list_orders(), [])

    def test_interrupted_operations_recover_without_resending(self):
        self.add()
        self.engine.receive_hl7_order(OML, "test-analyzer", "127.0.0.1")
        order = self.engine.orders[0]
        for state, expected in (("PROCESSING", "SAMPLE_LOADED"), ("SENDING_RESULT", "RESULT_READY"),
                                ("GENERATING_IMAGE", "ORDER_RECEIVED"), ("NOTIFYING_MIRTH", "NOTIFICATION_ERROR")):
            with self.subTest(state=state):
                order["status"] = state
                self.engine.store.save_order(order)
                rebooted = SimulationEngine()
                self.assertEqual(rebooted.list_orders()[0]["status"], expected)
                self.assertIn("explicit retry", rebooted.list_orders()[0]["recoveryNote"])

    def test_astm_idempotence_and_plain_code_ending_in_hex_letters(self):
        self.add(protocol="ASTM", supportedTests=["CBC"])
        self.assertTrue(self.engine.receive_astm_order(ASTM, "test-analyzer", "127.0.0.1"))
        self.assertTrue(self.engine.receive_astm_order(ASTM, "test-analyzer", "127.0.0.1"))
        self.assertEqual(self.engine.list_orders()[0]["requestedTests"], ["CBC"])
        self.assertEqual(len(self.engine.list_orders()), 1)
        self.assertFalse(self.engine.receive_astm_order(ASTM.replace("CBC", "BAD"), "test-analyzer", "127.0.0.1"))
