from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SourceName(StrEnum):
    google_places = "google_places"
    firm_website = "firm_website"
    meta_ads = "meta_ads"
    news = "news"
    directories = "directories"


class SignalKind(StrEnum):
    meta_ad = "meta_ad"
    news = "news"
    hiring = "hiring"
    review = "review"
    website = "website"


class FirmRecord(StrictModel):
    """Discovery-layer record. Enrichment fills in everything below the line."""

    # --- identity ---
    id: int | None = None
    place_id: str | None = None            # google places primary key when available
    name: str
    normalized_name: str                   # lowercase, punctuation-stripped, used for dedup

    # --- contact / location ---
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = "US"
    latitude: float | None = None
    longitude: float | None = None
    phone: str | None = None
    website: HttpUrl | None = None
    email: str | None = None

    # --- google places fields ---
    rating: float | None = None            # 0-5
    user_ratings_total: int | None = None
    business_status: str | None = None     # OPERATIONAL / CLOSED_TEMPORARILY / etc.

    # --- enrichment derived ---
    attorney_count: int | None = None
    has_pi_practice_page: bool | None = None
    last_website_post_at: datetime | None = None
    established_year: int | None = None

    # --- bookkeeping ---
    discovered_via: SourceName = SourceName.google_places
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    raw: dict[str, Any] | None = None      # source-specific payload for debugging


class Attorney(StrictModel):
    id: int | None = None
    firm_id: int
    name: str
    title: str | None = None               # "Partner", "Associate", "Of Counsel"
    practice_areas: list[str] = Field(default_factory=list)
    bio_url: HttpUrl | None = None
    years_practicing: int | None = None
    bar_admissions: list[str] = Field(default_factory=list)
    source: SourceName = SourceName.firm_website


class Signal(StrictModel):
    id: int | None = None
    firm_id: int
    kind: SignalKind
    source: SourceName
    observed_at: datetime
    summary: str                           # human-readable one-liner
    payload: dict[str, Any] = Field(default_factory=dict)


class MetaAdSignal(StrictModel):
    """Payload schema for kind=meta_ad signals."""

    is_running_ads: bool
    active_ad_count: int = 0
    ad_themes: list[str] = Field(default_factory=list)
    regions_targeted: list[str] = Field(default_factory=list)
    fb_page_id: str | None = None


class NewsItem(StrictModel):
    title: str
    url: HttpUrl
    published_at: datetime
    snippet: str | None = None
    matched_keywords: list[str] = Field(default_factory=list)


class EnrichmentResult(StrictModel):
    """What every enricher returns. Sources that can't contribute return empty lists."""

    firm_id: int
    source: SourceName
    signals: list[Signal] = Field(default_factory=list)
    patch: dict[str, Any] = Field(default_factory=dict)   # fields to merge into FirmRecord
    errors: list[str] = Field(default_factory=list)


class ScoreComponent(StrictModel):
    key: str                               # matches a weight key in config.yaml
    weight: int
    triggered: bool
    note: str | None = None


class ScoreBreakdown(StrictModel):
    firm_id: int
    score: int                             # 0-100
    bucket: str                            # red / yellow / green
    components: list[ScoreComponent]
    computed_at: datetime


class DiscoverRun(StrictModel):
    id: int | None = None
    started_at: datetime
    finished_at: datetime | None = None
    location: str
    radius_meters: int
    query: str
    firm_count: int = 0
    error: str | None = None
