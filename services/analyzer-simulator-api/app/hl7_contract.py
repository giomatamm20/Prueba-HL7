"""Inference, rendering and strict validation of analyzer-specific HL7 contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class HL7Delimiters:
    field: str = "|"
    component: str = "^"
    repetition: str = "~"
    escape: str = "\\"
    subcomponent: str = "&"

    def as_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "component": self.component,
            "repetition": self.repetition,
            "escape": self.escape,
            "subcomponent": self.subcomponent,
        }


@dataclass
class HL7Field:
    raw: str
    repetitions: list[list[list[str]]]


@dataclass
class HL7Segment:
    name: str
    fields: dict[int, HL7Field]


@dataclass
class HL7Message:
    delimiters: HL7Delimiters
    segments: list[HL7Segment]


class HL7ContractError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Contrato HL7 incumplido: " + "; ".join(errors))


FIELD_METADATA: dict[str, tuple[str, str]] = {
    "MSH-2": ("ENCODING_CHARACTERS", "ST"),
    "MSH-3": ("SENDING_APPLICATION", "HD"),
    "MSH-4": ("SENDING_FACILITY", "HD"),
    "MSH-5": ("RECEIVING_APPLICATION", "HD"),
    "MSH-6": ("RECEIVING_FACILITY", "HD"),
    "MSH-7": ("MESSAGE_DATETIME", "TS"),
    "MSH-9.1": ("MESSAGE_TYPE", "ID"),
    "MSH-9.2": ("TRIGGER_EVENT", "ID"),
    "MSH-9.3": ("MESSAGE_STRUCTURE", "ID"),
    "MSH-10": ("MESSAGE_CONTROL_ID", "ST"),
    "MSH-11": ("PROCESSING_ID", "PT"),
    "MSH-12": ("HL7_VERSION", "VID"),
    "PID-3.1": ("PATIENT_IDENTIFIER", "ST"),
    "PID-3.4": ("ASSIGNING_AUTHORITY", "HD"),
    "PID-3.5": ("IDENTIFIER_TYPE", "ID"),
    "PID-5.1": ("FAMILY_NAME", "FN"),
    "PID-5.2": ("GIVEN_NAME", "ST"),
    "PID-7": ("BIRTH_DATE", "DT"),
    "PID-8": ("ADMINISTRATIVE_SEX", "IS"),
    "PV1-2": ("PATIENT_CLASS", "IS"),
    "ORC-1": ("ORDER_CONTROL", "ID"),
    "ORC-2.1": ("PLACER_ORDER_NUMBER", "ST"),
    "ORC-3.1": ("FILLER_ORDER_NUMBER", "ST"),
    "OBR-2.1": ("PLACER_ORDER_NUMBER", "ST"),
    "OBR-3.1": ("FILLER_ORDER_NUMBER", "ST"),
    "OBR-4.1": ("TEST_CODE", "ST"),
    "OBR-4.2": ("TEST_NAME", "ST"),
    "OBX-1": ("SET_ID", "SI"),
    "OBX-2": ("VALUE_TYPE", "ID"),
    "OBX-3.1": ("OBSERVATION_IDENTIFIER", "ST"),
    "OBX-3.2": ("OBSERVATION_TEXT", "ST"),
    "OBX-5": ("OBSERVATION_VALUE", "VARIES"),
    "OBX-6": ("UNITS", "CE"),
    "OBX-7": ("REFERENCE_RANGE", "ST"),
    "OBX-8": ("ABNORMAL_FLAGS", "IS"),
    "OBX-11": ("OBSERVATION_STATUS", "ID"),
    "SPM-2.1": ("SPECIMEN_IDENTIFIER", "ST"),
    "SPM-4.1": ("SPECIMEN_TYPE_CODE", "ST"),
    "SPM-4.2": ("SPECIMEN_TYPE_NAME", "ST"),
}

ALLOWED_VALUES: dict[str, list[str]] = {
    "PID-8": ["M", "F", "O", "U", "A", "N"],
    "PV1-2": ["E", "I", "O", "P", "R", "B", "C"],
    "OBX-8": ["L", "H", "LL", "HH", "N", "A", "AA"],
    "OBX-11": ["F", "P", "C", "I", "D", "X"],
}

FIXED_PATHS = {
    "MSH-2", "MSH-9.1", "MSH-9.2", "MSH-9.3", "MSH-11", "MSH-12",
    "ORC-1",
}

KNOWN_REPEATABLE_SEGMENTS = {"NTE", "OBR", "OBX", "SPM"}


def parse_hl7(raw_message: str) -> HL7Message:
    normalized = raw_message.replace("\r\n", "\r").replace("\n", "\r").strip("\r")
    lines = [line for line in normalized.split("\r") if line]
    if not lines or not lines[0].startswith("MSH") or len(lines[0]) < 4:
        raise ValueError("El mensaje debe iniciar con un segmento MSH válido")

    field_separator = lines[0][3]
    msh_parts = lines[0].split(field_separator)
    encoding = msh_parts[1] if len(msh_parts) > 1 else "^~\\&"
    if len(encoding) != 4 or len(set(field_separator + encoding)) != 5 or any(ord(c) < 32 for c in field_separator + encoding):
        raise ValueError("MSH-1/MSH-2 must define five distinct separators")
    delimiters = HL7Delimiters(
        field=field_separator,
        component=encoding[0],
        repetition=encoding[1],
        escape=encoding[2],
        subcomponent=encoding[3],
    )

    segments: list[HL7Segment] = []
    for line in lines:
        if any(c in line for c in ("\x00", "\x0b", "\x1c")):
            raise ValueError("HL7 payload contains invalid control characters")
        parts = line.split(delimiters.field)
        name = parts[0].strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{3}", name):
            raise ValueError(f"Nombre de segmento inválido: {parts[0]!r}")
        fields: dict[int, HL7Field] = {}
        for index, raw_value in enumerate(parts[1:], start=1):
            field_number = index + 1 if name == "MSH" else index
            if name == "MSH" and field_number == 2:
                fields[field_number] = HL7Field(raw=raw_value, repetitions=[[[raw_value]]])
                continue
            repetitions = []
            for repetition in raw_value.split(delimiters.repetition):
                components = [
                    component.split(delimiters.subcomponent)
                    for component in repetition.split(delimiters.component)
                ]
                repetitions.append(components)
            fields[field_number] = HL7Field(raw=raw_value, repetitions=repetitions)
        segments.append(HL7Segment(name=name, fields=fields))
    return HL7Message(delimiters=delimiters, segments=segments)


def message_type(message: HL7Message) -> str:
    msh = next((segment for segment in message.segments if segment.name == "MSH"), None)
    if not msh or 9 not in msh.fields:
        return ""
    repetitions = msh.fields[9].repetitions
    components = repetitions[0] if repetitions else []
    return "^".join(component[0] if component else "" for component in components[:2]).strip("^")


def canonical_message(raw_message: str) -> str:
    """Convert negotiated separators to the canonical internal HL7 separators."""
    message = parse_hl7(raw_message)
    if message.delimiters == HL7Delimiters():
        return raw_message
    source = message.delimiters
    def atom(value):
        import re
        tokens = re.split(f"({re.escape(source.escape)}(?:[FSRTE]|X[0-9A-Fa-f]+){re.escape(source.escape)})", value)
        for index, token in enumerate(tokens):
            if index % 2:
                tokens[index] = "\\" + token[1:-1] + "\\"
            else:
                tokens[index] = "".join({"|": "\\F\\", "^": "\\S\\", "~": "\\R\\", "\\": "\\E\\", "&": "\\T\\"}.get(char, char) for char in token)
        return "".join(tokens)
    rows = []
    for segment in message.segments:
        row = [segment.name]
        first = 2 if segment.name == "MSH" else 1
        for number in range(first, max(segment.fields, default=first) + 1):
            field = segment.fields.get(number)
            if segment.name == "MSH" and number == 2:
                row.append("^~\\&")
            elif field:
                row.append("~".join("^".join("&".join(atom(sub) for sub in comp) for comp in rep) for rep in field.repetitions))
            else:
                row.append("")
        rows.append("|".join(row))
    return "\r".join(rows) + "\r"


def infer_hl7_contract(raw_message: str) -> dict[str, Any]:
    message = parse_hl7(raw_message)
    grouped: list[tuple[str, list[HL7Segment]]] = []
    for segment in message.segments:
        if grouped and grouped[-1][0] == segment.name:
            grouped[-1][1].append(segment)
        else:
            grouped.append((segment.name, [segment]))

    segment_rules = []
    for name, occurrences in grouped:
        fields: dict[str, Any] = {}
        field_numbers = sorted({number for segment in occurrences for number in segment.fields})
        for number in field_numbers:
            samples = [segment.fields[number] for segment in occurrences if number in segment.fields]
            populated = [sample for sample in samples if sample.raw != ""]
            if not populated:
                continue
            fields[str(number)] = _infer_field(name, number, populated, required=len(populated) == len(occurrences))
        observed = len(occurrences)
        repeatable = observed > 1 or name in KNOWN_REPEATABLE_SEGMENTS
        segment_rules.append({
            "name": name,
            "min": 1,
            "max": "*" if repeatable else 1,
            "observed": observed,
            "fields": fields,
        })

    contract = {
        "formatVersion": 2,
        "messageType": message_type(message),
        "delimiters": message.delimiters.as_dict(),
        "options": {
            "validationMode": "STRUCTURE",
            "allowUnexpectedSegments": False,
            "allowUnexpectedFields": False,
            "allowUnexpectedComponents": False,
        },
        "segments": segment_rules,
    }
    contract["preview"] = render_contract_preview(contract)
    # A contract inferred from a sample must always accept that same sample.
    validate_hl7_contract(raw_message, contract)
    return contract


def _infer_field(name: str, number: int, samples: list[HL7Field], required: bool = True) -> dict[str, Any]:
    max_repetitions = max(len(field.repetitions) for field in samples)
    composite = any(
        len(repetition) > 1 or any(len(component) > 1 for component in repetition)
        for field in samples for repetition in field.repetitions
    )
    repetitions = []
    for repetition_index in range(max_repetitions):
        reps = [field.repetitions[repetition_index] for field in samples if len(field.repetitions) > repetition_index]
        max_components = max((len(rep) for rep in reps), default=1)
        components: dict[str, Any] = {}
        for component_index in range(max_components):
            component_samples = [rep[component_index] for rep in reps if len(rep) > component_index]
            max_subcomponents = max((len(component) for component in component_samples), default=1)
            if max_subcomponents > 1:
                subcomponents = {}
                for sub_index in range(max_subcomponents):
                    values = [component[sub_index] for component in component_samples if len(component) > sub_index and component[sub_index] != ""]
                    if values:
                        path = f"{name}-{number}.{component_index + 1}.{sub_index + 1}"
                        rule = _value_rule(path, values)
                        rule["required"] = all(len(rep) > component_index and len(rep[component_index]) > sub_index and rep[component_index][sub_index] != "" for rep in reps)
                        subcomponents[str(sub_index + 1)] = rule
                if subcomponents:
                    components[str(component_index + 1)] = {"required": all(len(rep) > component_index and any(rep[component_index]) for rep in reps), "subcomponents": subcomponents}
            else:
                values = [component[0] for component in component_samples if component and component[0] != ""]
                if values:
                    path = f"{name}-{number}.{component_index + 1}" if max_components > 1 else f"{name}-{number}"
                    rule = _value_rule(path, values)
                    rule["required"] = all(len(rep) > component_index and rep[component_index] and rep[component_index][0] != "" for rep in reps)
                    components[str(component_index + 1)] = rule
        repetitions.append({"index": repetition_index + 1, "components": components})

    return {
        "required": required,
        "minRepetitions": 1 if required else 0,
        "maxRepetitions": max_repetitions,
        "composite": composite,
        "repetitions": repetitions,
    }


def _value_rule(path: str, values: list[str]) -> dict[str, Any]:
    base_path = re.sub(r"\[\d+\]", "", path)
    semantic, datatype = FIELD_METADATA.get(base_path, ("UNKNOWN", "ST"))
    rule: dict[str, Any] = {
        "required": True,
        "semantic": semantic,
        "datatype": datatype,
        "example": values[0],
    }
    if base_path in ALLOWED_VALUES:
        # Catalog values are advisory in inferred contracts. Analyzer-specific
        # codes may vary while remaining in the exact same HL7 location.
        rule["suggestedAllowed"] = ALLOWED_VALUES[base_path]
    if base_path in FIXED_PATHS:
        rule["const"] = values[0]
    return rule


def render_contract_preview(contract: dict[str, Any]) -> str:
    lines = []
    delimiters = contract.get("delimiters") or {}
    field_sep = delimiters.get("field", "|")
    component_sep = delimiters.get("component", "^")
    repetition_sep = delimiters.get("repetition", "~")
    subcomponent_sep = delimiters.get("subcomponent", "&")
    for segment in contract.get("segments") or []:
        name = str(segment.get("name", ""))
        fields = segment.get("fields") or {}
        max_field = max((int(number) for number in fields), default=1 if name == "MSH" else 0)
        values = [name]
        first_field = 3 if name == "MSH" else 1
        if name == "MSH":
            values.append(
                delimiters.get("component", "^")
                + delimiters.get("repetition", "~")
                + delimiters.get("escape", "\\")
                + delimiters.get("subcomponent", "&")
            )
        for number in range(first_field, max_field + 1):
            rule = fields.get(str(number))
            values.append(_render_field(rule, component_sep, repetition_sep, subcomponent_sep) if rule else "")
        lines.append(field_sep.join(values))
    return "\r".join(lines) + ("\r" if lines else "")


def _render_field(rule: dict[str, Any] | None, component_sep: str, repetition_sep: str, subcomponent_sep: str) -> str:
    if not rule:
        return ""
    rendered_repetitions = []
    for repetition in rule.get("repetitions") or []:
        components = repetition.get("components") or {}
        max_component = max((int(number) for number in components), default=0)
        rendered_components = []
        for component_number in range(1, max_component + 1):
            component = components.get(str(component_number))
            if not component:
                rendered_components.append("")
            elif component.get("subcomponents"):
                subs = component["subcomponents"]
                max_sub = max(int(number) for number in subs)
                rendered_components.append(subcomponent_sep.join(
                    _placeholder(subs.get(str(index))) if subs.get(str(index)) else ""
                    for index in range(1, max_sub + 1)
                ))
            else:
                rendered_components.append(_placeholder(component))
        rendered_repetitions.append(component_sep.join(rendered_components))
    return repetition_sep.join(rendered_repetitions)


def _placeholder(rule: dict[str, Any]) -> str:
    if "const" in rule:
        return str(rule["const"])
    semantic = rule.get("semantic") or rule.get("datatype") or "VALOR"
    if rule.get("allowed"):
        return f"<{semantic}:{'/'.join(str(value) for value in rule['allowed'])}>"
    datatype = rule.get("datatype")
    suffix = ":YYYYMMDD" if datatype == "DT" else ":YYYYMMDDHHMMSS" if datatype == "TS" else ""
    return f"<{semantic}{suffix}>"


def hl7_contract_issues(raw_message: str, contract: dict[str, Any]) -> list[str]:
    if not contract or int(contract.get("formatVersion") or 1) < 2:
        raise ValueError("El contrato no usa el formato estructural HL7 v2")
    message = parse_hl7(raw_message)
    errors: list[str] = []
    expected_type = str(contract.get("messageType") or "")
    actual_type = message_type(message)
    if expected_type and actual_type != expected_type:
        errors.append(f"MSH-9: se esperaba {expected_type!r}, se recibió {actual_type or '<vacío>'!r}")
    expected_delimiters = contract.get("delimiters") or {}
    for delimiter_name, actual_value in message.delimiters.as_dict().items():
        expected_value = expected_delimiters.get(delimiter_name)
        if expected_value and actual_value != expected_value:
            errors.append(f"MSH: separador {delimiter_name} esperado {expected_value!r}, recibido {actual_value!r}")

    definitions = contract.get("segments") or []
    options = contract.get("options") or {}
    actual_index = 0
    for definition in definitions:
        name = str(definition.get("name") or "").upper()
        start = actual_index
        maximum = definition.get("max", 1)
        max_count = len(message.segments) if maximum == "*" else int(maximum)
        while actual_index < len(message.segments) and message.segments[actual_index].name == name and actual_index - start < max_count:
            occurrence = actual_index - start + 1
            _validate_segment(message.segments[actual_index], definition, occurrence, options, errors)
            actual_index += 1
        count = actual_index - start
        minimum = int(definition.get("min", 0))
        if count < minimum:
            found = message.segments[actual_index].name if actual_index < len(message.segments) else "fin del mensaje"
            errors.append(f"{name}: faltan segmentos; mínimo {minimum}, encontrados {count} antes de {found}")

    if actual_index < len(message.segments) and not options.get("allowUnexpectedSegments", False):
        for index, segment in enumerate(message.segments[actual_index:], start=actual_index + 1):
            errors.append(f"{segment.name}[{index}]: segmento no definido o fuera de orden")

    return errors[:25]


def validate_hl7_contract(raw_message: str, contract: dict[str, Any]) -> None:
    errors = hl7_contract_issues(raw_message, contract)
    if errors:
        raise HL7ContractError(errors)


def _validate_segment(segment: HL7Segment, definition: dict[str, Any], occurrence: int, options: dict[str, Any], errors: list[str]) -> None:
    field_rules = definition.get("fields") or {}
    for number_text, rule in field_rules.items():
        number = int(number_text)
        actual = segment.fields.get(number)
        location = f"{segment.name}[{occurrence}]-{number}"
        if rule.get("required") and (not actual or actual.raw == ""):
            errors.append(f"{location}: campo obligatorio ausente")
            continue
        if not actual or actual.raw == "":
            continue
        repetitions = [rep for rep in actual.repetitions if _repetition_has_value(rep)]
        min_reps = int(rule.get("minRepetitions", 1))
        max_reps_raw = rule.get("maxRepetitions", 1)
        max_reps = len(repetitions) if max_reps_raw == "*" else int(max_reps_raw)
        if len(repetitions) < min_reps or len(repetitions) > max_reps:
            errors.append(f"{location}: repeticiones {len(repetitions)}, se esperaban entre {min_reps} y {max_reps_raw}")
        templates = rule.get("repetitions") or []
        for rep_index, repetition in enumerate(repetitions, start=1):
            template = templates[rep_index - 1] if rep_index <= len(templates) else (templates[-1] if max_reps_raw == "*" and templates else None)
            if template is None:
                errors.append(f"{location}[{rep_index}]: repetición no definida")
                continue
            _validate_repetition(segment.name, number, occurrence, rep_index, repetition, template, options, errors)

    if not options.get("allowUnexpectedFields", False):
        for number, field_value in segment.fields.items():
            if field_value.raw != "" and str(number) not in field_rules:
                errors.append(f"{segment.name}[{occurrence}]-{number}: campo no definido en el contrato")


def _validate_repetition(segment_name: str, field_number: int, segment_occurrence: int, repetition_index: int, actual: list[list[str]], template: dict[str, Any], options: dict[str, Any], errors: list[str]) -> None:
    rules = template.get("components") or {}
    for component_text, rule in rules.items():
        component_index = int(component_text)
        values = actual[component_index - 1] if len(actual) >= component_index else []
        location = f"{segment_name}[{segment_occurrence}]-{field_number}[{repetition_index}].{component_index}"
        if rule.get("subcomponents"):
            subrules = rule["subcomponents"]
            for sub_text, subrule in subrules.items():
                sub_index = int(sub_text)
                value = values[sub_index - 1] if len(values) >= sub_index else ""
                _validate_value(value, subrule, f"{location}.{sub_index}", errors)
            if not options.get("allowUnexpectedComponents", False):
                for sub_index, value in enumerate(values, start=1):
                    if value != "" and str(sub_index) not in subrules:
                        errors.append(f"{location}.{sub_index}: subcomponente no definido")
        else:
            value = values[0] if values else ""
            _validate_value(value, rule, location, errors)

    if not options.get("allowUnexpectedComponents", False):
        for component_index, values in enumerate(actual, start=1):
            if any(value != "" for value in values) and str(component_index) not in rules:
                errors.append(f"{segment_name}[{segment_occurrence}]-{field_number}[{repetition_index}].{component_index}: componente no definido")


def _validate_value(value: str, rule: dict[str, Any], location: str, errors: list[str]) -> None:
    if rule.get("required") and value == "":
        errors.append(f"{location}: valor obligatorio ausente")
        return
    if value == "":
        return
    if "const" in rule and value != str(rule["const"]):
        errors.append(f"{location}: se esperaba {str(rule['const'])!r}, se recibió {value!r}")
    allowed = [str(item) for item in rule.get("allowed") or []]
    if allowed and value not in allowed:
        errors.append(f"{location}: valor {value!r} no permitido; permitidos: {', '.join(allowed)}")
    datatype = str(rule.get("datatype") or "").upper()
    if datatype == "DT" and not _valid_hl7_date(value):
        errors.append(f"{location}: {value!r} no es una fecha HL7 DT válida (YYYYMMDD)")
    elif datatype in {"TS", "DTM"} and not _valid_hl7_datetime(value):
        errors.append(f"{location}: {value!r} no es una fecha/hora HL7 válida")
    elif datatype in {"SI", "NM"} and not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
        errors.append(f"{location}: {value!r} no es numérico")
    pattern = rule.get("pattern")
    if pattern:
        try:
            if not re.fullmatch(str(pattern), value):
                errors.append(f"{location}: {value!r} no cumple el patrón {pattern!r}")
        except re.error:
            errors.append(f"{location}: el patrón configurado {pattern!r} es inválido")


def _repetition_has_value(repetition: list[list[str]]) -> bool:
    return any(value != "" for component in repetition for value in component)


def _valid_hl7_date(value: str) -> bool:
    if not re.fullmatch(r"\d{8}", value):
        return False
    try:
        datetime.strptime(value, "%Y%m%d")
        return True
    except ValueError:
        return False


def _valid_hl7_datetime(value: str) -> bool:
    match = re.fullmatch(r"(\d{4}(?:\d{2}){0,5})(?:\.\d{1,4})?(?:[+-]\d{4})?", value)
    if not match:
        return False
    core = match.group(1)
    formats = {4: "%Y", 6: "%Y%m", 8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}
    try:
        datetime.strptime(core, formats[len(core)])
        return True
    except (ValueError, KeyError):
        return False
