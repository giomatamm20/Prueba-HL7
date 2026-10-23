"""Validate editable profile definitions and actual outbound messages."""
import copy
import re

from .hl7_contract import parse_hl7, validate_hl7_contract
from .hl7_orders import extract_metadata
from .profile_parser import validate_strict_profile

PATH = re.compile(r"([A-Z0-9]{3})-([1-9]\d*)(?:\[([1-9]\d*)\])?(?:\.([1-9]\d*))?(?:\.([1-9]\d*))?")
SOURCES = {"analyzer", "patient", "order", "sample", "test", "study", "resultDocument", "system", "result", "counter", "customFields", "inbound"}


def profile_ready(profile: dict | None) -> bool:
    if not profile:
        return False
    inbound, outbound = profile.get("inbound") or {}, profile.get("outbound") or {}
    events = outbound.get("events") or {}
    return bool(inbound.get("schema") and (outbound.get("schema") or events.get("RESULT_FINAL") or events.get("IMAGE_READY")))


def valid_path(path: str) -> bool:
    match = PATH.fullmatch(str(path))
    return bool(match and all(int(n) <= 1000 for n in match.groups()[1:] if n))


def validate_schema(schema: dict) -> None:
    if not isinstance(schema, dict):
        raise ValueError("Contract schema must be a JSON object")
    if not schema:
        return
    if int(schema.get("formatVersion") or 1) < 2:
        for fields in schema.values():
            if not isinstance(fields, dict):
                raise ValueError("Invalid legacy contract fields")
            for path, rule in fields.items():
                if not valid_path(path) or not isinstance(rule, dict):
                    raise ValueError(f"Invalid contract rule {path}")
        return
    segments = schema.get("segments")
    if not isinstance(segments, list) or not segments or segments[0].get("name") != "MSH":
        raise ValueError("Contract must begin with MSH")
    if not re.fullmatch(r"[A-Z0-9]{3}\^[A-Z0-9]{3}(?:\^[A-Z0-9_]+)?", str(schema.get("messageType", ""))):
        raise ValueError("Contract must define a valid messageType")
    delimiters = schema.get("delimiters") or {}
    chars = [delimiters.get(key, default) for key, default in
             zip(("field", "component", "repetition", "escape", "subcomponent"), "|^~\\&")]
    if any(not isinstance(char, str) or len(char) != 1 or ord(char) < 32 for char in chars) or len(set(chars)) != 5:
        raise ValueError("Contract delimiters must be five distinct printable characters")
    for segment in segments:
        if not re.fullmatch(r"[A-Z0-9]{3}", str(segment.get("name", ""))):
            raise ValueError("Invalid segment name")
        minimum, maximum = int(segment.get("min", 0)), segment.get("max", 1)
        if minimum < 0 or (maximum != "*" and not minimum <= int(maximum) <= 1000):
            raise ValueError("Invalid segment cardinality")
    # Walk all nested rules; malformed structures must fail at save, not at reception.
    def check(value):
        if isinstance(value, dict):
            if "pattern" in value:
                try:
                    re.compile(str(value["pattern"]))
                except re.error as error:
                    raise ValueError("Invalid contract regular expression") from error
            for key in ("minRepetitions", "maxRepetitions", "minComponents", "maxComponents"):
                if key in value and value[key] != "*" and not 0 <= int(value[key]) <= 1000:
                    raise ValueError(f"Invalid {key}")
            for key in ("fields", "components", "subcomponents"):
                if key in value:
                    if not isinstance(value[key], dict) or any(not str(n).isdigit() or not 1 <= int(n) <= 1000 or not isinstance(rule, dict) for n, rule in value[key].items()):
                        raise ValueError(f"Invalid contract {key}")
            if "repetitions" in value and not isinstance(value["repetitions"], list):
                raise ValueError("Invalid contract repetitions")
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                if not isinstance(child, (str, int, float, bool, dict, list, type(None))):
                    raise ValueError("Invalid contract rule")
                check(child)
    check(schema)


def validate_profile(payload: dict) -> dict:
    try:
        return _validate_profile(payload)
    except (TypeError, AttributeError, KeyError, OverflowError) as error:
        raise ValueError(f"Invalid profile structure: {error}") from error


