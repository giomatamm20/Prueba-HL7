from .hl7_orders import HL7OrderError, component, field, segments_from_message, hl7_path_value


DEFAULT_HL7_BEHAVIOR = {
    "acceptedMessageTypes": ["ORM^O01", "OML^O21", "OML^O33"],
    "requiredSegments": ["MSH", "PID", "ORC", "OBR"],
    "requiredFields": ["MSH-10", "PID-3", "PID-5", "ORC-2", "OBR-4.1"],
    "allowedOrderControls": ["NW", "CA"],
    "ackSuccessCode": "AA",
    "ackErrorCode": "AE",
    "ackSuccessText": "",
    "outboundEvents": ["REPORT"],
    "allowedScenarios": [
        "NORMAL",
        "FORCE_CRITICAL",
        "SAMPLE_ERROR",
        "NO_RESPONSE",
        "MALFORMED_RESULT",
    ],
    "reportStatuses": ["F"],
    "inboundMappings": {
        "patientId": "PID-3",
        "patientName": "PID-5",
        "orderControl": "ORC-1",
        "orderId": "ORC-2,OBR-2,OBR-3",
        "fillerOrderId": "ORC-3,OBR-3",
        "sampleId": "SPM-2,OBR-3,ORC-3",
        "sampleCode": "SPM-4.1",
        "sampleName": "SPM-4.2,SPM-4",
        "procedureCode": "OBR-4.1",
        "procedureName": "OBR-4.2",
        "priority": "OBR-27,ORC-7",
        "clinicalInfo": "OBR-13",
    },
    "resultFormat": "NUMERIC",
    "outboundMappings": {
        "orderId": "OBR-2",
        "sampleId": "OBR-3",
        "procedureCode": "OBR-4.1",
        "procedureName": "OBR-4.2",
        "resultCode": "OBX-3.1",
        "resultValue": "OBX-5",
        "resultUnit": "OBX-6",
        "resultStatus": "OBX-11",
    },
}





def default_behavior(protocol: str = "HL7") -> dict:
    if protocol != "HL7":
        return {}
    template = DEFAULT_HL7_BEHAVIOR
    return {key: list(value) if isinstance(value, list) else value for key, value in template.items()}


def normalize_behavior(behavior: dict | None, protocol: str = "HL7") -> dict:
    base = default_behavior(protocol)
    if protocol != "HL7":
        return behavior or {}
    incoming = behavior or {}
    normalized = {**base, **incoming}
    for key in [
        "acceptedMessageTypes",
        "requiredSegments",
        "requiredFields",
        "allowedOrderControls",
        "outboundEvents",
        "allowedScenarios",
        "reportStatuses",
    ]:
        normalized[key] = _list(incoming.get(key, normalized.get(key, [])))
    normalized["ackSuccessCode"] = str(normalized.get("ackSuccessCode") or "AA").upper()
    normalized["ackErrorCode"] = str(normalized.get("ackErrorCode") or "AE").upper()
    normalized["ackSuccessText"] = str(normalized.get("ackSuccessText") or "")
    mappings = incoming.get("inboundMappings", normalized.get("inboundMappings", {}))
    normalized["inboundMappings"] = {
        str(key): str(value).strip()
        for key, value in (mappings.items() if isinstance(mappings, dict) else [])
        if str(key).strip() and str(value).strip()
    }
    outbound_mappings = incoming.get("outboundMappings", normalized.get("outboundMappings", {}))
    normalized["outboundMappings"] = {
        str(key): str(value).strip()
        for key, value in (outbound_mappings.items() if isinstance(outbound_mappings, dict) else [])
        if str(key).strip() and str(value).strip()
    }
    normalized["resultFormat"] = str(normalized.get("resultFormat") or "NUMERIC").upper()
    return normalized


def validate_hl7_behavior(raw_message: str, payload: dict, behavior: dict) -> None:
    required_segments = set(_list(behavior.get("requiredSegments")))
    present_segments = {segment[0] for segment in segments_from_message(raw_message)}
    for segment in required_segments:
        if segment and segment not in present_segments:
            raise HL7OrderError(f"Missing required {segment} segment")

    accepted_types = set(_list(behavior.get("acceptedMessageTypes")))
    if accepted_types and payload.get("messageType") not in accepted_types:
        raise HL7OrderError(f"Unsupported order message type: {payload.get('messageType') or 'empty'}")

    allowed_controls = set(_list(behavior.get("allowedOrderControls")))
    if allowed_controls and payload.get("orderControl") not in allowed_controls:
        raise HL7OrderError(f"Unsupported ORC-1 order control: {payload.get('orderControl') or 'empty'}")

    for required_field in _list(behavior.get("requiredFields")):
        if not hl7_path_value(raw_message, required_field):
            raise HL7OrderError(f"Missing {required_field}")


def _list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
