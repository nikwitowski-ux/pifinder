"""Unit tests for the _Aggregated roll-up logic. No Playwright."""
from __future__ import annotations

from datetime import datetime, timezone

from pifinder.sources._html_parser import PageExtract, ParsedAttorney
from pifinder.sources.firm_website import _Aggregated, _merge_into_result
from pifinder.models import EnrichmentResult, FirmRecord, SourceName


def _firm(id_: int = 1, website: str = "https://x.example.com") -> FirmRecord:
    return FirmRecord(id=id_, name="X", normalized_name="x", website=website)


def test_aggregator_merges_attorneys_keeping_richer_title():
    agg = _Aggregated()
    agg.absorb(PageExtract(attorneys=[ParsedAttorney(name="Jane Doe", title=None)]))
    agg.absorb(PageExtract(attorneys=[ParsedAttorney(name="Jane Doe", title="Founding Partner")]))
    assert len(agg.attorney_by_name) == 1
    assert agg.attorney_by_name["jane doe"].title == "Founding Partner"


def test_aggregator_practice_areas_union_and_pi_flag():
    agg = _Aggregated()
    agg.absorb(PageExtract(practice_areas=["car accident", "slip and fall"]))
    agg.absorb(PageExtract(practice_areas=["slip and fall", "medical malpractice"], has_pi_terms=True))
    assert agg.practice_areas == {"car accident", "slip and fall", "medical malpractice"}
    assert agg.has_pi_terms is True


def test_aggregator_latest_post_keeps_most_recent():
    agg = _Aggregated()
    agg.absorb(PageExtract(last_post_at=datetime(2024, 1, 1, tzinfo=timezone.utc)))
    agg.absorb(PageExtract(last_post_at=datetime(2026, 3, 12, tzinfo=timezone.utc)))
    agg.absorb(PageExtract(last_post_at=datetime(2025, 6, 1, tzinfo=timezone.utc)))
    assert agg.last_post_at == datetime(2026, 3, 12, tzinfo=timezone.utc)


def test_aggregator_established_year_keeps_earliest():
    agg = _Aggregated()
    agg.absorb(PageExtract(established_year=2010))
    agg.absorb(PageExtract(established_year=1985))
    agg.absorb(PageExtract(established_year=None))
    assert agg.established_year == 1985


def test_merge_into_result_populates_patch_and_signal():
    agg = _Aggregated()
    agg.absorb(PageExtract(
        attorneys=[
            ParsedAttorney(name="Jane Doe", title="Partner", bio_url="https://x.example.com/a/jane"),
            ParsedAttorney(name="John Smith", title="Associate"),
        ],
        practice_areas=["car accident", "personal injury"],
        has_pi_terms=True,
        last_post_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        established_year=1995,
    ))

    result = EnrichmentResult(firm_id=1, source=SourceName.firm_website)
    _merge_into_result(result, _firm(), agg)

    assert result.patch["attorney_count"] == 2
    assert result.patch["has_pi_practice_page"] is True
    assert result.patch["established_year"] == 1995
    assert result.patch["last_website_post_at"] == datetime(2026, 3, 1, tzinfo=timezone.utc)
    attorneys = result.patch["_attorneys"]
    assert {a["name"] for a in attorneys} == {"Jane Doe", "John Smith"}

    # Recent-activity signal exists with practice areas in payload.
    assert len(result.signals) == 1
    sig = result.signals[0]
    assert sig.kind.value == "website"
    assert sig.observed_at == datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert "car accident" in sig.payload["practice_areas"]


def test_merge_no_post_produces_no_signal():
    agg = _Aggregated()
    agg.absorb(PageExtract(
        attorneys=[ParsedAttorney(name="Jane Doe", title="Partner")],
        practice_areas=["personal injury"],
        has_pi_terms=True,
        last_post_at=None,
    ))
    result = EnrichmentResult(firm_id=1, source=SourceName.firm_website)
    _merge_into_result(result, _firm(), agg)
    assert result.patch["has_pi_practice_page"] is True
    assert result.signals == []
    assert result.patch["last_website_post_at"] is None