def _validate_profile(payload: dict) -> dict:
    profile = copy.deepcopy(payload)
    for direction in ("inbound", "outbound"):
        block = profile.get(direction) or {}
        if not isinstance(block, dict):
            raise ValueError(f"{direction} must be an object")
        validate_schema(block.get("schema") or {})
        profile[direction] = block
    inbound = profile["inbound"]
    schema = inbound.get("schema") or {}
    if int(schema.get("formatVersion") or 1) >= 2:
        inbound["messageType"] = schema["messageType"]
    mode = inbound.get("validationMode") or ("STRICT" if inbound.get("strictValidation", False) else "WARN")
    if mode not in {"STRICT", "WARN"}:
        raise ValueError("validationMode must be STRICT or WARN")
    inbound["validationMode"] = mode
    inbound["strictValidation"] = mode == "STRICT"
    mappings = inbound.get("mappings") or []
    if not isinstance(mappings, list):
        raise ValueError("Inbound mappings must be a list")
    for mapping in mappings:
        if not isinstance(mapping, dict) or not mapping.get("target"):
            raise ValueError("Every inbound mapping needs a target")
        alternatives = mapping.get("alternatives") or []
        if not isinstance(alternatives, list):
            raise ValueError("Mapping alternatives must be a list")
        for path in [mapping.get("source", ""), *alternatives]:
            if path and not valid_path(path):
                raise ValueError(f"Invalid inbound mapping path: {path}")
        if mapping.get("required") and not mapping.get("source"):
            raise ValueError("Required mapping needs a source")
    events = profile["outbound"].get("events") or {}
    for schema in (profile["outbound"].get("eventSchemas") or {}).values():
        validate_schema(schema)
    if not isinstance(events, dict):
        raise ValueError("Outbound events must be an object")
    for event, config in events.items():
        if not isinstance(config, dict):
            raise ValueError(f"Invalid event {event}")
        segments = config.get("segments")
        if not isinstance(segments, list) or not segments or segments[0].get("name") != "MSH":
            raise ValueError(f"Event {event} must begin with MSH")
        if not config.get("messageType"):
            raise ValueError(f"Event {event} requires messageType")
        validate_schema(config.get("schema") or {})
        def all_segments(items, depth=0):
            if depth > 6 or not isinstance(items, list):
                raise ValueError("Invalid outbound child segments")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Invalid outbound segment")
                yield item
                yield from all_segments(item.get("children", []), depth + 1)
        for segment in all_segments(segments):
            if not isinstance(segment, dict) or not re.fullmatch(r"[A-Z0-9]{3}", str(segment.get("name", ""))):
                raise ValueError("Invalid outbound segment")
            if segment.get("repeatBy") not in {None, "results", "studies"}:
                raise ValueError("Unsupported repeatBy")
            if segment.get("requiredWhen") not in {None, "hasResults", "hasNote"}:
                raise ValueError("Unsupported requiredWhen")
            for field in segment.get("fields") or []:
                path = field.get("target", "")
                if not valid_path(path) or not path.startswith(segment["name"] + "-") or path == "MSH-1":
                    raise ValueError(f"Invalid outbound target: {path}")
                source_type = field.get("sourceType", "dynamic")
                if source_type not in {"fixed", "default", "dynamic"}:
                    raise ValueError("Invalid sourceType")
                if source_type == "dynamic" and str(field.get("source", "")).split(".")[0] not in SOURCES:
                    raise ValueError(f"Unknown outbound source: {field.get('source')}")
                if str(field.get("source", "")).startswith("inbound.") and not valid_path(field["source"][8:]):
                    raise ValueError("Inbound source must name an HL7 path, e.g. inbound.ZXX-1")
                if field.get("transform") not in {None, "upper", "decimal2", "stripSampleSequence", "component1", "component2"}:
                    raise ValueError("Unsupported mapping transform")
        msh = segments[0].get("fields") or []
        for required in ("MSH-9", "MSH-10", "MSH-12"):
            if not any(f["target"] == required or f["target"].startswith(required + ".") for f in msh):
                raise ValueError(f"Event {event} requires a {required} mapping")
    return profile


def validate_outbound(message: str, profile: dict | None, event: str, control_id: str) -> None:
    parsed = parse_hl7(message)
    metadata = extract_metadata(message)
    if not metadata.get("messageType") or metadata.get("messageControlId") != control_id:
        raise ValueError("Outbound message type/control ID is missing or inconsistent")
    if not parsed.segments[0].fields.get(12) or not parsed.segments[0].fields[12].raw:
        raise ValueError("Outbound message requires MSH-12")
    block = (profile or {}).get("outbound") or {}
    config = (block.get("events") or {}).get(event) or {}
    if config.get("messageType") and metadata["messageType"] != config["messageType"]:
        raise ValueError("Outbound message type differs from event definition")
    schema = config.get("schema") or (block.get("eventSchemas") or {}).get(event) or block.get("schema")
    if schema:
        validate_strict_profile(message, schema)
