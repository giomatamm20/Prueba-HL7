import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVICE_ROOT))

from app.main import END_BLOCK, START_BLOCK, Hl7MessageProcessor, ThreadingMllpServer


class MllpListenerTests(unittest.TestCase):
    def test_receives_mllp_message_stores_json_and_returns_ack(self) -> None:
        raw_message = (
            PROJECT_ROOT / "samples" / "hl7" / "abbott_cbc_001.hl7"
        ).read_text(encoding="utf-8").replace("\n", "\r")
        store = Mock()

        server = ThreadingMllpServer(("127.0.0.1", 0), Hl7MessageProcessor(store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=2) as connection:
                connection.sendall(START_BLOCK + raw_message.encode("utf-8") + END_BLOCK)
                response = connection.recv(4096)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn(b"MSA|AA|MSG001", response)
        stored = store.save.call_args.kwargs
        self.assertEqual("12345", stored["parsed_json"]["patientId"])
        self.assertEqual("WBC", stored["parsed_json"]["tests"][0]["code"])
        self.assertEqual("MSG001", stored["message_control_id"])

    def test_invalid_message_returns_error_ack_and_stores_parse_error(self) -> None:
        invalid_message = (
            "MSH|^~\\&|ABBOTT|LAB|LIS|HOSPITAL|202605261700||ORU^R01|BAD001|P|2.3\r"
            "PID|1||12345||MATA^GIOVANNI\r"
            "OBR|1||ORD001|CBC^HEMOGRAMA\r"
        )
        store = Mock()
        processor = Hl7MessageProcessor(store)

        ack = processor.process(invalid_message, "127.0.0.1")

        self.assertIn("MSA|AE|BAD001", ack)
        stored = store.save.call_args.kwargs
        self.assertIsNone(stored["parsed_json"])
        self.assertIn("Missing required OBX", stored["parse_error"])


if __name__ == "__main__":
    unittest.main()
