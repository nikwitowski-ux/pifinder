"""FastAPI dashboard: paper-ink-orange editorial UI over the firms DB.

Routes:
  GET  /                    main split-pane view
  GET  /firm/{id}           single-firm detail page
  GET  /api/firms           filtered firm list as JSON
  GET  /api/firms/{id}      one firm with attorneys + signals
  GET  /api/export.csv      filtered CSV download
  GET  /healthz             readiness check
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db as db_module
from ..config import get_score_buckets, get_settings


WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="pifinder", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        buckets = _bucket_thresholds()
        stats = _stats()
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"buckets": buckets, "stats": stats},
        )

    @app.get("/firm/{firm_id}", response_class=HTMLResponse)
    async def firm_detail(request: Request, firm_id: int) -> HTMLResponse:
        firm = _firm_with_relations(firm_id)
        if firm is None:
            raise HTTPException(status_code=404, detail="firm not found")
        return TEMPLATES.TemplateResponse(
            request,
            "firm_detail.html",
            {"firm": firm},
        )

    @app.get("/api/firms")
    async def api_firms(
        q: str | None = Query(default=None, description="case-insensitive name match"),
        min_score: int = Query(default=0, ge=0, le=100),
        has_website: bool | None = Query(default=None),
        enriched_only: bool = Query(default=False, description="must have attorney_count > 0"),
    ) -> JSONResponse:
        rows = _list_firms(q=q, min_score=min_score, has_website=has_website, enriched_only=enriched_only)
        return JSONResponse({"firms": rows, "count": len(rows)})

    @app.get("/api/firms/{firm_id}")
    async def api_firm(firm_id: int) -> JSONResponse:
        firm = _firm_with_relations(firm_id)
        if firm is None:
            raise HTTPException(status_code=404, detail="firm not found")
        return JSONResponse(firm)

    @app.get("/api/export.csv")
    async def api_export_csv(
        q: str | None = None,
        min_score: int = 0,
        has_website: bool | None = None,
        enriched_only: bool = False,
    ) -> Response:
        rows = _list_firms(q=q, min_score=min_score, has_website=has_website, enriched_only=enriched_only)
        buf = io.StringIO()
        fields = [
            "name", "score", "bucket", "address", "city", "state", "phone",
            "website", "rating", "user_ratings_total", "attorney_count",
            "latitude", "longitude", "place_id",
        ]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"content-disposition": 'attachment; filename="pifinder.csv"'},
        )

    return app


# ---- data access ----

def _conn() -> sqlite3.Connection:
    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)
    return conn


def _bucket_thresholds() -> dict[str, int]:
    cfg = get_score_buckets()
    return {"red_max": int(cfg.get("red_max", 39)), "yellow_max": int(cfg.get("yellow_max", 69))}


def _bucket_for(score: int | None, thresholds: dict[str, int]) -> str:
    if score is None:
        return "unscored"
    if score <= thresholds["red_max"]:
        return "cold"
    if score <= thresholds["yellow_max"]:
        return "warm"
    return "hot"


def _row_to_public(row: sqlite3.Row, thresholds: dict[str, int]) -> dict[str, Any]:
    d = dict(row)
    score = d.get("score")
    return {
        "id": d["id"],
        "name": d["name"],
        "place_id": d.get("place_id"),
        "address": d.get("address"),
        "city": d.get("city"),
        "state": d.get("state"),
        "postal_code": d.get("postal_code"),
        "phone": d.get("phone"),
        "website": d.get("website"),
        "email": d.get("email"),
        "latitude": d.get("latitude"),
        "longitude": d.get("longitude"),
        "rating": d.get("rating"),
        "user_ratings_total": d.get("user_ratings_total"),
        "business_status": d.get("business_status"),
        "attorney_count": d.get("attorney_count"),
        "has_pi_practice_page": bool(d["has_pi_practice_page"]) if d.get("has_pi_practice_page") is not None else None,
        "last_website_post_at": d.get("last_website_post_at"),
        "established_year": d.get("established_year"),
        "score": score,
        "bucket": _bucket_for(score, thresholds),
    }


def _list_firms(
    *,
    q: str | None,
    min_score: int,
    has_website: bool | None,
    enriched_only: bool,
) -> list[dict[str, Any]]:
    conn = _conn()
    sql = [
        "SELECT f.*, s.score FROM firms f",
        "LEFT JOIN scores s ON s.firm_id = f.id",
        "WHERE 1=1",
    ]
    params: list[Any] = []
    if q:
        sql.append("AND lower(f.name) LIKE ?")
        params.append(f"%{q.lower()}%")
    if min_score > 0:
        sql.append("AND COALESCE(s.score, 0) >= ?")
        params.append(min_score)
    if has_website is True:
        sql.append("AND f.website IS NOT NULL AND f.website != ''")
    elif has_website is False:
        sql.append("AND (f.website IS NULL OR f.website = '')")
    if enriched_only:
        sql.append("AND COALESCE(f.attorney_count, 0) > 0")
    sql.append("ORDER BY COALESCE(s.score, 0) DESC, f.name ASC")

    thresholds = _bucket_thresholds()
    rows = conn.execute(" ".join(sql), params).fetchall()
    return [_row_to_public(r, thresholds) for r in rows]


def _firm_with_relations(firm_id: int) -> dict[str, Any] | None:
    conn = _conn()
    base = conn.execute(
        "SELECT f.*, s.score FROM firms f LEFT JOIN scores s ON s.firm_id = f.id WHERE f.id = ?",
        (firm_id,),
    ).fetchone()
    if base is None:
        return None
    thresholds = _bucket_thresholds()
    out = _row_to_public(base, thresholds)

    attorneys = conn.execute(
        "SELECT name, title, bio_url, practice_areas FROM attorneys WHERE firm_id = ? ORDER BY name",
        (firm_id,),
    ).fetchall()
    out["attorneys"] = [
        {
            "name": a["name"],
            "title": a["title"],
            "bio_url": a["bio_url"],
            "practice_areas": json.loads(a["practice_areas"] or "[]"),
        }
        for a in attorneys
    ]

    signals = conn.execute(
        "SELECT kind, source, observed_at, summary, payload_json FROM signals "
        "WHERE firm_id = ? ORDER BY observed_at DESC",
        (firm_id,),
    ).fetchall()
    out["signals"] = [
        {
            "kind": s["kind"],
            "source": s["source"],
            "observed_at": s["observed_at"],
            "summary": s["summary"],
            "payload": json.loads(s["payload_json"] or "{}"),
        }
        for s in signals
    ]
    return out


def _stats() -> dict[str, int]:
    conn = _conn()
    n_firms = conn.execute("SELECT COUNT(*) AS n FROM firms").fetchone()["n"]
    n_enriched = conn.execute("SELECT COUNT(*) AS n FROM firms WHERE COALESCE(attorney_count, 0) > 0").fetchone()["n"]
    n_scored = conn.execute("SELECT COUNT(*) AS n FROM scores").fetchone()["n"]
    return {"firms": n_firms, "enriched": n_enriched, "scored": n_scored}


# ASGI app instance for uvicorn imports.
app = create_app()
