import base64
import socket
import time
from datetime import datetime, timezone
from .hl7_values import safe_order, escaped


START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"


def build_hl7_oru(analyzer: dict, sample: dict, results: list[dict], sequence: int) -> tuple[str, str]:
    sample = safe_order(sample)
    analyzer = safe_order(analyzer)
    if analyzer.get("profile") == "ANAPATH":
        return build_anapath_oru(analyzer, sample, sequence)
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d%H%M%S")
    control_id = f"{analyzer['id']}-{now.strftime('%Y%m%d%H%M%S%f')}-{sequence:06d}"
    order_id = sample.get("orderId") or f"ORD-{sample['sampleId']}"
    patient_name = sample.get("patientName") or "SIMULATED^PATIENT"
    patient_id = sample.get("patientId") or sample["sampleId"]
    panel_code = sample.get("panelCode") or "SIM"
    panel_name = sample.get("panelName") or "SIMULATED PANEL"
    behavior = analyzer.get("behavior") or {}
    result_format = behavior.get("resultFormat", "NUMERIC").upper()

    segments = [
        f"MSH|^~\\&|{analyzer['id']}|SIMLAB|MIRTH|LIS|{timestamp}||ORU^R01|{control_id}|P|2.5.1",
        f"PID|1||{patient_id}||{patient_name}",
        f"OBR|1||{order_id}|{panel_code}^{panel_name}||||||||||||||||||F",
    ]
    pdf_payload = ""
    if result_format == "PDF":
        pdf_payload = _build_pdf_base64(sample, "F")
        # OBX-5 is populated exclusively by the configured outbound mapping.
        # This lets a vendor use OBX-5.2 (or any other valid target) without
        # leaving a duplicate Base64 payload in OBX-5.1.
        segments.append("OBX|1|ED|PDF^PDF||||||||F")
    else:
        for index, result in enumerate(results, start=1):
            segments.append(
                "OBX|{index}|NM|{code}^{code}||{value}|{unit}|{reference}|{flag}|||F".format(
                    index=index,
                    code=result["code"],
                    value=result["value"],
                    unit=result["unit"],
                    reference=result["referenceRange"],
                    flag=result["abnormalFlag"],
                )
            )
    first_result = results[0] if results else {}
    context = {
        **sample,
        "orderId": order_id,
        "procedureCode": panel_code,
        "procedureName": panel_name,
        "resultCode": "PDF" if result_format == "PDF" else first_result.get("code", ""),
        "resultValue": pdf_payload if result_format == "PDF" else str(first_result.get("value", "")),
        "resultUnit": "" if result_format == "PDF" else first_result.get("unit", ""),
        "resultStatus": "F",
        "results": results if result_format != "PDF" else [],
        "perResultMappings": result_format != "PDF",
    }
    segments = _apply_outbound_mappings(segments, behavior.get("outboundMappings"), context)
    return "\r".join(segments) + "\r", control_id


def _apply_outbound_mappings(segments: list[str], mappings: dict | None, context: dict) -> list[str]:
    """Places semantic values in provider-selected HL7 paths, including Z segments."""
    rows = [segment.split("|") for segment in segments]
    for name, path in (mappings or {}).items():
        value = context.get(name)
        if value is None or not str(path).strip() or "-" not in str(path):
            continue
        segment_name, position = str(path).strip().upper().split("-", 1)
        try:
            field_text, *component_text = position.split(".", 1)
            field_index = int(field_text)
            component_index = int(component_text[0]) - 1 if component_text else None
        except ValueError:
            continue
        if segment_name == "MSH" and field_index > 1:
            field_index -= 1
        targets = [row for row in rows if row and row[0] == segment_name]
        if not targets:
            targets = [[segment_name]]
            rows.append(targets[0])
        for result_index, row in enumerate(targets):
            row_value = value
            if segment_name == "OBX" and context.get("perResultMappings") and result_index < len(context.get("results") or []):
                result = context["results"][result_index]
                result_values = {"resultCode": result.get("code"), "resultValue": result.get("value"),
                                 "resultUnit": result.get("unit"), "resultStatus": "F"}
                row_value = result_values.get(name, value)
            while len(row) <= field_index:
                row.append("")
            if component_index is None:
                row[field_index] = str(row_value)
            else:
                components = row[field_index].split("^") if row[field_index] else []
                while len(components) <= component_index:
                    components.append("")
                components[component_index] = str(row_value)
                row[field_index] = "^".join(components)
    return ["|".join(row) for row in rows]


