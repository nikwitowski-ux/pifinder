"""Integration: DB persistence for ScoreBreakdown + the CLI helper that scores
a list of firm rows and writes them via db.upsert_score.

The persistence path is what the dashboard reads — so this test is the contract
between scoring.py (pure logic) and the UI.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from pifinder import db as db_module
from pifinder.models import FirmRecord, SourceName


@pytest.fixture
def conn(tmp_path):
    c = db_module.connect(tmp_path / "firms.db")
    db_module.migrate(c)
    yield c
    c.close()


def _seed(conn) -> tuple[int, int, int]:
    """Three firms covering hot/warm/cold output shapes."""
    hot = FirmRecord(
        place_id="P_HOT", name="Hot Firm", normalized_name="hot",
        website="https://hot.example.com", rating=4.8,
        user_ratings_total=325, discovered_via=SourceName.google_places,
    )
    warm = FirmRecord(
        place_id="P_WARM", name="Warm Firm", normalized_name="warm",
        website="https://warm.example.com", rating=4.3,
        user_ratings_total=60, discovered_via=SourceName.google_places,
    )
    cold = FirmRecord(
        place_id="P_COLD", name="Cold Firm", normalized_name="cold",
        website=None, rating=3.5, user_ratings_total=4,
        discovered_via=SourceName.google_places,
    )
    h = db_module.upsert_firm(conn, hot)
    w = db_module.upsert_firm(conn, warm)
    c = db_module.upsert_firm(conn, cold)
    # Give hot firm enrichment fields so the multi-attorney + pi + established triggers fire
    conn.execute(
        "UPDATE firms SET attorney_count = 7, has_pi_practice_page = 1, established_year = 1995, "
        "last_website_post_at = ? WHERE id = ?",
        (datetime(2026, 4, 12, tzinfo=timezone.utc).isoformat(), h),
    )
    return h, w, c


def test_upsert_score_writes_and_replaces(conn):
    h, _, _ = _seed(conn)
    db_module.upsert_score(
        conn,
        firm_id=h,
        score=88,
        bucket="hot",
        components=[{"key": "high_review_volume", "weight": 30, "triggered": True, "note": "325 google reviews"}],
        computed_at="2026-05-14T00:00:00+00:00",
    )
    row = conn.execute("SELECT score, bucket, components_json, computed_at FROM scores WHERE firm_id = ?", (h,)).fetchone()
    assert row["score"] == 88
    assert row["bucket"] == "hot"
    comps = json.loads(row["components_json"])
    assert comps[0]["key"] == "high_review_volume"

    # Re-upsert replaces
    db_module.upsert_score(
        conn, firm_id=h, score=72, bucket="hot",
        components=[{"key": "quality_rating", "weight": 20, "triggered": True}],
        computed_at="2026-05-14T01:00:00+00:00",
    )
    row = conn.execute("SELECT score, components_json FROM scores WHERE firm_id = ?", (h,)).fetchone()
    assert row["score"] == 72
    assert json.loads(row["components_json"])[0]["key"] == "quality_rating"
    # Only one row per firm.
    n = conn.execute("SELECT COUNT(*) AS n FROM scores WHERE firm_id = ?", (h,)).fetchone()["n"]
    assert n == 1


def test_firms_needing_score_default_skips_already_scored(conn):
    h, w, c = _seed(conn)
    # Score just hot
    db_module.upsert_score(conn, firm_id=h, score=80, bucket="hot", components=[])

    pending = db_module.firms_needing_score(conn, recompute=False)
    ids = {f["id"] for f in pending}
    assert ids == {w, c}                       # hot is already scored

    everyone = db_module.firms_needing_score(conn, recompute=True)
    assert {f["id"] for f in everyone} == {h, w, c}


def test_cli_score_writes_expected_breakdown(conn, tmp_path, monkeypatch):
    """End-to-end: cli._run_score reads firm rows, scores them, persists."""
    monkeypatch.setenv("PIFINDER_DB_PATH", str(tmp_path / "firms.db"))
    monkeypatch.setenv("PIFINDER_CACHE_DIR", str(tmp_path / "cache"))
    from pifinder import config as cfg
    cfg.get_settings.cache_clear()
    cfg.get_yaml_config.cache_clear()

    # Use a fresh conn pointed at the patched path
    c2 = db_module.connect(tmp_path / "firms.db")
    db_module.migrate(c2)
    h, w, cold = _seed(c2)

    from pifinder.cli import _run_score
    _run_score(firm_id=None, all_=True, recompute=True)

    rows = {
        r["firm_id"]: r
        for r in c2.execute("SELECT firm_id, score, bucket FROM scores").fetchall()
    }
    assert set(rows.keys()) == {h, w, cold}

    # Hot firm: high(30) + quality(20) + multi(15) + pi(10) + established(10) + website(5)
    # recent_activity uses datetime.now() in the CLI path, so it MAY or MAY NOT
    # fire depending on real wall-clock vs. the seeded date. Don't assert it.
    hot_score = rows[h]["score"]
    assert hot_score >= 90                     # at minimum the 6 deterministic components
    assert rows[h]["bucket"] == "hot"

    # Warm firm: moderate(15) + website(5) = 20  → cold per our bucket thresholds.
    # (red_max=39). So this is actually cold given our weight set — verify reality.
    assert rows[w]["score"] == 20
    assert rows[w]["bucket"] == "cold"

    # Cold firm: nothing fires (rating 3.5, no reviews>=50, no website, no enrichment) = 0
    assert rows[cold]["score"] == 0
    assert rows[cold]["bucket"] == "cold"

    cfg.get_settings.cache_clear()
    cfg.get_yaml_config.cache_clear()
