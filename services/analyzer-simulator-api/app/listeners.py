import logging
import os
import socketserver
import threading

from .astm import ACK


START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\r"
LOGGER = logging.getLogger("analyzer-simulator.listeners")


class OrderMllpHandler(socketserver.BaseRequestHandler):
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
                ack = self.server.engine.receive_hl7_order(
                    raw_message=raw_message,
                    analyzer_id=self.server.analyzer_id,
                    source_ip=self.client_address[0],
                )
                self.request.sendall(START_BLOCK + ack.encode("utf-8") + END_BLOCK)
                if not buffer:
                    break


class OrderMllpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine, analyzer_id: str):
        self.engine = engine
        self.analyzer_id = analyzer_id
        super().__init__(address, OrderMllpHandler)


class AstmOrderHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = b""
        while True:
            chunk = self.request.recv(8192)
            if not chunk:
                break
            data += chunk
            if b"L|" in data or b"\x03" in data:
                break
        raw_message = data.decode("ascii", errors="replace")
        accepted = self.server.engine.receive_astm_order(
            raw_message=raw_message,
            analyzer_id=self.server.analyzer_id,
            source_ip=self.client_address[0],
        )
        self.request.sendall(ACK.encode("ascii") if accepted else b"\x15")


class AstmOrderServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], engine, analyzer_id: str):
        self.engine = engine
        self.analyzer_id = analyzer_id
        super().__init__(address, AstmOrderHandler)


class ListenerManager:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.host = os.environ.get("ORDER_LISTEN_HOST", "0.0.0.0")
        self.servers: list[OrderMllpServer | AstmOrderServer] = []
        self.threads: list[threading.Thread] = []

    def start(self) -> None:
        enabled = os.environ.get("ORDER_LISTENERS_ENABLED", "true").lower() not in {"0", "false", "no"}
        if not enabled:
            LOGGER.info("Order listeners disabled")
            return

        for analyzer in self.engine.list_analyzers():
            if analyzer["protocol"] not in {"HL7", "ASTM"}:
                continue
            if not int(analyzer.get("listenPort") or 0):
                continue
            analyzer_id = analyzer["id"]
            port = int(analyzer["listenPort"])
            try:
                server = (
                    OrderMllpServer((self.host, port), self.engine, analyzer_id)
                    if analyzer["protocol"] == "HL7"
                    else AstmOrderServer((self.host, port), self.engine, analyzer_id)
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.servers.append(server)
                self.threads.append(thread)
                self.engine.mark_listener(analyzer_id, True, self.host, port, None)
                LOGGER.info("%s order listener active for %s on %s:%s", analyzer["protocol"], analyzer_id, self.host, port)
            except OSError as error:
                self.engine.mark_listener(analyzer_id, False, self.host, port, str(error))
                LOGGER.exception("Cannot start listener for %s on %s:%s", analyzer_id, self.host, port)

    def stop(self) -> None:
        for server in self.servers:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)
        self.servers.clear()
        self.threads.clear()

    def restart(self) -> None:
        self.stop()
        self.start()
