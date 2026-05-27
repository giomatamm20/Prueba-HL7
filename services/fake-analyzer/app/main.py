import argparse
import itertools
import logging
import os
import socket
import time
from pathlib import Path


START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"
DEFAULT_HOST = os.environ.get("DESTINATION_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("DESTINATION_PORT", "2575"))
DEFAULT_INTERVAL = float(os.environ.get("SEND_INTERVAL_SECONDS", "10"))
DEFAULT_SAMPLES_DIR = Path(
    os.environ.get(
        "SAMPLES_DIR",
        str(Path(__file__).resolve().parents[3] / "samples" / "hl7"),
    )
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("fake-analyzer")


def normalize_message(message: str) -> str:
    lines = message.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    return "\r".join(line for line in lines if line) + "\r"


def available_samples(samples_dir: Path) -> list[Path]:
    return sorted(samples_dir.glob("*.hl7"))


def prepare_message(sample: Path) -> str:
    message = normalize_message(sample.read_text(encoding="utf-8"))
    msh = message.split("\r", 1)[0].split("|")
    if len(msh) < 12 or msh[0] != "MSH":
        raise ValueError(f"{sample.name} does not contain a valid MSH segment")
    return message


def send_message(message: str, sample_name: str, host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.settimeout(8)
        connection.sendall(START_BLOCK + message.encode("utf-8") + END_BLOCK)
        response = b""
        while END_BLOCK not in response:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("Listener closed before sending an ACK")
            response += chunk
    ack = response[response.find(START_BLOCK) + 1 : response.find(END_BLOCK)].decode(
        "utf-8", errors="replace"
    )
    LOGGER.info("Sent %s to %s:%s", sample_name, host, port)
    LOGGER.info("Received ACK: %s", ack.replace("\r", "\\r"))


def send_once(sample_name: str | None, samples_dir: Path, host: str, port: int) -> None:
    samples = available_samples(samples_dir)
    sample = samples_dir / sample_name if sample_name else (samples[0] if samples else None)
    if sample is None or not sample.exists():
        raise FileNotFoundError("No HL7 sample is available")
    send_message(prepare_message(sample), sample.name, host, port)


def auto_send(samples_dir: Path, host: str, port: int, interval: float) -> None:
    samples = available_samples(samples_dir)
    if not samples:
        raise FileNotFoundError("No HL7 sample is available")
    LOGGER.info(
        "Sending to %s:%s every %ss using %s",
        host,
        port,
        interval,
        ", ".join(sample.name for sample in samples),
    )
    for sample in itertools.cycle(samples):
        try:
            send_message(prepare_message(sample), sample.name, host, port)
        except OSError as error:
            LOGGER.error("Send failed: %s", error)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal local fake HL7 analyzer")
    parser.add_argument("mode", choices=["auto", "send"], help="Periodic or one-time send")
    parser.add_argument("--sample", help="HL7 filename for one-time send")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Server hosting the HL7 listener")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="HL7 MLLP TCP port")
    parser.add_argument("--interval", default=DEFAULT_INTERVAL, type=float, help="Seconds between sends")
    parser.add_argument("--samples-dir", default=DEFAULT_SAMPLES_DIR, type=Path)
    arguments = parser.parse_args()

    if arguments.mode == "auto":
        auto_send(arguments.samples_dir, arguments.host, arguments.port, arguments.interval)
    else:
        send_once(arguments.sample, arguments.samples_dir, arguments.host, arguments.port)


if __name__ == "__main__":
    main()
