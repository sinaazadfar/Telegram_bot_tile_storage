import hashlib
import re
from pathlib import Path
from shutil import rmtree

from .config import DATA_DIR
from .utils import clean_text, normalize_code_value, normalize_query

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_CATALOG_IMAGES = 10
_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("_", text.lower()).strip("_")


def catalog_id_for_row(row: dict) -> str:
    code_display = clean_text(row.get("code_display", ""))
    name_display = clean_text(row.get("name_display", ""))
    code_norm = normalize_code_value(code_display)
    base = _slugify(code_norm)
    if base:
        return base
    source = f"{code_display}::{name_display}".strip(":")
    if not source:
        source = str(row.get("row", "unknown"))
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    name_norm = normalize_query(name_display)
    fallback = _slugify(name_norm)
    if fallback:
        return f"{fallback}_{digest}"
    return f"design_{digest}"


def catalog_root(warehouse_key: str) -> Path:
    path = DATA_DIR / warehouse_key / "catalogs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def catalog_dir_path(warehouse_key: str, row: dict) -> Path:
    return catalog_root(warehouse_key) / catalog_id_for_row(row)


def list_catalog_images(warehouse_key: str, row: dict) -> list[Path]:
    catalog_dir = catalog_dir_path(warehouse_key, row)
    if not catalog_dir.exists():
        return []
    images = [
        path
        for path in catalog_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(images, key=lambda path: path.name)


def catalog_image_count(warehouse_key: str, row: dict) -> int:
    return len(list_catalog_images(warehouse_key, row))


def clear_catalog(warehouse_key: str, row: dict) -> None:
    catalog_dir = catalog_dir_path(warehouse_key, row)
    if catalog_dir.exists():
        rmtree(catalog_dir)


def next_catalog_image_path(warehouse_key: str, row: dict, extension: str) -> Path:
    catalog_dir = catalog_dir_path(warehouse_key, row)
    catalog_dir.mkdir(parents=True, exist_ok=True)
    count = catalog_image_count(warehouse_key, row)
    if count >= MAX_CATALOG_IMAGES:
        raise ValueError("Catalog image limit reached.")
    ext = extension.lower().strip()
    if not ext.startswith("."):
        ext = f".{ext}"
    if ext not in IMAGE_EXTENSIONS:
        ext = ".jpg"
    index = count + 1
    return catalog_dir / f"img_{index:02d}{ext}"
