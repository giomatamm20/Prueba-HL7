"""Declarative synthetic study catalogs. No user-supplied code is evaluated."""
import copy
import math
import random
import re

from .catalog import TEST_PROFILES
from .hl7_values import escaped

MAX_RESULTS = 2000
TYPES = {"NM", "ST", "TX", "CE", "CWE"}


def text(value, label, optional=False):
    if optional and value in (None, ""):
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > 2000 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label}: texto válido requerido (máximo 2000 caracteres)")
    return value.strip()


def identifier(value, label):
    value = text(value, label)
    if not re.fullmatch(r"[\w%+.:/-]{1,80}", value):
        raise ValueError(f"{label}: use letras, números, punto, guion o barra")
    return value


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: número finito requerido")
    return value


def count(value, label, minimum=0, maximum=50):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label}: entero entre {minimum} y {maximum}")
    return value


def validate_value(value, value_type):
    if value_type == "NM":
        number(value, "Valor NM")
    elif value_type in {"CE", "CWE"}:
        if not isinstance(value, dict) or set(value) - {"code", "name", "system"}:
            raise ValueError("Valor codificado: defina code, name y system")
        for key in ("code", "name", "system"):
            text(value.get(key), f"Valor codificado {key}")
    else:
        text(value, "Valor de texto")


def validate_generator(generator, value_type):
    if not isinstance(generator, dict):
        raise ValueError("Generador requerido; configure los valores de prueba")
    kind = generator.get("kind")
    allowed = {"numeric": {"kind", "min", "max", "decimals"}, "fixed": {"kind", "value"},
               "choice": {"kind", "values"}, "sequence": {"kind", "values"}}
    if kind not in allowed or set(generator) - allowed[kind]:
        raise ValueError("Generador inválido: numeric, fixed, choice o sequence")
    if kind == "numeric":
        if value_type != "NM":
            raise ValueError("El generador numeric requiere tipo NM")
        low, high = number(generator.get("min"), "Mínimo"), number(generator.get("max"), "Máximo")
        if low > high or not math.isfinite(high - low):
            raise ValueError("Mínimo no puede superar máximo")
        count(generator.get("decimals", 1), "Decimales", 0, 8)
    elif kind == "fixed":
        validate_value(generator.get("value"), value_type)
    else:
        values = generator.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 500:
            raise ValueError("Lista de valores vacía o demasiado grande")
        for value in values:
            validate_value(value, value_type)


