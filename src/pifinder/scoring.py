"""Pure scoring engine. No I/O. No DB calls. Just inputs -> ScoreBreakdown.

Components are aggregation-derivable from Places + firm-website enrichment.
Buying-intent signals (Meta ads, news) are deliberately absent until those
sources are built; adding them is a matter of (a) a weight key in config.yaml
and (b) a new entry in COMPONENT_EVALUATORS below.

The orchestrator silently ignores weight keys that have no evaluator, so
config can lead implementation without breaking runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


# --- public types ---

@dataclass
class ScoringInputs:
    firm_id: int
    rating: float | None = None
    user_ratings_total: int | None = None
    has_pi_practice_page: bool | None = None
    attorney_count: int | None = None
    last_website_post_at: datetime | None = None
    established_year: int | None = None
    website: str | None = None
    now: datetime = datetime.now(timezone.utc)


@dataclass
class ScoreComponent:
    key: str
    weight: int
    triggered: bool
    note: str | None = None


@dataclass
class ScoreBreakdown:
    firm_id: int
    score: int
    bucket: str
    components: list[ScoreComponent]
    computed_at: datetime


# --- component evaluators ---
# Each evaluator returns (triggered, note). They are pure: only inputs matter.

Evaluator = Callable[[ScoringInputs], tuple[bool, str | None]]


def _high_review_volume(i: ScoringInputs) -> tuple[bool, str | None]:
    n = i.user_ratings_total
    if n is None or n < 100:
        return False, None
    return True, f"{n} google reviews"


def _moderate_review_volume(i: ScoringInputs) -> tuple[bool, str | None]:
    n = i.user_ratings_total
    if n is None or n < 50 or n >= 100:
        return False, None
    return True, f"{n} google reviews"


def _quality_rating(i: ScoringInputs) -> tuple[bool, str | None]:
    if i.rating is None or i.rating < 4.5:
        return False, None
    return True, f"{i.rating:.1f}★"


def _multi_attorney(i: ScoringInputs) -> tuple[bool, str | None]:
    if i.attorney_count is None or i.attorney_count < 5:
        return False, None
    return True, f"{i.attorney_count} attorneys"


def _pi_specialized(i: ScoringInputs) -> tuple[bool, str | None]:
    if i.has_pi_practice_page is True:
        return True, "dedicated PI practice page"
    return False, None


def _recent_activity(i: ScoringInputs) -> tuple[bool, str | None]:
    if i.last_website_post_at is None:
        return False, None
    delta = i.now - i.last_website_post_at
    # Future-dated posts are never "recent activity" — treat as bad data.
    if delta < timedelta(0):
        return False, None
    if delta > timedelta(days=60):
        return False, None
    return True, f"latest post {i.last_website_post_at.date().isoformat()}"


def _established(i: ScoringInputs) -> tuple[bool, str | None]:
    if i.established_year is None:
        return False, None
    age = i.now.year - i.established_year
    if age < 5:
        return False, None
    return True, f"{age} years operating"


def _has_website(i: ScoringInputs) -> tuple[bool, str | None]:
    if not i.website:
        return False, None
    return True, None


COMPONENT_EVALUATORS: dict[str, Evaluator] = {
    "high_review_volume":     _high_review_volume,
    "moderate_review_volume": _moderate_review_volume,
    "quality_rating":         _quality_rating,
    "multi_attorney":         _multi_attorney,
    "pi_specialized":         _pi_specialized,
    "recent_activity":        _recent_activity,
    "established":            _established,
    "has_website":            _has_website,
}


# --- orchestrator ---

def score(
    inputs: ScoringInputs,
    weights: dict[str, int],
    buckets: dict[str, int],
) -> ScoreBreakdown:
    """Return a ScoreBreakdown summing weights of triggered components.

    Weight keys without an evaluator (e.g. future `meta_ads_running`) are
    silently ignored — config can lead implementation. Component order in the
    output follows the order of `weights` so the dashboard rendering is stable.
    """
    components: list[ScoreComponent] = []
    total = 0
    for key, weight in weights.items():
        evaluator = COMPONENT_EVALUATORS.get(key)
        if evaluator is None:
            continue
        triggered, note = evaluator(inputs)
        components.append(ScoreComponent(key=key, weight=int(weight), triggered=triggered, note=note))
        if triggered:
            total += int(weight)

    bucket = _bucket_for(total, buckets)
    return ScoreBreakdown(
        firm_id=inputs.firm_id,
        score=total,
        bucket=bucket,
        components=components,
        computed_at=datetime.now(timezone.utc),
    )


def _bucket_for(value: int, buckets: dict[str, int]) -> str:
    red_max = int(buckets.get("red_max", 39))
    yellow_max = int(buckets.get("yellow_max", 69))
    if value <= red_max:
        return "cold"
    if value <= yellow_max:
        return "warm"
    return "hot"
