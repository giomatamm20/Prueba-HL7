import base64
import gc
import os
import tempfile
import unittest
from urllib.parse import quote, urlencode
from unittest.mock import patch
from support import install_contract

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.pacs import (
    PACS_ANALYZER_ID,
    PacsSecureLinkError,
    decrypt_legacy_secure_payload,
    render_study_svg,
)


PACS_ORM = (
    "MSH|^~\\&|HIS||||20261007082245||ORM^O01|PACS-MSG-TEST-001|P|2.3.1\r"
    "PID|||PAT-PACS-001||PRUEBA^SINTETICA||19800101|F\r"
    "PV1||O|||||00000^EXTERNO^MEDICO||||CONSULTA EXTERNA\r"
    "ORC|NW|PACS-TEST-0001|PACS-TEST-0001||AR||^^^20261007082243^^BAJA|||CONSULTA EXTERNA||0000000000^MEDICO^RNH\r"
    "OBR||PACS-TEST-0001|PACS-TEST-0001|IMGS_0091^TC CEREBRAL SIMPLE||||||||||||||||||||CT|||BAJA\r"
)

LEGACY_ENV = {
    "PACS_LEGACY_KEY_CODE": "RIS_LOCAL",
    "PACS_LEGACY_KEY": "local-pacs-integration-key-change-me",
    "PACS_LEGACY_IV_HEX": "00112233445566778899aabbccddeeff",
    "PACS_LEGACY_USERNAME": "ris_local",
}


def encrypt_legacy_payload(parameters: list[tuple[str, str]]) -> str:
    plaintext = urlencode(parameters).encode("utf-8")
    key = LEGACY_ENV["PACS_LEGACY_KEY"].encode("utf-8")[:32].ljust(32, b"0")
    iv = bytes.fromhex(LEGACY_ENV["PACS_LEGACY_IV_HEX"])
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return quote(base64.b64encode(encrypted).decode("ascii"), safe="")