def validate_catalog(payload):
    try:
        result = copy.deepcopy(payload)
        result = {key: result[key] for key in ("name", "studies") if key in result}
        result["name"] = text(result.get("name"), "Nombre del catálogo")
        studies = result.get("studies")
        if not isinstance(studies, list) or not 1 <= len(studies) <= 200:
            raise ValueError("Defina entre 1 y 200 estudios")
        aliases = set()
        for study in studies:
            study["code"] = identifier(study.get("code"), "Código del estudio")
            study["name"] = text(study.get("name"), "Nombre del estudio")
            if not isinstance(study.get("aliases", []), list):
                raise ValueError("aliases debe ser una lista")
            for alias in [study["code"], *study.get("aliases", [])]:
                alias = identifier(alias, "Alias")
                if alias in aliases:
                    raise ValueError(f"Código/alias duplicado: {alias}")
                aliases.add(alias)
            paths = {}
            group_paths = set()

            def walk(node, prefix="", depth=0):
                if depth > 4:
                    raise ValueError("Máximo cuatro niveles de grupos")
                parameters, groups = node.get("parameters", []), node.get("groups", [])
                if not isinstance(parameters, list) or not isinstance(groups, list):
                    raise ValueError("parameters y groups deben ser listas")
                seen = set()
                for parameter in parameters:
                    code = identifier(parameter.get("code"), "Código del parámetro")
                    if code in seen:
                        raise ValueError(f"Parámetro duplicado: {prefix}{code}")
                    seen.add(code)
                    text(parameter.get("name"), "Nombre del parámetro")
                    value_type = parameter.get("valueType")
                    if value_type not in TYPES:
                        raise ValueError("Tipo permitido: NM, ST, TX, CE o CWE")
                    validate_generator(parameter.get("generator"), value_type)
                    for key in ("unit", "referenceRange", "note"):
                        text(parameter.get(key), key, optional=True)
                    if parameter.get("status", "F") not in {"F", "P", "C", "X"}:
                        raise ValueError("Estado de resultado inválido")
                    if parameter.get("abnormalFlag", "") not in {"", "N", "H", "L", "A", "HH", "LL"}:
                        raise ValueError("Indicador de anormalidad inválido")
                    if prefix + code in paths:
                        raise ValueError(f"Ruta de parámetro ambigua: {prefix + code}")
                    paths[prefix + code] = parameter
                group_ids = set()
                total = len(parameters)
                for group in groups:
                    group_id = identifier(group.get("id"), "Identificador de grupo")
                    if "." in group_id:
                        raise ValueError("Los identificadores de grupo no pueden contener puntos")
                    if group_id in group_ids:
                        raise ValueError(f"Grupo duplicado: {prefix}{group_id}")
                    group_ids.add(group_id)
                    text(group.get("name"), "Nombre del grupo")
                    group_paths.add(prefix + group_id)
                    total += count(group.get("count", 1), "Repeticiones") * walk(group, prefix + group_id + ".", depth + 1)
                if total > MAX_RESULTS:
                    raise ValueError(f"El estudio supera {MAX_RESULTS} resultados")
                return total

            if not walk(study):
                raise ValueError("El estudio necesita al menos un parámetro generado")
            scenarios = study.get("scenarios", {})
            if not isinstance(scenarios, dict) or len(scenarios) > 50:
                raise ValueError("scenarios debe ser un objeto de hasta 50 escenarios")
            for scenario, overrides in scenarios.items():
                identifier(scenario, "Escenario")
                if not isinstance(overrides, dict) or set(overrides) - {"parameters", "groups"}:
                    raise ValueError("Escenario: use parameters y groups")
                for path, generator in overrides.get("parameters", {}).items():
                    if path not in paths:
                        raise ValueError(f"Parámetro de escenario desconocido: {path}")
                    validate_generator(generator, paths[path]["valueType"])
                for path, repetitions in overrides.get("groups", {}).items():
                    if path not in group_paths:
                        raise ValueError(f"Grupo de escenario desconocido: {path}")
                    count(repetitions, "Repeticiones de escenario")
                def scenario_size(node, prefix=""):
                    return len(node.get("parameters", [])) + sum(
                        overrides.get("groups", {}).get(prefix + group["id"], group.get("count", 1)) *
                        scenario_size(group, prefix + group["id"] + ".") for group in node.get("groups", []))
                if not 1 <= scenario_size(study) <= MAX_RESULTS:
                    raise ValueError("Escenario vacío o supera el límite de resultados")
        return result
    except (TypeError, AttributeError, KeyError, RecursionError, OverflowError) as exc:
        raise ValueError("Estructura del catálogo inválida") from exc


def seed_catalog():
    def parameter(code):
        profile = TEST_PROFILES[code]
        return {"code": code, "name": code, "valueType": "NM", "unit": profile["unit"],
                "referenceRange": f"{profile['low']}-{profile['high']}",
                "generator": {"kind": "numeric", "min": profile["low"], "max": profile["high"], "decimals": profile["decimals"]}}
    studies = [{"code": "CBC", "name": "Hemograma de prueba", "parameters": [parameter(code) for code in
                ("WBC", "RBC", "HGB", "HCT", "MCV", "MCH", "MCHC", "PLT")]}]
    for code in TEST_PROFILES:
        p = parameter(code)
        if code == "NIT":
            p.update(valueType="ST", generator={"kind": "choice", "values": ["NEG", "POS"]})
        studies.append({"code": code, "name": code, "parameters": [p]})
    studies.append({"code": "GENERIC", "name": "Plantilla genérica de prueba", "parameters": [
        {"code": "VALUE", "name": "Valor sintético", "valueType": "NM", "generator": {"kind": "numeric", "min": 1, "max": 10, "decimals": 1}}]})
    return {"name": "Catálogo inicial editable", "studies": studies}


