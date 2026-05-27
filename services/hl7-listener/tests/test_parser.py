import sys
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SERVICE_ROOT))

from app.parser import HL7ParseError, build_ack, extract_metadata, parse_oru_result


class ParserTests(unittest.TestCase):
    def sample(self, filename: str) -> str:
        return (PROJECT_ROOT / "samples" / "hl7" / filename).read_text(encoding="utf-8")

    def test_parses_oru_to_json(self) -> None:
        result = parse_oru_result(self.sample("abbott_cbc_001.hl7"))

        self.assertEqual("12345", result["patientId"])
        self.assertEqual("ORD001", result["orderId"])
        self.assertEqual(["WBC", "HGB", "PLT"], [test["code"] for test in result["tests"]])
        self.assertEqual(8.2, result["tests"][0]["value"])

    def test_builds_positive_ack_from_message_metadata(self) -> None:
        metadata = extract_metadata(self.sample("abbott_cbc_001.hl7"))

        ack = build_ack(metadata, "AA")

        self.assertIn("MSA|AA|MSG001", ack)
        self.assertIn("|ABBOTT|LAB|", ack)

    def test_rejects_message_without_obx(self) -> None:
        invalid_message = "\r".join(self.sample("abbott_cbc_001.hl7").splitlines()[:3])

        with self.assertRaisesRegex(HL7ParseError, "Missing required OBX"):
            parse_oru_result(invalid_message)


if __name__ == "__main__":
    unittest.main()
