"""Synthetic fixtures and real local TCP peers; no hospital services or live DB."""
import copy
from contextlib import contextmanager
import os
import socket
import socketserver
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.engine import SimulationEngine
from app.hl7_orders import extract_metadata
from app.listeners import ListenerManager
from app.astm import astm_message_complete
from app.hl7_contract import infer_hl7_contract

OML = (
    "MSH|^~\\&|HIS_TEST|LAB_TEST|SIM|LAB|20261015120000||OML^O21|MSG-1|P|2.5.1\r"
    "PID|||PAT-SYNTHETIC||PRUEBA^SINTETICA||19800101|M\r"
    "PV1|1|O\rORC|NW|ORDER-1\rOBR|1|ORDER-1|SAMPLE-1|CBC^Hemograma\r"
)
ASTM = "H|\\^&|||HIS_TEST\rP|1||PAT-SYNTHETIC||PRUEBA^SINTETICA\rO|1|SAMPLE-1|ORDER-1||^^^CBC\rL|1|N\r"


def config(identifier="test-analyzer", **changes):
    return {"id": identifier, "name": identifier, "protocol": "HL7", "mode": "BIDIRECTIONAL",
            "listenPort": 0, "supportedTests": ["CBC", "GLU"], "resultDestinationHost": "127.0.0.1",
            "resultDestinationPort": 2575, **changes}


def outgoing():
    return {"events": {"RESULT_FINAL": {"messageType": "ORU^R01", "segments": [
        {"name": "MSH", "fields": [
            {"target": "MSH-3", "source": "analyzer.id"},
            {"target": "MSH-7", "source": "system.timestamp"},
            {"target": "MSH-9", "sourceType": "fixed", "value": "ORU^R01"},
            {"target": "MSH-10", "source": "system.messageId"},
            {"target": "MSH-11", "sourceType": "fixed", "value": "P"},
            {"target": "MSH-12", "sourceType": "fixed", "value": "2.5.1"},
        ]},
        {"name": "PID", "fields": [{"target": "PID-3", "source": "patient.id", "required": True},
                                    {"target": "PID-5", "source": "patient.name"}]},
        {"name": "OBR", "fields": [{"target": "OBR-2", "source": "order.id"},
                                    {"target": "OBR-3", "source": "sample.id"},
                                    {"target": "OBR-4.1", "source": "test.code"}]},
        {"name": "OBX", "repeatBy": "results", "fields": [
            {"target": "OBX-1", "source": "counter"},
            {"target": "OBX-2", "sourceType": "fixed", "value": "NM"},
            {"target": "OBX-3", "source": "result.code", "required": True},
            {"target": "OBX-5", "source": "result.value", "required": True},
            {"target": "OBX-6", "source": "result.unit"},
            {"target": "OBX-11", "sourceType": "fixed", "value": "F"},
        ]},
    ]}}}


def outbound_contract():
    # Explicit contract for existing built-in ORU writers. Individual strict
    # writer tests use full schemas, with field and cardinality constraints.
    return {"schema": {"formatVersion": 2, "messageType": "ORU^R01", "segments": [
        {"name": "MSH", "min": 1, "max": 1, "fields": {}}
    ], "options": {"allowUnexpectedSegments": True, "allowUnexpectedFields": True, "allowUnexpectedComponents": True}}}


def install_contract(engine, identifier, raw=OML):
    return engine.save_protocol_profile(identifier, {"name": "Synthetic configured contract",
        "inbound": {"schema": infer_hl7_contract(raw), "strictValidation": False}, "outbound": outbound_contract()})


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="p36-synthetic-")
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {
            "SIMULATOR_DB_PATH": os.path.join(self.directory.name, "simulator.db"),
            "ORDER_LISTEN_HOST": "127.0.0.1", "ORDER_LISTENERS_ENABLED": "true",
            "SIMULATOR_ALLOWED_LISTEN_PORTS": "", "SIMULATOR_RESERVED_PORTS": "8090",
            "SIMULATOR_READ_TIMEOUT": "0.3", "SIMULATOR_MAX_MESSAGE_BYTES": "4194304",
            "PACS_ORDER_PORT": "5020", "PACS_NOTIFICATION_HOST": "127.0.0.1",
            "PACS_NOTIFICATION_PORT": "2575",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.engine = SimulationEngine()
        self.engine.update_analyzer("pacs-simulator", {"listenPort": 0})
        self.manager = ListenerManager(self.engine)
        self.addCleanup(self.manager.stop)

    def add(self, identifier="test-analyzer", contract=True, **changes):
        self.engine.create_analyzer(config(identifier, **changes))
        if contract and self.engine.analyzers[identifier]["protocol"] == "HL7":
            install_contract(self.engine, identifier)
        return self.engine.analyzers[identifier]


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def read_frame(connection):
    data = b""
    while b"\x1c\r" not in data:
        chunk = connection.recv(8192)
        if not chunk:
            raise ConnectionError("No complete MLLP response")
        data += chunk
    return data


def exchange(port, raw, fragments=False):
    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
        connection.settimeout(2)
        framed = b"\x0b" + raw.encode() + b"\x1c\r"
        if fragments:
            for offset in range(0, len(framed), 7):
                connection.sendall(framed[offset:offset + 7])
        else:
            connection.sendall(framed)
        return read_frame(connection).decode()


@contextmanager
def receiver(protocol="HL7", responses=("AA",), wrong_id=False, malformed=False):
    """A real socket peer. It records messages before choosing ACK/NAK/drop."""
    messages = []
    errors = []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                self.request.settimeout(2)
                data = b""
                while not (b"\x1c\r" in data if protocol == "HL7" else astm_message_complete(data)):
                    chunk = self.request.recv(8192)
                    if not chunk:
                        return
                    data += chunk
                raw = data[1:-2].decode() if protocol == "HL7" else data.decode("ascii")
                index = len(messages)
                messages.append(raw)
                response = responses[min(index, len(responses) - 1)]
                if response is None:
                    return
                if malformed:
                    self.request.sendall(b"unframed ACK")
                    return
                if protocol == "HL7":
                    identity = "WRONG-ID" if wrong_id else extract_metadata(raw)["messageControlId"]
                    ack = f"MSH|^~\\&|RECEIVER|LAB|SIM|LAB|20261015120100||ACK^R01|ACK-TEST|P|2.5.1\rMSA|{response}|{identity}\r"
                    framed = b"\x0b" + ack.encode() + b"\x1c\r"
                    # Return a split ACK to exercise the sender's buffer.
                    self.request.sendall(framed[:13])
                    self.request.sendall(framed[13:])
                else:
                    self.request.sendall(b"\x06" if response == "AA" else b"\x15")
            except BaseException as error:
                errors.append(repr(error))
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    server = Server(("127.0.0.1", 0), Handler)
    server.messages, server.errors = messages, errors
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
