from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

from .config import get_settings


def _cache_root() -> Path:
    root = get_settings().cache_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def make_key(source: str, parts: dict[str, Any]) -> str:
    """Stable hash of a request descriptor. Sorting keys keeps it deterministic."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{source}:{digest}"


def _path_for(key: str) -> Path:
    safe = key.replace(":", "__")
    return _cache_root() / f"{safe}.json"


def get_json(key: str) -> Any | None:
    p = _path_for(key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except json.JSONDecodeError:
        logger.warning("Corrupt cache entry {}, deleting", p)
        p.unlink(missing_ok=True)
        return None


def put_json(key: str, value: Any) -> None:
    p = _path_for(key)
    p.write_text(json.dumps(value, default=str), encoding="utf-8")
