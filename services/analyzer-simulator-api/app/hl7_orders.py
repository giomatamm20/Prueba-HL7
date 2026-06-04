from datetime import datetime, timezone


class HL7OrderError(ValueError):
    pass


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
    parts = value.split("^")
    return parts[position] if len(parts) > position and parts[position] else None


def extract_metadata(raw_message: str) -> dict:
    for segment in segments_from_message(raw_message):
        if segment[0] == "MSH":
            return {
                "sendingApplication": field(segment, 2),
                "sendingFacility": field(segment, 3),
                "messageType": field(segment, 8),
                "messageControlId": field(segment, 9),
                "version": field(segment, 11) or "2.5.1",
            }
    return {
        "sendingApplication": None,
        "sendingFacility": None,
        "messageType": None,
        "messageControlId": None,
        "version": "2.5.1",
    }


def parse_order(raw_message: str) -> dict:
    by_type: dict[str, list[list[str]]] = {}
    for segment in segments_from_message(raw_message):
        by_type.setdefault(segment[0], []).append(segment)

    if "MSH" not in by_type:
        raise HL7OrderError("Missing required MSH segment")
    metadata = extract_metadata(raw_message)
    if not metadata["messageControlId"]:
        raise HL7OrderError("Missing MSH-10 message control ID")
    if metadata["messageType"] not in {"ORM^O01", "OML^O33"}:
        raise HL7OrderError(f"Unsupported order message type: {metadata['messageType'] or 'empty'}")
    if "PID" not in by_type:
        raise HL7OrderError("Missing required PID segment")
    if "OBR" not in by_type:
        raise HL7OrderError("Missing required OBR segment")

    pid = by_type["PID"][0]
    obr = by_type["OBR"][0]
    spm = by_type.get("SPM", [None])[0]
    orc = by_type.get("ORC", [None])[0]
    patient_id = field(pid, 3)
    if not patient_id:
        raise HL7OrderError("Missing PID-3 patient identifier")

    order_id = field(orc, 2) or field(obr, 2) or field(obr, 3)
    if not order_id:
        raise HL7OrderError("Missing order identifier")

    requested = []
    for order_segment in by_type["OBR"]:
        code = component(field(order_segment, 4), 0)
        if code:
            requested.append(code)
    if not requested:
        raise HL7OrderError("Missing requested test or panel in OBR-4")

    panel = field(obr, 4)
    sample_id = field(spm, 2) or field(obr, 3) or order_id
    return {
        "messageType": metadata["messageType"],
        "messageControlId": metadata["messageControlId"],
        "orderId": order_id,
        "sampleId": sample_id,
        "barcode": sample_id,
        "patientId": patient_id,
        "patientName": field(pid, 5),
        "priority": field(obr, 27) or "ROUTINE",
        "requestedTests": requested,
        "panelCode": component(panel, 0),
        "panelName": component(panel, 1),
    }


def build_ack(metadata: dict, ack_code: str, error_message: str | None = None) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    control_id = metadata.get("messageControlId") or "UNKNOWN"
    trigger = "O33" if metadata.get("messageType") == "OML^O33" else "O01"
    segments = [
        (
            "MSH|^~\\&|ANALYZER_SIMULATOR|SIMLAB|"
            f"{metadata.get('sendingApplication') or 'LIS'}|{metadata.get('sendingFacility') or 'FACILITY'}|"
            f"{timestamp}||ACK^{trigger}|ACK-{control_id}|P|{metadata.get('version') or '2.5.1'}"
        ),
        f"MSA|{ack_code}|{control_id}" + (f"|{error_message[:160]}" if error_message else ""),
    ]
    return "\r".join(segments) + "\r"
