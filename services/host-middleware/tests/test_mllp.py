import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVICE_ROOT))

from app.mllp import ConnectionState, END_BLOCK, START_BLOCK, Hl7Processor, MllpServer


class MllpTests(unittest.TestCase):
    def sample(self, filename: str) -> str:
        return (
            PROJECT_ROOT / "samples" / "hl7" / filename
        ).read_text(encoding="utf-8").replace("\n", "\r")

    def test_fragmented_mllp_result_is_stored_and_acknowledged(self) -> None:
        store = Mock()
        store.save_received.return_value = 12
        state = ConnectionState()
        server = MllpServer(("127.0.0.1", 0), Hl7Processor(store, state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        framed = START_BLOCK + self.sample("abbott_cbc_001.hl7").encode("utf-8") + END_BLOCK
        try:
            with socket.create_connection(server.server_address, timeout=2) as connection:
                connection.sendall(framed[:30])
                connection.sendall(framed[30:])
                response = connection.recv(4096)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIn(b"MSA|AA|MSG001", response)
        stored = store.save_received.call_args.kwargs
        self.assertEqual("RESULT", stored["parsed"]["eventType"])
        self.assertEqual("127.0.0.1", stored["source_ip"])
        self.assertEqual("AA", state.snapshot()["lastAckCode"])

    def test_invalid_result_returns_ae_and_is_audited(self) -> None:
        store = Mock()
        state = ConnectionState()
        processor = Hl7Processor(store, state)
        invalid_message = (
            "MSH|^~\\&|ABBOTT|LAB|HOST|CLINIC|202605271000||ORU^R01|BAD01|P|2.3\r"
            "PID|1||12345||MATA^GIOVANNI\r"
            "OBR|1||ORD001|CBC^HEMOGRAMA\r"
        )

        ack = processor.process(invalid_message, "192.168.1.122")

        self.assertIn("MSA|AE|BAD01", ack)
        self.assertIn("Missing required OBX", store.save_rejected.call_args.kwargs["error"])
        self.assertEqual("AE", state.snapshot()["lastAckCode"])


if __name__ == "__main__":
    unittest.main()

