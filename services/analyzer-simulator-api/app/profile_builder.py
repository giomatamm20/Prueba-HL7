"""Profile-driven HL7 writer for analyzer communication contracts."""

from datetime import datetime, timezone
import re
from typing import Any

from .protocols import _build_pdf_base64
from .hl7_values import escaped, declare_utf8


def build_profile_message(profile: dict, analyzer: dict, order: dict, results: list[dict], event: str, sequence: int) -> tuple[str, str]:
    outbound = profile.get("outbound") or {}
    event_config = (outbound.get("events") or {}).get(event)
    if not event_config:
        raise ValueError(f"Profile {profile.get('name')} does not define outbound event {event}")
    now = datetime.now(timezone.utc)
    control_id = f"{analyzer['id']}-{event}-{now.strftime('%Y%m%d%H%M%S%f')}-{sequence:06d}"
    base = _base_context(analyzer, order, results, now, control_id, event)
    rows: list[list[str]] = []
    for segment in event_config.get("segments") or []:
        repetitions = results if segment.get("repeatBy") == "results" else [None]
        if segment.get("requiredWhen") == "hasResults" and not results:
            continue
        for index, result in enumerate(repetitions, start=1):
            context = {**base, "result": result or {}, "counter": index}
            row = [segment["name"]]
            if segment["name"] == "MSH":
                row.append("^~\\&")
            for field in segment.get("fields") or []:
                value = resolve_value(field, context)
                if field.get("required") and value in (None, ""):
                    raise ValueError(f"Outbound mapping requires {field.get('source') or field['target']}")
                if value is not None:
                    if field["target"] != "MSH-2":
                        _write_path(row, field["target"], escaped(value, scalar="." in field["target"]))
            rows.append(row)
    message = declare_utf8("\r".join("|".join(row) for row in rows) + "\r")
    schema = event_config.get("schema") or outbound.get("schema") or {}
    delimiters = schema.get("delimiters") or {}
    separators = "".join(delimiters.get(key, default) for key, default in
                         zip(("field", "component", "repetition", "escape", "subcomponent"), "|^~\\&"))
    return message.translate(str.maketrans("|^~\\&", separators)), control_id


def resolve_value(field: dict, context: dict) -> Any:
    source_type = field.get("sourceType", "dynamic")
    if source_type == "fixed":
        value: Any = field.get("value", "")
    elif source_type == "default":
        value = field.get("default", "")
    else:
        source = field.get("source", "")
        if source.startswith("inbound."):
            from .hl7_orders import hl7_path_value
            value = hl7_path_value(context.get("inbound") or "", source[8:])
        else:
            value = _resolve_path(context, source)
        if value in (None, ""):
            value = field.get("default")
    if value is None:
        return None
    transform = field.get("transform")
    if transform == "upper":
        return str(value).upper()
    if transform == "decimal2":
        return f"{float(value):.2f}"
    if transform == "stripSampleSequence":
        # AnaPath correlates ORC-4 with the base specimen identifier.  HIS
        # can send a technical aliquot suffix such as "-01" in SPM-2/OBR-3;
        # that suffix must not be echoed as the external order identifier.
        return re.sub(r"-\d+$", "", str(value))
    if transform in {"component1", "component2"}:
        index = 0 if transform == "component1" else 1
        components = str(value).split("^")
        return components[index] if len(components) > index else ""
    return value


def _base_context(analyzer: dict, order: dict, results: list[dict], now: datetime, control_id: str, event: str) -> dict:
    pdf_base64 = _build_pdf_base64(order, "F")
    return {
        "inbound": order.get("sourceRawMessage"),
        "customFields": order.get("customFields") or {},
        "analyzer": {"id": analyzer.get("id"), "name": analyzer.get("name"), "vendor": analyzer.get("vendor")},
        "patient": {
            "id": order.get("patientId"), "name": order.get("patientName"),
            "birthDate": order.get("patientBirthDate"), "sex": order.get("patientSex"),
            "address": order.get("patientAddress"), "visitType": order.get("visitType"),
            "visitNumber": order.get("visitNumber"),
        },
        "order": {
            "id": order.get("orderId"), "fillerId": order.get("fillerOrderId"), "status": order.get("orderStatus"),
            "orc2": order.get("orc2"), "orc3": order.get("orc3"), "orc4": order.get("orc4"),
            "obr2": order.get("obr2"), "obr3": order.get("obr3"),
        },
        "sample": {"id": order.get("sampleId"), "code": order.get("sampleCode"), "name": order.get("sampleName")},
        "test": {"code": order.get("procedureCode") or order.get("panelCode"), "name": order.get("procedureName") or order.get("panelName")},
        "resultDocument": {"pdfBase64": pdf_base64, "mimeType": "application/pdf", "fileName": f"{order.get('sampleId') or 'result'}.pdf"},
        "system": {"timestamp": now.strftime("%Y%m%d%H%M%S"), "messageId": control_id, "event": event},
    }


def _resolve_path(context: dict, path: str) -> Any:
    value: Any = context
    for item in (path or "").split("."):
        if not item:
            continue
        if not isinstance(value, dict):
            return None
        value = value.get(item)
    return value


def _write_path(row: list[str], target: str, value: str) -> None:
    """Writes SEG-Field[repetition].component.subcomponent into a row."""
    if "-" not in target:
        raise ValueError(f"Invalid HL7 target path: {target}")
    segment, position = target.upper().split("-", 1)
    if row[0] != segment:
        raise ValueError(f"Target {target} does not belong to {row[0]}")
    match = re.fullmatch(r"(\d+)(?:\[(\d+)\])?(?:\.(\d+))?(?:\.(\d+))?", position)
    if not match:
        raise ValueError(f"Invalid HL7 target path: {target}")
    field_text, repetition_text, component_text, subcomponent_text = match.groups()
    field_index = int(field_text)
    if field_index < 1 or any(int(number) < 1 for number in (repetition_text, component_text, subcomponent_text) if number):
        raise ValueError(f"HL7 indexes must be positive: {target}")
    if segment == "MSH" and field_index > 1:
        field_index -= 1
    while len(row) <= field_index:
        row.append("")
    repetition_index = int(repetition_text or 1) - 1
    repetitions = row[field_index].split("~") if row[field_index] else []
    while len(repetitions) <= repetition_index:
        repetitions.append("")
    if not component_text:
        repetitions[repetition_index] = value
        row[field_index] = "~".join(repetitions)
        return
    component_index = int(component_text) - 1
    components = repetitions[repetition_index].split("^") if repetitions[repetition_index] else []
    while len(components) <= component_index:
        components.append("")
    if subcomponent_text:
        subcomponent_index = int(subcomponent_text) - 1
        subcomponents = components[component_index].split("&") if components[component_index] else []
        while len(subcomponents) <= subcomponent_index:
            subcomponents.append("")
        subcomponents[subcomponent_index] = value
        components[component_index] = "&".join(subcomponents)
    else:
        components[component_index] = value
    repetitions[repetition_index] = "^".join(components)
    row[field_index] = "~".join(repetitions)
