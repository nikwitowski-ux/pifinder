"""Persistence layer for enrichment results. Proves patch + attorneys + signals land in sqlite."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pifinder import db as db_module
from pifinder.models import (
    EnrichmentResult,
    FirmRecord,
    Signal,
    SignalKind,
    SourceName,
)


@pytest.fixture
def conn(tmp_path):
    c = db_module.connect(tmp_path / "firms.db")
    db_module.migrate(c)
    yield c
    c.close()


def _seed_firm(conn) -> int:
    firm = FirmRecord(
        place_id="PLACE_X",
        name="Smith Injury Law, PLLC",
        normalized_name="smith injury law pllc".replace(" pllc", "").strip(),  # use the normalizer in practice
        website="https://smith.example.com/",
        discovered_via=SourceName.google_places,
    )
    return db_module.upsert_firm(conn, firm)


def test_apply_firm_patch_filters_unknown_keys(conn):
    firm_id = _seed_firm(conn)
    db_module.apply_firm_patch(
        conn,
        firm_id,
        {
            "attorney_count": 7,
            "has_pi_practice_page": True,
            "established_year": 1990,
            "last_website_post_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
            # private + unknown keys must be silently dropped
            "_attorneys": [{"name": "X"}],
            "name": "EVIL OVERWRITE ATTEMPT",
            "place_id": "EVIL",
        },
    )
    row = db_module.fetch_firm(conn, firm_id)
    assert row["attorney_count"] == 7
    assert row["has_pi_practice_page"] == 1
    assert row["established_year"] == 1990
    assert row["last_website_post_at"].startswith("2026-03-01")
    # Untouched
    assert row["name"] == "Smith Injury Law, PLLC"
    assert row["place_id"] == "PLACE_X"


def test_upsert_attorney_idempotent(conn):
    firm_id = _seed_firm(conn)
    db_module.upsert_attorney(conn, firm_id, {"name": "Jane Doe", "title": None})
    db_module.upsert_attorney(conn, firm_id, {"name": "Jane Doe", "title": "Founding Partner"})
    db_module.upsert_attorney(conn, firm_id, {"name": "John Smith", "title": "Associate"})

    rows = conn.execute("SELECT name, title FROM attorneys WHERE firm_id = ? ORDER BY name", (firm_id,)).fetchall()
    by_name = {r["name"]: r["title"] for r in rows}
    assert by_name == {"Jane Doe": "Founding Partner", "John Smith": "Associate"}


def test_insert_signal_and_query_back(conn):
    firm_id = _seed_firm(conn)
    sig = Signal(
        firm_id=firm_id,
        kind=SignalKind.website,
        source=SourceName.firm_website,
        observed_at=datetime(2026, 3, 12, tzinfo=timezone.utc),
        summary="latest post 2026-03-12",
        payload={"practice_areas": ["car accident", "slip and fall"]},
    )
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
    rows = conn.execute("SELECT kind, summary, payload_json FROM signals WHERE firm_id = ?", (firm_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "website"
    assert "car accident" in rows[0]["payload_json"]


def test_full_enrichment_round_trip(conn):
    """EnrichmentResult shape end-to-end: cli helper -> db helpers."""
    from pifinder.cli import _persist_enrichment, _row_to_firm

    firm_id = _seed_firm(conn)
    firm = _row_to_firm(db_module.fetch_firm(conn, firm_id))

    result = EnrichmentResult(firm_id=firm_id, source=SourceName.firm_website)
    result.patch.update(
        attorney_count=2,
        has_pi_practice_page=True,
        last_website_post_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        established_year=1985,
        _attorneys=[
            {"name": "Jane Doe", "title": "Founding Partner", "bio_url": "https://smith.example.com/a/jane",
             "practice_areas": ["car accident"]},
            {"name": "John Smith", "title": "Associate", "bio_url": None, "practice_areas": []},
        ],
    )
    result.signals.append(
        Signal(
            firm_id=firm_id,
            kind=SignalKind.website,
            source=SourceName.firm_website,
            observed_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            summary="latest post 2026-03-01",
            payload={"practice_areas": ["car accident"]},
        )
    )

    _persist_enrichment(conn, firm, result)

    row = db_module.fetch_firm(conn, firm_id)
    assert row["attorney_count"] == 2
    assert row["has_pi_practice_page"] == 1
    assert row["established_year"] == 1985

    attorneys = conn.execute(
        "SELECT name, title FROM attorneys WHERE firm_id = ? ORDER BY name", (firm_id,)
    ).fetchall()
    assert {a["name"] for a in attorneys} == {"Jane Doe", "John Smith"}

    sigs = conn.execute("SELECT kind, summary FROM signals WHERE firm_id = ?", (firm_id,)).fetchall()
    assert len(sigs) == 1
    assert sigs[0]["kind"] == "website"
