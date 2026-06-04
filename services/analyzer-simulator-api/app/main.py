from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import SimulationEngine
from .listeners import ListenerManager


engine = SimulationEngine()
listener_manager = ListenerManager(engine)


@asynccontextmanager
async def lifespan(_: FastAPI):
    listener_manager.start()
    yield
    listener_manager.stop()


app = FastAPI(title="Analyzer Simulation Lab", version="0.2.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class ManualSampleRequest(BaseModel):
    analyzerId: str
    sampleId: str = Field(min_length=1)
    barcode: str | None = None
    patientId: str | None = None
    patientName: str | None = None
    priority: str = "ROUTINE"
    tests: list[str] = Field(min_length=1)


class LoadSampleRequest(BaseModel):
    barcode: str | None = None


class ProcessRequest(BaseModel):
    sendResult: bool = True


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
    listenPort: int = 0
    resultDestinationHost: str = "localhost"
    resultDestinationPort: int = 2575
    supportedTests: list[str] = Field(default_factory=list)
    mode: str = "MANUAL"


@app.get("/")
def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/dashboard")
def dashboard() -> dict:
    return engine.dashboard()


@app.get("/api/analyzers")
def analyzers() -> list[dict]:
    return engine.list_analyzers()


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


@app.post("/api/analyzers", status_code=201)
def create_analyzer(payload: AnalyzerConfigRequest) -> dict:
    try:
        if not payload.id:
            raise ValueError("Analyzer id is required")
        analyzer = engine.create_analyzer(payload.model_dump(exclude_none=True))
        listener_manager.restart()
        return analyzer
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/analyzers/{analyzer_id}")
def update_analyzer(analyzer_id: str, payload: AnalyzerConfigRequest) -> dict:
    try:
        data = payload.model_dump(exclude_none=True)
        data["id"] = analyzer_id
        analyzer = engine.update_analyzer(analyzer_id, data)
        listener_manager.restart()
        return analyzer
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders")
def orders() -> list[dict]:
    return engine.list_orders()


@app.get("/api/messages")
def messages() -> list[dict]:
    return engine.list_messages()


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
