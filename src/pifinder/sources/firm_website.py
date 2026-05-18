"""Firm-website enrichment source.

Crawls a firm's own site to extract: attorney roster, practice areas,
recent blog/news activity, established year. Returns an EnrichmentResult
containing a patch for the firms row plus per-attorney rows and a
"website activity" signal when a recent post is detected.

Politeness:
- robots.txt enforced for every URL (uses our settings UA)
- max_pages cap from config.yaml -> scraping.firm_website.max_pages
- per-host sleep from config.yaml -> scraping.firm_website.delay_s
- Single shared UA from settings.pifinder_user_agent
- Soft-fail on every error; never raises out of `enrich`
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from loguru import logger
from playwright.async_api import async_playwright, Browser, BrowserContext

from ..config import get_settings, get_yaml_config
from ..models import (
    Attorney,
    EnrichmentResult,
    FirmRecord,
    Signal,
    SignalKind,
    SourceName,
)
from ._html_parser import (
    ATTORNEY_PATHS,
    BLOG_PATHS,
    PageExtract,
    extract_page,
    select_candidate_paths,
)


def _scraping_cfg() -> dict[str, Any]:
    cfg = get_yaml_config().get("scraping", {}).get("firm_website", {}) or {}
    return {
        "max_pages": int(cfg.get("max_pages", 10)),
        "delay_s": float(cfg.get("delay_s", 2.0)),
        "candidate_paths": tuple(cfg.get("candidate_paths") or ATTORNEY_PATHS),
    }


class FirmWebsiteScraper:
    """Enricher that crawls firm-owned domains."""

    name = SourceName.firm_website.value

    def __init__(self, *, browser: Browser | None = None) -> None:
        self._browser = browser
        self._owns_browser = browser is None
        self._playwright = None
        self._settings = get_settings()
        self._cfg = _scraping_cfg()

    async def __aenter__(self) -> "FirmWebsiteScraper":
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_browser and self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def enrich(self, firm: FirmRecord) -> EnrichmentResult:
        result = EnrichmentResult(firm_id=firm.id or 0, source=SourceName.firm_website)
        if firm.website is None:
            result.errors.append("no_website_on_firm")
            return result
        if firm.id is None:
            result.errors.append("firm_missing_id")
            return result

        homepage = str(firm.website)
        assert self._browser is not None, "Use as `async with FirmWebsiteScraper()`"

        context = await self._browser.new_context(user_agent=self._settings.pifinder_user_agent)
        try:
            rp = await _load_robots(context, homepage, self._settings.pifinder_user_agent)
            crawl = _Crawl(
                context=context,
                ua=self._settings.pifinder_user_agent,
                robots=rp,
                max_pages=self._cfg["max_pages"],
                delay_s=self._cfg["delay_s"],
                hint_paths=tuple(self._cfg["candidate_paths"]),
            )
            await crawl.run(homepage)
        finally:
            await context.close()

        _merge_into_result(result, firm, crawl.aggregated)
        return result


# ---- internals ----

class _Crawl:
    """Single-firm crawl state. Visits homepage, then candidate paths, then blog paths."""

    def __init__(
        self,
        *,
        context: BrowserContext,
        ua: str,
        robots: RobotFileParser | None,
        max_pages: int,
        delay_s: float,
        hint_paths: tuple[str, ...],
    ) -> None:
        self.context = context
        self.ua = ua
        self.robots = robots
        self.max_pages = max_pages
        self.delay_s = delay_s
        self.hint_paths = hint_paths
        self.aggregated: _Aggregated = _Aggregated()

    async def run(self, homepage: str) -> None:
        visited: set[str] = set()
        queue: list[str] = [homepage]

        while queue and len(visited) < self.max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            if not _can_fetch(self.robots, self.ua, url):
                logger.debug("robots blocked {}", url)
                visited.add(url)
                continue

            html = await self._fetch(url)
            visited.add(url)
            if not html:
                continue

            extract = extract_page(html, url)
            self.aggregated.absorb(extract)

            # Plan: after the homepage, enqueue attorney + blog candidate paths.
            if len(visited) == 1:
                candidates = (
                    select_candidate_paths(extract.same_origin_links, url, self.hint_paths)
                    + select_candidate_paths(extract.same_origin_links, url, BLOG_PATHS)
                )
                for c in candidates:
                    if c not in visited and c not in queue:
                        queue.append(c)

            await asyncio.sleep(self.delay_s)

    async def _fetch(self, url: str) -> str | None:
        page = await self.context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            return await page.content()
        except Exception as e:                                    # noqa: BLE001
            logger.warning("fetch failed {}: {}", url, e)
            return None
        finally:
            await page.close()


class _Aggregated:
    """Rolls up multiple PageExtracts from one firm's crawl into a single view."""

    def __init__(self) -> None:
        self.attorney_by_name: dict[str, Any] = {}
        self.practice_areas: set[str] = set()
        self.has_pi_terms: bool = False
        self.last_post_at: datetime | None = None
        self.established_year: int | None = None

    def absorb(self, e: PageExtract) -> None:
        for a in e.attorneys:
            key = a.name.lower()
            existing = self.attorney_by_name.get(key)
            if existing is None or (not existing.title and a.title):
                self.attorney_by_name[key] = a
        self.practice_areas.update(e.practice_areas)
        self.has_pi_terms = self.has_pi_terms or e.has_pi_terms
        if e.last_post_at and (self.last_post_at is None or e.last_post_at > self.last_post_at):
            self.last_post_at = e.last_post_at
        if e.established_year and (self.established_year is None or e.established_year < self.established_year):
            self.established_year = e.established_year


