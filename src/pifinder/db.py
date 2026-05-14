from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import HttpUrl

from .models import FirmRecord, SourceName

MIGRATION_PKG = "pifinder.migrations"


def _resolve_db_path(path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = _resolve_db_path(db_path)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit-ish; we use explicit txns
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply every migration with version > current. Migrations live in pifinder/migrations/NNNN_*.sql."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    current = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version").fetchone()["v"]

    migrations_dir = files(MIGRATION_PKG)
    sql_files = sorted(
        (p for p in migrations_dir.iterdir() if p.name.endswith(".sql")),
        key=lambda p: p.name,
    )
    for entry in sql_files:
        m = re.match(r"^(\d+)_", entry.name)
        if not m:
            continue
        version = int(m.group(1))
        if version <= current:
            continue
        logger.info("Applying migration {}", entry.name)
        sql = entry.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# -------- helpers --------

_SUFFIX_RE = re.compile(
    r"\b("
    r"llc|llp|pllc|p\s*c|pc|p\s*a|inc|esq"
    r"|attorneys?\s+at\s+law"
    r"|law\s+offices?\s+of"
    r"|law\s+firm"
    r"|the"
    r")\b"
)


def normalize_firm_name(name: str) -> str:
    """Lossy normalization for dedup. Stable across casing, punctuation, and common suffixes."""
    name = name.lower()
    # Collapse separators into spaces so "P.C." -> "p c " before suffix stripping.
    name = re.sub(r"[,.\-&/]+", " ", name)
    name = _SUFFIX_RE.sub("", name)
    return re.sub(r"\s+", " ", name).strip()


def _serialize_url(v: HttpUrl | str | None) -> str | None:
    return str(v) if v is not None else None


def upsert_firm(conn: sqlite3.Connection, firm: FirmRecord) -> int:
    """Insert or update by place_id (preferred) or normalized_name. Returns firm id."""
    normalized = firm.normalized_name or normalize_firm_name(firm.name)
    existing: sqlite3.Row | None = None
    if firm.place_id:
        existing = conn.execute("SELECT id FROM firms WHERE place_id = ?", (firm.place_id,)).fetchone()
    if existing is None:
        existing = conn.execute(
            "SELECT id FROM firms WHERE normalized_name = ? AND (city IS ? OR city = ?)",
            (normalized, firm.city, firm.city),
        ).fetchone()

    payload = {
        "place_id": firm.place_id,
        "name": firm.name,
        "normalized_name": normalized,
        "address": firm.address,
        "city": firm.city,
        "state": firm.state,
        "postal_code": firm.postal_code,
        "country": firm.country,
        "latitude": firm.latitude,
        "longitude": firm.longitude,
        "phone": firm.phone,
        "website": _serialize_url(firm.website),
        "email": firm.email,
        "rating": firm.rating,
        "user_ratings_total": firm.user_ratings_total,
        "business_status": firm.business_status,
        "attorney_count": firm.attorney_count,
        "has_pi_practice_page": (
            None if firm.has_pi_practice_page is None else int(firm.has_pi_practice_page)
        ),
        "last_website_post_at": firm.last_website_post_at.isoformat() if firm.last_website_post_at else None,
        "established_year": firm.established_year,
        "discovered_via": firm.discovered_via.value,
        "raw_json": json.dumps(firm.raw, default=str) if firm.raw else None,
    }

    if existing is None:
        cols = ", ".join(payload.keys())
        placeholders = ", ".join(f":{k}" for k in payload.keys())
        cur = conn.execute(
            f"INSERT INTO firms ({cols}) VALUES ({placeholders})",
            payload,
        )
        return int(cur.lastrowid)
    else:
        firm_id = int(existing["id"])
        # Only overwrite columns we have non-null values for, except always bump last_seen_at.
        set_parts = [f"{k} = COALESCE(:{k}, {k})" for k in payload.keys()]
        set_parts.append("last_seen_at = datetime('now')")
        conn.execute(
            f"UPDATE firms SET {', '.join(set_parts)} WHERE id = :id",
            {**payload, "id": firm_id},
        )
        return firm_id


def insert_run(
    conn: sqlite3.Connection, *, location: str, radius_meters: int, query: str
) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, location, radius_meters, query) VALUES (datetime('now'), ?, ?, ?)",
        (location, radius_meters, query),
    )
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, *, firm_count: int, error: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = datetime('now'), firm_count = ?, error = ? WHERE id = ?",
        (firm_count, error, run_id),
    )


def fetch_firms(
    conn: sqlite3.Connection,
    *,
    min_score: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT f.*, s.score, s.bucket FROM firms f LEFT JOIN scores s ON s.firm_id = f.id"
    params: list[Any] = []
    if min_score is not None:
        sql += " WHERE s.score >= ?"
        params.append(min_score)
    sql += " ORDER BY COALESCE(s.score, 0) DESC, f.name ASC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_cached(conn: sqlite3.Connection, cache_key: str) -> bytes | None:
    row = conn.execute(
        "SELECT body FROM cached_responses WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    return row["body"] if row else None


def put_cached(conn: sqlite3.Connection, *, cache_key: str, source: str, body: bytes) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO cached_responses (cache_key, source, fetched_at, body) VALUES (?, ?, datetime('now'), ?)",
        (cache_key, source, body),
    )


def discovered_sources() -> Iterable[SourceName]:
    return list(SourceName)
