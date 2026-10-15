import unittest
import os
import tempfile
import gc

from app.hl7_contract import HL7ContractError, infer_hl7_contract, validate_hl7_contract
from app.hl7_orders import build_ack, hl7_path_value
from app.profile_builder import build_profile_message
from app.profile_parser import parse_with_profile
from support import outbound_contract


OML = (
    "MSH|^~\\&|HIS|FAC|SIM|LAB|202608171200||OML^O21|CTRL1|P|2.5.1\r"
    "PID|||11407-25^^^^EXP~542049^^^^DUI||PEREZ^JUAN||19800101|M|\r"
    "ORC|NW|ORD1\r"
    "OBR|1|ORD1|S1|CBC^Hemograma\r"
)


class HL7ContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = infer_hl7_contract(OML)

    def test_infers_repetitions_and_placeholder_preview(self):
        preview = self.contract["preview"]
        self.assertEqual(self.contract["messageType"], "OML^O21")
        self.assertIn("<PATIENT_IDENTIFIER>^^^^<IDENTIFIER_TYPE>~<PATIENT_IDENTIFIER>^^^^<IDENTIFIER_TYPE>", preview)
        self.assertIn("<BIRTH_DATE:YYYYMMDD>", preview)

    def test_accepts_cr_and_lf_segment_separators(self):
        validate_hl7_contract(OML, self.contract)
        validate_hl7_contract(OML.replace("\r", "\n"), self.contract)

    def test_proprietary_catalog_value_can_change_in_the_same_position(self):
        raw = OML.replace("\rORC", "\rPV1|1|R01\rORC")
        contract = infer_hl7_contract(raw)
        validate_hl7_contract(raw, contract)
        self.assertIn("<PATIENT_CLASS>", contract["preview"])
        validate_hl7_contract(raw.replace("|R01\r", "|R02\r"), contract)

    def test_identifier_value_can_change_but_its_component_is_required(self):
        validate_hl7_contract(OML.replace("^^^^DUI", "^^^^032759172"), self.contract)
        with self.assertRaises(HL7ContractError) as caught:
            validate_hl7_contract(OML.replace("^^^^DUI", ""), self.contract)
        self.assertIn("PID[1]-3[2].5", str(caught.exception))
        self.assertIn("obligatorio ausente", str(caught.exception))

    def test_rejects_invalid_date_and_unexpected_segment(self):
        with self.assertRaises(HL7ContractError) as caught:
            validate_hl7_contract(OML.replace("19800101", "19801301"), self.contract)
        self.assertIn("fecha HL7 DT válida", str(caught.exception))
        with self.assertRaises(HL7ContractError) as caught:
            validate_hl7_contract(OML.replace("\rORC", "\rZZZ|X\rORC"), self.contract)
        self.assertIn("segmento no definido o fuera de orden", str(caught.exception))

    def test_mapping_paths_select_repetitions_and_components(self):
        self.assertEqual(hl7_path_value(OML, "PID-3[1].1"), "11407-25")
        self.assertEqual(hl7_path_value(OML, "PID-3[1].5"), "EXP")
        self.assertEqual(hl7_path_value(OML, "PID-3[2].1"), "542049")
        self.assertEqual(hl7_path_value(OML, "PID-3[2].5"), "DUI")

    def test_oru_profile_can_be_parsed_without_orc(self):
        raw = (
            "MSH|^~\\&|AN|LAB|LIS|FAC|202608171200||ORU^R01|R1|P|2.5.1\r"
            "PID|||P1||PEREZ^JUAN\r"
            "OBR|1|O1|S1|CBC^Hemograma\r"
            "OBX|1|NM|WBC^Leucocitos||5.2|10^9/L|||N|||F\r"
        )
        schema = infer_hl7_contract(raw)
        payload = parse_with_profile(raw, {
            "messageType": "ORU^R01",
            "schema": schema,
            "mappings": [
                {"target": "patient.id", "source": "PID-3[1].1"},
                {"target": "order.id", "source": "OBR-2[1].1"},
                {"target": "sample.id", "source": "OBR-3[1].1"},
                {"target": "test.code", "source": "OBR-4[1].1"},
            ],
        })
        self.assertEqual(payload["patientId"], "P1")
        self.assertEqual(payload["orderControl"], "NW")
        self.assertEqual(payload["requestedTests"], ["CBC"])

    def test_ack_contains_detailed_err_segment(self):
        ack = build_ack(
            {"messageControlId": "CTRL1", "messageType": "OML^O21", "version": "2.5.1"},
            "AE",
            "Contrato HL7 incumplido: PID[1]-3[2].5: se esperaba 'DUI'",
        )
        self.assertIn("MSA|AE|CTRL1", ack)
        self.assertIn("ERR|||", ack)
        self.assertIn("PID[1]-3[2].5", ack)
        warning_ack = build_ack(
            {"messageControlId": "CTRL2", "messageType": "OML^O21", "version": "2.5.1"},
            "AA",
            warnings=["PID[1]-11[1].1: valor obligatorio ausente"],
        )
        self.assertIn("MSA|AA|CTRL2", warning_ack)
        self.assertIn("|W|||PID[1]-11[1].1", warning_ack)

    def test_profile_builder_writes_repetition_component_paths(self):
        profile = {"outbound": {"events": {"RESULT_FINAL": {"segments": [
            {"name": "MSH", "fields": [
                {"target": "MSH-9.1", "sourceType": "fixed", "value": "ORU"},
                {"target": "MSH-9.2", "sourceType": "fixed", "value": "R01"},
                {"target": "MSH-10", "source": "system.messageId"},
            ]},
            {"name": "PID", "fields": [
                {"target": "PID-3[1].1", "source": "patient.id"},
                {"target": "PID-3[1].5", "sourceType": "fixed", "value": "EXP"},
                {"target": "PID-3[2].1", "source": "patient.id"},
                {"target": "PID-3[2].5", "sourceType": "fixed", "value": "DUI"},
            ]},
        ]}}}}
        message, _ = build_profile_message(
            profile,
            {"id": "A1", "name": "A1", "vendor": "Test"},
            {"patientId": "P123", "sampleId": "S1"},
            [],
            "RESULT_FINAL",
            1,
        )
        self.assertIn("PID|||P123^^^^EXP~P123^^^^DUI", message)

    def test_engine_persists_and_enforces_contract_for_its_analyzer(self):
        previous_db = os.environ.get("SIMULATOR_DB_PATH")
        with tempfile.TemporaryDirectory() as directory:
            os.environ["SIMULATOR_DB_PATH"] = os.path.join(directory, "simulator.db")
            from app.engine import SimulationEngine

            engine = SimulationEngine()
            engine.create_analyzer({
                "id": "AN-CONTRACT",
                "name": "Analizador contrato",
                "protocol": "HL7",
                "mode": "BIDIRECTIONAL",
                "supportedTests": ["CBC"],
                "listenPort": 0,
            })
            profile = engine.save_protocol_profile("AN-CONTRACT", {
                "name": "Contrato OML",
                "activate": True,
                "inbound": {
                    "strictValidation": True,
                    "messageType": "OML^O21",
                    "schema": self.contract,
                    "rawHL7": OML,
                    "mappings": [
                        {"target": "patient.id", "source": "PID-3[1].1"},
                        {"target": "patient.name", "source": "PID-5"},
                        {"target": "order.id", "source": "ORC-2[1].1"},
                        {"target": "sample.id", "source": "OBR-3[1].1"},
                        {"target": "test.code", "source": "OBR-4[1].1"},
                        {"target": "test.name", "source": "OBR-4[1].2"},
                    ],
                },
                "outbound": outbound_contract(),
            })
            self.assertEqual(profile["analyzerId"], "AN-CONTRACT")
            self.assertEqual(engine.store.get_active_protocol_profile("AN-CONTRACT")["id"], profile["id"])
            second = engine.save_protocol_profile("AN-CONTRACT", {
                "name": "Contrato alternativo",
                "activate": True,
                "inbound": profile["inbound"],
                "outbound": outbound_contract(),
            })
            self.assertEqual(engine.store.get_active_protocol_profile("AN-CONTRACT")["id"], second["id"])
            engine.activate_protocol_profile("AN-CONTRACT", profile["id"])
            statuses = {item["id"]: item["status"] for item in engine.list_protocol_profiles("AN-CONTRACT")}
            self.assertEqual(statuses[profile["id"]], "ACTIVE")
            self.assertEqual(statuses[second["id"]], "ARCHIVED")
            before_update_count = len(engine.list_protocol_profiles("AN-CONTRACT"))
            updated = engine.update_protocol_profile("AN-CONTRACT", profile["id"], {
                "name": "Contrato OML actualizado",
                "activate": True,
                "inbound": profile["inbound"],
                "outbound": outbound_contract(),
            })
            self.assertNotEqual(updated["id"], profile["id"])
            self.assertGreater(updated["version"], profile["version"])
            self.assertEqual(updated["revisionOf"], profile["id"])
            self.assertEqual(len(engine.list_protocol_profiles("AN-CONTRACT")), before_update_count + 1)
            self.assertIn("MSA|AA|CTRL1", engine.receive_hl7_order(OML, "AN-CONTRACT", "127.0.0.1"))
            accepted_with_warning = engine.receive_hl7_order(
                OML.replace("CTRL1", "CTRL2").replace("ORD1", "ORD2").replace("S1", "S2").replace("^^^^DUI", ""),
                "AN-CONTRACT",
                "127.0.0.1",
            )
            self.assertIn("MSA|AE|CTRL2", accepted_with_warning)
            self.assertIn("|E|||PID[1]-3[2].5", accepted_with_warning)
            self.assertEqual(len(engine.list_orders()), 1)
            del engine
            gc.collect()
        if previous_db is None:
            os.environ.pop("SIMULATOR_DB_PATH", None)
        else:
            os.environ["SIMULATOR_DB_PATH"] = previous_db


if __name__ == "__main__":
    unittest.main()
