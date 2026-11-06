"""Catalog and designer revisions survive engine restarts, including AnaPath."""
import copy
from unittest.mock import patch

from app.engine import SimulationEngine
from app.hl7_contract import infer_hl7_contract
from app.study_catalog import dynamic_outbound
from support import EngineCase
from test_catalog_reception import ORM, urine_catalog

ANAPATH_ORDER = ORM.replace("ORM^O01^ORM_O01", "OML^O21")


class ConfigurationPersistenceTests(EngineCase):
    def test_anapath_catalog_assignment_and_designer_revisions_survive_restart(self):
        self.add(profile="ANAPATH", acceptUnknownTests=True)
        inbound = {"schema": infer_hl7_contract(ANAPATH_ORDER), "rawHL7": ANAPATH_ORDER, "strictValidation": False, "mappings": [
            {"target": "order.id", "source": "ORC-4.1", "required": True},
            {"target": "sample.id", "source": "ORC-4.1", "required": True},
            {"target": "patient.id", "source": "PID-3.1"},
        ]}
        profile = self.engine.save_protocol_profile("test-analyzer", {"name": "Persistencia diseñador",
            "inbound": inbound, "outbound": dynamic_outbound()})
        catalog = self.engine.save_catalog(urine_catalog())
        self.engine.assign_catalog("test-analyzer", catalog["id"])
        active = self.engine.store.get_active_protocol_profile("test-analyzer")
        self.assertEqual(active["id"], profile["id"])
        self.assertEqual(active["outbound"], profile["outbound"])
        self.assertEqual(active["inbound"], profile["inbound"])

        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(ANAPATH_ORDER, "test-analyzer", "127.0.0.1"))
        order = self.engine.list_orders()[0]
        self.assertEqual(order["orderId"], "TEST-2611050002")
        self.engine.load_sample(order["id"], order["barcode"])
        with patch("app.engine.time.sleep"):
            result = self.engine.process_order(order["id"], False)
        self.assertEqual(result["results"][0]["value"], 6)

        revised_definition = urine_catalog()
        revised_definition["studies"][0]["parameters"][0]["generator"]["value"] = 7
        revised = self.engine.save_catalog(revised_definition, catalog["catalogId"])
        self.engine.assign_catalog("test-analyzer", revised["id"])
        revised_inbound = copy.deepcopy(inbound)
        revised_inbound["mappings"][0]["alternatives"] = ["ORC-2.1"]
        revised_profile = self.engine.update_protocol_profile("test-analyzer", profile["id"], {
            "name": "Persistencia diseñador", "inbound": revised_inbound, "outbound": dynamic_outbound(), "activate": True})
        rebooted = SimulationEngine()
        analyzer = rebooted.analyzers["test-analyzer"]
        self.assertEqual(analyzer["catalogRevisionId"], revised["id"])
        self.assertTrue(analyzer["acceptUnknownTests"])
        self.assertEqual(rebooted.catalog_revision(catalog["id"])["studies"], urine_catalog()["studies"])
        self.assertEqual(rebooted.catalog_revision(revised["id"])["studies"], revised_definition["studies"])
        recovered = rebooted.store.get_active_protocol_profile("test-analyzer")
        for key in ("id", "version", "inbound", "outbound"):
            self.assertEqual(recovered[key], revised_profile[key])
        self.assertEqual(recovered["inbound"]["rawHL7"], ANAPATH_ORDER)
        self.assertEqual(next(p for p in rebooted.list_protocol_profiles("test-analyzer") if p["id"] == profile["id"])["status"], "ARCHIVED")
        self.assertEqual(rebooted.list_orders()[0]["results"][0]["value"], 6)
        self.assertIn("OBX|", rebooted.preview_order_results(order["id"])["message"])

    def test_unassigned_anapath_keeps_legacy_report_workflow(self):
        self.add(profile="ANAPATH")
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(ANAPATH_ORDER, "test-analyzer", "127.0.0.1"))
        order = self.engine.list_orders()[0]
        self.assertIsNone(order.get("catalogRevisionId"))
        self.assertTrue(all(not s.get("definition") for s in order["studies"]))

    def test_pacs_still_cannot_receive_a_laboratory_catalog(self):
        catalog = self.engine.save_catalog(urine_catalog())
        with self.assertRaisesRegex(ValueError, "laboratorio"):
            self.engine.assign_catalog("pacs-simulator", catalog["id"])
