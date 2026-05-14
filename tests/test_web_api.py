"""Smoke + filter tests for the FastAPI dashboard.

Uses httpx.ASGITransport so we never bind a socket — direct in-process requests
into the FastAPI app. Each test gets an isolated DB+cache in a tmp_path.
"""
from __future__ import annotations

import httpx
import pytest

from pifinder import db as db_module
from pifinder.models import FirmRecord, SourceName


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIFINDER_DB_PATH", str(tmp_path / "firms.db"))
    monkeypatch.setenv("PIFINDER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "not-real")

    from pifinder import config as cfg
    cfg.get_settings.cache_clear()
    cfg.get_yaml_config.cache_clear()

    # Re-import the app so it picks up the patched settings at module-import time.
    import importlib
    import pifinder.web.app as webmod
    importlib.reload(webmod)

    yield tmp_path, webmod.app

    cfg.get_settings.cache_clear()
    cfg.get_yaml_config.cache_clear()


def _seed_three_firms(db_path):
    conn = db_module.connect(db_path)
    db_module.migrate(conn)
    firms = [
        FirmRecord(
            place_id="P_HOT", name="Alpha Injury Law",
            normalized_name="alpha injury law",
            city="Irvine", state="CA",
            latitude=33.6846, longitude=-117.8265,
            phone="+1 949-555-0001",
            website="https://alpha.example.com/",
            rating=4.7, user_ratings_total=210,
            discovered_via=SourceName.google_places,
        ),
        FirmRecord(
            place_id="P_WARM", name="Beta Trial Lawyers, LLP",
            normalized_name="beta trial lawyers",
            city="Anaheim", state="CA",
            latitude=33.8366, longitude=-117.9143,
            phone="+1 714-555-0002",
            website="https://beta.example.com",
            rating=4.2, user_ratings_total=60,
            discovered_via=SourceName.google_places,
        ),
        FirmRecord(
            place_id="P_COLD", name="Gamma Estate Planning",
            normalized_name="gamma estate planning",
            city="Newport Beach", state="CA",
            latitude=33.6189, longitude=-117.9298,
            phone=None,
            website=None,
            rating=3.8, user_ratings_total=8,
            discovered_via=SourceName.google_places,
        ),
    ]
    ids = [db_module.upsert_firm(conn, f) for f in firms]
    # Give two firms scores so the bucket logic gets exercised.
    conn.execute(
        "INSERT INTO scores (firm_id, score, bucket, components_json) VALUES (?, ?, ?, ?)",
        (ids[0], 82, "hot", "[]"),
    )
    conn.execute(
        "INSERT INTO scores (firm_id, score, bucket, components_json) VALUES (?, ?, ?, ?)",
        (ids[1], 55, "warm", "[]"),
    )
    # Mark alpha as enriched for the enriched_only filter test.
    conn.execute("UPDATE firms SET attorney_count = 5, has_pi_practice_page = 1 WHERE id = ?", (ids[0],))
    return ids


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_healthz(isolated_env):
    _, app = isolated_env
    async with await _client(app) as c:
        r = await c.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


async def test_index_renders(isolated_env):
    tmp, app = isolated_env
    _seed_three_firms(tmp / "firms.db")
    async with await _client(app) as c:
        r = await c.get("/")
        assert r.status_code == 200
        assert "PIFINDER" in r.text
        assert "id=\"map\"" in r.text
        assert "id=\"grid\"" in r.text


async def test_api_firms_shape_and_bucket_classification(isolated_env):
    tmp, app = isolated_env
    _seed_three_firms(tmp / "firms.db")
    async with await _client(app) as c:
        r = await c.get("/api/firms")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 3
        names = {f["name"] for f in data["firms"]}
        assert names == {"Alpha Injury Law", "Beta Trial Lawyers, LLP", "Gamma Estate Planning"}

        # Sorted by score desc — alpha (82) first
        assert data["firms"][0]["name"] == "Alpha Injury Law"
        assert data["firms"][0]["bucket"] == "hot"
        assert data["firms"][0]["score"] == 82

        beta = next(f for f in data["firms"] if f["name"].startswith("Beta"))
        assert beta["bucket"] == "warm"

        gamma = next(f for f in data["firms"] if f["name"].startswith("Gamma"))
        assert gamma["score"] is None
        assert gamma["bucket"] == "unscored"


async def test_api_firms_filters(isolated_env):
    tmp, app = isolated_env
    _seed_three_firms(tmp / "firms.db")
    async with await _client(app) as c:
        # name search
        r = await c.get("/api/firms?q=alpha")
        assert r.status_code == 200
        assert r.json()["count"] == 1

        # min_score = 60 → only hot
        r = await c.get("/api/firms?min_score=60")
        assert r.json()["count"] == 1
        assert r.json()["firms"][0]["name"] == "Alpha Injury Law"

        # has_website=true drops gamma
        r = await c.get("/api/firms?has_website=true")
        names = {f["name"] for f in r.json()["firms"]}
        assert "Gamma Estate Planning" not in names

        # enriched_only=true keeps just alpha (attorney_count > 0)
        r = await c.get("/api/firms?enriched_only=true")
        firms = r.json()["firms"]
        assert len(firms) == 1
        assert firms[0]["name"] == "Alpha Injury Law"


async def test_firm_detail_html_and_api(isolated_env):
    tmp, app = isolated_env
    ids = _seed_three_firms(tmp / "firms.db")
    alpha_id = ids[0]

    # Seed an attorney + a signal so the detail page exercises both sections.
    conn = db_module.connect(tmp / "firms.db")
    db_module.upsert_attorney(conn, alpha_id, {
        "name": "Jane Doe", "title": "Founding Partner",
        "bio_url": "https://alpha.example.com/a/jane",
        "practice_areas": ["car accident", "slip and fall"],
    })
    db_module.insert_signal(conn, {
        "firm_id": alpha_id,
        "kind": "website",
        "source": "firm_website",
        "observed_at": "2026-03-01T00:00:00+00:00",
        "summary": "latest post 2026-03-01",
        "payload": {"practice_areas": ["car accident"]},
    })

    async with await _client(app) as c:
        r = await c.get(f"/firm/{alpha_id}")
        assert r.status_code == 200
        assert "Alpha Injury Law" in r.text
        assert "Jane Doe" in r.text

        r = await c.get(f"/api/firms/{alpha_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "Alpha Injury Law"
        assert body["bucket"] == "hot"
        assert len(body["attorneys"]) == 1
        assert body["attorneys"][0]["practice_areas"] == ["car accident", "slip and fall"]
        assert len(body["signals"]) == 1
        assert body["signals"][0]["kind"] == "website"


async def test_csv_export_respects_filters(isolated_env):
    tmp, app = isolated_env
    _seed_three_firms(tmp / "firms.db")
    async with await _client(app) as c:
        r = await c.get("/api/export.csv?min_score=60")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        body = r.text
        # Header + 1 firm
        lines = [ln for ln in body.splitlines() if ln.strip()]
        assert len(lines) == 2
        assert "Alpha Injury Law" in body
        assert "Gamma Estate Planning" not in body


async def test_404_on_missing_firm(isolated_env):
    _, app = isolated_env
    async with await _client(app) as c:
        r = await c.get("/firm/9999")
        assert r.status_code == 404
        r = await c.get("/api/firms/9999")
        assert r.status_code == 404
