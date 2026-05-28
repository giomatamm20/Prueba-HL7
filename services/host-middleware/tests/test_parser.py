import sys
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVICE_ROOT))

from app.parser import HL7ParseError, build_ack, extract_metadata, parse_message


class ParserTests(unittest.TestCase):
    def sample(self, filename: str) -> str:
        return (PROJECT_ROOT / "samples" / "hl7" / filename).read_text(encoding="utf-8")

    def test_parses_oru_result(self) -> None:
        result = parse_message(self.sample("abbott_cbc_001.hl7"))

        self.assertEqual("RESULT", result["eventType"])
        self.assertEqual("ORU^R01", result["messageType"])
        self.assertEqual("12345", result["patient"]["id"])
        self.assertEqual("ORD001", result["order"]["id"])
        self.assertEqual(["WBC", "HGB", "PLT"], [test["code"] for test in result["tests"]])

    def test_parses_orm_order(self) -> None:
        result = parse_message(self.sample("abbott_order_001.hl7"))

        self.assertEqual("ORDER", result["eventType"])
        self.assertEqual("ORM^O01", result["messageType"])
        self.assertEqual("ORD003", result["order"]["id"])
        self.assertEqual("NW", result["order"]["status"])
        self.assertEqual([], result["tests"])

    def test_ack_matches_message_trigger(self) -> None:
        result_ack = build_ack(extract_metadata(self.sample("abbott_cbc_001.hl7")), "AA")
        order_ack = build_ack(extract_metadata(self.sample("abbott_order_001.hl7")), "AA")

        self.assertIn("ACK^R01", result_ack)
        self.assertIn("MSA|AA|MSG001", result_ack)
        self.assertIn("ACK^O01", order_ack)
        self.assertIn("MSA|AA|ORM001", order_ack)

    def test_rejects_result_without_obx(self) -> None:
        invalid_message = "\r".join(self.sample("abbott_cbc_001.hl7").splitlines()[:3])

        with self.assertRaisesRegex(HL7ParseError, "Missing required OBX"):
            parse_message(invalid_message)


if __name__ == "__main__":
    unittest.main()

