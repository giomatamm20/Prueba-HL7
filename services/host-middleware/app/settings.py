import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_local_environment() -> None:
    environment_file = PROJECT_ROOT / ".env.local"
    if not environment_file.exists():
        return
    for raw_line in environment_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_local_environment()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://hl7host:hl7password@localhost:5432/hl7crm",
)
MLLP_HOST = os.environ.get("MLLP_HOST", "0.0.0.0")
MLLP_PORT = int(os.environ.get("MLLP_PORT", "2575"))
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "8088"))
SCHEMA_FILE = PROJECT_ROOT / "database" / "init.sql"
STATIC_DIR = Path(__file__).resolve().parent / "static"

