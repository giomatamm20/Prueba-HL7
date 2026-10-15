"""Profile-driven inbound mapper from HL7 paths to the simulator canonical model."""

from .hl7_orders import hl7_path_value, parse_order


CANONICAL_TARGETS = {
    "patient.id": "patientId", "patient.name": "patientName",
    "order.id": "orderId", "order.fillerId": "fillerOrderId", "order.control": "orderControl",
    "sample.id": "sampleId", "sample.code": "sampleCode", "sample.name": "sampleName",
    "test.code": "procedureCode", "test.name": "procedureName",
    "visit.type": "visitType", "visit.number": "visitNumber",
    "order.priority": "priority", "order.clinicalInfo": "clinicalInfo",
}


def parse_with_profile(raw_message: str, inbound: dict) -> dict:
    """Parses the common model, then overlays explicit profile mappings."""
    from .hl7_contract import canonical_message
    raw_message = canonical_message(raw_message)
    message_type = inbound.get("messageType")
    schema = inbound.get("schema") or {}
    required_order_segments = None
    if int(schema.get("formatVersion") or 1) >= 2:
        required_order_segments = {
            str(segment.get("name") or "").upper()
            for segment in schema.get("segments") or []
            if int(segment.get("min", 0)) > 0 and str(segment.get("name") or "").upper() in {"ORC", "OBR"}
        }
    base = parse_order(
        raw_message,
        {message_type} if message_type else None,
        required_order_segments=required_order_segments,
    )
    custom_fields: dict[str, str] = dict(base.get("customFields") or {})
    for mapping in inbound.get("mappings") or []:
        target = mapping.get("target", "")
        paths = [mapping.get("source", ""), *(mapping.get("alternatives") or [])]
        value = next((value for path in paths if (value := hl7_path_value(raw_message, path))), None)
        if value is None:
            if mapping.get("required"):
                raise ValueError(f"Profile requires {target}")
            continue
        canonical = CANONICAL_TARGETS.get(target)
        if canonical:
            base[canonical] = value
            if canonical == "sampleId":
                base["barcode"] = value
            elif canonical == "procedureCode":
                base["procedureCode"] = value
                base["panelCode"] = value
                base["requestedTests"] = [value]
            elif canonical == "procedureName":
                base["panelName"] = value
        else:
            custom_fields[target] = value
    base["customFields"] = custom_fields
    return base

def validate_strict_profile(raw_message: str, schema: dict) -> None:
    if not schema:
        return
    if int(schema.get("formatVersion") or 1) >= 2:
        from .hl7_contract import validate_hl7_contract

        validate_hl7_contract(raw_message, schema)
        return
        
    found_values = {}
    segments = raw_message.replace("\r\n", "\r").replace("\n", "\r").strip("\r").split("\r")
    for seg in segments:
        seg = seg.strip()
        if not seg: continue
        parts = seg.split("|")
        seg_name = parts[0]
        start_idx = 2 if seg_name == "MSH" else 1
        
        # MSH-2 especial
        if seg_name == "MSH" and len(parts) > 1:
            found_values["MSH-2"] = parts[1]
            
        for i in range(start_idx, len(parts)):
            field_idx = i + 1 if seg_name == "MSH" else i
            if not parts[i].strip(): continue
            
            subcomps = parts[i].split("^")
            if len(subcomps) > 1:
                for j, sub in enumerate(subcomps):
                    if sub.strip():
                        found_values[f"{seg_name}-{field_idx}.{j+1}"] = sub.strip()
            else:
                found_values[f"{seg_name}-{field_idx}"] = parts[i].strip()
                
    # Validar requeridos
    missing = []
    for seg_name, fields in schema.items():
        for field_path, rules in fields.items():
            if rules.get("required") and field_path not in found_values:
                missing.append(field_path)
    
    if missing:
        raise ValueError(f"Faltan campos obligatorios según el contrato: {', '.join(sorted(missing))}")
        
    # Validar valores permitidos y campos extra
    extra = []
    for path, value in found_values.items():
        seg_name = path.split("-")[0]
        rules = schema.get(seg_name, {}).get(path)
        
        if not rules:
            extra.append(path)
            continue
            
        allowed = rules.get("allowed")
        if allowed and value not in allowed:
            raise ValueError(f"Valor inválido en {path}: '{value}'. Permitidos: {', '.join(allowed)}")
            
    if extra:
        raise ValueError(f"Campos o subcomponentes no permitidos enviados: {', '.join(sorted(extra))}")
