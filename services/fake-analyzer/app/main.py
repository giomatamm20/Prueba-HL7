import argparse
import itertools
import logging
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path


START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"
DESTINATION_HOST = os.environ.get("DESTINATION_HOST", "192.168.1.101")
DESTINATION_PORT = int(os.environ.get("DESTINATION_PORT", "2575"))
SEND_INTERVAL_SECONDS = float(os.environ.get("SEND_INTERVAL_SECONDS", "10"))
ACK_TIMEOUT_SECONDS = float(os.environ.get("ACK_TIMEOUT_SECONDS", "8"))
RETRY_DELAY_SECONDS = float(os.environ.get("RETRY_DELAY_SECONDS", "5"))
ANALYZER_NAME = os.environ.get("ANALYZER_NAME", "ABBOTT_CELL_DYN")

if "SAMPLES_DIR" in os.environ:
    SAMPLES_DIR = Path(os.environ["SAMPLES_DIR"])
else:
    local_project_root = Path(__file__).resolve().parents[3]
    SAMPLES_DIR = local_project_root / "samples" / "hl7"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("fake-analyzer")


def normalize_message(message: str) -> str:
    lines = message.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    return "\r".join(line for line in lines if line) + "\r"


def samples() -> list[Path]:
    return sorted(SAMPLES_DIR.glob("*.hl7"))


def build_transmission(sample: Path, sequence: int) -> tuple[str, str]:
    segments = normalize_message(sample.read_text(encoding="utf-8")).rstrip("\r").split("\r")
    msh = segments[0].split("|")
    if len(msh) < 12 or msh[0] != "MSH":
        raise ValueError(f"{sample.name} does not begin with a complete MSH segment")
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d%H%M%S")
    unique_stamp = now.strftime("%Y%m%d%H%M%S%f")
    control_id = f"{ANALYZER_NAME}-{unique_stamp}-{sequence:06d}"
    msh[2] = ANALYZER_NAME
    msh[6] = timestamp
    msh[9] = control_id
    segments[0] = "|".join(msh)
    return "\r".join(segments) + "\r", control_id


def read_ack(connection: socket.socket) -> str:
    response = b""
    while END_BLOCK not in response:
        chunk = connection.recv(4096)
        if not chunk:
            raise ConnectionError("Host closed the connection without an ACK")
        response += chunk
    start = response.find(START_BLOCK)
    end = response.find(END_BLOCK, start + 1)
    if start < 0 or end < 0:
        raise ConnectionError("Host returned data without MLLP framing")
    return response[start + 1 : end].decode("utf-8", errors="replace")


def ack_code(ack: str) -> str | None:
    for segment in ack.replace("\n", "\r").split("\r"):
        if segment.startswith("MSA|"):
            fields = segment.split("|")
            return fields[1] if len(fields) > 1 else None
    return None


def transmit(message: str, sample_name: str, control_id: str) -> bool:
    with socket.create_connection((DESTINATION_HOST, DESTINATION_PORT), timeout=5) as connection:
        connection.settimeout(ACK_TIMEOUT_SECONDS)
        connection.sendall(START_BLOCK + message.encode("utf-8") + END_BLOCK)
        ack = read_ack(connection)
    code = ack_code(ack)
    LOGGER.info(
        "Sent sample=%s control_id=%s destination=%s:%s ack=%s",
        sample_name,
        control_id,
        DESTINATION_HOST,
        DESTINATION_PORT,
        code or "MISSING",
    )
    LOGGER.debug("ACK payload: %s", ack.replace("\r", "\\r"))
    return code == "AA"


def send_once(sample_name: str | None) -> None:
    available = samples()
    selected = SAMPLES_DIR / sample_name if sample_name else (available[0] if available else None)
    if selected is None or not selected.exists():
        raise FileNotFoundError("No sample HL7 message was selected")
    message, control_id = build_transmission(selected, 1)
    if not transmit(message, selected.name, control_id):
        raise RuntimeError("Host rejected the message or did not return a positive ACK")


def auto_send() -> None:
    available = samples()
    if not available:
        raise FileNotFoundError("No HL7 samples found")
    LOGGER.info(
        "Analyzer %s sending ORU/ORM messages to %s:%s every %ss",
        ANALYZER_NAME,
        DESTINATION_HOST,
        DESTINATION_PORT,
        SEND_INTERVAL_SECONDS,
    )
    for sequence, sample in enumerate(itertools.cycle(available), start=1):
        message, control_id = build_transmission(sample, sequence)
        try:
            accepted = transmit(message, sample.name, control_id)
            if not accepted:
                LOGGER.warning("Host returned a non-AA ACK; message will not be considered accepted")
        except OSError as error:
            LOGGER.error("Transmission failed for %s: %s", control_id, error)
            time.sleep(RETRY_DELAY_SECONDS)
        time.sleep(SEND_INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="HL7 medical analyzer simulator over MLLP")
    parser.add_argument("mode", choices=["auto", "send"])
    parser.add_argument("--sample", help="Filename mounted in /app/samples for a one-time send")
    arguments = parser.parse_args()
    if arguments.mode == "auto":
        auto_send()
    else:
        send_once(arguments.sample)


if __name__ == "__main__":
    main()
