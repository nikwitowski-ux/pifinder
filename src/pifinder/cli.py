from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger

from . import db as db_module
from .config import get_discovery_defaults, get_settings
from .logging_setup import init_logging
from .models import FirmRecord
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


@app.command(name="dbinit")
def dbinit() -> None:
    """Create or migrate the SQLite database. Idempotent."""
    settings = get_settings()
    conn = db_module.connect(settings.db_path)
    db_module.migrate(conn)
    typer.echo(f"DB ready at {settings.db_path}")


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