def bind_studies(revision, groups, policy="BLOCK", fallback=None):
    index = {alias: study for study in revision["studies"] for alias in [study["code"], *study.get("aliases", [])]}
    if policy not in {"BLOCK", "TEMPLATE"}:
        raise ValueError("Política desconocida: BLOCK o TEMPLATE")
    if policy == "TEMPLATE" and fallback not in index:
        raise ValueError("Seleccione una plantilla genérica existente")
    bound = []
    for group in groups:
        study = index.get(group["code"])
        used_fallback = study is None
        if used_fallback:
            if policy == "BLOCK":
                raise ValueError(f"Estudio desconocido en catálogo {revision['name']}: {group['code']}")
            study = index[fallback]
        bound.append({**copy.deepcopy(group), "definition": copy.deepcopy(study),
                      "catalogRevisionId": revision["id"], "catalogVersion": revision["version"],
                      "fallbackUsed": used_fallback, "templateCode": study["code"] if used_fallback else None})
    # Reject oversized orders at receipt, before creating a processing job.
    def actual_size(node, overrides, path=""):
        return len(node.get("parameters", [])) + sum(
            overrides.get(path + group["id"], group.get("count", 1)) * actual_size(group, overrides, path + group["id"] + ".")
            for group in node.get("groups", []))
    scenarios = {"NORMAL", *[name for s in bound for name in s["definition"].get("scenarios", {})]}
    for scenario in scenarios:
        if sum(actual_size(s["definition"], s["definition"].get("scenarios", {}).get(scenario, {}).get("groups", {})) for s in bound) > MAX_RESULTS:
            raise ValueError(f"Orden supera {MAX_RESULTS} resultados en escenario {scenario}")
    return bound


def generate_results(studies, scenario="NORMAL", next_index=None):
    results = []
    local_sequences = {}
    for study_index, study in enumerate(studies, 1):
        definition = study["definition"]
        overrides = definition.get("scenarios", {}).get(scenario, {})

        def walk(node, path="", instance="", parent=""):
            for parameter in node.get("parameters", []):
                key = path + parameter["code"]
                generator = overrides.get("parameters", {}).get(key, parameter["generator"])
                kind = generator["kind"]
                if kind == "numeric":
                    value = round(random.uniform(generator["min"], generator["max"]), generator.get("decimals", 1))
                elif kind == "fixed":
                    value = copy.deepcopy(generator["value"])
                elif kind == "choice":
                    value = copy.deepcopy(random.choice(generator["values"]))
                else:
                    sequence_key = f"catalog:{study['catalogRevisionId']}:{definition['code']}:{scenario}:{key}"
                    index = next_index(sequence_key) if next_index else local_sequences.get(sequence_key, 0)
                    local_sequences[sequence_key] = index + 1
                    value = copy.deepcopy(generator["values"][index % len(generator["values"])])
                value_type = parameter["valueType"]
                hl7_value = "^".join(escaped(value[k], scalar=True) for k in ("code", "name", "system")) if value_type in {"CE", "CWE"} else escaped(value, scalar=True)
                results.append({"studyKey": study["key"], "studyCode": study["code"], "studyName": study.get("name") or definition["name"],
                                "studyIndex": study_index, "parameterPath": key, "code": parameter["code"], "name": parameter["name"],
                                "valueType": value_type, "value": value, "hl7Value": hl7_value,
                                "unit": parameter.get("unit", ""), "referenceRange": parameter.get("referenceRange", ""),
                                "abnormalFlag": parameter.get("abnormalFlag", ""), "status": parameter.get("status", "F"),
                                "subId": instance, "groupId": instance, "parentGroupId": parent,
                                "sampleId": study.get("sampleId"), "sample": study.get("sample", {}),
                                "note": parameter.get("note", ""), "catalogRevisionId": study["catalogRevisionId"]})
                if len(results) > MAX_RESULTS:
                    raise ValueError(f"Orden supera {MAX_RESULTS} resultados; reduzca grupos o estudios")
            for group in node.get("groups", []):
                group_path = path + group["id"]
                for index in range(overrides.get("groups", {}).get(group_path, group.get("count", 1))):
                    group_instance = f"{instance + '.' if instance else ''}{group['id']}.{index + 1}"
                    walk(group, group_path + ".", group_instance, instance)
        walk(definition)
    return results


