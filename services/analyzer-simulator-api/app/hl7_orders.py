from datetime import datetime, timezone
import re


class HL7OrderError(ValueError):
    pass


def segments_from_message(raw_message: str) -> list[list[str]]:
    normalized = raw_message.replace("\r\n", "\r").replace("\n", "\r")
    lines = [line for line in normalized.split("\r") if line]
    separator = lines[0][3] if lines and lines[0].startswith("MSH") and len(lines[0]) > 3 else "|"
    return [line.split(separator) for line in lines]


def field(segment: list[str] | None, position: int) -> str | None:
    if segment is None or len(segment) <= position or segment[position] == "":
        return None
    return segment[position]


def component(value: str | None, position: int, separator: str = "^") -> str | None:
    if not value:
        return None
    parts = value.split(separator)
    return parts[position] if len(parts) > position and parts[position] else None


def extract_metadata(raw_message: str) -> dict:
    for segment in segments_from_message(raw_message):
        if segment[0] == "MSH":
            return {
                "sendingApplication": field(segment, 2),
                "sendingFacility": field(segment, 3),
                "receivingApplication": field(segment, 4),
                "receivingFacility": field(segment, 5),
                "messageType": "^".join((field(segment, 8) or "").split((field(segment, 1) or "^")[0])[:2]),
                "messageControlId": field(segment, 9),
                "version": field(segment, 11) or "2.5.1",
                "delimiters": {"field": raw_message[3], "encoding": field(segment, 1)},
            }
    return {
        "sendingApplication": None,
        "sendingFacility": None,
        "receivingApplication": None,
        "receivingFacility": None,
        "messageType": None,
        "messageControlId": None,
        "version": "2.5.1",
    }


