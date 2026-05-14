"""Google Places API v1 — discovery layer.

Uses places.googleapis.com (the new API), not the legacy /maps/api/place/* endpoints.
Field mask is required by the new API; without it the call costs nothing but returns
nothing useful. Responses are cached on disk so dev loops are free.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .. import cache
from ..config import get_http_config, get_settings
from ..models import FirmRecord, SourceName

PLACES_BASE = "https://places.googleapis.com/v1"
SEARCH_TEXT_URL = f"{PLACES_BASE}/places:searchText"

# We pay per requested field, so list only what we actually use.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.addressComponents",
        "places.location",
        "places.internationalPhoneNumber",
        "places.nationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.businessStatus",
        "places.types",
        "nextPageToken",
    ]
)


class GooglePlacesError(RuntimeError):
    pass


class GooglePlacesSource:
    """Discovery source backed by Places API v1 searchText."""

    name = SourceName.google_places.value

    def __init__(self, *, api_key: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        resolved_key = api_key or (
            settings.google_places_api_key.get_secret_value()
            if settings.google_places_api_key
            else None
        )
        if not resolved_key:
            raise GooglePlacesError(
                "GOOGLE_PLACES_API_KEY not set. Copy .env.example to .env and add a key."
            )
        self._api_key = resolved_key
        self._client = client
        self._owns_client = client is None
        self._timeout = settings.pifinder_http_timeout_s
        self._user_agent = settings.pifinder_user_agent
        self._sleep_s = 1.0 / float(get_http_config().get("per_host_rps", 1.0) or 1.0)
        self._retries = int(get_http_config().get("retries", 3) or 3)

    async def __aenter__(self) -> "GooglePlacesSource":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def discover(
        self, *, location: str, radius_meters: int, query: str
    ) -> list[FirmRecord]:
        """Yield FirmRecords matching `query` near `location`. Pages until exhausted.

        For now, locality is conveyed by appending it to the text query — the new
        API interprets it correctly and we avoid a separate geocoding round-trip.
        `radius_meters` is recorded but only enforced when we have lat/lng to bias on.
        """
        text_query = f"{query} {location}".strip()
        logger.info("places.searchText query='{}'", text_query)

        firms: list[FirmRecord] = []
        page_token: str | None = None
        page = 0
        while True:
            page += 1
            body: dict[str, Any] = {
                "textQuery": text_query,
                "pageSize": 20,
            }
            if page_token:
                body["pageToken"] = page_token

            data = await self._fetch_page(body)
            places = data.get("places") or []
            logger.info("places page {} returned {} results", page, len(places))
            for raw in places:
                firms.append(_to_firm_record(raw))

            page_token = data.get("nextPageToken")
            if not page_token:
                break
            # Places requires a brief delay before nextPageToken is valid.
            await asyncio.sleep(max(self._sleep_s, 2.0))

        return firms

    # ---- internals ----

    async def _fetch_page(self, body: dict[str, Any]) -> dict[str, Any]:
        # Cache key derived from body — every distinct request gets one hit.
        cache_key = cache.make_key(self.name, {"endpoint": "searchText", "body": body})
        cached = cache.get_json(cache_key)
        if cached is not None:
            logger.debug("cache hit {}", cache_key)
            return cached

        assert self._client is not None, "Use as `async with GooglePlacesSource(...)`"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": FIELD_MASK,
            "User-Agent": self._user_agent,
        }

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._retries),
            wait=wait_exponential(multiplier=1.5, min=1.0, max=10.0),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.post(SEARCH_TEXT_URL, headers=headers, json=body)
                if resp.status_code >= 500:
                    resp.raise_for_status()  # triggers retry
                if resp.status_code >= 400:
                    # 4xx is a hard error; surface it without retry noise
                    raise GooglePlacesError(
                        f"Places API {resp.status_code}: {resp.text[:300]}"
                    )
                data = resp.json()

        cache.put_json(cache_key, data)
        await asyncio.sleep(self._sleep_s)
        return data


def _to_firm_record(raw: dict[str, Any]) -> FirmRecord:
    from ..db import normalize_firm_name

    name = (raw.get("displayName") or {}).get("text") or raw.get("formattedAddress") or "Unknown"
    address = raw.get("formattedAddress")
    components = {c.get("types", [None])[0]: c.get("shortText") or c.get("longText")
                  for c in (raw.get("addressComponents") or []) if c.get("types")}
    loc = raw.get("location") or {}

    website = raw.get("websiteUri")
    return FirmRecord(
        place_id=raw.get("id"),
        name=name,
        normalized_name=normalize_firm_name(name),
        address=address,
        city=components.get("locality") or components.get("postal_town"),
        state=components.get("administrative_area_level_1"),
        postal_code=components.get("postal_code"),
        country=components.get("country") or "US",
        latitude=loc.get("latitude"),
        longitude=loc.get("longitude"),
        phone=raw.get("internationalPhoneNumber") or raw.get("nationalPhoneNumber"),
        website=website if website else None,
        rating=raw.get("rating"),
        user_ratings_total=raw.get("userRatingCount"),
        business_status=raw.get("businessStatus"),
        discovered_via=SourceName.google_places,
        raw=raw,
    )
