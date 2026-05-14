"""TDD: scoring engine. Tests are written against the API we WISH to have.

A score() call takes a ScoringInputs object plus a weights dict (so tests
inject a known weight set instead of depending on config.yaml), and returns
a ScoreBreakdown with: score (int 0..max), bucket (cold/warm/hot/unscored),
and a list of components (each: key, weight, triggered, optional note).

Time-based components take `now` from ScoringInputs so tests are deterministic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pifinder.scoring import ScoringInputs, score


# A "full" weight set matching config.yaml. Tests inject it explicitly so they
# don't break if production weights change.
DEFAULT_WEIGHTS = {
    "high_review_volume": 30,
    "moderate_review_volume": 15,
    "quality_rating": 20,
    "multi_attorney": 15,
    "pi_specialized": 10,
    "recent_activity": 10,
    "established": 10,
    "has_website": 5,
}

DEFAULT_BUCKETS = {"red_max": 39, "yellow_max": 69}
DEFAULT_NOW = datetime(2026, 5, 14, tzinfo=timezone.utc)


def _inputs(**overrides) -> ScoringInputs:
    base = dict(
        firm_id=1,
        rating=None,
        user_ratings_total=None,
        has_pi_practice_page=None,
        attorney_count=None,
        last_website_post_at=None,
        established_year=None,
        website=None,
        now=DEFAULT_NOW,
    )
    base.update(overrides)
    return ScoringInputs(**base)


def _triggered_keys(breakdown) -> set[str]:
    return {c.key for c in breakdown.components if c.triggered}


# ---- empty case ----

def test_empty_firm_scores_zero_and_is_cold():
    b = score(_inputs(), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert b.score == 0
    assert b.bucket == "cold"
    # Every component is present but none triggered — discoverable in UI.
    assert len(b.components) == len(DEFAULT_WEIGHTS)
    assert _triggered_keys(b) == set()


# ---- review volume tiers (mutually exclusive) ----

def test_high_review_volume_fires_above_threshold():
    b = score(_inputs(user_ratings_total=100), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "high_review_volume" in _triggered_keys(b)
    assert "moderate_review_volume" not in _triggered_keys(b)
    assert b.score == 30


def test_high_review_volume_is_inclusive_at_100():
    b = score(_inputs(user_ratings_total=100), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "high_review_volume" in _triggered_keys(b)


def test_moderate_review_volume_band_50_to_99():
    b50 = score(_inputs(user_ratings_total=50), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    b99 = score(_inputs(user_ratings_total=99), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    for b in (b50, b99):
        assert "moderate_review_volume" in _triggered_keys(b)
        assert "high_review_volume" not in _triggered_keys(b)
        assert b.score == 15


def test_under_50_reviews_neither_review_tier_fires():
    b = score(_inputs(user_ratings_total=49), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "moderate_review_volume" not in _triggered_keys(b)
    assert "high_review_volume" not in _triggered_keys(b)


def test_review_tiers_mutually_exclusive_never_both():
    # Sanity check across the full range.
    for n in (0, 49, 50, 99, 100, 1000):
        b = score(_inputs(user_ratings_total=n), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
        triggered = _triggered_keys(b)
        assert not ({"high_review_volume", "moderate_review_volume"} <= triggered), (
            f"both tiers fired at n={n}"
        )


# ---- quality rating ----

def test_quality_rating_fires_at_4_5_inclusive():
    b = score(_inputs(rating=4.5), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "quality_rating" in _triggered_keys(b)
    assert b.score == 20


def test_quality_rating_does_not_fire_below_4_5():
    b = score(_inputs(rating=4.4), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "quality_rating" not in _triggered_keys(b)


def test_quality_rating_none_does_not_fire_or_crash():
    b = score(_inputs(rating=None), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "quality_rating" not in _triggered_keys(b)


# ---- multi-attorney ----

def test_multi_attorney_fires_at_5_inclusive():
    b = score(_inputs(attorney_count=5), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "multi_attorney" in _triggered_keys(b)
    assert b.score == 15


def test_multi_attorney_does_not_fire_at_4():
    b = score(_inputs(attorney_count=4), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "multi_attorney" not in _triggered_keys(b)


# ---- pi specialized ----

def test_pi_specialized_fires_only_on_true():
    b_true = score(_inputs(has_pi_practice_page=True), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    b_false = score(_inputs(has_pi_practice_page=False), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    b_none = score(_inputs(has_pi_practice_page=None), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "pi_specialized" in _triggered_keys(b_true)
    assert "pi_specialized" not in _triggered_keys(b_false)
    assert "pi_specialized" not in _triggered_keys(b_none)
    assert b_true.score == 10


# ---- recent activity ----

def test_recent_activity_fires_within_60_days():
    recent = DEFAULT_NOW - timedelta(days=30)
    b = score(_inputs(last_website_post_at=recent), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "recent_activity" in _triggered_keys(b)
    assert b.score == 10


def test_recent_activity_does_not_fire_at_61_days():
    stale = DEFAULT_NOW - timedelta(days=61)
    b = score(_inputs(last_website_post_at=stale), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "recent_activity" not in _triggered_keys(b)


def test_recent_activity_fires_at_60_days_exactly():
    edge = DEFAULT_NOW - timedelta(days=60)
    b = score(_inputs(last_website_post_at=edge), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "recent_activity" in _triggered_keys(b)


def test_future_dated_post_does_not_fire():
    # Defensive: a future date is clock skew or bad parsing — never a real post.
    future = DEFAULT_NOW + timedelta(days=10)
    b = score(_inputs(last_website_post_at=future), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "recent_activity" not in _triggered_keys(b)


# ---- established ----

def test_established_fires_at_5_years_inclusive():
    # now is 2026-05-14; year 2021 is exactly 5 calendar years prior.
    b = score(_inputs(established_year=2021), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "established" in _triggered_keys(b)
    assert b.score == 10


def test_established_does_not_fire_at_4_years():
    b = score(_inputs(established_year=2022), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "established" not in _triggered_keys(b)


def test_established_future_year_does_not_fire():
    b = score(_inputs(established_year=2030), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "established" not in _triggered_keys(b)


# ---- website ----

def test_has_website_fires_only_when_present():
    b_yes = score(_inputs(website="https://x.example.com"), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    b_empty = score(_inputs(website=""), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    b_none = score(_inputs(website=None), DEFAULT_WEIGHTS, DEFAULT_BUCKETS)
    assert "has_website" in _triggered_keys(b_yes)
    assert "has_website" not in _triggered_keys(b_empty)
    assert "has_website" not in _triggered_keys(b_none)
    assert b_yes.score == 5


# ---- summation / buckets ----

def test_max_score_when_all_aggregation_signals_fire():
    b = score(
        _inputs(
            rating=4.8,
            user_ratings_total=325,                    # high tier
            attorney_count=7,
            has_pi_practice_page=True,
            last_website_post_at=DEFAULT_NOW - timedelta(days=10),
            established_year=1995,
            website="https://strong.example.com",
        ),
        DEFAULT_WEIGHTS,
        DEFAULT_BUCKETS,
    )
    # high(30) + quality(20) + multi(15) + pi(10) + recent(10) + established(10) + website(5) = 100
    assert b.score == 100
    assert b.bucket == "hot"


def test_summation_matches_triggered_weights():
    """The displayed score equals sum-of-weights for every triggered component."""
    b = score(
        _inputs(
            rating=4.6,                                 # quality 20
            user_ratings_total=80,                      # moderate 15
            website="https://x.example.com",            # 5
        ),
        DEFAULT_WEIGHTS,
        DEFAULT_BUCKETS,
    )
    expected = sum(c.weight for c in b.components if c.triggered)
    assert b.score == expected == 40


def test_bucket_thresholds_partition_correctly():
    # red_max=39, yellow_max=69
    boundaries = [(0, "cold"), (39, "cold"), (40, "warm"), (69, "warm"), (70, "hot"), (100, "hot")]
    for value, expected_bucket in boundaries:
        # Construct inputs that produce exactly `value`. Trick: drive it through
        # the orchestrator with weights and triggered components we can predict.
        # Easier: call the score path with weights that match the chosen value.
        weights = {
            "high_review_volume": value,
            "moderate_review_volume": 0,
            "quality_rating": 0,
            "multi_attorney": 0,
            "pi_specialized": 0,
            "recent_activity": 0,
            "established": 0,
            "has_website": 0,
        }
        b = score(
            _inputs(user_ratings_total=100),
            weights,
            DEFAULT_BUCKETS,
        )
        assert b.score == value
        assert b.bucket == expected_bucket, f"score {value} should bucket as {expected_bucket}, got {b.bucket}"


# ---- robustness ----

def test_unknown_weight_keys_in_config_are_ignored_not_fatal():
    """Adding meta_ads_running later shouldn't break runs that don't compute it.
    A weight key with no matching component evaluator should be silently dropped."""
    weights = {**DEFAULT_WEIGHTS, "meta_ads_running": 25}
    b = score(_inputs(user_ratings_total=120, rating=4.6), weights, DEFAULT_BUCKETS)
    # meta_ads_running is not represented in components (no evaluator), so it
    # cannot contribute to the total or appear as a row.
    assert "meta_ads_running" not in {c.key for c in b.components}
    assert b.score == 50  # 30 + 20


def test_weights_can_zero_out_a_component():
    weights = {**DEFAULT_WEIGHTS, "quality_rating": 0}
    b = score(_inputs(rating=4.9), weights, DEFAULT_BUCKETS)
    # Component still triggers, but contributes 0 to the score.
    qr = next(c for c in b.components if c.key == "quality_rating")
    assert qr.triggered is True
    assert qr.weight == 0
    assert b.score == 0


def test_components_order_matches_weights_order_for_stable_ui():
    weights = {
        "has_website": 5,
        "quality_rating": 20,
        "high_review_volume": 30,
        "moderate_review_volume": 15,
        "multi_attorney": 15,
        "pi_specialized": 10,
        "recent_activity": 10,
        "established": 10,
    }
    b = score(_inputs(), weights, DEFAULT_BUCKETS)
    keys_in_order = [c.key for c in b.components]
    assert keys_in_order == list(weights.keys())