def parse_order(
    raw_message: str,
    accepted_message_types: set[str] | None = None,
    inbound_mappings: dict[str, str] | None = None,
    required_order_segments: set[str] | None = None,
) -> dict:
    from .hl7_contract import parse_hl7, canonical_message
    raw_message = canonical_message(raw_message)
    parsed_message = parse_hl7(raw_message)
    component_separator = parsed_message.delimiters.component
    charset = hl7_path_value(raw_message, "MSH-18")
    if charset and charset not in {"ASCII", "UNICODE UTF-8", "UTF-8"}:
        raise HL7OrderError("Unsupported character set: this simulator accepts ASCII/UTF-8")
    by_type: dict[str, list[list[str]]] = {}
    for segment in segments_from_message(raw_message):
        by_type.setdefault(segment[0], []).append(segment)

    if "MSH" not in by_type:
        raise HL7OrderError("Missing required MSH segment")
    metadata = extract_metadata(raw_message)
    if not metadata["messageControlId"]:
        raise HL7OrderError("Missing MSH-10 message control ID")
    accepted = accepted_message_types or {"ORM^O01", "OML^O21", "OML^O33"}
    if metadata["messageType"] not in accepted:
        raise HL7OrderError(f"Unsupported order message type: {metadata['messageType'] or 'empty'}")
    required = {"ORC", "OBR"} if required_order_segments is None else required_order_segments
    for required_segment in required:
        if required_segment not in by_type:
            raise HL7OrderError(f"Missing required {required_segment} segment")

    pid = by_type.get("PID", [None])[0]
    pv1 = by_type.get("PV1", [None])[0]
    obr = by_type.get("OBR", [None])[0]
    spm = by_type.get("SPM", [None])[0]
    orc = by_type.get("ORC", [None])[0]
    ntes = by_type.get("NTE", [])

    mappings = inbound_mappings or {}
    mapped = lambda name, fallback=None: mapped_value(raw_message, mappings.get(name)) or fallback
    patient_id = mapped("patientId", field(pid, 3) or "UNKNOWN")
    patient_name = mapped("patientName", field(pid, 5) or "SIMULATED^PATIENT")

    order_id = mapped("orderId", field(orc, 2) or field(obr, 2) or field(obr, 3) or metadata["messageControlId"])

    procedure_code = mapped("procedureCode", component(field(obr, 4), 0, component_separator) or "UNKNOWN")
    procedure_name = mapped("procedureName", component(field(obr, 4), 1, component_separator) or "PROCEDIMIENTO DESCONOCIDO")

    requested = []
    for order_segment in by_type.get("OBR", []):
        code = component(field(order_segment, 4), 0, component_separator)
        if code:
            requested.append(code)

    order_control = mapped("orderControl", field(orc, 1) or "NW")
    order_status = field(orc, 5)
    sample_id = mapped("sampleId", field(spm, 2) or field(obr, 3) or order_id)
    sample_name = mapped("sampleName", component(field(spm, 4), 1, component_separator) or field(spm, 4))
    sample_code = mapped("sampleCode", component(field(spm, 4), 0, component_separator) or sample_id)
    topography_code = mapped("topographyCode", component(field(spm, 8), 0) or component(field(obr, 15), 0))
    topography_name = mapped("topographyName", component(field(spm, 8), 1) or component(field(obr, 15), 1))
    laterality = mapped("laterality", component(field(spm, 8), 2))
    observations = [field(segment, 3) for segment in ntes if field(segment, 3)]
    known_mapping_names = {
        "patientId", "patientName", "orderControl", "orderId", "fillerOrderId",
        "sampleId", "sampleCode", "sampleName", "procedureCode", "procedureName",
        "visitType", "visitNumber", "priority", "clinicalInfo", "topographyCode",
        "topographyName", "laterality",
    }
    custom_fields = {
        name: value
        for name, path in mappings.items()
        if name not in known_mapping_names
        if (value := mapped_value(raw_message, path)) is not None
    }

    # Keep each ORC/OBR/SPM group in wire order, including repeated study codes.
    # SPM may precede OBR (OML^O33) or follow it (ORM/OML^O21).
    studies = []
    current_orc, current_spm, current_study = None, None, None
    spm_consumed = False
    for segment in segments_from_message(raw_message):
        if segment[0] == "ORC":
            current_orc = segment
            current_study = None
        elif segment[0] == "SPM":
            current_spm = segment
            spm_consumed = False
            if metadata["messageType"] == "OML^O33":
                current_study = None
            elif current_study is not None:
                current_study["sample"] = {"id": field(segment, 2), "code": component(field(segment, 4), 0),
                                           "name": component(field(segment, 4), 1), "raw": segment}
                current_study["sampleId"] = field(segment, 2) or current_study["sampleId"]
                spm_consumed = True
        elif segment[0] == "OBR":
            scoped_spm = current_spm if (not spm_consumed or metadata["messageType"] == "OML^O33" or
                                         field(current_spm, 2) == field(segment, 3)) else None
            group_message = "\r".join("|".join(row) for row in [by_type["MSH"][0], *([current_orc] if current_orc else []),
                                                                 segment, *([scoped_spm] if scoped_spm else [])]) + "\r"
            group_value = lambda name, default: mapped_value(group_message, mappings.get(name)) or default
            specimen_id = field(scoped_spm, 2)
            specimen_id = group_value("sampleId", specimen_id or field(segment, 3) or sample_id)
            current_study = {"key": f"study-{len(studies) + 1}",
                             "code": group_value("procedureCode", component(field(segment, 4), 0) or "UNKNOWN"),
                             "name": group_value("procedureName", component(field(segment, 4), 1) or ""),
                             "orderId": field(current_orc, 2) or field(segment, 2) or order_id,
                             "fillerOrderId": field(current_orc, 3) or field(segment, 3),
                             "sampleId": specimen_id, "sample": {"id": specimen_id,
                               "code": component(field(scoped_spm, 4), 0), "name": component(field(scoped_spm, 4), 1), "raw": scoped_spm},
                             "orc": current_orc, "obr": segment, "notes": []}
            studies.append(current_study)
            spm_consumed = scoped_spm is not None
        elif segment[0] == "NTE" and current_study is not None:
            current_study["notes"].append(field(segment, 3) or "")

    if studies:
        # The queue barcode identifies the first study's specimen, never an
        # unrelated SPM encountered later in a multi-specimen message.
        sample_id = studies[0]["sampleId"]
        sample_code = studies[0]["sample"].get("code") or sample_id
        sample_name = studies[0]["sample"].get("name") or sample_name

    return {
        "messageType": metadata["messageType"],
        "messageControlId": metadata["messageControlId"],
        "orderControl": order_control,
        "orderStatus": order_status,
        # Preserve the exact identifiers received from HIS.  AnaPath reports
        # must echo them so Mirth can correlate the ORU with the OML.
        "orc2": field(orc, 2),
        "orc3": field(orc, 3),
        "orc4": field(orc, 4),
        "obr2": field(obr, 2),
        "obr3": field(obr, 3),
        "orderId": order_id,
        "fillerOrderId": mapped("fillerOrderId", field(orc, 3) or field(obr, 3)),
        "prescriptionId": field(orc, 4),
        "sampleId": sample_id,
        "barcode": sample_id,
        "sampleCode": sample_code,
        "sampleName": sample_name,
        "sampleCount": field(spm, 12),
        "patientId": patient_id,
        "patientName": patient_name,
        "patientBirthDate": field(pid, 7),
        "patientSex": field(pid, 8),
        "patientAddress": field(pid, 11),
        "visitType": mapped("visitType", field(pv1, 2)),
        "visitNumber": mapped("visitNumber", field(pv1, 19)),
        "priority": mapped("priority", field(obr, 27) or field(orc, 7) or "ROUTINE"),
        "requestedAt": field(obr, 6) or field(orc, 9),
        "requestedTests": [study["code"] for study in studies] or requested or [procedure_code],
        "studies": studies,
        "panelCode": procedure_code,
        "panelName": procedure_name,
        "procedureCode": procedure_code,
        "procedureName": procedure_name,
        "clinicalInfo": mapped("clinicalInfo", field(obr, 13)),
        "observations": observations,
        "topographyCode": topography_code,
        "topographyName": topography_name,
        "laterality": laterality,
        "customFields": custom_fields,
        "rawMetadata": metadata,
    }


