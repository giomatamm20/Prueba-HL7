"""Real listener reception, one-shot semantic learning and pasted line endings."""
import copy
import sqlite3
from unittest.mock import patch
from starlette.testclient import TestClient

from app import main
from app.engine import SimulationEngine
from app.hl7_contract import infer_hl7_contract, parse_hl7
from app.hl7_orders import build_ack, extract_metadata, parse_order
from app.hl7_text import normalize_hl7
from support import EngineCase, OML, exchange, free_port, outgoing

ADT = 'MSH|^~\\&|QA|TEST|SIM|LAB|202611061200||ADT^A01|ADT-1|T|2.5.1\rPID|||QA-PATIENT\r'


class PermissiveLearningTests(EngineCase):
    def profile(self, mode="PERMISSIVE", **inbound):
        return self.engine.save_protocol_profile("test-analyzer", {"name": "Equipo en aprendizaje",
            "inbound": {"receptionMode": mode, **inbound}, "outbound": {}})

    def test_permissive_without_contract_opens_socket_and_accepts_different_hl7_types(self):
        port = free_port()
        self.add(contract=False, listenPort=port)
        profile = self.profile()
        self.assertEqual(profile["status"], "ACTIVE")
        self.manager.sync("test-analyzer")
        self.assertTrue(self.engine.analyzers["test-analyzer"]["listenerActive"])
        ack = exchange(port, ADT)
        self.assertIn("ACK^A01", ack)
        self.assertIn("MSA|AA|ADT-1", ack)
        self.assertEqual(self.engine.list_orders(), [])
        self.assertEqual(self.engine.list_messages()[0]["messageType"], "ADT^A01")
        self.assertIn("MSA|AA|", exchange(port, OML))
        order = self.engine.list_orders()[0]
        order["status"] = "RESULT_READY"
        with self.assertRaisesRegex(ValueError, "contrato de salida"):
            self.engine.emit_order_event(order["id"], "REPORT")
        self.assertIn("MSA|AE|", exchange(port, ADT.replace('|ADT-1|', '||')))

    def test_unchecked_strict_validation_without_schema_activates_saved_draft(self):
        self.add(contract=False)
        profile = self.engine.save_protocol_profile("test-analyzer", {"name": "Perfil permisivo",
            "activate": False, "inbound": {"strictValidation": False}, "outbound": outgoing()})
        self.assertEqual(profile["status"], "DRAFT")
        active = self.engine.activate_protocol_profile("test-analyzer", profile["id"])
        self.assertEqual(active["inbound"]["receptionMode"], "PERMISSIVE")
        self.assertTrue(self.engine.communication_ready("test-analyzer"))
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(ADT, "test-analyzer", "127.0.0.1"))

    def test_api_activation_starts_learning_listener_and_exposes_learned_revision(self):
        port = free_port()
        self.add(contract=False, listenPort=port)
        with patch.object(main, 'engine', self.engine), patch.object(main, 'listener_manager', self.manager):
            client = TestClient(main.app)
            response = client.post('/api/analyzers/test-analyzer/protocol-profiles', json={
                'name': 'Aprender equipo externo', 'activate': False,
                'inbound': {'receptionMode': 'LEARN'}, 'outbound': {}})
            self.assertEqual(response.status_code, 201, response.text)
            profile_id = response.json()['id']
            activated = client.post(f'/api/analyzers/test-analyzer/protocol-profiles/{profile_id}/activate')
            self.assertEqual(activated.status_code, 200, activated.text)
            before = next(a for a in client.get('/api/analyzers').json() if a['id'] == 'test-analyzer')
            self.assertTrue(before['learningPending'])
            self.assertTrue(before['listenerActive'])
            self.assertFalse(before['outboundReady'])
            self.assertIn('MSA|AA|', exchange(port, ADT.replace('\r', '/r')))
            after = next(a for a in client.get('/api/analyzers').json() if a['id'] == 'test-analyzer')
            self.assertFalse(after['learningPending'])
            self.assertEqual(after['receptionMode'], 'PERMISSIVE')
            self.assertNotEqual(after['activeProtocolProfileId'], profile_id)
            self.assertEqual(len(client.get('/api/analyzers/test-analyzer/protocol-profiles').json()), 2)

    def test_learning_creates_one_persistent_editable_revision_and_preserves_mappings_and_output(self):
        self.add(contract=False)
        mappings = [{"target": "order.id", "source": "ORC-4.1", "alternatives": ["ORC-2.1"], "required": True}]
        first = self.engine.save_protocol_profile("test-analyzer", {"name": "Mi equipo",
            "inbound": {"receptionMode": "LEARN", "mappings": mappings}, "outbound": outgoing()})
        expected_outbound = copy.deepcopy(first["outbound"])
        self.assertIn("MSA|AE|", self.engine.receive_hl7_order('INVALID', "test-analyzer", '127.0.0.1'))
        self.assertEqual(self.engine.store.get_active_protocol_profile("test-analyzer")["id"], first["id"])
        ack = self.engine.receive_hl7_order(OML.replace('\r', r'\r'), "test-analyzer", '127.0.0.1')
        self.assertIn("MSA|AA|", ack)
        learned = self.engine.store.get_active_protocol_profile("test-analyzer")
        self.assertNotEqual(learned["id"], first["id"])
        self.assertEqual(learned["name"], "Mi equipo")
        self.assertEqual(learned["revisionOf"], first["id"])
        self.assertEqual(learned["inbound"]["receptionMode"], "PERMISSIVE")
        self.assertEqual(learned["inbound"]["learnedFromMessageId"], "MSG-1")
        self.assertEqual(learned["inbound"]["mappings"], mappings)
        self.assertEqual(learned["inbound"]["rawHL7"], OML)
        self.assertEqual(learned["inbound"]["schema"]["formatVersion"], 2)
        self.assertEqual(learned["outbound"], expected_outbound)
        self.assertEqual(self.engine.list_orders()[0]["inboundProfileId"], learned["id"])
        self.assertEqual(self.engine.receive_hl7_order(OML.replace('\r', '\r\n'), "test-analyzer", '127.0.0.1'), ack)
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(ADT, "test-analyzer", '127.0.0.1'))
        restarted = SimulationEngine()
        self.assertEqual(restarted.store.get_active_protocol_profile("test-analyzer"), learned)
        self.assertEqual(len(restarted.list_protocol_profiles("test-analyzer")), 2)
        self.assertFalse(next(a for a in restarted.list_analyzers() if a["id"] == "test-analyzer")["learningPending"])

    def test_learning_failure_rolls_back_revision_and_order_together(self):
        self.add(contract=False)
        first = self.profile("LEARN")
        with patch.object(self.engine.store, "save_order", side_effect=sqlite3.OperationalError("synthetic disk failure")):
            self.assertIn("MSA|AE|", self.engine.receive_hl7_order(OML, "test-analyzer", '127.0.0.1'))
        self.assertEqual(self.engine.store.get_active_protocol_profile("test-analyzer")["id"], first["id"])
        self.assertEqual(self.engine.list_orders(), [])
        self.assertEqual(len(self.engine.list_protocol_profiles("test-analyzer")), 1)

    def test_unknown_exams_and_missing_vendor_fields_are_received_without_inventing_results(self):
        self.add(contract=False, supportedTests=["CBC"])
        self.profile("PERMISSIVE", mappings=[{"target": "sample.id", "source": "SPM-2", "required": True}])
        raw = OML.replace('CBC^Hemograma', 'LAB_UNKNOWN^Desconocido').replace('PV1|1|O\r', '')
        self.assertIn("MSA|AA|", self.engine.receive_hl7_order(raw, "test-analyzer", '127.0.0.1'))
        order = self.engine.list_orders()[0]
        self.assertEqual(order["pendingCatalogStudies"], ["LAB_UNKNOWN"])
        self.assertEqual(order["results"], [])
        self.assertIsNone(order["studies"][0]["definition"])

    def test_line_endings_in_parser_inference_and_ack_errors(self):
        endings = ['\r', '\n', '\r\n', r'\r', r'\n', r'\r\n', '/r', '/r/n']
        raw = OML + 'NTE|1||https://qa/ruta y escape \\R\\ conservado\r'
        for ending in endings:
            with self.subTest(ending=repr(ending)):
                pasted = raw.replace('\r', ending)
                self.assertEqual(normalize_hl7(pasted), raw)
                self.assertEqual(extract_metadata(pasted)["messageControlId"], "MSG-1")
                self.assertEqual(parse_order(pasted)["patientId"], "PAT-SYNTHETIC")
                schema = infer_hl7_contract(pasted)
                self.assertEqual([s["name"] for s in schema["segments"]][-1], 'NTE')
        ack = build_ack(extract_metadata(OML), 'AE', 'Error de prueba\rNTE|inyectado\nsegunda línea')
        self.assertEqual([s.name for s in parse_hl7(ack).segments], ['MSH', 'MSA', 'ERR'])
        self.assertIn('\\X0D\\', ack)
