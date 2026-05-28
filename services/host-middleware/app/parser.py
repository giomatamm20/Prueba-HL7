from dataclasses import dataclass
from datetime import datetime, timezone


class HL7ParseError(ValueError):
    pass


@dataclass
class MessageMetadata:
    analyzer_code: str | None = None
    sending_facility: str | None = None
    message_type: str | None = None
    message_control_id: str | None = None
    version: str = "2.3"


def segments_from_message(raw_message: str) -> list[list[str]]:
    normalized = raw_message.replace("\r\n", "\r").replace("\n", "\r")
    return [line.split("|") for line in normalized.split("\r") if line]


def field(segment: list[str] | None, position: int) -> str | None:
    if segment is None or len(segment) <= position or segment[position] == "":
        return None
    return segment[position]


def component(value: str | None, position: int) -> str | None:
    if not value:
        return None
    components = value.split("^")
    return components[position] if len(components) > position and components[position] else None


def extract_metadata(raw_message: str) -> MessageMetadata:
    for segment in segments_from_message(raw_message):
        if segment[0] == "MSH":
            return MessageMetadata(
                analyzer_code=field(segment, 2),
                sending_facility=field(segment, 3),
                message_type=field(segment, 8),
                message_control_id=field(segment, 9),
                version=field(segment, 11) or "2.3",
            )
    return MessageMetadata()


def parse_numeric_or_text(value: str, value_type: str | None) -> float | str:
    if value_type == "NM":
        try:
            return float(value)
        except ValueError as error:
            raise HL7ParseError(f"Invalid numeric OBX value: {value}") from error
    return value


def parse_message(raw_message: str) -> dict:
    segments = segments_from_message(raw_message)
    by_type: dict[str, list[list[str]]] = {}
    for segment in segments:
        by_type.setdefault(segment[0], []).append(segment)
    if "MSH" not in by_type:
        raise HL7ParseError("Missing required MSH segment")
    if "PID" not in by_type:
        raise HL7ParseError("Missing required PID segment")
    if "OBR" not in by_type:
        raise HL7ParseError("Missing required OBR segment")

    metadata = extract_metadata(raw_message)
    if not metadata.message_control_id:
        raise HL7ParseError("Missing MSH-10 message control ID")
    if metadata.message_type not in {"ORU^R01", "ORM^O01"}:
        raise HL7ParseError(f"Unsupported message type: {metadata.message_type or 'empty'}")

    pid = by_type["PID"][0]
    obr = by_type["OBR"][0]
    orc = by_type.get("ORC", [None])[0]
    patient_id = field(pid, 3)
    if not patient_id:
        raise HL7ParseError("Missing PID-3 patient identifier")

    is_result = metadata.message_type == "ORU^R01"
    if is_result and "OBX" not in by_type:
        raise HL7ParseError("Missing required OBX segment for ORU result")
    order_id = (
        field(obr, 3)
        if is_result
        else field(orc, 2) or field(obr, 2) or field(obr, 3)
    )
    if not order_id:
        raise HL7ParseError("Missing order identifier")

    tests = []
    for obx in by_type.get("OBX", []):
        code = component(field(obx, 3), 0)
        raw_value = field(obx, 5)
        if not code or raw_value is None:
            raise HL7ParseError("OBX segment missing test code or value")
        tests.append(
            {
                "code": code,
                "value": parse_numeric_or_text(raw_value, field(obx, 2)),
                "unit": field(obx, 6),
                "referenceRange": field(obx, 7),
                "abnormalFlag": field(obx, 8),
                "status": field(obx, 11),
            }
        )

    panel = field(obr, 4)
    return {
        "eventType": "RESULT" if is_result else "ORDER",
        "messageType": metadata.message_type,
        "messageControlId": metadata.message_control_id,
        "analyzerCode": metadata.analyzer_code or "UNKNOWN",
        "patient": {"id": patient_id, "name": field(pid, 5)},
        "order": {
            "id": order_id,
            "panelCode": component(panel, 0),
            "panelName": component(panel, 1),
            "status": field(orc, 1) if orc else ("RESULTED" if is_result else None),
        },
        "tests": tests,
    }


def build_ack(
    metadata: MessageMetadata, ack_code: str, error_message: str | None = None
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    control_id = metadata.message_control_id or "UNKNOWN"
    trigger = "R01" if metadata.message_type == "ORU^R01" else "O01"
    segments = [
        (
            "MSH|^~\\&|HOST_MIDDLEWARE|CRM|"
            f"{metadata.analyzer_code or 'ANALYZER'}|{metadata.sending_facility or 'LAB'}|"
            f"{timestamp}||ACK^{trigger}|ACK-{control_id}|P|{metadata.version}"
        ),
        f"MSA|{ack_code}|{control_id}"
        + (f"|{error_message[:160]}" if error_message else ""),
    ]
    return "\r".join(segments) + "\r"
