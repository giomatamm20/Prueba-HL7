import os
import random
import threading
import time
import uuid
import copy
import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum

from .astm import ASTMOrderError, build_astm_result, parse_astm_order, send_astm_tcp
from .behavior import normalize_behavior, validate_hl7_behavior
from .study_catalog import bind_studies, dynamic_outbound, generate_results, seed_catalog, validate_catalog
from .hl7_orders import HL7OrderError, build_ack, extract_metadata, parse_order, validate_anapath_order
from .hl7_contract import hl7_contract_issues, infer_hl7_contract
from .protocols import build_anapath_event, build_hl7_oru, send_mllp
from .profile_builder import build_profile_message
from .profile_parser import parse_with_profile
from .pacs import (
    PACS_ANALYZER_ID,
    build_image_ready_oru,
    decorate_pacs_order,
    default_pacs_analyzer,
)
from .store import SimulatorStore
from .configuration import validate_ports
from .profile_validation import validate_profile, validate_outbound, valid_path, profile_ready
from .hl7_values import declare_utf8

LOGGER = logging.getLogger("analyzer-simulator.engine")


STATE_PROGRESS = {
    "STANDBY": 0,
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
    "CANCELLED": 0,
    "ORDER_RECEIVED": 25,
    "GENERATING_IMAGE": 65,
    "IMAGE_READY": 85,
    "NOTIFYING_MIRTH": 92,
}


class AnalyzerState(StrEnum):
    STANDBY = "STANDBY"
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
    CANCELLED = "CANCELLED"
    ORDER_RECEIVED = "ORDER_RECEIVED"
    GENERATING_IMAGE = "GENERATING_IMAGE"
    IMAGE_READY = "IMAGE_READY"
    NOTIFYING_MIRTH = "NOTIFYING_MIRTH"


class AnalyzerMode(StrEnum):
    MANUAL = "MANUAL"
    BIDIRECTIONAL = "BIDIRECTIONAL"


