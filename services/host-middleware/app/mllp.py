import json
import logging
import socketserver
import threading
from datetime import datetime, timezone

from .parser import HL7ParseError, build_ack, extract_metadata, parse_message


START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"
LOGGER = logging.getLogger("host-middleware.mllp")


class ConnectionState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_at = datetime.now(timezone.utc)
        self.last_connection_at: datetime | None = None
        self.last_source_ip: str | None = None
        self.last_ack_code: str | None = None
        self.last_message_control_id: str | None = None

    def record(self, source_ip: str, ack_code: str, message_control_id: str | None) -> None:
        with self.lock:
            self.last_connection_at = datetime.now(timezone.utc)
            self.last_source_ip = source_ip
            self.last_ack_code = ack_code
            self.last_message_control_id = message_control_id

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "startedAt": self.started_at,
                "lastConnectionAt": self.last_connection_at,
                "lastSourceIp": self.last_source_ip,
                "lastAckCode": self.last_ack_code,
                "lastMessageControlId": self.last_message_control_id,
            }


class Hl7Processor:
    def __init__(self, store, state: ConnectionState):
        self.store = store
        self.state = state

    def process(self, raw_message: str, source_ip: str) -> str:
        metadata = extract_metadata(raw_message)
        LOGGER.info("RAW from %s: %s", source_ip, raw_message.replace("\r", "\\r"))
        try:
            parsed = parse_message(raw_message)
            ack = build_ack(metadata, "AA")
            raw_id = self.store.save_received(
                raw_message=raw_message, source_ip=source_ip, parsed=parsed, ack=ack
            )
            self.state.record(source_ip, "AA", metadata.message_control_id)
            LOGGER.info("Stored raw_id=%s JSON=%s", raw_id, json.dumps(parsed))
            return ack
        except HL7ParseError as error:
            ack = build_ack(metadata, "AE", str(error))
            self.store.save_rejected(
                raw_message=raw_message,
                source_ip=source_ip,
                analyzer_code=metadata.analyzer_code,
                message_type=metadata.message_type,
                message_control_id=metadata.message_control_id,
                ack=ack,
                error=str(error),
            )
            self.state.record(source_ip, "AE", metadata.message_control_id)
            LOGGER.warning("Rejected message: %s", error)
            return ack
        except Exception as error:
            self.state.record(source_ip, "AE", metadata.message_control_id)
            LOGGER.exception("Processing failure: %s", error)
            return build_ack(metadata, "AE", "Host processing failure")


class MllpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        buffer = b""
        while True:
            data = self.request.recv(8192)
            if not data:
                return
            buffer += data
            while True:
                start = buffer.find(START_BLOCK)
                end = buffer.find(END_BLOCK, start + 1) if start >= 0 else -1
                if start < 0:
                    buffer = buffer[-1:]
                    break
                if end < 0:
                    buffer = buffer[start:]
                    break
                raw_message = buffer[start + 1 : end].decode("utf-8", errors="replace")
                buffer = buffer[end + len(END_BLOCK) :]
                ack = self.server.processor.process(raw_message, self.client_address[0])
                self.request.sendall(START_BLOCK + ack.encode("utf-8") + END_BLOCK)
                if not buffer:
                    break


class MllpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], processor: Hl7Processor):
        self.processor = processor
        super().__init__(address, MllpHandler)
