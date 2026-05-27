from dataclasses import dataclass
from datetime import datetime, timezone


class HL7ParseError(ValueError):
    pass


@dataclass
class MessageMetadata:
    sending_application: str | None = None
    sending_facility: str | None = None
    message_type: str | None = None
    message_control_id: str | None = None
    version: str = "2.3"


def segments_from_message(raw_message: str) -> list[list[str]]:
    normalized = raw_message.replace("\r\n", "\r").replace("\n", "\r")
    return [line.split("|") for line in normalized.split("\r") if line]


def field(segment: list[str], position: int) -> str | None:
    if len(segment) <= position or segment[position] == "":
        return None
    return segment[position]


def component(field_value: str | None, position: int) -> str | None:
    if not field_value:
        return None
    components = field_value.split("^")
    return components[position] if len(components) > position and components[position] else None


def extract_metadata(raw_message: str) -> MessageMetadata:
    for segment in segments_from_message(raw_message):
        if segment[0] == "MSH":
            return MessageMetadata(
                sending_application=field(segment, 2),
                sending_facility=field(segment, 3),
                message_type=field(segment, 8),
                message_control_id=field(segment, 9),
                version=field(segment, 11) or "2.3",
            )
    return MessageMetadata()


def parse_value(value: str, value_type: str | None) -> float | str:
    if value_type == "NM":
        try:
            return float(value)
        except ValueError as error:
            raise HL7ParseError(f"Invalid numeric OBX value: {value}") from error
    return value


def parse_oru_result(raw_message: str) -> dict:
    segments = segments_from_message(raw_message)
    by_type: dict[str, list[list[str]]] = {}
    for segment in segments:
        by_type.setdefault(segment[0], []).append(segment)

    for required in ("MSH", "PID", "OBR", "OBX"):
        if required not in by_type:
            raise HL7ParseError(f"Missing required {required} segment")

    metadata = extract_metadata(raw_message)
    if metadata.message_type != "ORU^R01":
        raise HL7ParseError(f"Unsupported message type: {metadata.message_type or 'empty'}")
    if not metadata.message_control_id:
        raise HL7ParseError("Missing MSH-10 message control ID")

    pid = by_type["PID"][0]
    obr = by_type["OBR"][0]
    patient_id = field(pid, 3)
    order_id = field(obr, 3)
    if not patient_id:
        raise HL7ParseError("Missing PID-3 patient identifier")
    if not order_id:
        raise HL7ParseError("Missing OBR-3 order identifier")

    tests = []
    for obx in by_type["OBX"]:
        code = component(field(obx, 3), 0)
        raw_value = field(obx, 5)
        if not code or raw_value is None:
            raise HL7ParseError("OBX segment missing test code or value")
        tests.append(
            {
                "code": code,
                "value": parse_value(raw_value, field(obx, 2)),
                "unit": field(obx, 6),
            }
        )

    return {
        "messageControlId": metadata.message_control_id,
        "analyzer": metadata.sending_application,
        "patientId": patient_id,
        "patientName": field(pid, 5),
        "orderId": order_id,
        "panel": component(field(obr, 4), 0),
        "tests": tests,
    }


def build_ack(
    metadata: MessageMetadata,
    acknowledgement_code: str,
    error_message: str | None = None,
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    control_id = metadata.message_control_id or "UNKNOWN"
    segments = [
        (
            "MSH|^~\\&|LISTENER|SANDBOX|"
            f"{metadata.sending_application or 'ANALYZER'}|{metadata.sending_facility or 'LAB'}|"
            f"{timestamp}||ACK^R01|ACK-{control_id}|P|{metadata.version}"
        ),
        f"MSA|{acknowledgement_code}|{control_id}"
        + (f"|{error_message[:120]}" if error_message else ""),
    ]
    return "\r".join(segments) + "\r"