def _merge_into_result(
    result: EnrichmentResult, firm: FirmRecord, agg: _Aggregated
) -> None:
    attorneys = list(agg.attorney_by_name.values())

    # Persist as Attorney models — caller handles upsert.
    for a in attorneys:
        try:
            result.signals  # touch attribute to keep linters quiet on unused import
        except AttributeError:
            pass

    # Patch fields on the firms row.
    has_pi_practice = bool(agg.has_pi_terms or "personal injury" in agg.practice_areas)
    result.patch.update(
        attorney_count=len(attorneys),
        has_pi_practice_page=has_pi_practice,
        last_website_post_at=agg.last_post_at,
        established_year=agg.established_year,
    )

    # Recent-website-activity signal feeds the scoring engine later.
    if agg.last_post_at is not None:
        practice_areas = sorted(agg.practice_areas)
        att_n = len(attorneys)
        pa_n = len(practice_areas)
        # Build a one-liner that tells a salesperson *what* the signal means,
        # not just that it exists. Date already lives on observed_at.
        bits = [f"active site"]
        if att_n:
            bits.append(f"{att_n} attorney{'s' if att_n != 1 else ''} parsed")
        if pa_n:
            preview = ", ".join(practice_areas[:3])
            extra = f" (+{pa_n - 3} more)" if pa_n > 3 else ""
            bits.append(f"{pa_n} practice area{'s' if pa_n != 1 else ''}: {preview}{extra}")
        result.signals.append(
            Signal(
                firm_id=firm.id or 0,
                kind=SignalKind.website,
                source=SourceName.firm_website,
                observed_at=agg.last_post_at,
                summary=" · ".join(bits),
                payload={
                    "practice_areas": practice_areas,
                    "attorney_count": att_n,
                },
            )
        )

    # Attach parsed attorneys onto the patch under a private key so the CLI
    # layer can upsert them without us doing DB I/O here.
    result.patch["_attorneys"] = [
        {
            "name": a.name,
            "title": a.title,
            "bio_url": a.bio_url,
            "practice_areas": a.practice_areas,
        }
        for a in attorneys
    ]
    _ = Attorney  # keep import for typing; constructed in cli.py


# ---- robots ----

async def _load_robots(context: BrowserContext, base_url: str, ua: str) -> RobotFileParser | None:
    parsed = urlparse(base_url)
    robots_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", "/robots.txt")
    page = await context.new_page()
    try:
        resp = await page.goto(robots_url, wait_until="domcontentloaded", timeout=10_000)
        if resp is None or resp.status >= 400:
            return None
        body = await resp.text()
    except Exception as e:                                        # noqa: BLE001
        logger.debug("robots fetch failed for {}: {}", robots_url, e)
        return None
    finally:
        await page.close()

    rp = RobotFileParser()
    rp.parse(body.splitlines())
    return rp


def _can_fetch(rp: RobotFileParser | None, ua: str, url: str) -> bool:
    if rp is None:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:                                             # noqa: BLE001
        # Be lenient: a malformed robots.txt shouldn't block our crawl entirely.
        return True


# Track current time helper for tests if needed.
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