def mapped_value(raw_message: str, paths: str | None) -> str | None:
    """Returns the first populated path from a comma-separated mapping list."""
    for path in (paths or "").split(","):
        value = hl7_path_value(raw_message, path)
        if value:
            return value
    return None


def hl7_path_value(raw_message: str, path: str) -> str | None:
    """Reads SEG-FIELD[repetition].component.subcomponent (indexes are 1-based)."""
    normalized = path.strip().upper()
    match = re.fullmatch(r"([A-Z0-9]{3})-(\d+)(?:\[(\d+)\])?(?:\.(\d+))?(?:\.(\d+))?", normalized)
    if not match:
        return None
    segment_name, field_text, repetition_text, component_text, subcomponent_text = match.groups()
    if any(int(number) < 1 for number in (field_text, repetition_text, component_text, subcomponent_text) if number):
        return None
    try:
        from .hl7_contract import parse_hl7

        message = parse_hl7(raw_message)
    except ValueError:
        return None
    segment = next((item for item in message.segments if item.name == segment_name), None)
    if not segment:
        return None
    parsed_field = segment.fields.get(int(field_text))
    if not parsed_field or parsed_field.raw == "":
        return None
    if repetition_text is None and component_text is None and subcomponent_text is None:
        return parsed_field.raw
    repetition_index = int(repetition_text or 1) - 1
    if repetition_index >= len(parsed_field.repetitions):
        return None
    repetition = parsed_field.repetitions[repetition_index]
    if component_text is None:
        delimiters = message.delimiters
        return delimiters.component.join(delimiters.subcomponent.join(part) for part in repetition)
    component_index = int(component_text) - 1
    if component_index >= len(repetition):
        return None
    parsed_component = repetition[component_index]
    if subcomponent_text is None:
        return message.delimiters.subcomponent.join(parsed_component) or None
    subcomponent_index = int(subcomponent_text) - 1
    if subcomponent_index >= len(parsed_component):
        return None
    return parsed_component[subcomponent_index] or None


def validate_anapath_order(payload: dict) -> None:
    if payload["messageType"] != "OML^O21":
        raise HL7OrderError("AnaPath expects OML^O21 messages")
    if payload["orderControl"] not in {"NW", "CA"}:
        raise HL7OrderError("AnaPath only supports ORC-1 NW or CA")
    if not payload.get("visitType"):
        raise HL7OrderError("Missing PV1-2 visit type")
    if not payload.get("orderId"):
        raise HL7OrderError("Missing HIS order number")
    if not payload.get("procedureCode"):
        raise HL7OrderError("Missing procedure code")
    if not payload.get("procedureName"):
        raise HL7OrderError("Missing procedure description")
    if payload["orderControl"] == "NW" and not payload.get("sampleId"):
        raise HL7OrderError("Missing specimen/sample identifier")


def build_ack(
    metadata: dict,
    ack_code: str,
    error_message: str | None = None,
    ack_text: str | None = None,
    warnings: list[str] | None = None,
) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    control_id = metadata.get("messageControlId") or "UNKNOWN"
    message_type = metadata.get("messageType")
    if message_type == "OML^O33":
        trigger = "O33"
    elif message_type == "OML^O21":
        trigger = "O21"
    else:
        trigger = "O01"
    msa = f"MSA|{ack_code}|{control_id}"
    from .hl7_values import escaped, declare_utf8
    if error_message:
        msa += f"|{escaped(error_message[:160], scalar=True)}"
    elif ack_text:
        msa += f"|{escaped(ack_text[:160], scalar=True)}"
    segments = [
        (
            "MSH|^~\\&|ANALYZER_SIMULATOR|SIMLAB|"
            f"{metadata.get('sendingApplication') or 'LIS'}|{metadata.get('sendingFacility') or 'FACILITY'}|"
            f"{timestamp}||ACK^{trigger}|ACK-{control_id}|P|{metadata.get('version') or '2.5.1'}"
        ),
        msa,
    ]
    def append_err(details: list[str], severity: str) -> None:
        for detail in details[:10]:
            escaped = (
                detail.replace("\\", "\\E\\")
                .replace("|", "\\F\\")
                .replace("^", "\\S\\")
                .replace("~", "\\R\\")
                .replace("&", "\\T\\")
            )
            segments.append(f"ERR|||207^Application internal error^HL70357|{severity}|||{escaped[:240]}")

    if error_message:
        append_err(error_message.removeprefix("Contrato HL7 incumplido: ").split("; "), "E")
    if warnings:
        append_err(warnings, "W")
    message = declare_utf8("\r".join(segments) + "\r")
    delimiters = metadata.get("delimiters") or {}
    separators = str(delimiters.get("field") or "|") + str(delimiters.get("encoding") or "^~\\&")
    if len(separators) == 5 and len(set(separators)) == 5 and all(ord(c) >= 32 for c in separators):
        message = message.translate(str.maketrans("|^~\\&", separators))
    return message