class SimulationEngine:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.store = SimulatorStore()
        if not self.store.list_catalogs():
            self.store.save_catalog({**validate_catalog(seed_catalog()), "id": "builtin-v1", "catalogId": "builtin",
                                     "version": 1, "createdAt": self._now()})
        self.analyzers = self.store.load_analyzers()
        for analyzer in self.analyzers.values():
            analyzer.setdefault("behaviorConfigured", analyzer.get("behavior") != normalize_behavior({}, analyzer.get("protocol")))
            analyzer["behavior"] = normalize_behavior(analyzer.get("behavior"), analyzer.get("protocol"))
            analyzer["listenerActive"] = False
            analyzer["listenerError"] = None
            analyzer["listenerHost"] = None
            self.store.save_analyzer(analyzer)
        if PACS_ANALYZER_ID not in self.analyzers:
            pacs_analyzer = self._configured_analyzer(default_pacs_analyzer())
            self.analyzers[PACS_ANALYZER_ID] = pacs_analyzer
            self.store.save_analyzer(pacs_analyzer)
        else:
            pacs_analyzer = self.analyzers[PACS_ANALYZER_ID]
            if os.environ.get("PACS_ORDER_PORT"):
                pacs_analyzer["listenPort"] = int(os.environ["PACS_ORDER_PORT"])
            if os.environ.get("PACS_NOTIFICATION_HOST"):
                pacs_analyzer["resultDestinationHost"] = os.environ["PACS_NOTIFICATION_HOST"]
            if os.environ.get("PACS_NOTIFICATION_PORT"):
                pacs_analyzer["resultDestinationPort"] = int(os.environ["PACS_NOTIFICATION_PORT"])
            pacs_analyzer["updatedAt"] = self._now()
            self.store.save_analyzer(pacs_analyzer)
        self.orders: list[dict] = self.store.load_orders()
        self.messages: list[dict] = self.store.load_messages()
        self._recover_interrupted()
        with self._atomic():
            for analyzer in self.analyzers.values():
                if analyzer["protocol"] == "HL7" and not self.communication_ready(analyzer):
                    self._set_state(analyzer["id"], AnalyzerState.STANDBY)
                    self.store.save_analyzer(analyzer)

    def communication_ready(self, analyzer) -> bool:
        if isinstance(analyzer, str):
            analyzer = self.analyzers[analyzer]
        if analyzer["protocol"] != "HL7":
            return True
        profile = self.store.get_active_protocol_profile(analyzer["id"])
        return profile_ready(profile)

    def _require_contract(self, analyzer_id):
        if not self.communication_ready(analyzer_id):
            raise ValueError("Equipo en STANDBY: complete y active el contrato HL7 de entrada y salida en el editor")

    @contextmanager
    def _atomic(self):
        """One local operation commits state, logs and receipts together."""
        with self.lock:
            nested = getattr(self.store._local, "connection", None) is not None
            snapshot = None if nested else copy.deepcopy((self.analyzers, self.orders, self.messages))
            try:
                with self.store.transaction():
                    yield
            except BaseException:
                if snapshot is not None:
                    self.analyzers, self.orders, self.messages = snapshot
                raise

    def _recover_interrupted(self):
        with self._atomic():
            for order in self.orders:
                if order.get("pendingOutbound"):
                    order["pendingOutbound"]["inFlight"] = False
                previous = order["status"]
                recovery = {"PROCESSING": "SAMPLE_LOADED", "SENDING_RESULT": "RESULT_READY",
                            "GENERATING_IMAGE": "ORDER_RECEIVED", "NOTIFYING_MIRTH": "NOTIFICATION_ERROR"}
                if previous in recovery:
                    order["status"] = recovery[previous]
                    order["recoveryNote"] = f"Interrupted {previous}; explicit retry required (delivery may be unknown)"
                    order["updatedAt"] = self._now()
                    self._set_state(order["analyzerId"], AnalyzerState.ERROR, order["recoveryNote"])
                    self.store.save_order(order)
                    self.store.save_analyzer(self.analyzers[order["analyzerId"]])

    def list_analyzers(self) -> list[dict]:
        with self.lock:
            return [{**item, "contractReady": self.communication_ready(item)} for item in self.analyzers.values()]

    def catalog_revision(self, revision_id):
        revision = next((r for r in self.store.list_catalogs() if r["id"] == revision_id), None)
        if revision is None:
            raise ValueError("Revisión de catálogo inexistente")
        return revision

    def save_catalog(self, payload, catalog_id=None):
        definition = validate_catalog(payload)
        with self._atomic():
            existing = [r for r in self.store.list_catalogs() if r["catalogId"] == catalog_id]
            if catalog_id and not existing:
                raise ValueError("Catálogo inexistente")
            revision = {**definition, "id": str(uuid.uuid4()), "catalogId": catalog_id or str(uuid.uuid4()),
                        "version": max((r["version"] for r in existing), default=0) + 1, "createdAt": self._now()}
            self.store.save_catalog(revision)
            return revision

    def assign_catalog(self, analyzer_id, revision_id, policy="BLOCK", template=None, configure_outbound=False):
        with self._atomic():
            analyzer = self.analyzers.get(analyzer_id)
            if not analyzer or analyzer.get("profile") in {"PACS", "ANAPATH"} or analyzer["protocol"] != "HL7":
                raise ValueError("Seleccione un analizador HL7 de laboratorio")
            revision = self.catalog_revision(revision_id)
            bind_studies(revision, [], policy, template)
            analyzer.update(catalogRevisionId=revision_id, unknownStudyPolicy=policy, genericStudyCode=template,
                            supportedTests=[alias for study in revision["studies"] for alias in [study["code"], *study.get("aliases", [])]])
            if configure_outbound:
                active = self.store.get_active_protocol_profile(analyzer_id)
                if not active:
                    raise ValueError("Configure primero el contrato HL7 de entrada en el Diseñador HL7")
                self._save_protocol_profile(analyzer_id, {"name": active["name"] + " / estudios dinámicos",
                    "inbound": active["inbound"], "outbound": {**dynamic_outbound()}, "activate": True})
            analyzer["updatedAt"] = self._now()
            self.store.save_analyzer(analyzer)
            return dict(analyzer)

    def _bind_order_catalog(self, order, analyzer):
        if analyzer.get("profile") in {"PACS", "ANAPATH"} or analyzer.get("protocol") != "HL7":
            return
        revision = self.catalog_revision(analyzer.get("catalogRevisionId") or "builtin-v1")
        groups = order.get("studies") or [
            {"key": f"study-{index}", "code": code, "name": "", "orderId": order.get("orderId"),
             "sampleId": order["sampleId"], "sample": {"id": order["sampleId"], "code": order.get("sampleCode"), "name": order.get("sampleName")}}
            for index, code in enumerate(order["requestedTests"], 1)]
        policy = analyzer.get("unknownStudyPolicy") or ("BLOCK" if analyzer.get("catalogRevisionId") else "TEMPLATE")
        order["studies"] = bind_studies(revision, groups, policy, analyzer.get("genericStudyCode") or "GENERIC")
        order["catalogRevisionId"] = revision["id"]
        order["catalogVersion"] = revision["version"]
        order["catalogFallbacks"] = [{"studyKey": s["key"], "code": s["code"], "templateCode": s["templateCode"]}
                                    for s in order["studies"] if s["fallbackUsed"]]

    def regenerate_results(self, order_id, scenario="NORMAL"):
        with self._atomic():
            order = self._find_order(order_id)
            if not order.get("studies") or not order["studies"][0].get("definition"):
                raise ValueError("La orden no tiene definiciones de catálogo")
            if order["status"] not in {"RESULT_READY", "COMPLETED", "ERROR"} or not order.get("results"):
                raise ValueError("Procese primero la orden antes de regenerar")
            if order.get("pendingOutbound", {}).get("inFlight"):
                raise ValueError("Envío en curso")
            self._generate_order_results(order, scenario)
            order.pop("pendingOutbound", None)
            order["status"] = "RESULT_READY"
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState.RESULT_READY, current_sample=order["sampleId"])
            self.store.save_analyzer(self.analyzers[order["analyzerId"]])
            self.store.save_order(order)
            return dict(order)

    def _generate_order_results(self, order, scenario):
        available = {"NORMAL", *[name for study in order["studies"] for name in study["definition"].get("scenarios", {})]}
        if scenario not in available:
            raise ValueError(f"Escenario no definido en el catálogo: {scenario}")
        previous = order.get("results")
        if previous:
            order.setdefault("resultHistory", []).append({"generation": order.get("generation", 1),
                "scenario": order.get("resultScenario"), "results": copy.deepcopy(previous), "archivedAt": self._now()})
        order["results"] = generate_results(order["studies"], scenario, self.store.next_generator_sequence)
        order["generation"] = order.get("generation", 0) + 1
        order["resultScenario"] = scenario

    def preview_order_results(self, order_id):
        with self.lock:
            order = self._find_order(order_id)
            if not order.get("results"):
                raise ValueError("Procese la orden para previsualizar los resultados")
            analyzer = self.analyzers[order["analyzerId"]]
            active = self.store.get_active_protocol_profile(analyzer["id"])
            if (active or {}).get("outbound", {}).get("events"):
                message, control_id = build_profile_message(active, analyzer, order, order["results"], "RESULT_FINAL", 0)
            else:
                message, control_id = build_hl7_oru(analyzer, order, order["results"], 0)
                message = declare_utf8(message)
            validate_outbound(message, active, "RESULT_FINAL", control_id)
            return {"results": order["results"], "studies": order.get("studies", []), "message": message,
                    "catalogRevisionId": order.get("catalogRevisionId"), "generation": order.get("generation")}

    def list_orders(self) -> list[dict]:
        with self.lock:
            return list(self.orders)

    def list_messages(self) -> list[dict]:
        with self.lock:
            return list(reversed(self.messages[-100:]))

    def list_protocol_profiles(self, analyzer_id: str) -> list[dict]:
        if analyzer_id not in self.analyzers:
            raise ValueError("Unknown analyzer")
        return self.store.list_protocol_profiles(analyzer_id)

    def save_protocol_profile(self, analyzer_id: str, payload: dict) -> dict:
        with self._atomic():
            return self._save_protocol_profile(analyzer_id, payload)

    def _save_protocol_profile(self, analyzer_id: str, payload: dict) -> dict:
        if analyzer_id not in self.analyzers:
            raise ValueError("Unknown analyzer")
        payload = validate_profile(payload)
        inbound = dict(payload.get("inbound") or {})
        schema = inbound.get("schema") or {}
        if schema and not isinstance(schema, dict):
            raise ValueError("El contrato Inbound debe ser un objeto JSON")
        if schema and int(schema.get("formatVersion") or 1) >= 2:
            segments = schema.get("segments")
            if not isinstance(segments, list) or not segments or segments[0].get("name") != "MSH":
                raise ValueError("El contrato estructural debe contener MSH como primer segmento")
            schema_message_type = str(schema.get("messageType") or "").strip()
            if not schema_message_type or "^" not in schema_message_type:
                raise ValueError("El contrato debe definir messageType, por ejemplo OML^O21 u ORU^R01")
            inbound["messageType"] = schema_message_type
            payload["inbound"] = inbound
        now = self._now()
        existing = self.store.list_protocol_profiles(analyzer_id)
        name = payload.get("name") or "Perfil sin nombre"
        version = max((item["version"] for item in existing), default=0) + 1
        profile = {
            **payload,
            "id": str(uuid.uuid4()),
            "analyzerId": analyzer_id,
            "name": name,
            "version": version,
            "status": "ACTIVE" if payload.get("activate", True) and profile_ready(payload) else "DRAFT",
            "createdAt": now,
            "updatedAt": now,
        }
        self.store.save_protocol_profile(profile)
        if profile["status"] == "ACTIVE" and self.analyzers[analyzer_id]["state"] == AnalyzerState.STANDBY:
            self._set_state(analyzer_id, AnalyzerState.IDLE)
            self.store.save_analyzer(self.analyzers[analyzer_id])
        return profile

    def update_protocol_profile(self, analyzer_id: str, profile_id: str, payload: dict) -> dict:
        with self._atomic():
            return self._revise_protocol_profile(analyzer_id, profile_id, payload)

    def _revise_protocol_profile(self, analyzer_id: str, profile_id: str, payload: dict) -> dict:
        if analyzer_id not in self.analyzers:
            raise ValueError("Unknown analyzer")
        existing = next(
            (item for item in self.store.list_protocol_profiles(analyzer_id) if item.get("id") == profile_id),
            None,
        )
        if existing is None:
            raise ValueError("Protocol profile not found")
        # Revisions are immutable. The old contract remains available for orders
        # and pending transmissions which already reference its ID.
        return self._save_protocol_profile(analyzer_id, {
            **payload, "revisionOf": existing["id"],
            "activate": payload.get("activate", existing["status"] == "ACTIVE"),
        })

    def delete_protocol_profile(self, analyzer_id: str, profile_id: str) -> None:
        if analyzer_id not in self.analyzers:
            raise ValueError("Unknown analyzer")
        with self._atomic():
            profile = next((p for p in self.store.list_protocol_profiles(analyzer_id) if p["id"] == profile_id), None)
            if profile is None:
                raise ValueError("Protocol profile not found")
            self.store.delete_protocol_profile(analyzer_id, profile_id)
            if not self.communication_ready(analyzer_id):
                self._set_state(analyzer_id, AnalyzerState.STANDBY)
                self.store.save_analyzer(self.analyzers[analyzer_id])

    def activate_protocol_profile(self, analyzer_id: str, profile_id: str) -> dict:
        if analyzer_id not in self.analyzers:
            raise ValueError("Unknown analyzer")
        profile = next(
            (item for item in self.store.list_protocol_profiles(analyzer_id) if item.get("id") == profile_id),
            None,
        )
        if profile is None:
            raise ValueError("Protocol profile not found")
        validate_profile(profile)
        if not profile_ready(profile):
            raise ValueError("Complete both inbound and outbound contracts before activation")
        with self._atomic():
            profile["status"] = "ACTIVE"
            profile["updatedAt"] = self._now()
            self.store.save_protocol_profile(profile)
            if self.analyzers[analyzer_id]["state"] == AnalyzerState.STANDBY:
                self._set_state(analyzer_id, AnalyzerState.IDLE)
                self.store.save_analyzer(self.analyzers[analyzer_id])
        return profile

    def preview_protocol_profile(self, analyzer_id: str, payload: dict) -> dict:
        if analyzer_id not in self.analyzers:
            raise ValueError("Unknown analyzer")
        payload = validate_profile(payload)
        sample = {
            "sourceRawMessage": (payload.get("inbound") or {}).get("rawHL7"),
            "patientId": "P-TEST-001", "patientName": "PACIENTE^PRUEBA",
            "orderId": "ORD-TEST-001", "fillerOrderId": "FILLER-001",
            "sampleId": "SMP-TEST-001", "sampleCode": "SMP-TEST-001",
            "sampleName": "MUESTRA DE PRUEBA", "procedureCode": "TEST-001",
            "procedureName": "PRUEBA SIMULADA", "orderStatus": "CM",
        }
        results = [{"code": "TEST-001", "name": "Prueba simulada", "value": 3.97, "unit": "g/dL", "referenceRange": "3.5 - 5.0", "abnormalFlag": "N"}]
        segments = ((payload.get("outbound") or {}).get("events", {}).get("RESULT_FINAL") or {}).get("segments", [])
        if any(s.get("repeatBy") == "studies" for s in segments):
            revision = self.catalog_revision(self.analyzers[analyzer_id].get("catalogRevisionId") or "builtin-v1")
            sample["studies"] = bind_studies(revision, [{"key": str(i), "code": s["code"], "name": s["name"],
                "orderId": sample["orderId"], "sampleId": sample["sampleId"]} for i, s in enumerate(revision["studies"])])
            results = generate_results(sample["studies"])
        message, control_id = build_profile_message(payload, self.analyzers[analyzer_id], sample, results, "RESULT_FINAL", self.store.next_sequence())
        return {"message": message, "controlId": control_id}

    def infer_protocol_contract(self, analyzer_id: str, raw_message: str) -> dict:
        if analyzer_id not in self.analyzers:
            raise ValueError("Unknown analyzer")
        contract = infer_hl7_contract(raw_message)
        return {
            "analyzerId": analyzer_id,
            "messageType": contract.get("messageType"),
            "schema": contract,
            "preview": contract.get("preview"),
        }

    def create_manual_sample(self, payload: dict) -> dict:
        with self._atomic():
            return self._create_manual_sample(payload)

    def _create_manual_sample(self, payload: dict) -> dict:
        analyzer = self.analyzers.get(payload["analyzerId"])
        if analyzer is None:
            raise ValueError("Unknown analyzer")
        if analyzer["mode"] != AnalyzerMode.MANUAL:
            raise ValueError("Manual sample creation is disabled while analyzer is in BIDIRECTIONAL mode")
        if analyzer.get("profile") != "ANAPATH" and not analyzer.get("catalogRevisionId"):
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
            "sampleCode": payload.get("barcode") or payload["sampleId"],
            "sampleName": payload.get("sampleName") or "MUESTRA SIMULADA",
            "sampleCount": "1",
            "patientId": payload.get("patientId") or payload["sampleId"],
            "patientName": payload.get("patientName") or "SIMULATED^PATIENT",
            "visitType": payload.get("visitType") or "O",
            "visitNumber": payload.get("visitNumber"),
            "priority": payload.get("priority", "ROUTINE"),
            "analyzerId": payload["analyzerId"],
            "requestedTests": payload["tests"],
            "panelCode": payload["tests"][0],
            "panelName": "MANUAL PANEL",
            "procedureCode": payload["tests"][0],
            "procedureName": "MANUAL PANEL",
            "orderId": payload.get("orderId") or payload["sampleId"],
            "fillerOrderId": payload.get("fillerOrderId") or payload["sampleId"],
            "prescriptionId": payload.get("prescriptionId"),
            "requestedAt": payload.get("requestedAt") or self._hl7_timestamp(),
            "clinicalInfo": payload.get("clinicalInfo"),
            "observations": payload.get("observations") or [],
            "topographyCode": payload.get("topographyCode"),
            "topographyName": payload.get("topographyName"),
            "laterality": payload.get("laterality"),
            "rackPosition": self._rack_position(),
            "createdAt": self._now(),
            "updatedAt": self._now(),
            "results": [],
        }
        with self._atomic():
            self._bind_order_catalog(order, analyzer)
            self.orders.append(order)
            self._set_state(payload["analyzerId"], AnalyzerState.WAITING_SAMPLE)
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[payload["analyzerId"]])
        return order

    def receive_hl7_order(self, raw_message: str, analyzer_id: str, source_ip: str) -> str:
        metadata = extract_metadata(raw_message)
        identity = json.dumps([metadata.get(key) for key in ("sendingApplication", "sendingFacility", "messageControlId")])
        normalized = raw_message.replace("\r\n", "\r").replace("\n", "\r").strip("\r")
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        try:
            with self._atomic():
                self._require_contract(analyzer_id)
                receipt = self.store.get_receipt(analyzer_id, "HL7", identity)
                if receipt:
                    if receipt[0] != digest:
                        raise HL7OrderError("Message control ID reused with different content; use a new MSH-10")
                    self._log_inbound_hl7(analyzer_id, None, source_ip, raw_message,
                                         (self.analyzers[analyzer_id].get("behavior") or {}).get("ackSuccessCode", "AA"),
                                         receipt[1], None, metadata["messageControlId"], metadata["messageType"])
                    return receipt[1]
                ack = self._receive_hl7_order(raw_message, analyzer_id, source_ip)
                last = self.messages[-1] if self.messages else {}
                if not last.get("error") and metadata.get("messageControlId"):
                    self.store.save_receipt(analyzer_id, "HL7", identity, digest, ack)
                return ack
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            LOGGER.warning("Inbound HL7 rejected for %s: %s", analyzer_id, error)
            ack = build_ack(metadata, "AE", str(error))
            try:
                with self._atomic():
                    self._log_inbound_hl7(analyzer_id, None, source_ip, raw_message, "AE", ack,
                                         str(error), metadata.get("messageControlId"), metadata.get("messageType"))
            except sqlite3.Error:
                LOGGER.exception("Cannot persist rejected HL7 message for %s", analyzer_id)
            return ack

    def _receive_hl7_order(self, raw_message: str, analyzer_id: str, source_ip: str) -> str:
        metadata = extract_metadata(raw_message)
        try:
            analyzer = self.analyzers.get(analyzer_id)
            if analyzer is None:
                raise HL7OrderError("Unknown analyzer listener")
            if analyzer["mode"] != AnalyzerMode.BIDIRECTIONAL:
                raise HL7OrderError("Analyzer is not in BIDIRECTIONAL mode")
            self._ensure_operational(analyzer_id)
            behavior = normalize_behavior(analyzer.get("behavior"), analyzer.get("protocol"))
            accepted_types = set(behavior.get("acceptedMessageTypes") or [])
            active_profile = self.store.get_active_protocol_profile(analyzer_id)
            profile_inbound = (active_profile or {}).get("inbound") or {}
            contract_warnings: list[str] = []
            
            strict_profile = profile_inbound.get("validationMode", "STRICT" if profile_inbound.get("strictValidation") else "WARN") == "STRICT"
            if profile_inbound.get("schema"):
                schema = profile_inbound["schema"]
                if int(schema.get("formatVersion") or 1) >= 2:
                    contract_warnings = hl7_contract_issues(raw_message, schema)
                else:
                    from .profile_parser import validate_strict_profile
                    try:
                        validate_strict_profile(raw_message, schema)
                    except ValueError as warning:
                        contract_warnings = [str(warning)]
                if strict_profile and contract_warnings:
                    raise HL7OrderError("Contrato HL7 incumplido: " + "; ".join(contract_warnings))
            
            order_payload = (
                parse_with_profile(raw_message, profile_inbound)
                if profile_inbound.get("mappings") or profile_inbound.get("messageType")
                else parse_order(raw_message, accepted_types or None, behavior.get("inboundMappings"))
            )
            # Generic devices defined by the editor use that contract as their
            # message definition. Explicit device behavior still adds its rules.
            if not profile_inbound.get("schema") or analyzer.get("behaviorConfigured"):
                validate_hl7_behavior(raw_message, order_payload, behavior)
            if order_payload["orderControl"] not in set(behavior.get("allowedOrderControls") or ["NW", "CA"]):
                raise HL7OrderError(f"Unsupported ORC-1 order control: {order_payload['orderControl']}")

            if analyzer.get("profile") == "ANAPATH":
                validate_anapath_order(order_payload)
            elif analyzer.get("profile") != "PACS" and not analyzer.get("catalogRevisionId"):
                unsupported = [
                    test
                    for test in order_payload["requestedTests"]
                    if analyzer["supportedTests"] and test not in analyzer["supportedTests"]
                ]
                if unsupported:
                    raise HL7OrderError("Unsupported tests for analyzer: " + ", ".join(unsupported))

            if order_payload["orderControl"] == "CA":
                canceled = self._cancel_order(analyzer_id, order_payload)
                ack_code = behavior.get("ackSuccessCode") or "AA"
                ack = build_ack(metadata, ack_code, warnings=contract_warnings)
                self._log_inbound_hl7(analyzer_id, canceled.get("sampleId"), source_ip, raw_message, ack_code, ack, None, order_payload["messageControlId"], order_payload["messageType"], warnings=contract_warnings)
                return ack

            if order_payload["orderControl"] == "XO":
                existing = next((o for o in self.orders if o["analyzerId"] == analyzer_id and o["orderId"] == order_payload["orderId"]), None)
                if not existing or existing["status"] not in {"WAITING_SAMPLE", "SAMPLE_LOADED", "ORDER_RECEIVED"}:
                    raise HL7OrderError("Order update requires an existing unprocessed order")
                if existing["barcode"] != order_payload["barcode"] or existing["patientId"] != order_payload["patientId"]:
                    raise HL7OrderError("Order update cannot change sample or patient identity")
                replacement = self._order_from_payload(order_payload, analyzer_id, "BIDIRECTIONAL")
                self._bind_order_catalog(replacement, analyzer)
                replacement["sourceRawMessage"] = raw_message
                replacement.update({key: existing[key] for key in ("id", "status", "createdAt", "rackPosition")})
                if active_profile:
                    replacement.update(inboundProfileId=active_profile["id"], inboundProfileVersion=active_profile["version"])
                existing.update(replacement)
                self.store.save_order(existing)
                ack_code = behavior.get("ackSuccessCode") or "AA"
                ack = build_ack(metadata, ack_code, warnings=contract_warnings)
                self._log_inbound_hl7(analyzer_id, existing["sampleId"], source_ip, raw_message, ack_code, ack, None,
                                     order_payload["messageControlId"], order_payload["messageType"], warnings=contract_warnings)
                return ack

            if order_payload["orderControl"] not in {"NW"}:
                raise HL7OrderError(f"Unsupported ORC-1 order control: {order_payload['orderControl']}")
            if analyzer.get("profile") == "PACS":
                account_no = order_payload.get("patientId")
                accession_no = order_payload.get("obr3")
                if self._pacs_accession_exists(accession_no):
                    raise HL7OrderError(f"accession number duplicado: {accession_no}")
            elif self._barcode_exists(analyzer_id, order_payload["barcode"]):
                raise HL7OrderError("muestra duplicada")

            order = self._order_from_payload(order_payload, analyzer_id, "BIDIRECTIONAL")
            self._bind_order_catalog(order, analyzer)
            order["sourceRawMessage"] = raw_message
            if active_profile:
                order["inboundProfileId"] = active_profile["id"]
                order["inboundProfileVersion"] = active_profile["version"]
            if analyzer.get("profile") == "PACS":
                order = decorate_pacs_order(order, raw_message)
            ack_text = self._ack_success_text(behavior)
            ack_code = behavior.get("ackSuccessCode") or "AA"
            ack = build_ack(metadata, ack_code, ack_text=ack_text, warnings=contract_warnings)
            with self.lock:
                self.orders.append(order)
                next_state = AnalyzerState.ORDER_RECEIVED if analyzer.get("profile") == "PACS" else AnalyzerState.WAITING_SAMPLE
                self._set_state(analyzer_id, next_state, current_sample=order["sampleId"])
                self.store.save_order(order)
                self.store.save_analyzer(self.analyzers[analyzer_id])
            self._log_inbound_hl7(analyzer_id, order["sampleId"], source_ip, raw_message, ack_code, ack, None, order_payload["messageControlId"], order_payload["messageType"], warnings=contract_warnings)
            return ack
        except (ValueError, TypeError, KeyError) as error:
            analyzer = self.analyzers.get(analyzer_id)
            behavior = normalize_behavior(analyzer.get("behavior"), analyzer.get("protocol")) if analyzer else {}
            ack_error_code = behavior.get("ackErrorCode") or "AE"
            ack = build_ack(metadata, ack_error_code, str(error))
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
                        ack_code=ack_error_code,
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
        digest = hashlib.sha256(raw_message.encode("utf-8")).hexdigest()
        try:
            with self._atomic():
                # The simplified variant has no message control ID. Its receipt
                # identity is the complete payload digest, never the order ID.
                receipt = self.store.get_receipt(analyzer_id, "ASTM", digest)
                if receipt:
                    self._append_message(self._message_record(
                        direction="IN", protocol="ASTM", message_type="ASTM_ORDER", analyzer_id=analyzer_id,
                        sample_id=None, source=source_ip, raw_message=raw_message, ack_code="ACK", ack_message="ACK",
                        error=None, message_control_id=None))
                    return True
                accepted = self._receive_astm_order(raw_message, analyzer_id, source_ip)
                if accepted:
                    self.store.save_receipt(analyzer_id, "ASTM", digest, digest, "ACK")
                return accepted
        except (ValueError, sqlite3.Error) as error:
            LOGGER.warning("ASTM rejected for %s: %s", analyzer_id, error)
            return False

    def _receive_astm_order(self, raw_message: str, analyzer_id: str, source_ip: str) -> bool:
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
        with self._atomic():
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
        with self._atomic():
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
        with self._atomic():
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
        with self._atomic():
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            if normalized != "STANDBY":
                self._require_contract(analyzer_id)
            self._set_state(analyzer_id, AnalyzerState(normalized), None)
            self.store.save_analyzer(self.analyzers[analyzer_id])
            return dict(self.analyzers[analyzer_id])

    def set_analyzer_scenario(self, analyzer_id: str, scenario: str) -> dict:
        normalized = scenario.upper()
        default_allowed = {
            "NORMAL",
            "FORCE_CRITICAL",
            "SAMPLE_ERROR",
            "NO_RESPONSE",
            "MALFORMED_RESULT",
            "REPORT_SUPPLEMENTARY",
            "REPORT_CANCELLED",
        }
        analyzer = self.analyzers.get(analyzer_id)
        behavior = normalize_behavior(analyzer.get("behavior"), analyzer.get("protocol")) if analyzer else {}
        allowed = default_allowed | set(behavior.get("allowedScenarios") or [])
        if normalized not in allowed:
            raise ValueError("Invalid analyzer scenario")
        with self._atomic():
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            analyzer = self.analyzers[analyzer_id]
            analyzer["scenario"] = normalized
            analyzer["updatedAt"] = self._now()
            self.store.save_analyzer(analyzer)
            return dict(analyzer)

    def create_analyzer(self, payload: dict) -> dict:
        analyzer_id = payload["id"]
        with self._atomic():
            if analyzer_id in self.analyzers:
                raise ValueError("Analyzer already exists")
            analyzer = self._configured_analyzer(payload)
            if analyzer["protocol"] == "HL7" and not self.communication_ready(analyzer):
                analyzer["state"] = AnalyzerState.STANDBY
            self.analyzers[analyzer_id] = analyzer
            self.store.save_analyzer(analyzer)
            return dict(analyzer)

    def update_analyzer(self, analyzer_id: str, payload: dict) -> dict:
        with self._atomic():
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            current = dict(self.analyzers[analyzer_id])
            current.update({key: value for key, value in payload.items() if value is not None})
            if "behavior" in payload:
                current["behaviorConfigured"] = bool(payload["behavior"])
            current["id"] = analyzer_id
            current = self._configured_analyzer(current, existing=current)
            if current["protocol"] == "HL7" and not self.communication_ready(current):
                current["state"] = AnalyzerState.STANDBY
            elif current["state"] == AnalyzerState.STANDBY:
                current["state"] = AnalyzerState.IDLE
            self.analyzers[analyzer_id] = current
            self.store.save_analyzer(current)
            return dict(current)

    def process_order(self, order_id: str, send_result: bool = True, result_scenario: str | None = None) -> dict:
        with self.lock:
            candidate = self._find_order(order_id)
            self._ensure_operational(candidate["analyzerId"])
            if self.analyzers[candidate["analyzerId"]].get("scenario") == "SAMPLE_ERROR":
                with self._atomic():
                    candidate["status"] = "ERROR"
                    candidate["updatedAt"] = self._now()
                    self._set_state(candidate["analyzerId"], AnalyzerState.SAMPLE_ERROR, "sample processing error", candidate["sampleId"])
                    self.store.save_order(candidate)
                    self.store.save_analyzer(self.analyzers[candidate["analyzerId"]])
                raise ValueError("sample processing error")
        with self._atomic():
            order = self._find_order(order_id)
            self._ensure_operational(order["analyzerId"])
            if order["status"] != "SAMPLE_LOADED":
                raise ValueError("Order is not ready to process")
            if result_scenario is not None and order.get("studies"):
                allowed = {"NORMAL", *[name for study in order["studies"] for name in study["definition"].get("scenarios", {})]}
                if result_scenario not in allowed:
                    raise ValueError(f"Escenario no definido: {result_scenario}")
            order["status"] = "PROCESSING"
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState.PROCESSING, current_sample=order["sampleId"])
            self.store.save_order(order)
            self.store.save_analyzer(self.analyzers[order["analyzerId"]])

        time.sleep(0.5)

        with self._atomic():
            order = self._find_order(order_id)
            force_critical = self.analyzers[order["analyzerId"]].get("scenario") == "FORCE_CRITICAL"
            if order.get("studies") and order["studies"][0].get("definition"):
                self._generate_order_results(order, result_scenario or "NORMAL")
            else:
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
        return self._send_event_checked(order_id, "REPORT", destination_host, destination_port)

    def emit_order_event(self, order_id: str, event_type: str, overrides: dict | None = None) -> dict:
        return self._send_event_checked(order_id, event_type, overrides=overrides)

    def _send_event_checked(self, order_id, event_type, destination_host=None, destination_port=None, overrides=None):
        try:
            return self._send_event(order_id, event_type, destination_host, destination_port, overrides)
        except ValueError as error:
            with self._atomic():
                order = self._find_order(order_id)
                self._append_message(self._message_record(
                    direction="OUT", protocol=self.analyzers[order["analyzerId"]]["protocol"],
                    message_type=event_type, analyzer_id=order["analyzerId"], sample_id=order["sampleId"],
                    source="configuration-validation", raw_message="", ack_code=None, ack_message=None,
                    error=str(error), message_control_id=None))
            raise

    def list_pacs_studies(self) -> list[dict]:
        with self.lock:
            return [dict(order) for order in reversed(self.orders) if order.get("deviceType") == "PACS"]

    def get_pacs_study(self, study_id: str) -> dict:
        with self.lock:
            order = self._find_order(study_id)
            if order.get("deviceType") != "PACS":
                raise ValueError("PACS study not found")
            return dict(order)

    def find_pacs_study(self, account_no: str, accession_no: str) -> dict:
        normalized_account_no = self._pacs_account_number(account_no)
        with self.lock:
            for order in reversed(self.orders):
                if order.get("deviceType") != "PACS":
                    continue
                stored_account_no = self._pacs_account_number(order.get("accountNo"))
                if stored_account_no == normalized_account_no and order.get("accessionNo") == accession_no:
                    return dict(order)
        raise ValueError("PACS study not found for account_no and accession_no")

    def process_pacs_study(self, study_id: str, send_notification: bool = False) -> dict:
        with self.lock:
            candidate = self._find_order(study_id)
            self._ensure_operational(candidate["analyzerId"])
            if candidate.get("deviceType") == "PACS" and candidate["status"] in {"ORDER_RECEIVED", "IMAGE_GENERATION_ERROR"} and self.analyzers[candidate["analyzerId"]].get("scenario") == "IMAGE_GENERATION_ERROR":
                with self._atomic():
                    candidate["status"] = "IMAGE_GENERATION_ERROR"
                    candidate["updatedAt"] = self._now()
                    self._set_state(candidate["analyzerId"], AnalyzerState.ERROR, "simulated image generation error", candidate["accessionNo"])
                    self.store.save_order(candidate)
                    self.store.save_analyzer(self.analyzers[candidate["analyzerId"]])
                raise ValueError("simulated image generation error")
        with self._atomic():
            study = self._find_order(study_id)
            if study.get("deviceType") != "PACS":
                raise ValueError("PACS study not found")
            analyzer = self.analyzers[study["analyzerId"]]
            self._ensure_operational(study["analyzerId"])
            if study["status"] not in {"ORDER_RECEIVED", "IMAGE_GENERATION_ERROR"}:
                raise ValueError("Study is not ready for image generation")
            study["status"] = "GENERATING_IMAGE"
            study["updatedAt"] = self._now()
            self._set_state(study["analyzerId"], AnalyzerState.GENERATING_IMAGE, current_sample=study["accessionNo"])
            self.store.save_order(study)
            self.store.save_analyzer(self.analyzers[study["analyzerId"]])

        time.sleep(0.25)

        with self._atomic():
            study = self._find_order(study_id)
            study["status"] = "IMAGE_READY"
            study["imageReadyAt"] = self._now()
            study["updatedAt"] = study["imageReadyAt"]
            self._set_state(study["analyzerId"], AnalyzerState.IMAGE_READY, current_sample=study["accessionNo"])
            self.store.save_order(study)
            self.store.save_analyzer(self.analyzers[study["analyzerId"]])

        if send_notification:
            return self.notify_pacs_study(study_id)
        return dict(study)

    def notify_pacs_study(self, study_id: str) -> dict:
        with self._atomic():
            study = self._find_order(study_id)
            self._require_contract(study["analyzerId"])
            if study.get("deviceType") != "PACS":
                raise ValueError("PACS study not found")
            if study["status"] not in {"IMAGE_READY", "NOTIFICATION_ERROR", "COMPLETED"}:
                raise ValueError("Image is not ready")
            analyzer = dict(self.analyzers[study["analyzerId"]])
            host = analyzer["resultDestinationHost"]
            port = int(analyzer["resultDestinationPort"])
            pending = study.get("pendingNotification")
            active_profile = self.store.get_active_protocol_profile(analyzer["id"])
            if pending and pending.get("profileId") == (active_profile or {}).get("id"):
                message, control_id = pending["message"], pending["controlId"]
                profile_id, profile_version = pending.get("profileId"), pending.get("profileVersion")
            else:
                sequence = self.store.next_sequence()
                if ((active_profile or {}).get("outbound") or {}).get("events"):
                    message, control_id = build_profile_message(active_profile, analyzer, study, [], "IMAGE_READY", sequence)
                else:
                    message, control_id = build_image_ready_oru(analyzer, study, sequence)
                if not ((active_profile or {}).get("outbound") or {}).get("events"):
                    message = declare_utf8(message)
                validate_outbound(message, active_profile, "IMAGE_READY", control_id)
                profile_id, profile_version = (active_profile or {}).get("id"), (active_profile or {}).get("version")
            study["pendingNotification"] = {"message": message, "controlId": control_id,
                "profileId": profile_id, "profileVersion": profile_version}
            study["status"] = "NOTIFYING_MIRTH"
            study["updatedAt"] = self._now()
            self._set_state(study["analyzerId"], AnalyzerState.NOTIFYING_MIRTH, current_sample=study["accessionNo"])
            self.store.save_order(study)
            self.store.save_analyzer(self.analyzers[study["analyzerId"]])

        try:
            if analyzer.get("scenario") == "NO_RESPONSE":
                raise TimeoutError("simulated no response from Mirth")
            ack, code = send_mllp(message, host, port)
            status = "COMPLETED" if code == "AA" else "NOTIFICATION_ERROR"
            error = None if code == "AA" else f"ACK no positivo: {code or 'MISSING'}"
        except (OSError, TimeoutError, ValueError) as exc:
            ack = None
            code = None
            status = "NOTIFICATION_ERROR"
            error = str(exc)

        with self._atomic():
            study = self._find_order(study_id)
            study["status"] = status
            if status == "COMPLETED":
                study.pop("pendingNotification", None)
            study["lastNotificationAt"] = self._now()
            study["messageControlId"] = control_id
            study["updatedAt"] = study["lastNotificationAt"]
            analyzer_state = AnalyzerState.COMPLETED if status == "COMPLETED" else AnalyzerState.ERROR
            self._set_state(study["analyzerId"], analyzer_state, error, study["accessionNo"])
            self.analyzers[study["analyzerId"]]["lastAckCode"] = code
            record = {
                "id": str(uuid.uuid4()),
                "direction": "OUT",
                "protocol": "HL7",
                "messageType": "ORU^R01",
                "analyzerId": study["analyzerId"],
                "sampleId": study["accessionNo"],
                "destination": f"{host}:{port}",
                "messageControlId": control_id,
                "profileId": profile_id, "profileVersion": profile_version,
                "rawMessage": message,
                "ackCode": code,
                "ackMessage": ack,
                "error": error,
                "createdAt": self._now(),
            }
            self.store.save_order(study)
            self.store.save_analyzer(self.analyzers[study["analyzerId"]])
            self._append_message(record)
            return {**dict(study), "lastMessage": record}

    def clear_analyzer_data(self, analyzer_id: str) -> dict:
        with self._atomic():
            if analyzer_id not in self.analyzers:
                raise ValueError("Unknown analyzer")
            if any(order["analyzerId"] == analyzer_id and (order["status"] in {"PROCESSING", "GENERATING_IMAGE", "NOTIFYING_MIRTH"} or (order.get("pendingOutbound") or {}).get("inFlight")) for order in self.orders):
                raise ValueError("Cannot clear data while an operation is in progress")
            self.orders = [order for order in self.orders if order["analyzerId"] != analyzer_id]
            self.messages = [message for message in self.messages if message.get("analyzerId") != analyzer_id]
            analyzer = self.analyzers[analyzer_id]
            analyzer["lastAckCode"] = None
            analyzer["lastError"] = None
            analyzer["currentSampleId"] = None
            analyzer["state"] = AnalyzerState.IDLE if self.communication_ready(analyzer) else AnalyzerState.STANDBY
            analyzer["progress"] = 0
            analyzer["updatedAt"] = self._now()
            self.store.delete_orders_by_analyzer(analyzer_id)
            self.store.delete_messages_by_analyzer(analyzer_id)
            self.store.clear_receipts(analyzer_id)
            self.store.save_analyzer(analyzer)
            return {
                "analyzerId": analyzer_id,
                "clearedOrders": True,
                "clearedMessages": True,
            }

    def dashboard(self) -> dict:
        with self.lock:
            active = [item for item in self.analyzers.values() if item["state"] not in {AnalyzerState.OFFLINE, AnalyzerState.MAINTENANCE, AnalyzerState.STANDBY}]
            listeners = [item for item in self.analyzers.values() if item.get("listenerActive")]
            pending = [item for item in self.orders if item["status"] in {"WAITING_SAMPLE", "SAMPLE_LOADED", "PROCESSING", "RESULT_READY", "ORDER_RECEIVED", "GENERATING_IMAGE", "IMAGE_READY", "NOTIFICATION_ERROR"}]
            errors = [item for item in self.analyzers.values() if item["state"] == AnalyzerState.ERROR]
            return {
                "analyzerCount": len(self.analyzers),
                "activeAnalyzerCount": len(active),
                "activeListenerCount": len(listeners),
                "pendingOrderCount": len(pending),
                "messageCount": len(self.messages),
                "errorCount": len(errors),
            }

    def _send_event(
        self,
        order_id: str,
        event_type: str,
        destination_host: str | None = None,
        destination_port: int | None = None,
        overrides: dict | None = None,
    ) -> dict:
        with self._atomic():
            order = self._find_order(order_id)
            self._require_contract(order["analyzerId"])
            if event_type == "REPORT" and order["status"] not in {"RESULT_READY", "COMPLETED", "ERROR"}:
                raise ValueError("Process the pending order before sending its results")
            self._ensure_operational(order["analyzerId"])
            analyzer = dict(self.analyzers[order["analyzerId"]])
            behavior = normalize_behavior(analyzer.get("behavior"), analyzer.get("protocol"))
            configured_events = set(behavior.get("outboundEvents") or [])
            normalized_event = overrides.get("eventType") if overrides and overrides.get("eventType") else event_type
            if configured_events and normalized_event not in configured_events:
                raise ValueError(f"Outbound event not enabled for analyzer: {normalized_event}")
            host = destination_host or analyzer["resultDestinationHost"]
            port = destination_port or analyzer["resultDestinationPort"]
            pending = order.get("pendingOutbound")
            active_profile = self.store.get_active_protocol_profile(analyzer["id"])
            if pending and pending.get("inFlight"):
                raise ValueError("An outbound operation is already in progress for this order")
            if pending and pending["event"] == event_type and pending["destination"] == f"{host}:{port}" and pending.get("profileId") == (active_profile or {}).get("id"):
                message, control_id, message_type = pending["message"], pending["controlId"], pending["messageType"]
                profile_id, profile_version = pending.get("profileId"), pending.get("profileVersion")
            else:
                sequence = self.store.next_sequence()
                active_profile = self.store.get_active_protocol_profile(analyzer["id"])
                if not ((active_profile or {}).get("outbound") or {}).get("events") and event_type != "REPORT" and analyzer.get("profile") != "ANAPATH":
                    raise ValueError("This event needs an explicit outbound profile definition")
                if analyzer["protocol"] == "HL7" and (active_profile or {}).get("outbound", {}).get("events"):
                    profile_event = "RESULT_FINAL" if event_type == "REPORT" else event_type
                    message, control_id = build_profile_message(active_profile, analyzer, order, order.get("results") or [], profile_event, sequence)
                    message_type = ((active_profile.get("outbound") or {}).get("events") or {}).get(profile_event, {}).get("messageType", "HL7_PROFILE")
                elif analyzer["protocol"] == "HL7" and analyzer.get("profile") == "ANAPATH":
                    message, control_id = build_anapath_event(analyzer, order, event_type, sequence, overrides)
                    message_type = "ORU^R01" if event_type == "REPORT" else "OML^O21"
                elif analyzer["protocol"] == "HL7":
                    message, control_id = build_hl7_oru(analyzer, order, order.get("results") or [], sequence)
                    message_type = "ORU^R01"
                elif analyzer["protocol"] == "ASTM":
                    message, control_id = build_astm_result(analyzer, order, order.get("results") or [], sequence)
                    message_type = "ASTM_RESULT"
                else:
                    raise ValueError("Unsupported analyzer protocol")
                profile_event = "RESULT_FINAL" if event_type == "REPORT" else event_type
                if analyzer["protocol"] == "HL7":
                    # Profile writers already negotiate their separators.
                    if not ((active_profile or {}).get("outbound") or {}).get("events"):
                        message = declare_utf8(message)
                    validate_outbound(message, active_profile, profile_event, control_id)
                profile_id = (active_profile or {}).get("id")
                profile_version = (active_profile or {}).get("version")
            order["pendingOutbound"] = {
                "event": event_type, "destination": f"{host}:{port}", "message": message,
                "controlId": control_id, "messageType": message_type,
                "profileId": profile_id, "profileVersion": profile_version, "inFlight": True,
            }
            if analyzer.get("scenario") == "MALFORMED_RESULT":
                message = message[: max(1, len(message) // 2)]
            order["status"] = "SENDING_RESULT" if event_type == "REPORT" else order["status"]
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
                status = "COMPLETED" if code == "ACK" else "ERROR"
                error = None if code == "ACK" else f"ASTM response is not ACK: {code or 'MISSING'}"
        except (OSError, TimeoutError, ConnectionError, ValueError) as exc:
            ack = None
            code = None
            status = "ERROR"
            error = str(exc)

        with self._atomic():
            order = self._find_order(order_id)
            order["pendingOutbound"]["inFlight"] = False
            if status == "COMPLETED":
                order.pop("pendingOutbound", None)
            if event_type == "DELETE_SAMPLE" and status == "COMPLETED":
                order["status"] = "CANCELLED"
            elif event_type == "REPORT":
                order["status"] = status
            order["lastOutboundEvent"] = event_type
            order["messageControlId"] = control_id
            order["updatedAt"] = self._now()
            self._set_state(order["analyzerId"], AnalyzerState(status if status in AnalyzerState.__members__ else "ERROR"), error, order["sampleId"])
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
                "profileId": profile_id,
                "profileVersion": profile_version,
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

    def _find_order(self, order_id: str) -> dict:
        for order in self.orders:
            if order["id"] == order_id:
                return order
        raise ValueError("Order not found")

    def _barcode_exists(self, analyzer_id: str, barcode: str) -> bool:
        return any(
            order["analyzerId"] == analyzer_id
            and order["barcode"] == barcode
            and order["status"] not in {"ERROR", "CANCELLED"}
            for order in self.orders
        )

    def _pacs_accession_exists(self, accession_no: str | None) -> bool:
        if not accession_no:
            return False
        return any(
            order.get("deviceType") == "PACS"
            and order.get("accessionNo") == accession_no
            and order.get("status") != "CANCELLED"
            for order in self.orders
        )

    def _pacs_account_number(self, value: str | None) -> str:
        return str(value or "").split("^", 1)[0].strip()

    def mark_listener(self, analyzer_id: str, active: bool, host: str, port: int, error: str | None) -> None:
        with self._atomic():
            analyzer = self.analyzers[analyzer_id]
            analyzer["listenerActive"] = active
            analyzer["listenerHost"] = f"{host}:{port}"
            analyzer["listenerError"] = error
            analyzer["updatedAt"] = self._now()
            if error:
                analyzer["lastError"] = error
            self.store.save_analyzer(analyzer)

    def _set_state(self, analyzer_id: str, state: AnalyzerState, error: str | None = None, current_sample: str | None = None) -> None:
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
        profile = payload.get("profile") or (existing or {}).get("profile") or "GENERIC"
        if not supported_tests and profile not in {"ANAPATH", "PACS"} and not (str(payload.get("protocol", "HL7")).upper() == "HL7" and str(payload.get("mode", "MANUAL")).upper() == "BIDIRECTIONAL"):
            raise ValueError("Analyzer needs at least one supported test")
        protocol = str(payload.get("protocol", "HL7")).upper()
        if protocol not in {"HL7", "ASTM"}:
            raise ValueError("Protocol must be HL7 or ASTM")
        transport = payload.get("transport") or ("MLLP" if protocol == "HL7" else "TCP_SIMULATED")
        mode = str(payload.get("mode", AnalyzerMode.MANUAL.value)).upper()
        if mode not in {AnalyzerMode.MANUAL.value, AnalyzerMode.BIDIRECTIONAL.value}:
            raise ValueError("Invalid analyzer mode")
        base = existing or {}
        behavior = normalize_behavior(payload.get("behavior") or base.get("behavior"), protocol)
        if protocol == "HL7":
            if behavior["ackSuccessCode"] not in {"AA", "CA"} or behavior["ackErrorCode"] not in {"AE", "AR", "CE", "CR"}:
                raise ValueError("Invalid HL7 ACK success/error code")
            for path in behavior.get("requiredFields") or []:
                if not valid_path(path):
                    raise ValueError(f"Invalid required HL7 field: {path}")
            for paths in (behavior.get("inboundMappings") or {}).values():
                if any(not valid_path(path.strip()) for path in paths.split(",") if path.strip()):
                    raise ValueError(f"Invalid inbound mapping: {paths}")
            for path in (behavior.get("outboundMappings") or {}).values():
                if not valid_path(path):
                    raise ValueError(f"Invalid outbound mapping: {path}")
        configured = {
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
            "profile": profile,
            "behavior": behavior,
            "catalogRevisionId": payload.get("catalogRevisionId") or base.get("catalogRevisionId"),
            "unknownStudyPolicy": payload.get("unknownStudyPolicy") or base.get("unknownStudyPolicy"),
            "genericStudyCode": payload.get("genericStudyCode") or base.get("genericStudyCode"),
            "behaviorConfigured": payload.get("behaviorConfigured", bool(payload.get("behavior"))),
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
        validate_ports(configured, list(self.analyzers.values()))
        if not str(configured["id"]).strip():
            raise ValueError("Analyzer id cannot be empty")
        if not configured["resultDestinationHost"].strip():
            raise ValueError("Result destination host cannot be empty")
        return configured

    def _cancel_order(self, analyzer_id: str, payload: dict) -> dict:
        with self._atomic():
            for order in reversed(self.orders):
                if order["analyzerId"] != analyzer_id:
                    continue
                if order.get("orderId") != payload["orderId"] and order["barcode"] != payload["barcode"]:
                    continue
                order["status"] = "CANCELLED"
                order["updatedAt"] = self._now()
                self._set_state(analyzer_id, AnalyzerState.CANCELLED, None, None)
                self.store.save_order(order)
                self.store.save_analyzer(self.analyzers[analyzer_id])
                return dict(order)
        raise HL7OrderError("Order to cancel not found")

    def _log_inbound_hl7(
        self,
        analyzer_id: str,
        sample_id: str | None,
        source_ip: str,
        raw_message: str,
        ack_code: str,
        ack_message: str,
        error: str | None,
        message_control_id: str | None,
        message_type: str | None,
        warnings: list[str] | None = None,
    ) -> None:
        with self._atomic():
            self._append_message(
                self._message_record(
                    direction="IN",
                    protocol="HL7",
                    message_type=message_type,
                    analyzer_id=analyzer_id,
                    sample_id=sample_id,
                    source=source_ip,
                    raw_message=raw_message,
                    ack_code=ack_code,
                    ack_message=ack_message,
                    error=error,
                    message_control_id=message_control_id,
                    warnings=warnings,
                )
            )

    def reject_transport(self, analyzer_id, protocol, raw_message, source_ip, reason):
        metadata = extract_metadata(raw_message) if protocol == "HL7" else {}
        ack = build_ack(metadata, "AE", reason) if protocol == "HL7" else "NAK"
        try:
            with self._atomic():
                self._append_message(self._message_record(
                    direction="IN", protocol=protocol, message_type=metadata.get("messageType"),
                    analyzer_id=analyzer_id, sample_id=None, source=source_ip, raw_message=raw_message,
                    ack_code="AE" if protocol == "HL7" else "NAK", ack_message=ack,
                    error=reason, message_control_id=metadata.get("messageControlId")))
        except sqlite3.Error:
            LOGGER.exception("Cannot persist transport rejection for %s", analyzer_id)
        return ack

    def _next_ack_text(self) -> str:
        return f"HIS-SEQ-{self.store.next_sequence():06d}"

    def _ack_success_text(self, behavior: dict) -> str | None:
        ack_text = behavior.get("ackSuccessText")
        if ack_text == "AUTO_SEQUENCE":
            return self._next_ack_text()
        return ack_text or None

    def _generate_results(self, requested_tests: list[str], force_critical: bool = False) -> list[dict]:
        revision = self.catalog_revision("builtin-v1")
        groups = [{"key": str(i), "code": code} for i, code in enumerate(requested_tests)]
        return generate_results(bind_studies(revision, groups, "TEMPLATE", "GENERIC"))

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _hl7_timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

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
        warnings: list[str] | None = None,
    ) -> dict:
        profile = self.store.get_active_protocol_profile(analyzer_id) if protocol == "HL7" else None
        return {
            "profileId": (profile or {}).get("id"),
            "profileVersion": (profile or {}).get("version"),
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
            "warnings": warnings or [],
            "createdAt": self._now(),
        }

    def _ensure_operational(self, analyzer_id: str) -> None:
        self._require_contract(analyzer_id)
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
            "sampleCode": payload.get("sampleCode"),
            "sampleName": payload.get("sampleName"),
            "sampleCount": payload.get("sampleCount"),
            "patientId": payload["patientId"],
            "patientName": payload.get("patientName") or "SIMULATED^PATIENT",
            "patientBirthDate": payload.get("patientBirthDate"),
            "patientSex": payload.get("patientSex"),
            "patientAddress": payload.get("patientAddress"),
            "visitType": payload.get("visitType"),
            "visitNumber": payload.get("visitNumber"),
            "priority": payload["priority"],
            "analyzerId": analyzer_id,
            "requestedTests": payload["requestedTests"],
            "studies": copy.deepcopy(payload.get("studies") or []),
            "panelCode": payload.get("panelCode"),
            "panelName": payload.get("panelName"),
            "procedureCode": payload.get("procedureCode"),
            "procedureName": payload.get("procedureName"),
            "orderId": payload["orderId"],
            "fillerOrderId": payload.get("fillerOrderId"),
            "prescriptionId": payload.get("prescriptionId"),
            "orc2": payload.get("orc2"),
            "orc3": payload.get("orc3"),
            "orc4": payload.get("orc4"),
            "obr2": payload.get("obr2"),
            "obr3": payload.get("obr3"),
            "requestedAt": payload.get("requestedAt"),
            "clinicalInfo": payload.get("clinicalInfo"),
            "observations": payload.get("observations") or [],
            "topographyCode": payload.get("topographyCode"),
            "topographyName": payload.get("topographyName"),
            "laterality": payload.get("laterality"),
            "customFields": payload.get("customFields") or {},
            "sourceMessageControlId": payload.get("messageControlId"),
            "rackPosition": self._rack_position(),
            "createdAt": self._now(),
            "updatedAt": self._now(),
            "results": [],
        }
