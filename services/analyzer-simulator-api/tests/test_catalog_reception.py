"""Receiving unknown HL7 studies does not authorize synthetic result generation."""
import copy
from unittest.mock import patch

from starlette.testclient import TestClient

from app import main
from app.engine import SimulationEngine
from support import EngineCase, OML, exchange, free_port, install_contract, receiver


# Same message structure as the reported ORM, with synthetic identifiers only.
ORM = (
    "MSH|^~\\&|SIS|TEST|LIS|LF1|20261105161212-0600||ORM^O01^ORM_O01|CATALOG-ORM-1|T|2.5.1|||AL|NE\r"
    "PID|1||TEST-104^^^TEST^MR~TEST-218^^^TEST^PI||PRUEBA^SINTETICA||19990406|M\r"
    "PV1|1|O|LF1^^^^^^^^Laboratorio de prueba||||||||||||||||24\r"
    "ORC|NW|TEST-5312^SIS||TEST-2611050002^SIS|||||20261105161143-0600\r"
    "OBR|1|TEST-5312^SIS||LAB_X6ED9^General de orina^L||20261105161143-0600||||||||20261105161212-0600|OR&Orina&L|||O|||||||||\r"
)


def urine_catalog():
    return {"name": "Orina de prueba", "studies": [{"code": "LAB_X6ED9", "name": "General de orina", "parameters": [
        {"code": "PH", "name": "pH sintético", "valueType": "NM", "generator": {"kind": "fixed", "value": 6}}
    ]}]}