def build_anapath_oru(analyzer: dict, sample: dict, sequence: int) -> tuple[str, str]:
    return build_anapath_event(analyzer, sample, "REPORT", sequence)


def build_anapath_event(analyzer: dict, order: dict, event_type: str, sequence: int | None = None, overrides: dict | None = None) -> tuple[str, str]:
    analyzer, order = safe_order(analyzer), safe_order(order)
    overrides = safe_order(overrides or {})
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d%H%M%S")
    sequence_number = sequence or 0
    control_id = f"{analyzer['id']}-{event_type}-{now.strftime('%Y%m%d%H%M%S%f')}-{sequence_number:06d}"
    payload = {**order, **(overrides or {})}
    patient_id = payload.get("patientId") or payload.get("sampleId") or "UNKNOWN"
    patient_name = payload.get("patientName") or "SIMULATED^PATIENT"
    visit_type = payload.get("visitType") or "O"
    order_id = payload.get("orderId") or payload.get("sampleId") or "ORDER"
    filler_order_id = payload.get("fillerOrderId") or payload.get("sampleId") or order_id
    procedure_code = payload.get("procedureCode") or payload.get("panelCode") or "P000"
    procedure_name = payload.get("procedureName") or payload.get("panelName") or "PROCEDIMIENTO DESCONOCIDO"
    sample_id = payload.get("sampleId") or filler_order_id
    sample_code = payload.get("sampleCode") or sample_id
    sample_name = payload.get("sampleName") or "MUESTRA NO ESPECIFICADA"
    sample_count = payload.get("sampleCount") or "1"
    requested_at = payload.get("requestedAt") or timestamp
    priority = payload.get("priority") or "ROUTINE"
    observations = payload.get("observations") or []

    base_segments = [
        f"PID|1||{patient_id}||{patient_name}",
        f"PV1|1|{visit_type}|||||||||||||||||{payload.get('visitNumber') or ''}",
        f"ORC|{payload.get('orderControl') or 'NW'}|{order_id}|{filler_order_id}|{payload.get('prescriptionId') or ''}|{payload.get('orderStatus') or ''}|||{requested_at}",
        f"OBR|1|{order_id}|{sample_id}|{procedure_code}^{procedure_name}||{requested_at}||||||{payload.get('clinicalInfo') or ''}||{_specimen_source(payload)}||||||||||{payload.get('obr25') or ''}|{priority}",
    ]
    if sample_id:
        base_segments.append(
            f"SPM|1|{sample_id}|{sample_id}|{sample_code}^{sample_name}||||{_topography(payload)}|||{sample_count}"
        )
    for index, note in enumerate(observations, start=1):
        base_segments.append(f"NTE|{index}||{note}")

    if event_type == "STATUS_CHANGE":
        segments = [
            f"MSH|^~\\&|ANAPATH|VITROLINK|MIRTH|HIS|{timestamp}||OML^O21|{control_id}|P|2.5",
            *base_segments[:2],
            f"ORC|SC|{order_id}|{filler_order_id}|{payload.get('prescriptionId') or ''}|IP|||{requested_at}",
            f"OBR|1|{order_id}|{sample_id}|{procedure_code}^{procedure_name}||{requested_at}||||||{payload.get('clinicalInfo') or ''}||{_specimen_source(payload)}||||||||||IP|{priority}",
            *base_segments[4:],
        ]
        return "\r".join(segments) + "\r", control_id

    if event_type == "NEW_SAMPLE":
        new_code = payload.get("newProcedureCode") or procedure_code
        new_name = payload.get("newProcedureName") or procedure_name
        new_sample_id = payload.get("newSampleId") or f"{sample_id}-A"
        new_sample_code = payload.get("newSampleCode") or new_sample_id
        new_sample_name = payload.get("newSampleName") or sample_name
        segments = [
            f"MSH|^~\\&|ANAPATH|VITROLINK|MIRTH|HIS|{timestamp}||OML^O21|{control_id}|P|2.5",
            f"PID|1||{patient_id}||{patient_name}",
            f"PV1|1|{visit_type}|||||||||||||||||{payload.get('visitNumber') or ''}",
            f"ORC|NW|{order_id}|{new_sample_id}|{payload.get('prescriptionId') or ''}|NW|||{requested_at}",
            f"OBR|1|{order_id}|{new_sample_id}|{new_code}^{new_name}||{requested_at}||||||{payload.get('clinicalInfo') or ''}||{_specimen_source(payload)}||||||||||NW|{priority}",
            f"SPM|1|{new_sample_id}|{new_sample_id}|{new_sample_code}^{new_sample_name}||||{_topography(payload)}|||{sample_count}",
        ]
        return "\r".join(segments) + "\r", control_id

    if event_type == "DELETE_SAMPLE":
        segments = [
            f"MSH|^~\\&|ANAPATH|VITROLINK|MIRTH|HIS|{timestamp}||OML^O21|{control_id}|P|2.5",
            f"PID|1||{patient_id}||{patient_name}",
            f"PV1|1|{visit_type}|||||||||||||||||{payload.get('visitNumber') or ''}",
            f"ORC|CA|{order_id}|{sample_id}|{payload.get('prescriptionId') or ''}|CA|||{requested_at}",
            f"OBR|1|{order_id}|{sample_id}|{procedure_code}^{procedure_name}||{requested_at}||||||{payload.get('clinicalInfo') or ''}||{_specimen_source(payload)}||||||||||CA|{priority}",
        ]
        return "\r".join(segments) + "\r", control_id

    report_status = payload.get("reportStatus")
    if not report_status:
        scenario = analyzer.get("scenario", "NORMAL")
        if scenario == "REPORT_SUPPLEMENTARY":
            report_status = "C"
        elif scenario == "REPORT_CANCELLED":
            report_status = "X"
        else:
            report_status = "F"
    # The report payload is placed exclusively by the analyzer's configured
    # outbound mapping, e.g. OBX-5, OBX-5.2, or a private Z segment.
    report_value = "CANCELACION INFORME" if report_status == "X" else _build_pdf_base64(payload, report_status)
    value_type = "TX" if report_status == "X" else "ED"
    obx_code = "PDF^PDF"
    orc2 = payload.get("orc2") or order_id
    orc3 = payload.get("orc3") or sample_id
    orc4 = payload.get("orc4") or ""
    obr2 = payload.get("obr2") or order_id
    obr3 = payload.get("obr3") or sample_id
    segments = [
        f"MSH|^~\\&|ANAPATH|VITROLINK|MIRTH|HIS|{timestamp}||ORU^R01|{control_id}|P|2.5",
        f"PID|1||{patient_id}||{patient_name}",
        f"PV1|1|{visit_type}|||||||||||||||||{payload.get('visitNumber') or ''}",
        f"ORC|RE|{orc2}|{orc3}|{orc4}",
        f"OBR|1|{obr2}|{obr3}|{procedure_code}^{procedure_name}||{requested_at}",
        f"OBX|1|{value_type}|{obx_code}||||||||{report_status}",
    ]
    segments = _apply_outbound_mappings(
        segments,
        (analyzer.get("behavior") or {}).get("outboundMappings"),
        {
            **payload,
            "orderId": order_id,
            "sampleId": sample_id,
            "procedureCode": procedure_code,
            "procedureName": procedure_name,
            "resultCode": "PDF",
            "resultValue": report_value,
            "resultUnit": "",
            "resultStatus": report_status,
        },
    )
    return "\r".join(segments) + "\r", control_id


