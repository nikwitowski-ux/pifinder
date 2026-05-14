from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Annotated, Any

import typer
from loguru import logger

from datetime import datetime, timezone

from . import db as db_module
from .config import (
    get_discovery_defaults,
    get_score_buckets,
    get_scoring_weights,
    get_settings,
)
from .logging_setup import init_logging
from .models import EnrichmentResult, FirmRecord, SourceName
from .scoring import ScoringInputs, score as score_firm
from .sources.firm_website import FirmWebsiteScraper
from .sources.google_places import GooglePlacesError, GooglePlacesSource

app = typer.Typer(no_args_is_help=True, add_completion=False, help="PI firm lead-gen.")


@app.callback()
def _root(
    log_level: Annotated[str | None, typer.Option("--log-level", help="DEBUG/INFO/WARN/ERROR")] = None,
) -> None:
    init_logging(log_level)


@app.command()
def discover(
    location: Annotated[str, typer.Option("--location", "-l", help='e.g. "Orange County, CA"')],
    radius: Annotated[float, typer.Option("--radius", "-r", help="miles")] = 25.0,
    query: Annotated[str | None, typer.Option("--query", "-q")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", help="CSV path")] = None,
) -> None:
    """Find PI firms near LOCATION and persist them to the local DB."""
    defaults = get_discovery_defaults()
    resolved_query = query or defaults.get("default_query") or "personal injury law firm"
    radius_meters = int(radius * 1609.344)

    firms = asyncio.run(_run_discover(location=location, radius_meters=radius_meters, query=resolved_query))
    typer.echo(f"Discovered {len(firms)} firms.")

    if output:
        _write_csv(firms, output)
        typer.echo(f"CSV written to {output}")


async def _run_discover(*, location: str, radius_meters: int, query: str) -> list[FirmRecord]:
    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)
    run_id = db_module.insert_run(conn, location=location, radius_meters=radius_meters, query=query)

    try:
        async with GooglePlacesSource() as source:
            firms = await source.discover(location=location, radius_meters=radius_meters, query=query)
    except GooglePlacesError as e:
        db_module.finish_run(conn, run_id, firm_count=0, error=str(e))
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=2)

    with db_module.transaction(conn):
        inserted_ids: list[int] = []
        for firm in firms:
            inserted_ids.append(db_module.upsert_firm(conn, firm))

    db_module.finish_run(conn, run_id, firm_count=len(firms))
    logger.info("run #{} stored {} firms", run_id, len(firms))
    return firms


@app.command()
def enrich(
    firm_id: Annotated[int | None, typer.Option("--firm-id", help="Enrich a single firm by id")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Enrich every firm with a website")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Cap when using --all")] = None,
) -> None:
    """Run enrichment sources (firm-website scraper) against existing firms."""
    if not firm_id and not all_:
        typer.echo("error: pass --firm-id N or --all", err=True)
        raise typer.Exit(code=2)

    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)

    if firm_id is not None:
        row = db_module.fetch_firm(conn, firm_id)
        if row is None:
            typer.echo(f"error: firm {firm_id} not found", err=True)
            raise typer.Exit(code=2)
        firms_rows = [row]
    else:
        firms_rows = db_module.fetch_firms_with_website(conn)
        if limit:
            firms_rows = firms_rows[:limit]

    typer.echo(f"Enriching {len(firms_rows)} firm(s)...")
    asyncio.run(_run_enrich(firms_rows))


async def _run_enrich(firm_rows: list[dict[str, Any]]) -> None:
    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)

    async with FirmWebsiteScraper() as scraper:
        for row in firm_rows:
            firm = _row_to_firm(row)
            if not firm.website:
                logger.info("skip firm {}: no website", firm.id)
                continue
            logger.info("enrich firm {} {}", firm.id, firm.name)
            try:
                result = await scraper.enrich(firm)
            except Exception as e:                                # noqa: BLE001
                logger.error("enrich failed for firm {}: {}", firm.id, e)
                continue
            _persist_enrichment(conn, firm, result)


def _row_to_firm(row: dict[str, Any]) -> FirmRecord:
    """Lossy reverse of upsert_firm — enough to drive enrichment."""
    return FirmRecord(
        id=row["id"],
        place_id=row.get("place_id"),
        name=row["name"],
        normalized_name=row["normalized_name"],
        address=row.get("address"),
        city=row.get("city"),
        state=row.get("state"),
        postal_code=row.get("postal_code"),
        country=row.get("country"),
        latitude=row.get("latitude"),
        longitude=row.get("longitude"),
        phone=row.get("phone"),
        website=row.get("website") or None,
        email=row.get("email"),
        rating=row.get("rating"),
        user_ratings_total=row.get("user_ratings_total"),
        business_status=row.get("business_status"),
        discovered_via=SourceName(row.get("discovered_via", "google_places")),
    )


