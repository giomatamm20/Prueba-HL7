import socket
from datetime import datetime, timezone


START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"


def build_hl7_oru(analyzer: dict, sample: dict, results: list[dict], sequence: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d%H%M%S")
    control_id = f"{analyzer['id']}-{now.strftime('%Y%m%d%H%M%S%f')}-{sequence:06d}"
    order_id = sample.get("orderId") or f"ORD-{sample['sampleId']}"
    patient_name = sample.get("patientName") or "SIMULATED^PATIENT"
    patient_id = sample.get("patientId") or sample["sampleId"]
    panel_code = sample.get("panelCode") or "SIM"
    panel_name = sample.get("panelName") or "SIMULATED PANEL"

    segments = [
        f"MSH|^~\\&|{analyzer['id']}|SIMLAB|MIRTH|LIS|{timestamp}||ORU^R01|{control_id}|P|2.5.1",
        f"PID|1||{patient_id}||{patient_name}",
        f"OBR|1||{order_id}|{panel_code}^{panel_name}||||||||||||||||||F",
    ]
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
    return "\r".join(segments) + "\r", control_id


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
        while END_BLOCK not in response:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("Destination closed the connection without ACK")
            response += chunk

    start = response.find(START_BLOCK)
    end = response.find(END_BLOCK, start + 1)
    if start < 0 or end < 0:
        raise ConnectionError("Destination returned data without MLLP framing")
    ack = response[start + 1 : end].decode("utf-8", errors="replace")
    return ack, ack_code(ack)