def _build_pdf_base64(order: dict, report_status: str) -> str:
    return base64.b64encode(_build_report_pdf(order, report_status)).decode("ascii")


def _build_report_pdf(order: dict, report_status: str) -> bytes:
    issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "SIMULADOR ANAPATH - INFORME MOCK",
        "",
        f"Estado informe: {_report_status_label(report_status)} ({report_status})",
        f"Fecha emision: {issued_at}",
        f"Paciente: {order.get('patientName') or 'N/D'}",
        f"Paciente ID: {order.get('patientId') or 'N/D'}",
        f"Orden HIS: {order.get('orderId') or 'N/D'}",
        f"Prescripcion: {order.get('prescriptionId') or 'N/D'}",
        f"Visita: {order.get('visitType') or 'N/D'} / {order.get('visitNumber') or 'N/D'}",
        f"Procedimiento: {order.get('procedureCode') or 'N/D'} - {order.get('procedureName') or 'N/D'}",
        f"Muestra: {order.get('sampleId') or 'N/D'} - {order.get('sampleName') or 'N/D'}",
        f"Codigo muestra: {order.get('sampleCode') or 'N/D'}",
        f"Topografia: {_join_non_empty(order.get('topographyCode'), order.get('topographyName')) or 'N/D'}",
        f"Lateralidad: {order.get('laterality') or 'N/D'}",
        f"Prioridad: {order.get('priority') or 'N/D'}",
        "",
        "Informacion clinica recibida:",
        _clip(order.get("clinicalInfo") or "No enviada"),
        "",
        "Observaciones:",
    ]
    observations = order.get("observations") or []
    if observations:
        lines.extend([f"- {_clip(note)}" for note in observations[:4]])
    else:
        lines.append("- Sin observaciones")
    lines.extend(
        [
            "",
            "Resultado mock:",
            "Descripcion macroscopica simulada compatible con la solicitud recibida.",
            "Diagnostico mock: material recibido y datos visibles correctamente en el simulador.",
            "Comentario: este PDF es generado dinamicamente solo para validar integracion.",
        ]
    )
    return _simple_pdf(lines)


