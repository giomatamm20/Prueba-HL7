from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import SimulationEngine
from .listeners import ListenerManager
from .configuration import port_set
from .pacs import (
    PacsSecureLinkError,
    decrypt_legacy_secure_payload,
    render_study_svg,
    render_viewer_html,
)


engine: SimulationEngine | None = None
listener_manager: ListenerManager | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global engine, listener_manager
    try:
        engine = SimulationEngine()
        listener_manager = ListenerManager(engine)
        listener_manager.start()
        yield
    finally:
        if listener_manager:
            listener_manager.stop()


app = FastAPI(title="Analyzer Simulation Lab", version="0.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class ManualSampleRequest(BaseModel):
    analyzerId: str
    sampleId: str = Field(min_length=1)
    barcode: str | None = None
    sampleName: str | None = None
    patientId: str | None = None
    patientName: str | None = None
    visitType: str | None = None
    visitNumber: str | None = None
    orderId: str | None = None
    fillerOrderId: str | None = None
    prescriptionId: str | None = None
    priority: str = "ROUTINE"
    requestedAt: str | None = None
    clinicalInfo: str | None = None
    topographyCode: str | None = None
    topographyName: str | None = None
    laterality: str | None = None
    observations: list[str] = Field(default_factory=list)
    tests: list[str] = Field(min_length=1)


class LoadSampleRequest(BaseModel):
    barcode: str | None = None


class ProcessRequest(BaseModel):
    sendResult: bool = True


class PacsProcessRequest(BaseModel):
    sendNotification: bool = False


class AnalyzerModeRequest(BaseModel):
    mode: str


class AnalyzerStateRequest(BaseModel):
    state: str


class AnalyzerScenarioRequest(BaseModel):
    scenario: str


class ScanBarcodeRequest(BaseModel):
    barcode: str = Field(min_length=1)


class AnalyzerConfigRequest(BaseModel):
    id: str | None = None
    name: str | None = None
    vendor: str | None = None
    protocol: str = "HL7"
    transport: str | None = None
    listenPort: int = Field(default=0, ge=0, le=65535)
    resultDestinationHost: str = "localhost"
    resultDestinationPort: int = Field(default=2575, ge=1, le=65535)
    supportedTests: list[str] = Field(default_factory=list)
    profile: str | None = None
    mode: str = "MANUAL"
    behavior: dict = Field(default_factory=dict)


class ProtocolProfileRequest(BaseModel):
    name: str = Field(min_length=1)
    activate: bool = True
    inbound: dict = Field(default_factory=dict)
    outbound: dict = Field(default_factory=dict)


class HL7ContractInferRequest(BaseModel):
    rawMessage: str = Field(min_length=4)


class EmitEventRequest(BaseModel):
    eventType: str
    newProcedureCode: str | None = None
    newProcedureName: str | None = None
    newSampleId: str | None = None
    newSampleCode: str | None = None
    newSampleName: str | None = None
    reportStatus: str | None = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/dashboard")
def dashboard() -> dict:
    return engine.dashboard()


@app.get("/api/analyzers")
def analyzers() -> list[dict]:
    return engine.list_analyzers()


@app.get("/api/ports")
def available_ports() -> dict:
    allowed = port_set(os.environ.get("SIMULATOR_ALLOWED_LISTEN_PORTS", ""))
    reserved = port_set(os.environ.get("SIMULATOR_RESERVED_PORTS", "8090"))
    used = {a["listenPort"]: a["id"] for a in engine.list_analyzers() if a["listenPort"]}
    return {"restricted": bool(allowed), "ports": [
        {"port": port, "analyzerId": used.get(port), "reserved": port in reserved}
        for port in sorted(allowed - reserved)
    ]}


@app.get("/api/health")
def health() -> dict:
    items = engine.list_analyzers()
    failed = [a["id"] for a in items if a["listenPort"] and a["contractReady"] and not a["listenerActive"] and listener_manager.enabled()]
    return {"status": "degraded" if failed else "ok", "failedListeners": failed}


@app.patch("/api/analyzers/{analyzer_id}/mode")
def set_analyzer_mode(analyzer_id: str, payload: AnalyzerModeRequest) -> dict:
    try:
        return engine.set_analyzer_mode(analyzer_id, payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/analyzers/{analyzer_id}/state")
def set_analyzer_state(analyzer_id: str, payload: AnalyzerStateRequest) -> dict:
    try:
        return engine.set_analyzer_state(analyzer_id, payload.state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/analyzers/{analyzer_id}/scenario")
def set_analyzer_scenario(analyzer_id: str, payload: AnalyzerScenarioRequest) -> dict:
    try:
        return engine.set_analyzer_scenario(analyzer_id, payload.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyzers/{analyzer_id}/clear")
def clear_analyzer_data(analyzer_id: str) -> dict:
    try:
        return engine.clear_analyzer_data(analyzer_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyzers", status_code=201)
def create_analyzer(payload: AnalyzerConfigRequest) -> dict:
    try:
        if not payload.id:
            raise ValueError("Analyzer id is required")
        return listener_manager.configure(payload.model_dump(exclude_none=True, exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/analyzers/{analyzer_id}")
def update_analyzer(analyzer_id: str, payload: AnalyzerConfigRequest) -> dict:
    try:
        data = payload.model_dump(exclude_none=True, exclude_unset=True)
        data["id"] = analyzer_id
        return listener_manager.configure(data, analyzer_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/analyzers/{analyzer_id}/protocol-profiles")
def protocol_profiles(analyzer_id: str) -> list[dict]:
    try:
        return engine.list_protocol_profiles(analyzer_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/analyzers/{analyzer_id}/protocol-profiles", status_code=201)
def create_protocol_profile(analyzer_id: str, payload: ProtocolProfileRequest) -> dict:
    try:
        profile = engine.save_protocol_profile(analyzer_id, payload.model_dump())
        listener_manager.sync(analyzer_id)
        return profile
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/analyzers/{analyzer_id}/protocol-profiles/{profile_id}")
def update_protocol_profile(analyzer_id: str, profile_id: str, payload: ProtocolProfileRequest) -> dict:
    try:
        profile = engine.update_protocol_profile(analyzer_id, profile_id, payload.model_dump())
        listener_manager.sync(analyzer_id)
        return profile
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/analyzers/{analyzer_id}/protocol-profiles/{profile_id}", status_code=204)
def delete_protocol_profile(analyzer_id: str, profile_id: str) -> None:
    try:
        engine.delete_protocol_profile(analyzer_id, profile_id)
        listener_manager.sync(analyzer_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyzers/{analyzer_id}/protocol-profiles/{profile_id}/activate")
def activate_protocol_profile(analyzer_id: str, profile_id: str) -> dict:
    try:
        profile = engine.activate_protocol_profile(analyzer_id, profile_id)
        listener_manager.sync(analyzer_id)
        return profile
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyzers/{analyzer_id}/protocol-profiles/preview")
def preview_protocol_profile(analyzer_id: str, payload: ProtocolProfileRequest) -> dict:
    try:
        return engine.preview_protocol_profile(analyzer_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyzers/{analyzer_id}/protocol-profiles/infer")
def infer_protocol_contract(analyzer_id: str, payload: HL7ContractInferRequest) -> dict:
    try:
        return engine.infer_protocol_contract(analyzer_id, payload.rawMessage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders")
def orders() -> list[dict]:
    return engine.list_orders()


@app.get("/api/messages")
def messages() -> list[dict]:
    return engine.list_messages()


@app.get("/api/pacs/studies")
def pacs_studies() -> list[dict]:
    return engine.list_pacs_studies()


@app.get("/api/pacs/studies/{study_id}")
def pacs_study(study_id: str) -> dict:
    try:
        return engine.get_pacs_study(study_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/pacs/studies/{study_id}/process")
def process_pacs_study(study_id: str, payload: PacsProcessRequest) -> dict:
    try:
        return engine.process_pacs_study(study_id, payload.sendNotification)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/pacs/studies/{study_id}/notify")
def notify_pacs_study(study_id: str) -> dict:
    try:
        return engine.notify_pacs_study(study_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/pacs/studies/{study_id}/image.svg")
def pacs_study_image(study_id: str) -> Response:
    try:
        study = engine.get_pacs_study(study_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if study["status"] not in {"IMAGE_READY", "NOTIFYING_MIRTH", "NOTIFICATION_ERROR", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Image is not ready")
    return Response(render_study_svg(study), media_type="image/svg+xml")


@app.get("/api/pacs/studies/{study_id}/viewer", response_class=HTMLResponse)
def pacs_study_viewer(study_id: str, request: Request) -> HTMLResponse:
    try:
        study = engine.get_pacs_study(study_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if study["status"] not in {"IMAGE_READY", "NOTIFYING_MIRTH", "NOTIFICATION_ERROR", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Image is not ready")
    image_url = str(request.url_for("pacs_study_image", study_id=study_id))
    return HTMLResponse(render_viewer_html(study, image_url))


@app.get("/api/pacs/viewer", response_class=HTMLResponse)
def pacs_viewer_by_key(request: Request, account_no: str, accession_no: str) -> HTMLResponse:
    try:
        study = engine.find_pacs_study(account_no, accession_no)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if study["status"] not in {"IMAGE_READY", "NOTIFYING_MIRTH", "NOTIFICATION_ERROR", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Image is not ready")
    image_url = str(request.url_for("pacs_study_image", study_id=study["id"]))
    return HTMLResponse(render_viewer_html(study, image_url))


@app.get("/secure_link/code/{key_code}/payload/{payload:path}", response_class=HTMLResponse)
def pacs_legacy_secure_link(key_code: str, payload: str, request: Request) -> HTMLResponse:
    try:
        parameters = decrypt_legacy_secure_payload(key_code, payload)
        study = engine.find_pacs_study(
            parameters["account_no"],
            parameters["accession_no"],
        )
    except PacsSecureLinkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if study["status"] not in {"IMAGE_READY", "NOTIFYING_MIRTH", "NOTIFICATION_ERROR", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Image is not ready")
    image_url = str(request.url_for("pacs_study_image", study_id=study["id"]))
    return HTMLResponse(
        render_viewer_html(study, image_url),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/manual-samples", status_code=201)
def create_manual_sample(payload: ManualSampleRequest) -> dict:
    try:
        return engine.create_manual_sample(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/orders/{order_id}/load")
def load_sample(order_id: str, payload: LoadSampleRequest) -> dict:
    try:
        return engine.load_sample(order_id, payload.barcode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/analyzers/{analyzer_id}/scan")
def scan_barcode(analyzer_id: str, payload: ScanBarcodeRequest) -> dict:
    try:
        return engine.scan_barcode(analyzer_id, payload.barcode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/orders/{order_id}/process")
def process_order(order_id: str, payload: ProcessRequest) -> dict:
    try:
        return engine.process_order(order_id, payload.sendResult)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/orders/{order_id}/send")
def send_result(order_id: str) -> dict:
    try:
        return engine.send_result(order_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/orders/{order_id}/emit")
def emit_order_event(order_id: str, payload: EmitEventRequest) -> dict:
    try:
        return engine.emit_order_event(order_id, payload.eventType, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
