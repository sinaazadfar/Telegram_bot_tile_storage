import os
import sys
from pathlib import Path
from threading import Lock

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

load_dotenv(ROOT_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_METRICS = {"physical", "sellable", "reserved"}

DATA_DIR = ROOT_DIR / "data"
TEMPLATE_DIR = ROOT_DIR / "template"
TEMPLATE_PATH = TEMPLATE_DIR / "template.xlsx"
TEMPLATE_LOCK = Lock()
WAREHOUSE_KEYS = ("fakhar", "dorin")

CONNECT_TIMEOUT = float(os.getenv("BOT_CONNECT_TIMEOUT", "30"))
READ_TIMEOUT = float(os.getenv("BOT_READ_TIMEOUT", "60"))
WRITE_TIMEOUT = float(os.getenv("BOT_WRITE_TIMEOUT", "60"))
POOL_TIMEOUT = float(os.getenv("BOT_POOL_TIMEOUT", "30"))
PROCESS_TIMEOUT_ENV = os.getenv("BOT_PROCESS_TIMEOUT", "")
PROCESS_TIMEOUT = float(PROCESS_TIMEOUT_ENV) if PROCESS_TIMEOUT_ENV else None

PROXY_URL = os.getenv("BOT_PROXY", "")
REQUEST_POOL_SIZE = int(os.getenv("BOT_POOL_SIZE", "8"))
UPDATES_POOL_SIZE = int(os.getenv("BOT_UPDATES_POOL_SIZE", "1"))


def warehouse_dir(key: str) -> Path:
    path = DATA_DIR / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def template_dir(key: str) -> Path:
    path = TEMPLATE_DIR / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def warehouse_input_path(key: str, suffix: str = ".xlsx") -> Path:
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return warehouse_dir(key) / f"input{suffix}"


def warehouse_template_path(key: str) -> Path:
    return template_dir(key) / "trmplate.xlsx"


def resolve_warehouse_input_path(key: str) -> Path | None:
    candidates = [
        warehouse_input_path(key, ".xlsx"),
        warehouse_input_path(key, ".pdf"),
    ]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def resolve_any_template_path() -> Path | None:
    candidates = [TEMPLATE_PATH]
    candidates.extend(warehouse_template_path(key) for key in WAREHOUSE_KEYS)
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def warehouse_output_path(key: str) -> Path:
    return warehouse_dir(key) / "output.xlsx"
