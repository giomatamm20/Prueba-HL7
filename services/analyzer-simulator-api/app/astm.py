import socket
from datetime import datetime, timezone


ACK = "\x06"
STX = "\x02"
ETX = "\x03"
CR = "\r"
LF = "\n"


class ASTMOrderError(ValueError):
    pass


def build_astm_result(analyzer: dict, sample: dict, results: list[dict], sequence: int) -> tuple[str, str]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    control_id = f"ASTM-{analyzer['id']}-{sequence:06d}"
    patient_name = sample.get("patientName") or "SIMULATED^PATIENT"
    patient_id = sample.get("patientId") or sample["sampleId"]
    order_id = sample.get("orderId") or f"ORD-{sample['sampleId']}"

    records = [
        f"H|\\^&|||{analyzer['name']}^Analyzer Simulation Lab|||||host||P|1|{timestamp}",
        f"P|1||{patient_id}||{patient_name}",
        f"O|1|{sample['sampleId']}|{order_id}||{_test_codes(results)}|R||||||A||||||||||F",
    ]
    for index, result in enumerate(results, start=1):
        records.append(
            "R|{index}|^^^{code}|{value}|{unit}|{reference}|{flag}|||F".format(
                index=index,
                code=result["code"],
                value=result["value"],
                unit=result["unit"],
                reference=result["referenceRange"],
                flag=result["abnormalFlag"],
            )
        )
    records.append("L|1|N")

    framed_records = []
    for frame_number, record in enumerate(records, start=1):
        body = f"{frame_number}{record}{CR}{ETX}"
        framed_records.append(f"{STX}{body}{_checksum(body)}{CR}{LF}")
    return "".join(framed_records), control_id


def send_astm_tcp(message: str, host: str, port: int, timeout: float = 8) -> tuple[str | None, str | None]:
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(timeout)
        connection.sendall(message.encode("ascii", errors="replace"))
        try:
            response = connection.recv(4096)
        except TimeoutError:
            return None, "NO_RESPONSE"
    if not response:
        return None, "NO_RESPONSE"
    decoded = response.decode("ascii", errors="replace")
    return decoded, decoded[:40]


def parse_astm_order(raw_message: str) -> dict:
    records = _records(raw_message)
    header = _first(records, "H")
    patient = _first(records, "P")
    order = _first(records, "O")
    if not header:
        raise ASTMOrderError("Missing ASTM H record")
    if not patient:
        raise ASTMOrderError("Missing ASTM P record")
    if not order:
        raise ASTMOrderError("Missing ASTM O record")

    patient_fields = patient.split("|")
    order_fields = order.split("|")
    patient_id = _field(patient_fields, 3) or _field(patient_fields, 2)
    patient_name = _field(patient_fields, 5) or "SIMULATED^PATIENT"
    sample_id = _field(order_fields, 2) or _field(order_fields, 3)
    order_id = _field(order_fields, 3) or sample_id
    tests = _tests(_field(order_fields, 5))
    if not patient_id:
        raise ASTMOrderError("Missing ASTM patient identifier")
    if not sample_id:
        raise ASTMOrderError("Missing ASTM sample identifier")
    if not tests:
        raise ASTMOrderError("Missing ASTM requested tests")

    return {
        "messageType": "ASTM_ORDER",
        "messageControlId": order_id,
        "orderId": order_id,
        "sampleId": sample_id,
        "barcode": sample_id,
        "patientId": patient_id,
        "patientName": patient_name,
        "priority": "ROUTINE",
        "requestedTests": tests,
        "panelCode": tests[0],
        "panelName": "ASTM PANEL",
    }


def _test_codes(results: list[dict]) -> str:
    return "^^^" + "\\^^^".join(result["code"] for result in results)


def _checksum(body: str) -> str:
    value = sum(body.encode("ascii", errors="replace")) % 256
    return f"{value:02X}"


def _records(raw_message: str) -> list[str]:
    normalized = raw_message.replace(STX, "").replace(ETX, "").replace(LF, CR)
    records = []
    for item in normalized.split(CR):
        line = item.strip()
        if not line:
            continue
        if line[0].isdigit() and len(line) > 1:
            line = line[1:]
        if len(line) > 2 and all(char in "0123456789ABCDEFabcdef" for char in line[-2:]):
            line = line[:-2]
        if "|" in line:
            records.append(line)
    return records


def _first(records: list[str], prefix: str) -> str | None:
    return next((record for record in records if record.startswith(prefix + "|")), None)


def _field(fields: list[str], position: int) -> str | None:
    return fields[position] if len(fields) > position and fields[position] else None


def _tests(value: str | None) -> list[str]:
    if not value:
        return []
    tests = []
    for raw in value.split("\\"):
        code = raw.split("^")[-1]
        if code:
            tests.append(code)
    return tests
