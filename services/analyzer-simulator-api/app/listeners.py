import logging
import os
import socket
import socketserver
import threading
import time

from .astm import astm_message_complete
from .configuration import validate_ports

START_BLOCK = b'\x0b'
END_BLOCK = b'\x1c\r'
LOGGER = logging.getLogger('analyzer-simulator.listeners')


class OrderMllpHandler(socketserver.BaseRequestHandler):
    def reject(self, message, reason):
        ack = self.server.engine.reject_transport(self.server.analyzer_id, 'HL7', message, self.client_address[0], reason)
        self.request.sendall(START_BLOCK + ack.encode('utf-8') + END_BLOCK)

    def handle(self):
        buffer = b''
        deadline = time.monotonic() + self.server.read_timeout
        try:
            while True:
                self.request.settimeout(max(0.01, deadline - time.monotonic()))
                chunk = self.request.recv(8192)
                if not chunk:
                    if buffer:
                        self.reject(buffer.decode('utf-8', errors='replace'), 'Incomplete MLLP frame at disconnect')
                    return
                buffer += chunk
                while buffer:
                    if buffer[:1] != START_BLOCK:
                        self.reject(buffer.decode('utf-8', errors='replace'), 'Missing MLLP start block')
                        return
                    end = buffer.find(END_BLOCK, 1)
                    if (end < 0 and len(buffer) > self.server.max_bytes) or end > self.server.max_bytes:
                        self.reject('', 'MLLP message exceeds configured size limit')
                        return
                    if end < 0:
                        break
                    try:
                        raw = buffer[1:end].decode('utf-8', errors='strict')
                    except UnicodeDecodeError:
                        self.reject('', 'Invalid UTF-8 encoding')
                        return
                    buffer = buffer[end + 2:]
                    ack = self.server.engine.receive_hl7_order(raw, self.server.analyzer_id, self.client_address[0])
                    self.request.sendall(START_BLOCK + ack.encode('utf-8') + END_BLOCK)
                    deadline = time.monotonic() + self.server.read_timeout
        except socket.timeout:
            try:
                if buffer:
                    self.reject(buffer.decode('utf-8', errors='replace'), 'MLLP read timeout')
            except OSError:
                pass
        except OSError:
            LOGGER.info('MLLP peer disconnected from %s', self.server.analyzer_id)
        except Exception:
            LOGGER.exception('Unhandled MLLP failure for %s', self.server.analyzer_id)
            try:
                self.reject('', 'Internal receiver failure; retry required')
            except OSError:
                pass


class AstmOrderHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = b''
        deadline = time.monotonic() + self.server.read_timeout
        try:
            while not astm_message_complete(data):
                self.request.settimeout(max(0.01, deadline - time.monotonic()))
                chunk = self.request.recv(8192)
                if not chunk:
                    raise ValueError('Incomplete ASTM message at disconnect')
                data += chunk
                if len(data) > self.server.max_bytes:
                    raise ValueError('ASTM message exceeds configured size limit')
            raw = data.decode('ascii', errors='strict')
            accepted = self.server.engine.receive_astm_order(raw, self.server.analyzer_id, self.client_address[0])
            self.request.sendall(b'\x06' if accepted else b'\x15')
        except (ValueError, OSError) as error:
            self.server.engine.reject_transport(self.server.analyzer_id, 'ASTM', data.decode('ascii', errors='replace'), self.client_address[0], str(error))
            try:
                self.request.sendall(b'\x15')
            except OSError:
                pass
        except Exception:
            LOGGER.exception('Unhandled ASTM receiver failure for %s', self.server.analyzer_id)


class OrderMllpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    handler = OrderMllpHandler

    def __init__(self, address, engine, analyzer_id):
        self.engine = engine
        self.analyzer_id = analyzer_id
        self.read_timeout = float(os.environ.get('SIMULATOR_READ_TIMEOUT', '30'))
        self.max_bytes = int(os.environ.get('SIMULATOR_MAX_MESSAGE_BYTES', '4194304'))
        self.slots = threading.BoundedSemaphore(int(os.environ.get('SIMULATOR_MAX_CONNECTIONS', '64')))
        self.connections = set()
        self.connection_lock = threading.Lock()
        super().__init__(address, self.handler)

    def verify_request(self, request, client_address):
        return self.slots.acquire(blocking=False)

    def process_request_thread(self, request, client_address):
        with self.connection_lock:
            self.connections.add(request)
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.connection_lock:
                self.connections.discard(request)
            self.slots.release()

    def close_connections(self):
        with self.connection_lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


