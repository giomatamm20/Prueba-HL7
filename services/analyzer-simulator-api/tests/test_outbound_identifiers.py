"""Filler identifiers for ORM orders without ORC-3 or OBR-3."""
import copy
from unittest.mock import patch

from app.engine import SimulationEngine
from app.hl7_contract import infer_hl7_contract
from app.hl7_orders import hl7_path_value, parse_order
from app.profile_builder import build_profile_message
from support import EngineCase, outgoing, receiver
from test_catalog_reception import ORM, urine_catalog


def filler_outbound():
    outbound = outgoing()
    segments = outbound["events"]["RESULT_FINAL"]["segments"]
    segments.insert(2, {"name": "ORC", "fields": [
        {"target": "ORC-1", "sourceType": "fixed", "value": "RE"},
        {"target": "ORC-2", "source": "order.id", "required": True},
        {"target": "ORC-3", "source": "order.fillerId", "required": True},
    ]})
    obr = next(segment for segment in segments if segment["name"] == "OBR")
    next(field for field in obr["fields"] if field["target"] == "OBR-3").update(source="order.fillerId", required=True)
    return outbound


class OutboundIdentifierTests(EngineCase):
    def test_custom_orc4_identifier_is_used_for_each_study_and_outbound_mapping(self):
        mappings = {"orderId": "ORC-4.1", "fillerOrderId": "ORC-4.1"}
        raw = ORM + "ORC|NW|OTHER-PLACER^SIS||OTHER-ORDER^SIS\rOBR|2|OTHER-PLACER^SIS||LAB_X6ED9^General de orina^L\r"
        parsed = parse_order(raw, inbound_mappings=mappings)
        self.assertEqual(parsed["orderId"], "TEST-2611050002")
        self.assertEqual(parsed["fillerOrderId"], "TEST-2611050002")
        self.assertEqual([s["orderId"] for s in parsed["studies"]], ["TEST-2611050002", "OTHER-ORDER"])
        self.assertEqual([s["fillerOrderId"] for s in parsed["studies"]], ["TEST-2611050002", "OTHER-ORDER"])
        outbound = filler_outbound()
        obr = next(s for s in outbound["events"]["RESULT_FINAL"]["segments"] if s["name"] == "OBR")
        obr["repeatBy"] = "studies"
        message, _ = build_profile_message({"outbound": outbound}, {"id": "sim"}, {**parsed, "id": "stored-order"}, [], "RESULT_FINAL", 1)
        rows = [row.split("|") for row in message.split("\r") if row.startswith("OBR|")]
        self.assertEqual([r[2] for r in rows], ["TEST-2611050002", "OTHER-ORDER"])
        self.assertEqual([r[3] for r in rows], ["TEST-2611050002", "OTHER-ORDER"])
        self.assertNotIn("SIM-stored-order", message)

    def test_received_orm_without_filler_can_preview_send_resend_and_restart(self):
        self.add()
        revision = self.engine.save_catalog(urine_catalog())
        self.engine.assign_catalog("test-analyzer", revision["id"])
        self.engine.save_protocol_profile("test-analyzer", {"name": "Required filler",
            "inbound": {"schema": infer_hl7_contract(ORM), "strictValidation": False}, "outbound": filler_outbound()})
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(ORM, "test-analyzer", "127.0.0.1"))
        order = self.engine.list_orders()[0]
        self.assertIsNone(order["fillerOrderId"])
        self.assertFalse(order["orc3"])
        self.assertFalse(order["obr3"])
        self.engine.load_sample(order["id"], order["barcode"])
        with patch("app.engine.time.sleep"):
            self.engine.process_order(order["id"], False)
        preview = self.engine.preview_order_results(order["id"])["message"]
        filler = hl7_path_value(preview, "ORC-3")
        self.assertTrue(filler.startswith("SIM-"))
        self.assertEqual(hl7_path_value(preview, "OBR-3"), filler)
        self.assertEqual(hl7_path_value(preview, "ORC-2"), order["orderId"])
        with receiver() as target:
            for _ in range(2):
                sent = self.engine.send_result(order["id"], "127.0.0.1", target.server_address[1])
                self.assertEqual(sent["status"], "COMPLETED")
            self.assertEqual([hl7_path_value(raw, "ORC-3") for raw in target.messages], [filler, filler])
        self.engine = SimulationEngine()
        restarted = self.engine.preview_order_results(order["id"])["message"]
        self.assertEqual(hl7_path_value(restarted, "ORC-3"), filler)
        recovered = self.engine.list_orders()[0]
        self.assertIsNone(recovered["fillerOrderId"])
        self.assertFalse(recovered["orc3"])
        self.assertFalse(recovered["obr3"])

    def test_existing_identifier_is_preserved_and_multiple_studies_do_not_share_it(self):
        outbound = filler_outbound()
        obr = next(s for s in outbound["events"]["RESULT_FINAL"]["segments"] if s["name"] == "OBR")
        obr["repeatBy"] = "studies"
        order = {"id": "stored-uuid", "orderId": "ORDER-1", "patientId": "SYNTHETIC", "fillerOrderId": "REAL-FILLER^LAB",
                 "studies": [
                     {"key": "study-1", "code": "CBC", "orderId": "ORDER-1", "fillerOrderId": "REAL-FILLER^LAB"},
                     {"key": "study-2", "code": "GLU", "orderId": "ORDER-2"},
                 ]}
        before = copy.deepcopy(order)
        first, _ = build_profile_message({"outbound": outbound}, {"id": "sim"}, order, [], "RESULT_FINAL", 1)
        second, _ = build_profile_message({"outbound": outbound}, {"id": "sim"}, order, [], "RESULT_FINAL", 2)
        filler_rows = lambda raw: [row.split("|")[3] for row in raw.split("\r") if row.startswith("OBR|")]
        self.assertEqual(hl7_path_value(first, "ORC-3"), "REAL-FILLER^LAB")
        fillers = filler_rows(first)
        self.assertEqual(fillers[0], "REAL-FILLER^LAB")
        self.assertTrue(fillers[1].startswith("SIM-"))
        self.assertNotEqual(fillers[1], fillers[0])
        self.assertEqual(filler_rows(second), fillers)
        self.assertEqual(order, before)
