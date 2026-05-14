"""End-to-end smoke for the discover pipeline with the Places API mocked.

Proves: source parses Places v1 response -> FirmRecord -> sqlite upsert -> CSV.
No network. No API key required.
"""
from __future__ import annotations

import csv
from pathlib import Path

import httpx
import pytest
import respx

from pifinder import db as db_module
from pifinder.cli import _run_discover, _write_csv
from pifinder.sources.google_places import SEARCH_TEXT_URL, GooglePlacesSource


PLACES_PAGE_1 = {
    "places": [
        {
            "id": "PLACE_AAA",
            "displayName": {"text": "Smith & Jones Personal Injury Attorneys"},
            "formattedAddress": "100 Main St, Irvine, CA 92614, USA",
            "addressComponents": [
                {"types": ["locality"], "longText": "Irvine", "shortText": "Irvine"},
                {"types": ["administrative_area_level_1"], "longText": "California", "shortText": "CA"},
                {"types": ["postal_code"], "longText": "92614", "shortText": "92614"},
                {"types": ["country"], "longText": "United States", "shortText": "US"},
            ],
            "location": {"latitude": 33.6846, "longitude": -117.8265},
            "internationalPhoneNumber": "+1 949-555-0101",
            "websiteUri": "https://example-pi-firm.com/",
            "rating": 4.6,
            "userRatingCount": 187,
            "businessStatus": "OPERATIONAL",
            "types": ["lawyer", "point_of_interest"],
        },
        {
            "id": "PLACE_BBB",
            "displayName": {"text": "Doe Injury Law LLP"},
            "formattedAddress": "200 Birch Ave, Newport Beach, CA 92660, USA",
            "addressComponents": [
                {"types": ["locality"], "longText": "Newport Beach", "shortText": "Newport Beach"},
                {"types": ["administrative_area_level_1"], "longText": "California", "shortText": "CA"},
                {"types": ["postal_code"], "longText": "92660", "shortText": "92660"},
                {"types": ["country"], "longText": "United States", "shortText": "US"},
            ],
            "location": {"latitude": 33.6189, "longitude": -117.9298},
            "nationalPhoneNumber": "(949) 555-0202",
            "websiteUri": "https://doeinjurylaw.example.com",
            "rating": 4.2,
            "userRatingCount": 42,
            "businessStatus": "OPERATIONAL",
            "types": ["lawyer"],
        },
    ],
    # No nextPageToken -> single page.
}


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Point DB + cache at tmp_path, inject a fake API key, clear settings cache."""
    monkeypatch.setenv("PIFINDER_DB_PATH", str(tmp_path / "firms.db"))
    monkeypatch.setenv("PIFINDER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key-not-real")

    from pifinder import config as cfg

    cfg.get_settings.cache_clear()
    cfg.get_yaml_config.cache_clear()
    yield tmp_path
    cfg.get_settings.cache_clear()
    cfg.get_yaml_config.cache_clear()


@respx.mock
async def test_discover_end_to_end(isolated_env, tmp_path):
    respx.post(SEARCH_TEXT_URL).mock(return_value=httpx.Response(200, json=PLACES_PAGE_1))

    firms = await _run_discover(
        location="Orange County, CA",
        radius_meters=40000,
        query="personal injury law firm",
    )

    assert len(firms) == 2
    names = {f.name for f in firms}
    assert names == {"Smith & Jones Personal Injury Attorneys", "Doe Injury Law LLP"}

    # DB persisted
    from pifinder.config import get_settings
    conn = db_module.connect(get_settings().db_path)
    rows = conn.execute(
        "SELECT name, city, state, phone, website, rating, user_ratings_total, place_id "
        "FROM firms ORDER BY name"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["name"] == "Doe Injury Law LLP"
    assert rows[0]["city"] == "Newport Beach"
    assert rows[0]["state"] == "CA"
    assert rows[0]["website"] == "https://doeinjurylaw.example.com/"
    assert rows[1]["place_id"] == "PLACE_AAA"
    assert rows[1]["rating"] == 4.6

    # Run is tracked + finished
    run = conn.execute("SELECT firm_count, error, finished_at FROM runs").fetchone()
    assert run["firm_count"] == 2
    assert run["error"] is None
    assert run["finished_at"] is not None

    # CSV export
    csv_path = tmp_path / "firms.csv"
    _write_csv(firms, csv_path)
    with csv_path.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    assert {r["name"] for r in rows} == names

    # Idempotent re-run (same input → same row count, place_id is unique)
    respx.post(SEARCH_TEXT_URL).mock(return_value=httpx.Response(200, json=PLACES_PAGE_1))
    firms_again = await _run_discover(
        location="Orange County, CA",
        radius_meters=40000,
        query="personal injury law firm",
    )
    assert len(firms_again) == 2
    n = conn.execute("SELECT COUNT(*) AS n FROM firms").fetchone()["n"]
    assert n == 2, f"upsert leaked duplicate rows: {n}"


@respx.mock
async def test_discover_paginates(isolated_env):
    page_1 = {
        **PLACES_PAGE_1,
        "nextPageToken": "token-xyz",
    }
    page_2 = {
        "places": [
            {
                "id": "PLACE_CCC",
                "displayName": {"text": "Third Firm PLLC"},
                "formattedAddress": "300 Oak St, Anaheim, CA 92805, USA",
                "addressComponents": [
                    {"types": ["locality"], "shortText": "Anaheim"},
                    {"types": ["administrative_area_level_1"], "shortText": "CA"},
                ],
                "rating": 3.9,
                "userRatingCount": 14,
            }
        ]
    }
    route = respx.post(SEARCH_TEXT_URL).mock(side_effect=[
        httpx.Response(200, json=page_1),
        httpx.Response(200, json=page_2),
    ])

    # Speed up the nextPageToken sleep between pages.
    import pifinder.sources.google_places as gp
    real_sleep = gp.asyncio.sleep
    async def fast_sleep(_):
        await real_sleep(0)
    gp.asyncio.sleep = fast_sleep  # type: ignore[assignment]
    try:
        firms = await _run_discover(
            location="Orange County, CA",
            radius_meters=40000,
            query="personal injury law firm",
        )
    finally:
        gp.asyncio.sleep = real_sleep  # type: ignore[assignment]

    assert route.call_count == 2
    assert len(firms) == 3
    assert any(f.place_id == "PLACE_CCC" for f in firms)


def test_normalize_firm_name():
    from pifinder.db import normalize_firm_name
    assert normalize_firm_name("The Law Offices of John P. Smith, LLP") == "john p smith"
    assert normalize_firm_name("Smith & Jones, P.C.") == "smith jones"
    assert normalize_firm_name("DOE INJURY LAW PLLC") == "doe injury law"
    # Same firm, different casing / suffix / punctuation -> same key.
    a = normalize_firm_name("Doe Injury Law, PLLC")
    b = normalize_firm_name("DOE INJURY LAW PLLC")
    assert a == b