class CatalogReceptionTests(EngineCase):
    def test_orm_unknown_is_rejected_by_default_then_received_over_mllp_and_persisted(self):
        port = free_port()
        self.add(listenPort=port)
        install_contract(self.engine, "test-analyzer", ORM)
        rejected = self.engine.receive_hl7_order(ORM, "test-analyzer", "127.0.0.1")
        self.assertIn("MSA|AE|", rejected)
        self.assertIn("LAB_X6ED9", rejected)
        self.assertEqual(self.engine.list_orders(), [])
        self.engine.update_analyzer("test-analyzer", {"acceptUnknownTests": True})
        self.manager.sync("test-analyzer")
        accepted = exchange(port, ORM, fragments=True)
        self.assertIn("MSA|AA|CATALOG-ORM-1", accepted)
        order = self.engine.list_orders()[0]
        self.assertEqual(order["pendingCatalogStudies"], ["LAB_X6ED9"])
        self.assertEqual(order["studies"][0]["name"], "General de orina")
        self.assertIsNone(order["studies"][0]["definition"])
        self.assertEqual(order["results"], [])
        self.assertEqual(exchange(port, ORM), accepted)
        self.assertEqual(len(self.engine.list_orders()), 1)
        self.manager.stop()
        self.engine = SimulationEngine()
        self.assertTrue(self.engine.analyzers["test-analyzer"]["acceptUnknownTests"])
        self.assertEqual(self.engine.list_orders()[0]["pendingCatalogStudies"], ["LAB_X6ED9"])

    def test_pending_study_requires_definition_then_same_order_can_process_and_send(self):
        self.add(acceptUnknownTests=True)
        raw = OML.replace("CBC^Hemograma", "LAB_X6ED9^General de orina")
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(raw, "test-analyzer", "127.0.0.1"))
        order = self.engine.list_orders()[0]
        self.engine.load_sample(order["id"], order["barcode"])
        for action in (lambda: self.engine.process_order(order["id"], False),
                       lambda: self.engine.send_result(order["id"]),
                       lambda: self.engine.preview_order_results(order["id"])):
            with self.assertRaisesRegex(ValueError, "pendientes"):
                action()
        current = self.engine.list_orders()[0]
        self.assertEqual(current["status"], "SAMPLE_LOADED")
        self.assertEqual(current["results"], [])
        revision = self.engine.save_catalog(urine_catalog())
        self.engine.assign_catalog("test-analyzer", revision["id"], configure_outbound=True)
        with patch("app.engine.time.sleep"):
            ready = self.engine.process_order(order["id"], False)
        self.assertEqual(ready["pendingCatalogStudies"], [])
        self.assertEqual(ready["results"][0]["value"], 6)
        with receiver() as target:
            sent = self.engine.send_result(order["id"], "127.0.0.1", target.server_address[1])
            self.assertEqual(sent["status"], "COMPLETED")
            self.assertIn("LAB_X6ED9", target.messages[0])

    def test_assigned_catalog_mixed_known_and_unknown_and_explicit_template(self):
        self.add(acceptUnknownTests=True)
        revision = self.engine.save_catalog(urine_catalog())
        self.engine.assign_catalog("test-analyzer", revision["id"])
        raw = OML.replace("CBC^Hemograma", "LAB_X6ED9^General de orina") + "OBR|2|ORDER-1|SAMPLE-1|UNKNOWN^Pendiente\r"
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(raw, "test-analyzer", "127.0.0.1"))
        order = self.engine.list_orders()[0]
        self.assertEqual(order["pendingCatalogStudies"], ["UNKNOWN"])
        self.assertEqual(order["studies"][0]["definition"]["code"], "LAB_X6ED9")
        self.assertIsNone(order["studies"][1]["definition"])
        self.engine.assign_catalog("test-analyzer", revision["id"], "TEMPLATE", "LAB_X6ED9")
        self.engine.load_sample(order["id"], order["barcode"])
        with patch("app.engine.time.sleep"):
            ready = self.engine.process_order(order["id"], False)
        self.assertEqual(len(ready["results"]), 2)
        self.assertEqual(ready["catalogFallbacks"][0]["code"], "UNKNOWN")

    def test_reception_option_does_not_bypass_required_fields_or_manual_validation(self):
        self.add(acceptUnknownTests=True)
        self.engine.update_analyzer("test-analyzer", {"behavior": {"requiredSegments": ["MSH", "PID", "ORC", "OBR"], "requiredFields": ["PID-3"]}})
        raw = OML.replace("CBC^Hemograma", "LAB_X6ED9^General de orina").replace("PID|||PAT-SYNTHETIC||PRUEBA^SINTETICA||19800101|M\r", "")
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order(raw, "test-analyzer", "127.0.0.1"))
        self.assertEqual(self.engine.list_orders(), [])
        self.engine.set_analyzer_mode("test-analyzer", "MANUAL")
        with self.assertRaisesRegex(ValueError, "Unsupported tests"):
            self.engine.create_manual_sample({"analyzerId": "test-analyzer", "sampleId": "MANUAL", "tests": ["LAB_X6ED9"]})

    def test_api_save_assign_restart_and_reception_setting_preserves_equipment(self):
        self.add()
        before = copy.deepcopy(self.engine.analyzers["test-analyzer"])
        with patch.object(main, "engine", self.engine), patch.object(main, "listener_manager", self.manager), TestClient(main.app) as client:
            response = client.put("/api/analyzers/test-analyzer", json={"acceptUnknownTests": True})
            self.assertEqual(response.status_code, 200, response.text)
            updated = response.json()
            self.assertTrue(updated["acceptUnknownTests"])
            for key in ("supportedTests", "mode", "listenPort", "resultDestinationHost", "resultDestinationPort"):
                self.assertEqual(updated[key], before[key])
            saved = client.post("/api/catalogs", json=urine_catalog())
            self.assertEqual(saved.status_code, 201, saved.text)
            revision = saved.json()
            assigned = client.put("/api/analyzers/test-analyzer/catalog", json={"revisionId": revision["id"]})
            self.assertEqual(assigned.status_code, 200, assigned.text)
        rebooted = SimulationEngine()
        self.assertEqual(rebooted.catalog_revision(revision["id"])["studies"], urine_catalog()["studies"])
        self.assertEqual(rebooted.analyzers["test-analyzer"]["catalogRevisionId"], revision["id"])