class PacsSimulatorTests(unittest.TestCase):
    def setUp(self):
        self.previous_db = os.environ.get("SIMULATOR_DB_PATH")
        self.directory = tempfile.TemporaryDirectory()
        os.environ["SIMULATOR_DB_PATH"] = os.path.join(self.directory.name, "simulator.db")
        from app.engine import SimulationEngine

        self.engine = SimulationEngine()
        install_contract(self.engine, PACS_ANALYZER_ID, PACS_ORM)

    def tearDown(self):
        del self.engine
        gc.collect()
        self.directory.cleanup()
        if self.previous_db is None:
            os.environ.pop("SIMULATOR_DB_PATH", None)
        else:
            os.environ["SIMULATOR_DB_PATH"] = self.previous_db

    def test_default_pacs_profile_receives_real_world_orm(self):
        analyzer = next(item for item in self.engine.list_analyzers() if item["id"] == PACS_ANALYZER_ID)
        self.assertEqual(analyzer["listenPort"], 5020)
        self.assertEqual(analyzer["mode"], "BIDIRECTIONAL")

        ack = self.engine.receive_hl7_order(PACS_ORM, PACS_ANALYZER_ID, "127.0.0.1")
        self.assertIn("MSA|AA|PACS-MSG-TEST-001", ack)
        study = self.engine.list_pacs_studies()[0]
        self.assertEqual(study["accountNo"], "PAT-PACS-001")
        self.assertEqual(study["accessionNo"], "PACS-TEST-0001")
        self.assertEqual(study["procedureCode"], "IMGS_0091")
        self.assertEqual(study["procedureName"], "TC CEREBRAL SIMPLE")
        self.assertEqual(study["modality"], "CT")
        self.assertEqual(study["priority"], "BAJA")
        self.assertEqual(study["service"], "CONSULTA EXTERNA")
        self.assertEqual(study["status"], "ORDER_RECEIVED")

    def test_generates_image_and_notifies_mirth_with_ready_oru(self):
        self.engine.receive_hl7_order(PACS_ORM, PACS_ANALYZER_ID, "127.0.0.1")
        study = self.engine.list_pacs_studies()[0]
        ready = self.engine.process_pacs_study(study["id"])
        self.assertEqual(ready["status"], "IMAGE_READY")
        svg = render_study_svg(ready)
        self.assertIn("PAT-PACS-001", svg)
        self.assertIn("PACS-TEST-0001", svg)
        self.assertIn("NO DIAGNOSTICA", svg)

        positive_ack = "MSH|^~\\&|MIRTH||||20261007082501||ACK^R01|ACK1|P|2.3.1\rMSA|AA|READY1\r"
        with patch("app.engine.send_mllp", return_value=(positive_ack, "AA")) as sender:
            completed = self.engine.notify_pacs_study(study["id"])
        self.assertEqual(completed["status"], "COMPLETED")
        message = sender.call_args.args[0]
        self.assertIn("ORU^R01", message)
        self.assertIn("PID|||PAT-PACS-001", message)
        self.assertIn("PACS-TEST-0001", message)
        self.assertIn("IMAGE_STATUS^ESTADO DE IMAGEN||READY", message)
        self.assertNotIn("http://", message)

    def test_rejects_duplicate_accession_number(self):
        first = self.engine.receive_hl7_order(PACS_ORM, PACS_ANALYZER_ID, "127.0.0.1")
        second = self.engine.receive_hl7_order(
            PACS_ORM.replace("PACS-MSG-TEST-001", "MSG-DUPLICATE"),
            PACS_ANALYZER_ID,
            "127.0.0.1",
        )
        self.assertIn("MSA|AA|PACS-MSG-TEST-001", first)
        self.assertIn("MSA|AE|MSG-DUPLICATE", second)
        self.assertIn("accession number duplicado", second)
        self.assertEqual(len(self.engine.list_pacs_studies()), 1)

    def test_lookup_requires_matching_patient_and_accession(self):
        self.engine.receive_hl7_order(PACS_ORM, PACS_ANALYZER_ID, "127.0.0.1")
        study = self.engine.find_pacs_study("PAT-PACS-001", "PACS-TEST-0001")
        self.assertEqual(study["procedureCode"], "IMGS_0091")
        with self.assertRaises(ValueError):
            self.engine.find_pacs_study("PACIENTE-INCORRECTO", "PACS-TEST-0001")

    def test_account_number_uses_first_pid_3_component_and_accepts_legacy_records(self):
        composite = PACS_ORM.replace("PAT-PACS-001", "EXP-DX-20261007-01^^^HNR^MR")
        self.engine.receive_hl7_order(composite, PACS_ANALYZER_ID, "127.0.0.1")
        study = self.engine.list_pacs_studies()[0]
        self.assertEqual(study["accountNo"], "EXP-DX-20261007-01")

        study["accountNo"] = "EXP-DX-20261007-01^^^HNR^MR"
        self.engine.store.save_order(study)
        found = self.engine.find_pacs_study("EXP-DX-20261007-01", "PACS-TEST-0001")
        self.assertEqual(found["accessionNo"], "PACS-TEST-0001")

    def test_decrypts_production_compatible_legacy_secure_link(self):
        parameters = [
            ("integration_type", "HIS"),
            ("generated_dt", "2026-10-07 14:31:00"),
            ("username", "ris_local"),
            ("account_no", "EXP-DX-20261007-01"),
            ("flag", "2"),
            ("accession_no", "R261007000000048"),
        ]
        payload = encrypt_legacy_payload(parameters)

        with patch.dict(os.environ, LEGACY_ENV, clear=False):
            decoded = decrypt_legacy_secure_payload("RIS_LOCAL", payload)

        self.assertEqual(decoded["account_no"], "EXP-DX-20261007-01")
        self.assertEqual(decoded["accession_no"], "R261007000000048")

    def test_secure_link_rejects_wrong_code_and_parameter_order(self):
        parameters = [
            ("integration_type", "HIS"),
            ("generated_dt", "2026-10-07 14:31:00"),
            ("account_no", "EXP-DX-20261007-01"),
            ("username", "ris_local"),
            ("flag", "2"),
            ("accession_no", "R261007000000048"),
        ]
        payload = encrypt_legacy_payload(parameters)

        with patch.dict(os.environ, LEGACY_ENV, clear=False):
            with self.assertRaises(PacsSecureLinkError) as wrong_code:
                decrypt_legacy_secure_payload("WRONG", payload)
            with self.assertRaises(PacsSecureLinkError) as wrong_order:
                decrypt_legacy_secure_payload("RIS_LOCAL", payload)

        self.assertEqual(wrong_code.exception.status_code, 403)
        self.assertIn("Orden", str(wrong_order.exception))

    def test_secure_link_rejects_invalid_ciphertext(self):
        with patch.dict(os.environ, LEGACY_ENV, clear=False):
            with self.assertRaises(PacsSecureLinkError) as error:
                decrypt_legacy_secure_payload("RIS_LOCAL", "not-base64")

        self.assertEqual(error.exception.status_code, 400)
        self.assertIn("Payload PACS invalido", str(error.exception))


if __name__ == "__main__":
    unittest.main()