def _simple_pdf(lines: list[str]) -> bytes:
    page_width = 612
    page_height = 792
    font_size = 11
    leading = 14
    start_x = 50
    start_y = 742
    content_lines = ["BT", f"/F1 {font_size} Tf", f"{leading} TL", f"1 0 0 1 {start_x} {start_y} Tm"]
    first = True
    for line in lines[:40]:
        safe_line = _pdf_escape(line)
        if first:
            content_lines.append(f"({safe_line}) Tj")
            first = False
        else:
            content_lines.append("T*")
            content_lines.append(f"({safe_line}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n",
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n",
        f"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 {page_width} {page_height}]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n".encode("ascii"),
        f"4 0 obj<</Length {len(stream)}>>stream\n".encode("ascii") + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer<</Size {len(offsets)}/Root 1 0 R>>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def _pdf_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _report_status_label(status: str) -> str:
    return {
        "F": "Informe final",
        "C": "Informe suplementario",
        "X": "Informe cancelado",
    }.get(status, "Informe")


def _join_non_empty(*values: str | None) -> str:
    return " - ".join(value for value in values if value)


def _clip(value: str, limit: int = 110) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _specimen_source(order: dict) -> str:
    code = order.get("sampleCode")
    name = order.get("sampleName")
    return f"{code or ''}^{name or ''}".strip("^")


def _topography(order: dict) -> str:
    code = order.get("topographyCode")
    name = order.get("topographyName")
    laterality = order.get("laterality")
    return "^".join(part for part in [code, name, laterality] if part)


def ack_code(ack: str) -> str | None:
    for segment in ack.replace("\n", "\r").split("\r"):
        if segment.startswith("MSA|"):
            fields = segment.split("|")
            return fields[1] if len(fields) > 1 else None
    return None


def send_mllp(message: str, host: str, port: int, timeout: float = 8) -> tuple[str, str | None]:
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(timeout)
        connection.sendall(START_BLOCK + message.encode("utf-8") + END_BLOCK)
        response = b""
        deadline = time.monotonic() + timeout
        while END_BLOCK not in response:
            connection.settimeout(max(0.01, deadline - time.monotonic()))
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("Destination closed the connection without ACK")
            response += chunk
            if len(response) > 4194304:
                raise ConnectionError("ACK exceeds maximum size")

    start = response.find(START_BLOCK)
    end = response.find(END_BLOCK, start + 1)
    if start < 0 or end < 0:
        raise ConnectionError("Destination returned data without MLLP framing")
    if start != 0 or end + len(END_BLOCK) != len(response):
        raise ConnectionError("Destination returned invalid MLLP ACK framing")
    ack = response[start + 1 : end].decode("utf-8", errors="strict")
    from .hl7_orders import extract_metadata, hl7_path_value
    metadata = extract_metadata(ack)
    from .hl7_contract import parse_hl7
    parse_hl7(ack)
    if not str(metadata.get("messageType") or "").startswith("ACK^"):
        raise ConnectionError("Destination did not return an HL7 ACK")
    if hl7_path_value(ack, "MSA-2") != extract_metadata(message).get("messageControlId"):
        raise ConnectionError("ACK MSA-2 does not match outbound MSH-10")
    code = hl7_path_value(ack, "MSA-1")
    if code not in {"AA", "AE", "AR", "CA", "CE", "CR"}:
        raise ConnectionError("Invalid ACK code")
    return ack, code
