import os
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import StrEnum

from .astm import ASTMOrderError, build_astm_result, parse_astm_order, send_astm_tcp
from .catalog import ANALYZERS, TEST_PROFILES
from .hl7_orders import HL7OrderError, build_ack, extract_metadata, parse_order
from .protocols import build_hl7_oru, send_mllp
from .store import SimulatorStore


STATE_PROGRESS = {
    "OFFLINE": 0,
    "IDLE": 0,
    "WAITING_ORDER": 10,
    "WAITING_SAMPLE": 25,
    "SAMPLE_LOADED": 45,
    "PROCESSING": 70,
    "RESULT_READY": 85,
    "SENDING_RESULT": 92,
    "COMPLETED": 100,
    "ERROR": 100,
    "MAINTENANCE": 0,
    "CALIBRATION": 15,
    "QC_RUNNING": 35,
    "REAGENT_LOW": 0,
    "RACK_FULL": 0,
    "DOOR_OPEN": 0,
    "SAMPLE_ERROR": 100,
}


class AnalyzerState(StrEnum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    WAITING_ORDER = "WAITING_ORDER"
    WAITING_SAMPLE = "WAITING_SAMPLE"
    SAMPLE_LOADED = "SAMPLE_LOADED"
    PROCESSING = "PROCESSING"
    RESULT_READY = "RESULT_READY"
    SENDING_RESULT = "SENDING_RESULT"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
    MAINTENANCE = "MAINTENANCE"
    CALIBRATION = "CALIBRATION"
    QC_RUNNING = "QC_RUNNING"
    REAGENT_LOW = "REAGENT_LOW"
    RACK_FULL = "RACK_FULL"
    DOOR_OPEN = "DOOR_OPEN"
    SAMPLE_ERROR = "SAMPLE_ERROR"


class AnalyzerMode(StrEnum):
    MANUAL = "MANUAL"
    BIDIRECTIONAL = "BIDIRECTIONAL"


class SimulationEngine:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.store = SimulatorStore()
        default_destination_host = os.environ.get("RESULT_DESTINATION_HOST")
        default_destination_port = os.environ.get("RESULT_DESTINATION_PORT")
        configured = self.store.load_analyzers()
        defaults = {
            item["id"]: {
                **item,
                "resultDestinationHost": default_destination_host or item["resultDestinationHost"],
                "resultDestinationPort": int(default_destination_port or item["resultDestinationPort"]),
                "mode": AnalyzerMode.MANUAL.value,
                "state": AnalyzerState.IDLE.value,
                "lastAckCode": None,
                "lastError": None,
                "listenerActive": False,
                "listenerHost": None,
                "listenerError": None,
                "currentSampleId": None,
                "scenario": "NORMAL",
                "progress": 0,
                "updatedAt": self._now(),
            }
            for item in ANALYZERS
        }
        self.analyzers = configured or defaults
        if not configured:
            for analyzer in self.analyzers.values():
                self.store.save_analyzer(analyzer)
        else:
            for analyzer in self.analyzers.values():
                analyzer["listenerActive"] = False
                analyzer["listenerError"] = None
                analyzer["listenerHost"] = None
        self.orders: list[dict] = self.store.load_orders()
        self.messages: list[dict] = self.store.load_messages()

    def list_analyzers(self) -> list[dict]:
        with self.lock:
            return [dict(item) for item in self.analyzers.values()]

    def list_orders(self) -> list[dict]:
        with self.lock:
            return list(self.orders)

    def list_messages(self) -> list[dict]:
        with self.lock:
            return list(reversed(self.messages[-100:]))

    def create_manual_sample(self, payload: dict) -> dict:
        analyzer = self.analyzers.get(payload["analyzerId"])
        if analyzer is None:
            raise ValueError("Unknown analyzer")
        if analyzer["mode"] != AnalyzerMode.MANUAL:
            raise ValueError("Manual sample creation is disabled while analyzer is in BIDIRECTIONAL mode")
        unsupported = [test for test in payload["tests"] if test not in analyzer["supportedTests"]]
        if unsupported:
            raise ValueError("Unsupported tests for analyzer: " + ", ".join(unsupported))
        if self._barcode_exists(payload["analyzerId"], payload.get("barcode") or payload["sampleId"]):
            raise ValueError("muestra duplicada")

        order = {
            "id": str(uuid.uuid4()),
            "source": "MANUAL",
            "status": "WAITING_SAMPLE",
            "sampleId": payload["sampleId"],
            "barcode": payload.get("barcode") or payload["sampleId"],
            "patientId": payload.get("patientId") or payload["sampleId"],
            "patientName": payload.get("patientName") or "SIMULATED^PATIENT",
            "priority": payload.get("priority", "ROUTINE"),
            "analyzerId": payload["analyzerId"],
            "requestedTests": payload["tests"],
            "panelCode": payload["tests"][0],
            "panelName": "MANUAL PANEL",
            "rackPosition": self._rack_position(),
            "createdAt": self._now(),
            "updatedAt": self._now(),
            "results": [],
        }
        with self.lock:
            self.orders.append(order)
            self._set_state(payload["analyzerId"], AnalyzerState.WAITING_SAMPLE)
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[payload["analyzerId"]])
        return order

    def receive_hl7_order(self, raw_message: str, analyzer_id: str, source_ip: str) -> str:
        metadata = extract_metadata(raw_message)
        try:
            analyzer = self.analyzers.get(analyzer_id)
            if analyzer is None:
                raise HL7OrderError("Unknown analyzer listener")
            if analyzer["mode"] != AnalyzerMode.BIDIRECTIONAL:
                raise HL7OrderError("Analyzer is not in BIDIRECTIONAL mode")
            self._ensure_operational(analyzer_id)
            order_payload = parse_order(raw_message)
            unsupported = [
                test
                for test in order_payload["requestedTests"]
                if test not in analyzer["supportedTests"]
            ]
            if unsupported:
                raise HL7OrderError("Unsupported tests for analyzer: " + ", ".join(unsupported))
            if self._barcode_exists(analyzer_id, order_payload["barcode"]):
                raise HL7OrderError("muestra duplicada")

            order = self._order_from_payload(order_payload, analyzer_id, "BIDIRECTIONAL")
            ack = build_ack(metadata, "AA")
            with self.lock:
                self.orders.append(order)
                self._set_state(analyzer_id, AnalyzerState.WAITING_SAMPLE, current_sample=order["sampleId"])
                self.store.save_order(order)
                self.store.save_analyzer(self.analyzers[analyzer_id])
                self._append_message(
                    self._message_record(
                        direction="IN",
                        protocol="HL7",
                        message_type=order_payload["messageType"],
                        analyzer_id=analyzer_id,
                        sample_id=order["sampleId"],
                        source=source_ip,
                        raw_message=raw_message,
                        ack_code="AA",
                        ack_message=ack,
                        error=None,
                        message_control_id=order_payload["messageControlId"],
                    )
                )
            return ack
        except HL7OrderError as error:
            ack = build_ack(metadata, "AE", str(error))
            with self.lock:
                self._append_message(
                    self._message_record(
                        direction="IN",
                        protocol="HL7",
                        message_type=metadata.get("messageType"),
                        analyzer_id=analyzer_id,
                        sample_id=None,
                        source=source_ip,
                        raw_message=raw_message,
                        ack_code="AE",
                        ack_message=ack,
                        error=str(error),
                        message_control_id=metadata.get("messageControlId"),
                    )
                )
                if analyzer_id in self.analyzers:
                    self._set_state(analyzer_id, AnalyzerState.ERROR, str(error))
                    self.store.save_analyzer(self.analyzers[analyzer_id])
            return ack

    def receive_astm_order(self, raw_message: str, analyzer_id: str, source_ip: str) -> bool:
        try:
            analyzer = self.analyzers.get(analyzer_id)
            if analyzer is None:
                raise ASTMOrderError("Unknown analyzer listener")
            if analyzer["mode"] != AnalyzerMode.BIDIRECTIONAL:
                raise ASTMOrderError("Analyzer is not in BIDIRECTIONAL mode")
            self._ensure_operational(analyzer_id)
            order_payload = parse_astm_order(raw_message)
            unsupported = [
                test
                for test in order_payload["requestedTests"]
                if test not in analyzer["supportedTests"]
            ]
            if unsupported:
                raise ASTMOrderError("Unsupported tests for analyzer: " + ", ".join(unsupported))
            if self._barcode_exists(analyzer_id, order_payload["barcode"]):
                raise ASTMOrderError("muestra duplicada")

            order = self._order_from_payload(order_payload, analyzer_id, "BIDIRECTIONAL")
            with self.lock:
                self.orders.append(order)
                self._set_state(analyzer_id, AnalyzerState.WAITING_SAMPLE, current_sample=order["sampleId"])
                self.store.save_order(order)
                self.store.save_analyzer(self.analyzers[analyzer_id])
                self._append_message(
                    self._message_record(
                        direction="IN",
                        protocol="ASTM",
                        message_type="ASTM_ORDER",
                        analyzer_id=analyzer_id,
                        sample_id=order["sampleId"],
                        source=source_ip,
                        raw_message=raw_message,
                        ack_code="ACK",
                        ack_message="ACK",
                        error=None,
                        message_control_id=order_payload["messageControlId"],
                    )
                )
            return True
        except (ASTMOrderError, ValueError) as error:
            with self.lock:
                self._append_message(
                    self._message_record(
                        direction="IN",
                        protocol="ASTM",
                        message_type="ASTM_ORDER",
                        analyzer_id=analyzer_id,
                        sample_id=None,
                        source=source_ip,
                        raw_message=raw_message,
                        ack_code="NAK",
                        ack_message="NAK",
                        error=str(error),
                        message_control_id=None,
                    )
                )
                if analyzer_id in self.analyzers:
                    self._set_state(analyzer_id, AnalyzerState.ERROR, str(error))
                    self.store.save_analyzer(self.analyzers[analyzer_id])
            return False

    def load_sample(self, order_id: str, barcode: str | None = None) -> dict:
        with self.lock:
            order = self._find_order(order_id)
            analyzer = self.analyzers[order["analyzerId"]]
            if analyzer["mode"] == AnalyzerMode.BIDIRECTIONAL and not barcode:
                self._set_state(order["analyzerId"], AnalyzerState.ERROR, "barcode requerido")
                raise ValueError("barcode requerido")
            if barcode and barcode != order["barcode"]:
                self._set_state(order["analyzerId"], AnalyzerState.ERROR, "barcode no encontrado")
                raise ValueError("barcode no encontrado")
            if order["status"] != "WAITING_SAMPLE":
                self._set_state(order["analyzerId"], AnalyzerState.ERROR, "muestra duplicada o no disponible")
                raise ValueError("muestra duplicada o no disponible")
            order["status"] = "SAMPLE_LOADED"
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState.SAMPLE_LOADED, current_sample=order["sampleId"])
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[order["analyzerId"]])
            return dict(order)

    def scan_barcode(self, analyzer_id: str, barcode: str) -> dict:
        with self.lock:
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            self._ensure_operational(analyzer_id)
            for order in self.orders:
                if order["analyzerId"] == analyzer_id and order["barcode"] == barcode:
                    if order["status"] != "WAITING_SAMPLE":
                        self._set_state(analyzer_id, AnalyzerState.ERROR, "muestra duplicada o no disponible")
                        raise ValueError("muestra duplicada o no disponible")
                    order["status"] = "SAMPLE_LOADED"
                    order["updatedAt"] = self._now()
                    self._set_state(analyzer_id, AnalyzerState.SAMPLE_LOADED, current_sample=order["sampleId"])
                    self.store.save_order(order)
                    self.store.save_analyzer(self.analyzers[analyzer_id])
                    return dict(order)
            self._set_state(analyzer_id, AnalyzerState.ERROR, "barcode no encontrado")
            raise ValueError("barcode no encontrado")

    def set_analyzer_mode(self, analyzer_id: str, mode: str) -> dict:
        normalized = mode.upper()
        if normalized not in {AnalyzerMode.MANUAL.value, AnalyzerMode.BIDIRECTIONAL.value}:
            raise ValueError("Invalid analyzer mode")
        with self.lock:
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            analyzer = self.analyzers[analyzer_id]
            analyzer["mode"] = AnalyzerMode(normalized)
            analyzer["updatedAt"] = self._now()
            if analyzer["state"] == AnalyzerState.IDLE:
                analyzer["progress"] = STATE_PROGRESS["WAITING_ORDER"] if analyzer["mode"] == AnalyzerMode.BIDIRECTIONAL else 0
            self.store.save_analyzer(analyzer)
            return dict(analyzer)

    def set_analyzer_state(self, analyzer_id: str, state: str) -> dict:
        normalized = state.upper()
        if normalized not in STATE_PROGRESS:
            raise ValueError("Invalid analyzer state")
        with self.lock:
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            self._set_state(analyzer_id, AnalyzerState(normalized), None)
            self.store.save_analyzer(self.analyzers[analyzer_id])
            return dict(self.analyzers[analyzer_id])

    def set_analyzer_scenario(self, analyzer_id: str, scenario: str) -> dict:
        normalized = scenario.upper()
        allowed = {"NORMAL", "FORCE_CRITICAL", "SAMPLE_ERROR", "NO_RESPONSE", "MALFORMED_RESULT"}
        if normalized not in allowed:
            raise ValueError("Invalid analyzer scenario")
        with self.lock:
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            analyzer = self.analyzers[analyzer_id]
            analyzer["scenario"] = normalized
            analyzer["updatedAt"] = self._now()
            self.store.save_analyzer(analyzer)
            return dict(analyzer)

    def create_analyzer(self, payload: dict) -> dict:
        analyzer_id = payload["id"]
        with self.lock:
            if analyzer_id in self.analyzers:
                raise ValueError("Analyzer already exists")
            analyzer = self._configured_analyzer(payload)
            self.analyzers[analyzer_id] = analyzer
            self.store.save_analyzer(analyzer)
            return dict(analyzer)

    def update_analyzer(self, analyzer_id: str, payload: dict) -> dict:
        with self.lock:
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            current = dict(self.analyzers[analyzer_id])
            current.update({key: value for key, value in payload.items() if value is not None})
            current["id"] = analyzer_id
            current = self._configured_analyzer(current, existing=current)
            self.analyzers[analyzer_id] = current
            self.store.save_analyzer(current)
            return dict(current)

    def process_order(self, order_id: str, send_result: bool = True) -> dict:
        with self.lock:
            order = self._find_order(order_id)
            self._ensure_operational(order["analyzerId"])
            if self.analyzers[order["analyzerId"]].get("scenario") == "SAMPLE_ERROR":
                order["status"] = "ERROR"
                order["updatedAt"] = self._now()
                self._set_state(order["analyzerId"], AnalyzerState.SAMPLE_ERROR, "sample processing error", order["sampleId"])
                self.store.save_order(order)
                self.store.save_analyzer(self.analyzers[order["analyzerId"]])
                raise ValueError("sample processing error")
            if order["status"] != "SAMPLE_LOADED":
                raise ValueError("Order is not ready to process")
            order["status"] = "PROCESSING"
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState.PROCESSING, current_sample=order["sampleId"])
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[order["analyzerId"]])

        time.sleep(0.8)

        with self.lock:
            order = self._find_order(order_id)
            force_critical = self.analyzers[order["analyzerId"]].get("scenario") == "FORCE_CRITICAL"
            order["results"] = self._generate_results(order["requestedTests"], force_critical=force_critical)
            order["status"] = "RESULT_READY"
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState.RESULT_READY, current_sample=order["sampleId"])
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[order["analyzerId"]])

        if send_result:
            return self.send_result(order_id)
        return dict(order)

    def send_result(self, order_id: str, destination_host: str | None = None, destination_port: int | None = None) -> dict:
        with self.lock:
            order = self._find_order(order_id)
            self._ensure_operational(order["analyzerId"])
            if not order["results"]:
                order["results"] = self._generate_results(order["requestedTests"])
            analyzer = dict(self.analyzers[order["analyzerId"]])
            sequence = self.store.next_sequence()
            if analyzer["protocol"] == "HL7":
                message, control_id = build_hl7_oru(analyzer, order, order["results"], sequence)
                message_type = "ORU^R01"
            elif analyzer["protocol"] == "ASTM":
                message, control_id = build_astm_result(analyzer, order, order["results"], sequence)
                message_type = "ASTM_RESULT"
            else:
                raise ValueError("Unsupported analyzer protocol")
            if analyzer.get("scenario") == "MALFORMED_RESULT":
                message = message[: max(1, len(message) // 2)]
            host = destination_host or analyzer["resultDestinationHost"]
            port = destination_port or analyzer["resultDestinationPort"]
            order["status"] = "SENDING_RESULT"
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState.SENDING_RESULT, current_sample=order["sampleId"])
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[order["analyzerId"]])

        try:
            if analyzer.get("scenario") == "NO_RESPONSE":
                raise TimeoutError("simulated no response from destination")
            if analyzer["protocol"] == "HL7":
                ack, code = send_mllp(message, host, int(port))
                status = "COMPLETED" if code == "AA" else "ERROR"
                error = None if code == "AA" else f"ACK no positivo: {code or 'MISSING'}"
            else:
                ack, code = send_astm_tcp(message, host, int(port))
                status = "COMPLETED"
                error = None
        except (OSError, TimeoutError) as exc:
            ack = None
            code = None
            status = "ERROR"
            error = str(exc)

        with self.lock:
            order = self._find_order(order_id)
            order["status"] = status
            order["messageControlId"] = control_id
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState(status), error, order["sampleId"])
            self.analyzers[order["analyzerId"]]["lastAckCode"] = code
            record = {
                "id": str(uuid.uuid4()),
                "direction": "OUT",
                "protocol": analyzer["protocol"],
                "messageType": message_type,
                "analyzerId": order["analyzerId"],
                "sampleId": order["sampleId"],
                "destination": f"{host}:{port}",
                "messageControlId": control_id,
                "rawMessage": message,
                "ackCode": code,
                "ackMessage": ack,
                "error": error,
                "createdAt": self._now(),
            }
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[order["analyzerId"]])
            self._append_message(record)
            return {**dict(order), "lastMessage": record}

    def dashboard(self) -> dict:
        with self.lock:
            active = [item for item in self.analyzers.values() if item["state"] not in {AnalyzerState.OFFLINE, AnalyzerState.MAINTENANCE}]
            listeners = [item for item in self.analyzers.values() if item.get("listenerActive")]
            pending = [item for item in self.orders if item["status"] in {"WAITING_SAMPLE", "SAMPLE_LOADED", "PROCESSING", "RESULT_READY"}]
            errors = [item for item in self.analyzers.values() if item["state"] == AnalyzerState.ERROR]
            return {
                "analyzerCount": len(self.analyzers),
                "activeAnalyzerCount": len(active),
                "activeListenerCount": len(listeners),
                "pendingOrderCount": len(pending),
                "messageCount": len(self.messages),
                "errorCount": len(errors),
            }

    def _find_order(self, order_id: str) -> dict:
        for order in self.orders:
            if order["id"] == order_id:
                return order
        raise ValueError("Order not found")

    def _barcode_exists(self, analyzer_id: str, barcode: str) -> bool:
        return any(
            order["analyzerId"] == analyzer_id
            and order["barcode"] == barcode
            and order["status"] not in {"ERROR"}
            for order in self.orders
        )

    def mark_listener(self, analyzer_id: str, active: bool, host: str, port: int, error: str | None) -> None:
        with self.lock:
            analyzer = self.analyzers[analyzer_id]
            analyzer["listenerActive"] = active
            analyzer["listenerHost"] = f"{host}:{port}"
            analyzer["listenerError"] = error
            analyzer["updatedAt"] = self._now()
            if error:
                analyzer["lastError"] = error
            self.store.save_analyzer(analyzer)

    def _set_state(
        self,
        analyzer_id: str,
        state: AnalyzerState,
        error: str | None = None,
        current_sample: str | None = None,
    ) -> None:
        analyzer = self.analyzers[analyzer_id]
        analyzer["state"] = state
        analyzer["lastError"] = error
        analyzer["lastAckCode"] = None if state != AnalyzerState.COMPLETED else "AA"
        analyzer["currentSampleId"] = current_sample if state not in {AnalyzerState.IDLE, AnalyzerState.COMPLETED} else None
        analyzer["progress"] = STATE_PROGRESS[state.value]
        analyzer["updatedAt"] = self._now()

    def _append_message(self, record: dict) -> None:
        self.messages.append(record)
        self.messages = self.messages[-250:]
        self.store.save_message(record)

    def _configured_analyzer(self, payload: dict, existing: dict | None = None) -> dict:
        now = self._now()
        supported_tests = payload.get("supportedTests") or payload.get("supported_tests") or []
        if isinstance(supported_tests, str):
            supported_tests = [item.strip() for item in supported_tests.split(",") if item.strip()]
        if not supported_tests:
            raise ValueError("Analyzer needs at least one supported test")
        protocol = str(payload.get("protocol", "HL7")).upper()
        if protocol not in {"HL7", "ASTM"}:
            raise ValueError("Protocol must be HL7 or ASTM")
        transport = payload.get("transport") or ("MLLP" if protocol == "HL7" else "TCP_SIMULATED")
        mode = str(payload.get("mode", AnalyzerMode.MANUAL.value)).upper()
        if mode not in {AnalyzerMode.MANUAL.value, AnalyzerMode.BIDIRECTIONAL.value}:
            raise ValueError("Invalid analyzer mode")
        base = existing or {}
        return {
            **base,
            "id": payload["id"],
            "name": payload.get("name") or payload["id"],
            "vendor": payload.get("vendor") or "Generic",
            "protocol": protocol,
            "transport": transport,
            "listenPort": int(payload.get("listenPort") or payload.get("listen_port") or 0),
            "resultDestinationHost": payload.get("resultDestinationHost") or payload.get("result_destination_host") or "localhost",
            "resultDestinationPort": int(payload.get("resultDestinationPort") or payload.get("result_destination_port") or 2575),
            "supportedTests": supported_tests,
            "mode": mode,
            "state": payload.get("state") or base.get("state") or AnalyzerState.IDLE.value,
            "lastAckCode": base.get("lastAckCode"),
            "lastError": base.get("lastError"),
            "listenerActive": base.get("listenerActive", False),
            "listenerHost": base.get("listenerHost"),
            "listenerError": base.get("listenerError"),
            "currentSampleId": base.get("currentSampleId"),
            "scenario": payload.get("scenario") or base.get("scenario") or "NORMAL",
            "progress": base.get("progress", 0),
            "updatedAt": now,
        }

    def _generate_results(self, requested_tests: list[str], force_critical: bool = False) -> list[dict]:
        expanded = []
        for test in requested_tests:
            if test == "CBC":
                expanded.extend(["WBC", "RBC", "HGB", "HCT", "MCV", "MCH", "MCHC", "PLT"])
            else:
                expanded.append(test)
        return [self._generate_result(test, force_critical=force_critical) for test in dict.fromkeys(expanded)]

    def _generate_result(self, code: str, force_critical: bool = False) -> dict:
        profile = TEST_PROFILES.get(code, {"unit": "", "low": 1, "high": 10, "decimals": 1})
        low = float(profile["low"])
        high = float(profile["high"])
        if code in {"NIT"}:
            value = random.choice(["NEG", "NEG", "POS"])
            flag = "A" if value == "POS" else "N"
        else:
            critical = force_critical or random.random() < 0.08
            if critical:
                value_number = random.choice([low * 0.65, high * 1.35])
            else:
                value_number = random.uniform(low, high)
            value = round(value_number, int(profile["decimals"]))
            flag = "L" if value_number < low else "H" if value_number > high else "N"
        reference = f"{profile['low']}-{profile['high']}"
        return {
            "code": code,
            "value": value,
            "unit": profile["unit"],
            "referenceRange": reference,
            "abnormalFlag": flag,
        }

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _rack_position(self) -> str:
        index = len(self.orders) % 50 + 1
        return f"R{(index - 1) // 10 + 1:02d}-P{(index - 1) % 10 + 1:02d}"

    def _message_record(
        self,
        *,
        direction: str,
        protocol: str,
        message_type: str | None,
        analyzer_id: str,
        sample_id: str | None,
        source: str,
        raw_message: str,
        ack_code: str | None,
        ack_message: str | None,
        error: str | None,
        message_control_id: str | None,
    ) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "direction": direction,
            "protocol": protocol,
            "messageType": message_type,
            "analyzerId": analyzer_id,
            "sampleId": sample_id,
            "destination": source,
            "messageControlId": message_control_id,
            "rawMessage": raw_message,
            "ackCode": ack_code,
            "ackMessage": ack_message,
            "error": error,
            "createdAt": self._now(),
        }

    def _ensure_operational(self, analyzer_id: str) -> None:
        analyzer = self.analyzers[analyzer_id]
        if str(analyzer["state"]) in {
            AnalyzerState.OFFLINE.value,
            AnalyzerState.MAINTENANCE.value,
            AnalyzerState.CALIBRATION.value,
            AnalyzerState.QC_RUNNING.value,
            AnalyzerState.REAGENT_LOW.value,
            AnalyzerState.RACK_FULL.value,
            AnalyzerState.DOOR_OPEN.value,
        }:
            raise ValueError(f"Analyzer is {analyzer['state']}")

    def _order_from_payload(self, payload: dict, analyzer_id: str, source: str) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "source": source,
            "status": "WAITING_SAMPLE",
            "sampleId": payload["sampleId"],
            "barcode": payload["barcode"],
            "patientId": payload["patientId"],
            "patientName": payload.get("patientName") or "SIMULATED^PATIENT",
            "priority": payload["priority"],
            "analyzerId": analyzer_id,
            "requestedTests": payload["requestedTests"],
            "panelCode": payload.get("panelCode"),
            "panelName": payload.get("panelName"),
            "orderId": payload["orderId"],
            "sourceMessageControlId": payload.get("messageControlId"),
            "rackPosition": self._rack_position(),
            "createdAt": self._now(),
            "updatedAt": self._now(),
            "results": [],
        }