class AstmOrderServer(OrderMllpServer):
    handler = AstmOrderHandler


class ListenerManager:
    def __init__(self, engine):
        self.engine = engine
        self.host = os.environ.get('ORDER_LISTEN_HOST', '0.0.0.0')
        self._running = {}
        self.lock = threading.RLock()

    @property
    def servers(self):
        return [entry[0] for entry in self._running.values()]

    @property
    def threads(self):
        return [entry[1] for entry in self._running.values()]

    def enabled(self):
        return os.environ.get('ORDER_LISTENERS_ENABLED', 'true').lower() not in {'0', 'false', 'no'}

    def _bind(self, analyzer):
        if not self.enabled() or not analyzer.get('listenPort'):
            return None
        if not self.engine.communication_ready(analyzer):
            return None
        validate_ports(analyzer, self.engine.list_analyzers())
        server_type = OrderMllpServer if analyzer['protocol'] == 'HL7' else AstmOrderServer
        return server_type((self.host, analyzer['listenPort']), self.engine, analyzer['id'])

    def _launch(self, analyzer, server):
        if server:
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            thread.start()
            self._running[analyzer['id']] = (server, thread, analyzer['protocol'], analyzer['listenPort'])
        self.engine.mark_listener(analyzer['id'], bool(server), self.host, analyzer['listenPort'], None)

    def _stop_one(self, analyzer_id):
        entry = self._running.pop(analyzer_id, None)
        if entry:
            entry[0].shutdown()
            entry[0].close_connections()
            entry[0].server_close()
            entry[1].join(timeout=2)

    def start(self):
        with self.lock:
            for analyzer in self.engine.list_analyzers():
                if analyzer['id'] in self._running:
                    continue
                try:
                    self._launch(analyzer, self._bind(analyzer))
                except (OSError, ValueError, OverflowError) as error:
                    self.engine.mark_listener(analyzer['id'], False, self.host, analyzer['listenPort'], str(error))
                    LOGGER.warning('Cannot start listener for %s: %s', analyzer['id'], error)

    def configure(self, payload, analyzer_id=None):
        # Reserve the new socket before committing configuration. Other devices
        # continue serving throughout this operation.
        with self.lock:
            current = next((a for a in self.engine.list_analyzers() if a['id'] == analyzer_id), None)
            if analyzer_id and current is None:
                raise ValueError('Unknown analyzer')
            merged = {**(current or {}), **payload}
            if analyzer_id:
                merged['id'] = analyzer_id
            proposed = self.engine._configured_analyzer(merged, existing=current)
            entry = self._running.get(proposed['id'])
            same = entry and (entry[2], entry[3]) == (proposed['protocol'], proposed['listenPort'])
            if same:
                self.engine.update_analyzer(analyzer_id, payload)
                return next(a for a in self.engine.list_analyzers() if a['id'] == analyzer_id)
            stopped = False
            server = None
            try:
                if entry and entry[3] == proposed['listenPort']:
                    self._stop_one(proposed['id'])
                    stopped = True
                server = self._bind(proposed)
                if server is None and self.enabled() and proposed.get('listenPort'):
                    # Reserve-check the port without accepting messages until
                    # the editor has a complete, active communication contract.
                    with socket.socket() as probe:
                        probe.bind((self.host, proposed['listenPort']))
                if analyzer_id:
                    self.engine.update_analyzer(analyzer_id, payload)
                else:
                    self.engine.create_analyzer(payload)
            except BaseException as error:
                if server:
                    server.server_close()
                if stopped:
                    self._launch(current, self._bind(current))
                if isinstance(error, (OSError, ValueError, OverflowError)):
                    raise ValueError(f'Listener configuration was not applied: {error}') from error
                raise
            if not stopped:
                self._stop_one(proposed['id'])
            self._launch(proposed, server)
            return next(a for a in self.engine.list_analyzers() if a['id'] == proposed['id'])

    def sync(self, analyzer_id):
        """A contract activation can start this device without restarting others."""
        with self.lock:
            analyzer = next(a for a in self.engine.list_analyzers() if a['id'] == analyzer_id)
            entry = self._running.get(analyzer_id)
            if self.engine.communication_ready(analyzer) and entry:
                return
            self._stop_one(analyzer_id)
            try:
                self._launch(analyzer, self._bind(analyzer))
            except (OSError, ValueError) as error:
                self.engine.mark_listener(analyzer_id, False, self.host, analyzer['listenPort'], str(error))

    def stop(self):
        with self.lock:
            for analyzer_id in list(self._running):
                self._stop_one(analyzer_id)

    def restart(self):
        self.stop()
        self.start()