def infer_studies(raw_message):
    """Only structural OBX metadata is suggested; never retain patient/value data."""
    from .hl7_contract import canonical_message
    from .hl7_orders import segments_from_message, field, component
    rows = segments_from_message(canonical_message(raw_message))
    studies, current = [], None
    for row in rows:
        if row[0] == "OBR":
            current = {"code": component(field(row, 4), 0) or f"STUDY-{len(studies) + 1}",
                       "name": component(field(row, 4), 1) or "Estudio importado", "parameters": []}
            studies.append(current)
        elif row[0] == "OBX":
            if current is None:
                current = {"code": "IMPORTED", "name": "Estudio importado", "parameters": []}
                studies.append(current)
            code = component(field(row, 3), 0) or f"P-{len(current['parameters']) + 1}"
            if any(p["code"] == code for p in current["parameters"]):
                continue
            current["parameters"].append({"code": code, "name": component(field(row, 3), 1) or code,
                                           "valueType": field(row, 2) or "ST", "unit": field(row, 6) or "",
                                           "referenceRange": field(row, 7) or "", "generator": None})
    if not studies or not any(s["parameters"] for s in studies):
        raise ValueError("El ejemplo debe contener parámetros OBX")
    return {"studies": studies, "warning": "Sugerencias sin valores ni identificadores de pacientes. Configure cada generador antes de guardar; revise grupos repetidos manualmente."}


def dynamic_outbound():
    def dynamic(target, source):
        return {"target": target, "source": source}
    def fixed(target, value):
        return {"target": target, "sourceType": "fixed", "value": value}
    return {"events": {"RESULT_FINAL": {"messageType": "ORU^R01", "segments": [
        {"name": "MSH", "fields": [dynamic("MSH-3", "analyzer.id"), dynamic("MSH-7", "system.timestamp"),
          fixed("MSH-9", "ORU^R01"), dynamic("MSH-10", "system.messageId"), fixed("MSH-11", "P"), fixed("MSH-12", "2.5.1")]},
        {"name": "PID", "fields": [dynamic("PID-3", "patient.id"), dynamic("PID-5", "patient.name")]},
        {"name": "OBR", "repeatBy": "studies", "fields": [dynamic("OBR-1", "study.counter"),
          dynamic("OBR-2", "study.orderId"), dynamic("OBR-3", "sample.id"), dynamic("OBR-4.1", "study.code"), dynamic("OBR-4.2", "study.name")],
         "children": [
            {"name": "SPM", "fields": [fixed("SPM-1", "1"), dynamic("SPM-2", "sample.id"), dynamic("SPM-4", "sample.code")]},
            {"name": "OBX", "repeatBy": "results", "fields": [dynamic("OBX-1", "counter"), dynamic("OBX-2", "result.valueType"),
             dynamic("OBX-3.1", "result.code"), dynamic("OBX-3.2", "result.name"), dynamic("OBX-4", "result.subId"),
             dynamic("OBX-5", "result.hl7Value"), dynamic("OBX-6", "result.unit"), dynamic("OBX-7", "result.referenceRange"),
             dynamic("OBX-8", "result.abnormalFlag"), dynamic("OBX-11", "result.status")],
             "children": [{"name": "NTE", "requiredWhen": "hasNote", "fields": [fixed("NTE-1", "1"), dynamic("NTE-3", "result.note")]}]}
         ]}
    ]}}}
