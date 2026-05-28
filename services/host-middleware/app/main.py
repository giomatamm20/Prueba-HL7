import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse

from .mllp import ConnectionState, Hl7Processor, MllpServer
from .settings import DATABASE_URL, MLLP_HOST, MLLP_PORT, SCHEMA_FILE, STATIC_DIR
from .storage import CrmStore


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("host-middleware")
store = CrmStore(DATABASE_URL, SCHEMA_FILE)
connection_state = ConnectionState()
mllp_server: MllpServer | None = None
mllp_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global mllp_server, mllp_thread
    store.initialize()
    processor = Hl7Processor(store, connection_state)
    mllp_server = MllpServer((MLLP_HOST, MLLP_PORT), processor)
    mllp_thread = threading.Thread(target=mllp_server.serve_forever, daemon=True)
    mllp_thread.start()
    LOGGER.info("MLLP listener active on %s:%s", MLLP_HOST, MLLP_PORT)
    yield
    mllp_server.shutdown()
    mllp_server.server_close()
    mllp_thread.join(timeout=3)


app = FastAPI(title="HL7 Host Middleware / CRM", version="0.2.0", lifespan=lifespan)


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
def status() -> dict:
    return {"listener": connection_state.snapshot(), "database": store.status_summary()}


@app.get("/api/messages")
def messages(limit: int = Query(default=25, ge=1, le=200)) -> list[dict]:
    return store.messages(limit)


@app.get("/api/patients")
def patients() -> list[dict]:
    return store.patients()


@app.get("/api/orders")
def orders() -> list[dict]:
    return store.orders()


@app.get("/api/results")
def results(limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return store.results(limit)