def _persist_enrichment(conn, firm: FirmRecord, result: EnrichmentResult) -> None:
    if result.errors:
        logger.warning("firm {} errors: {}", firm.id, result.errors)

    patch = dict(result.patch)
    attorneys = patch.pop("_attorneys", [])

    with db_module.transaction(conn):
        if patch:
            db_module.apply_firm_patch(conn, firm.id, patch)
        for a in attorneys:
            db_module.upsert_attorney(conn, firm.id, a)
        for sig in result.signals:
            db_module.insert_signal(
                conn,
                {
                    "firm_id": sig.firm_id,
                    "kind": sig.kind.value,
                    "source": sig.source.value,
                    "observed_at": sig.observed_at.isoformat(),
                    "summary": sig.summary,
                    "payload": sig.payload,
                },
            )
    logger.info(
        "firm {} -> patched {}, attorneys={}, signals={}",
        firm.id, list(patch.keys()), len(attorneys), len(result.signals),
    )


@app.command(name="dbinit")
def dbinit() -> None:
    """Create or migrate the SQLite database. Idempotent."""
    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)
    typer.echo(f"DB ready at {settings.db_path}")


@app.command()
def score(
    firm_id: Annotated[int | None, typer.Option("--firm-id", help="Score a single firm")] = None,
    all_: Annotated[bool, typer.Option("--all", help="Score every firm")] = False,
    recompute: Annotated[bool, typer.Option("--recompute", help="Re-score already-scored firms")] = False,
) -> None:
    """Compute the lead score for firms and persist into the scores table."""
    if not firm_id and not all_:
        typer.echo("error: pass --firm-id N or --all", err=True)
        raise typer.Exit(code=2)
    _run_score(firm_id=firm_id, all_=all_, recompute=recompute)


def _run_score(*, firm_id: int | None, all_: bool, recompute: bool) -> None:
    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)

    if firm_id is not None:
        row = db_module.fetch_firm(conn, firm_id)
        if row is None:
            typer.echo(f"error: firm {firm_id} not found", err=True)
            raise typer.Exit(code=2)
        rows = [row]
    else:
        rows = db_module.firms_needing_score(conn, recompute=recompute)

    if not rows:
        typer.echo("nothing to score (use --recompute to rescore everyone)")
        return

    weights = get_scoring_weights()
    buckets = get_score_buckets()
    now = datetime.now(timezone.utc)

    with db_module.transaction(conn):
        for row in rows:
            inputs = _row_to_scoring_inputs(row, now=now)
            breakdown = score_firm(inputs, weights, buckets)
            db_module.upsert_score(
                conn,
                firm_id=breakdown.firm_id,
                score=breakdown.score,
                bucket=breakdown.bucket,
                components=[
                    {"key": c.key, "weight": c.weight, "triggered": c.triggered, "note": c.note}
                    for c in breakdown.components
                ],
                computed_at=breakdown.computed_at.isoformat(),
            )
            logger.info(
                "score firm {} -> {} ({})",
                breakdown.firm_id, breakdown.score, breakdown.bucket,
            )
    typer.echo(f"scored {len(rows)} firm(s)")


def _row_to_scoring_inputs(row: dict[str, Any], *, now: datetime) -> ScoringInputs:
    raw_post = row.get("last_website_post_at")
    last_post: datetime | None
    if raw_post:
        try:
            last_post = datetime.fromisoformat(raw_post)
            if last_post.tzinfo is None:
                last_post = last_post.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            last_post = None
    else:
        last_post = None

    has_pi = row.get("has_pi_practice_page")
    return ScoringInputs(
        firm_id=row["id"],
        rating=row.get("rating"),
        user_ratings_total=row.get("user_ratings_total"),
        has_pi_practice_page=bool(has_pi) if has_pi is not None else None,
        attorney_count=row.get("attorney_count"),
        last_website_post_at=last_post,
        established_year=row.get("established_year"),
        website=row.get("website") or None,
        now=now,
    )


@app.command()
def dashboard(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
    reload_: Annotated[bool, typer.Option("--reload", help="autoreload on file change (dev)")] = False,
) -> None:
    """Run the dashboard at http://HOST:PORT."""
    import uvicorn

    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)
    typer.echo(f"pifinder dashboard → http://{host}:{port}")
    uvicorn.run(
        "pifinder.web.app:app",
        host=host,
        port=port,
        reload=reload_,
        log_level="info",
    )


@app.command()
def info() -> None:
    """Show basic config / DB stats."""
    settings = get_settings()
    typer.echo(f"db:    {settings.db_path}")
    typer.echo(f"cache: {settings.cache_dir}")
    has_key = bool(settings.google_places_api_key and settings.google_places_api_key.get_secret_value())
    typer.echo(f"google_places_key: {'set' if has_key else 'MISSING'}")

    if settings.db_path.exists():
        conn = db_module.connect(settings.db_path)
        n = conn.execute("SELECT COUNT(*) AS n FROM firms").fetchone()["n"]
        runs = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
        typer.echo(f"firms: {n}")
        typer.echo(f"runs:  {runs}")


def _write_csv(firms: list[FirmRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "name", "address", "city", "state", "postal_code",
        "phone", "website", "rating", "user_ratings_total",
        "latitude", "longitude", "business_status", "place_id",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for firm in firms:
            row = firm.model_dump(mode="json")
            writer.writerow({k: row.get(k, "") for k in fields})


if __name__ == "__main__":
    app()
