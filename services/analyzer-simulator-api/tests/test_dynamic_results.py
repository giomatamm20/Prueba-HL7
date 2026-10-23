"""Synthetic catalog acceptance: real MLLP sockets, received HL7 and durable state."""
import copy
import json
from unittest.mock import patch

from starlette.testclient import TestClient

from app import main
from app.engine import SimulationEngine
from app.hl7_orders import parse_order
from app.profile_parser import parse_with_profile
from app.study_catalog import dynamic_outbound, infer_studies, validate_catalog
from support import EngineCase, OML, exchange, free_port, receiver


def parameter(code, value_type, generator, **extras):
    return {"code": code, "name": code + " sintético", "valueType": value_type, "generator": generator, **extras}


def definition():
    return {"name": "Aceptación sintética", "studies": [
        {"code": "MIX", "name": "Estudio mixto", "aliases": ["EXT-MIX"], "parameters": [
            parameter("NEW-NUM", "NM", {"kind": "numeric", "min": 1, "max": 2, "decimals": 2}, unit="u-prueba"),
            parameter("NEW-TEXT", "TX", {"kind": "fixed", "value": "texto ^ & | ~ de prueba"}),
            parameter("NEW-CODE", "CWE", {"kind": "choice", "values": [{"code": "X", "name": "Código de prueba", "system": "SIM"}]})]},
        {"code": "CULT", "name": "Cultivo de prueba", "parameters": [parameter("CULTURE", "ST", {"kind": "fixed", "value": "Crecimiento de prueba"})],
         "groups": [{"id": "isolate", "name": "Aislamiento", "count": 2, "parameters": [
             parameter("ORG", "CE", {"kind": "sequence", "values": [
                 {"code": "ORG1", "name": "Microorganismo sintético 1", "system": "SIM"},
                 {"code": "ORG2", "name": "Microorganismo sintético 2", "system": "SIM"}]})],
             "groups": [{"id": "ast", "name": "Sensibilidad", "count": 2, "parameters": [
                 parameter("AB", "CE", {"kind": "sequence", "values": [
                     {"code": "AB1", "name": "Antibiótico sintético 1", "system": "SIM"},
                     {"code": "AB2", "name": "Antibiótico sintético 2", "system": "SIM"}]}),
                 parameter("CONC", "NM", {"kind": "fixed", "value": 2}, unit="u-prueba"),
                 parameter("INTERP", "ST", {"kind": "choice", "values": ["S", "I", "R"]}, note="Nota de prueba")]}]}],
         "scenarios": {"NEGATIVE": {"groups": {"isolate": 0}, "parameters": {"CULTURE": {"kind": "fixed", "value": "Sin crecimiento de prueba"}}}}}]}


def mixed_order():
    return OML.replace("CBC^Hemograma", "EXT-MIX^Mixto").replace("SAMPLE-1|EXT", "SAMPLE-A|EXT") + "ORC|NW|ORDER-2\rOBR|2|ORDER-2|SAMPLE-B|CULT^Cultivo\rSPM|1|SAMPLE-B||SYN^Muestra sintética\r"


