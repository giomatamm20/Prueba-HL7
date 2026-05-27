import json
import logging
import os
import socketserver

from .parser import HL7ParseError, build_ack, extract_metadata, parse_oru_result


START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "2575"))
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://hl7user:hl7password@postgres:5432/hl7sandbox",
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("hl7-listener")


class Hl7MessageProcessor:
    def __init__(self, message_store):
        self.message_store = message_store

    def process(self, raw_message: str, source_ip: str) -> str:
        LOGGER.info("RAW HL7 from %s: %s", source_ip, raw_message.replace("\r", "\\r"))
        metadata = extract_metadata(raw_message)

        try:
            parsed_json = parse_oru_result(raw_message)
            ack = build_ack(metadata, "AA")
            parse_error = None
        except HL7ParseError as error:
            ack = build_ack(metadata, "AE", str(error))
            parsed_json = None
            parse_error = str(error)
            LOGGER.warning("Parse error: %s", error)

        try:
            self.message_store.save(
                raw_message=raw_message,
                source_ip=source_ip,
                message_control_id=metadata.message_control_id,
                ack_message=ack,
                parsed_json=parsed_json,
                parse_error=parse_error,
            )
        except Exception as error:
            LOGGER.error("Unable to store received message: %s", error)
            return build_ack(metadata, "AE", "Database insert failed")

        if parsed_json is not None:
            LOGGER.info("JSON: %s", json.dumps(parsed_json))
        LOGGER.info("ACK: %s", ack.replace("\r", "\\r"))
        return ack


class MllpRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        buffer = b""
        source_ip = self.client_address[0]
        while True:
            data = self.request.recv(8192)
            if not data:
                return
            buffer += data
            start = buffer.find(START_BLOCK)
            end = buffer.find(END_BLOCK, start + len(START_BLOCK))
            if start < 0 or end < 0:
                continue
            raw_message = buffer[start + len(START_BLOCK) : end].decode(
                "utf-8", errors="replace"
            )
            ack = self.server.processor.process(raw_message, source_ip)
            self.request.sendall(START_BLOCK + ack.encode("utf-8") + END_BLOCK)
            return


class ThreadingMllpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], processor: Hl7MessageProcessor):
        self.processor = processor
        super().__init__(server_address, MllpRequestHandler)


def main() -> None:
    from .storage import PostgresMessageStore

    LOGGER.info("Listening for HL7 MLLP messages on %s:%s", LISTEN_HOST, LISTEN_PORT)
    processor = Hl7MessageProcessor(PostgresMessageStore(DATABASE_URL))
    with ThreadingMllpServer((LISTEN_HOST, LISTEN_PORT), processor) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