class DynamicResultsTests(EngineCase):
    def setup_catalog(self, port=0):
        self.add(listenPort=port)
        revision = self.engine.save_catalog(definition())
        self.engine.assign_catalog("test-analyzer", revision["id"], configure_outbound=True)
        return revision

    def process(self, raw=None):
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(raw or mixed_order(), "test-analyzer", "127.0.0.1"))
        order = self.engine.list_orders()[-1]
        self.engine.load_sample(order["id"], order["barcode"])
        with patch("app.engine.time.sleep"):
            return self.engine.process_order(order["id"], False)

    def test_multistudy_mixed_types_two_isolates_real_mllp_and_stable_resend(self):
        port = free_port()
        revision = self.setup_catalog(port)
        self.manager.sync("test-analyzer")
        self.assertIn("MSA|AA|", exchange(port, mixed_order(), fragments=True))
        order = self.engine.list_orders()[0]
        self.assertEqual(order["requestedTests"], ["EXT-MIX", "CULT"])
        self.assertEqual([s["orderId"] for s in order["studies"]], ["ORDER-1", "ORDER-2"])
        self.assertEqual([s["sampleId"] for s in order["studies"]], ["SAMPLE-A", "SAMPLE-B"])
        self.engine.load_sample(order["id"], order["barcode"])
        with patch("app.engine.time.sleep"):
            ready = self.engine.process_order(order["id"], False)
        self.assertEqual(len(ready["results"]), 18)
        before = copy.deepcopy(ready["results"])
        preview = self.engine.preview_order_results(order["id"])
        self.assertEqual(preview["results"], before)
        with receiver() as target:
            for _ in range(2):
                complete = self.engine.send_result(order["id"], "127.0.0.1", target.server_address[1])
                self.assertEqual(complete["status"], "COMPLETED")
                self.assertEqual(complete["lastMessage"]["ackCode"], "AA")
            first = [s.split("|") for s in target.messages[0].split("\r") if s]
            second = [s.split("|") for s in target.messages[1].split("\r") if s]
            obrs = [r for r in first if r[0] == "OBR"]
            self.assertEqual([r[4].split("^")[0] for r in obrs], ["EXT-MIX", "CULT"])
            self.assertEqual([r[2] for r in obrs], ["ORDER-1", "ORDER-2"])
            obxs = [r for r in first if r[0] == "OBX"]
            self.assertEqual(obxs, [r for r in second if r[0] == "OBX"])
            self.assertEqual({r[2] for r in obxs}, {"NM", "TX", "ST", "CE", "CWE"})
            text_row = next(r for r in obxs if r[3].startswith("NEW-TEXT^"))
            self.assertIn("\\S\\", text_row[5]); self.assertIn("\\F\\", text_row[5]); self.assertIn("\\R\\", text_row[5])
            organisms = [r for r in obxs if r[3].startswith("ORG^")]
            self.assertEqual([r[4] for r in organisms], ["isolate.1", "isolate.2"])
            self.assertEqual([r[5].split("^")[0] for r in organisms], ["ORG1", "ORG2"])
            for group in ("isolate.1", "isolate.2"):
                children = [r for r in obxs if r[4].startswith(group + ".ast.")]
                self.assertEqual(len(children), 6)
                self.assertEqual({r[4] for r in children}, {group + ".ast.1", group + ".ast.2"})
                self.assertEqual([r[5].split("^")[0] for r in children if r[3].startswith("AB^")], ["AB1", "AB2"])
            self.assertEqual(len([r for r in first if r[0] == "NTE"]), 4)
        rebooted = SimulationEngine()
        recovered = rebooted.list_orders()[0]
        self.assertEqual(recovered["results"], before)
        self.assertEqual(recovered["catalogRevisionId"], revision["id"])
        self.assertEqual(rebooted.catalog_revision(revision["id"])["studies"], revision["studies"])
        self.assertEqual(rebooted.analyzers["test-analyzer"]["catalogRevisionId"], revision["id"])

    def test_new_parameter_new_order_without_restart_and_pinned_old_generation(self):
        original = self.setup_catalog()
        old = self.process()
        updated_definition = definition()
        updated_definition["studies"][0]["parameters"].append(parameter("ADDED-LIVE", "ST", {"kind": "fixed", "value": "Nuevo parámetro"}))
        revised = self.engine.save_catalog(updated_definition, original["catalogId"])
        self.assertEqual(revised["version"], 2)
        self.assertEqual(self.engine.analyzers["test-analyzer"]["catalogRevisionId"], original["id"])
        self.engine.assign_catalog("test-analyzer", revised["id"], configure_outbound=True)
        fresh = self.process(mixed_order().replace("MSG-1", "MSG-NEW").replace("SAMPLE-A", "SAMPLE-NEW"))
        self.assertIn("ADDED-LIVE", [r["code"] for r in fresh["results"]])
        self.assertNotIn("ADDED-LIVE", [r["code"] for r in old["results"]])
        regenerated = self.engine.regenerate_results(old["id"], "NEGATIVE")
        self.assertNotIn("ADDED-LIVE", [r["code"] for r in regenerated["results"]])
        self.assertEqual(regenerated["generation"], 2)
        self.assertEqual(len(regenerated["results"]), 4)
        self.assertEqual(len(regenerated["resultHistory"][0]["results"]), 18)

    def test_unknown_policy_invalid_catalog_and_assignment_rollback(self):
        revision = self.setup_catalog()
        unknown = mixed_order().replace("EXT-MIX^Mixto", "UNKNOWN^Desconocido")
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order(unknown, "test-analyzer", "127.0.0.1"))
        self.assertEqual(self.engine.list_orders(), [])
        self.engine.assign_catalog("test-analyzer", revision["id"], "TEMPLATE", "MIX")
        order = self.process(unknown)
        self.assertEqual(order["catalogFallbacks"], [{"studyKey": "study-1", "code": "UNKNOWN", "templateCode": "MIX"}])
        self.assertEqual(order["studies"][0]["definition"]["code"], "MIX")
        for policy, template in (("IGNORE", "MIX"), ("TEMPLATE", "NOPE")):
            with self.assertRaises(ValueError):
                self.engine.assign_catalog("test-analyzer", revision["id"], policy, template)
        self.assertEqual(self.engine.analyzers["test-analyzer"]["unknownStudyPolicy"], "TEMPLATE")
        invalids = []
        for generator in ({"kind": "numeric", "min": 3, "max": 2}, {"kind": "choice", "values": []},
                          {"kind": "python", "value": "print(1)"}, {"kind": "numeric", "min": float("nan"), "max": 2},
                          {"kind": "fixed", "value": "wrong numeric type"}):
            value = definition(); value["studies"][0]["parameters"][0]["generator"] = generator; invalids.append(value)
        duplicate = definition(); duplicate["studies"][1]["aliases"] = ["EXT-MIX"]; invalids.append(duplicate)
        unknown_scenario = definition(); unknown_scenario["studies"][0]["scenarios"] = {"BROKEN": {"parameters": {"missing": {"kind": "fixed", "value": 1}}}}; invalids.append(unknown_scenario)
        invalids.extend([{"name": "Invalid", "studies": []}, {"name": "Invalid", "studies": [7]}])
        for invalid in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.engine.save_catalog(invalid, revision["catalogId"])
        self.assertEqual(len([r for r in self.engine.store.list_catalogs() if r["catalogId"] == revision["catalogId"]]), 1)

    def test_import_only_suggests_structure_and_requires_generators(self):
        oru = "MSH|^~\\&|A|B|C|D|20261022120000||ORU^R01|PRIVATE-CONTROL|P|2.5.1\rPID|||PRIVATE-PATIENT||PRIVATE^NAME\rOBR|1|||NEW^Nuevo\rOBX|1|NM|VALUE^Valor||123.987|mg/dL|1-10||||F\r"
        suggestion = infer_studies(oru)
        serialized = json.dumps(suggestion)
        for secret in ("PRIVATE-PATIENT", "PRIVATE-CONTROL", "PRIVATE^NAME", "123.987"):
            self.assertNotIn(secret, serialized)
        p = suggestion["studies"][0]["parameters"][0]
        self.assertEqual((p["valueType"], p["unit"], p["referenceRange"], p["generator"]), ("NM", "mg/dL", "1-10", None))
        with self.assertRaises(ValueError):
            validate_catalog({"name": "Importado", "studies": suggestion["studies"]})

    def test_per_group_inbound_mapping_preserves_two_studies_and_specimens(self):
        parsed = parse_with_profile(mixed_order(), {"mappings": [
            {"target": "test.code", "source": "OBR-4.1", "required": True},
            {"target": "test.name", "source": "OBR-4.2"},
            {"target": "sample.id", "source": "SPM-2", "alternatives": ["OBR-3"], "required": True}]})
        self.assertEqual(parsed["requestedTests"], ["EXT-MIX", "CULT"])
        self.assertEqual([s["sampleId"] for s in parsed["studies"]], ["SAMPLE-A", "SAMPLE-B"])
        raw = OML.replace("OML^O21", "OML^O33").replace("ORC|NW|ORDER-1\r", "SPM|1|SPM-A||A^Primera\rORC|NW|ORDER-1\r")
        raw += "SPM|2|SPM-B||B^Segunda\rORC|NW|ORDER-2\rOBR|2|ORDER-2|SAMPLE-B|GLU^Glucosa\r"
        self.assertEqual([s["sampleId"] for s in parse_order(raw)["studies"]], ["SPM-A", "SPM-B"])
        # A specimen attached to one group must not leak into the next OBR.
        third = mixed_order() + "ORC|NW|ORDER-3\rOBR|3|ORDER-3|SAMPLE-C|GLU^Glucosa\r"
        from app.behavior import DEFAULT_HL7_BEHAVIOR
        parsed = parse_order(third, inbound_mappings=DEFAULT_HL7_BEHAVIOR["inboundMappings"])
        self.assertEqual([s["sampleId"] for s in parsed["studies"]], ["SAMPLE-A", "SAMPLE-B", "SAMPLE-C"])
        self.assertEqual(parsed["barcode"], "SAMPLE-A")

    def test_api_create_preview_assign_revise_and_reject_invalid(self):
        self.add()
        with patch.object(main, "engine", self.engine), patch.object(main, "listener_manager", self.manager):
            client = TestClient(main.app)
            created = client.post("/api/catalogs", json=definition())
            self.assertEqual(created.status_code, 201)
            revision = created.json()
            preview = client.post("/api/catalogs/preview", json={"catalog": definition()})
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(len(preview.json()["results"]), 18)
            assigned = client.put("/api/analyzers/test-analyzer/catalog", json={"revisionId": revision["id"], "configureOutbound": True})
            self.assertEqual(assigned.status_code, 200)
            self.assertEqual(assigned.json()["catalogRevisionId"], revision["id"])
            order = self.process()
            self.assertEqual(client.get(f"/api/orders/{order['id']}/results/preview").status_code, 200)
            regenerated = client.post(f"/api/orders/{order['id']}/results/regenerate", json={"scenario": "NEGATIVE"})
            self.assertEqual(regenerated.status_code, 200)
            self.assertEqual(len(regenerated.json()["results"]), 4)
            self.assertEqual(client.post(f"/api/catalogs/{revision['catalogId']}/revisions", json=definition()).status_code, 201)
            self.assertEqual(client.post("/api/catalogs", json={"name": "Broken", "studies": [None]}).status_code, 400)
            self.assertEqual(client.put("/api/analyzers/test-analyzer/catalog", json={"revisionId": "missing"}).status_code, 400)

    def test_sequence_continues_after_restart_and_preview_does_not_consume_it(self):
        self.setup_catalog()
        sequence = definition()
        sequence["studies"] = [{"code": "SEQ", "name": "Secuencia", "parameters": [parameter("NEXT", "NM", {"kind": "sequence", "values": [10, 20, 30]})]}]
        revision = self.engine.save_catalog(sequence)
        self.engine.assign_catalog("test-analyzer", revision["id"], configure_outbound=True)
        first = self.process(OML.replace("CBC^Hemograma", "SEQ^Secuencia"))
        self.assertEqual(first["results"][0]["value"], 10)
        self.engine.preview_order_results(first["id"])
        self.engine = SimulationEngine()
        second = self.process(OML.replace("CBC^Hemograma", "SEQ^Secuencia").replace("MSG-1", "MSG-2").replace("SAMPLE-1", "SAMPLE-2"))
        self.assertEqual(second["results"][0]["value"], 20)

    def test_oversized_orders_rejected_before_processing(self):
        self.add()
        catalog = {"name": "Límite", "studies": [{"code": "MANY", "name": "Muchos", "groups": [
            {"id": "g", "name": "Grupo", "count": 50, "groups": [{"id": "nested", "name": "Subgrupo", "count": 30,
             "parameters": [parameter("P", "NM", {"kind": "fixed", "value": 1})]}]}]}]}
        revision = self.engine.save_catalog(catalog)
        self.engine.assign_catalog("test-analyzer", revision["id"])
        raw = OML.replace("CBC^Hemograma", "MANY^Muchos") + "OBR|2|ORDER-1|SAMPLE-1|MANY^Muchos\r"
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order(raw, "test-analyzer", "127.0.0.1"))
        self.assertEqual(self.engine.list_orders(), [])
