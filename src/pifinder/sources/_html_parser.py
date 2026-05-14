"""Pure HTML extractors for firm websites. No I/O — easy to unit-test.

The structured-data path runs first (schema.org Person / Attorney microdata, JSON-LD).
The heuristic path catches the long tail: card components, name+title patterns, /attorneys
URL shapes. We accept some recall loss in exchange for not hallucinating attorneys.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

# Canonical PI practice areas. Anchored as a set of normalized phrases.
# Match is case-insensitive substring on the normalized page text.
PI_PRACTICE_AREAS: tuple[str, ...] = (
    "personal injury",
    "auto accident",
    "car accident",
    "motor vehicle accident",
    "truck accident",
    "18-wheeler",
    "motorcycle accident",
    "pedestrian accident",
    "bicycle accident",
    "slip and fall",
    "premises liability",
    "medical malpractice",
    "wrongful death",
    "mass tort",
    "product liability",
    "workers compensation",
    "workers' compensation",
    "dog bite",
    "nursing home abuse",
    "brain injury",
    "traumatic brain injury",
    "spinal cord injury",
    "birth injury",
    "catastrophic injury",
)

ATTORNEY_PATHS = ("/attorneys", "/our-team", "/lawyers", "/about", "/attorney-profiles", "/our-attorneys", "/team")
BLOG_PATHS = ("/blog", "/news", "/press", "/articles", "/insights")


@dataclass
class ParsedAttorney:
    name: str
    title: str | None = None
    bio_url: str | None = None
    practice_areas: list[str] = field(default_factory=list)


@dataclass
class PageExtract:
    """Everything we pulled from a single page."""

    attorneys: list[ParsedAttorney] = field(default_factory=list)
    practice_areas: list[str] = field(default_factory=list)
    has_pi_terms: bool = False
    last_post_at: datetime | None = None
    established_year: int | None = None
    same_origin_links: list[str] = field(default_factory=list)


# ---- normalization ----

_WS_RE = re.compile(r"\s+")


def _norm_text(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _normalize_lower(s: str) -> str:
    return _WS_RE.sub(" ", s).strip().lower()


# ---- entry point ----

def extract_page(html: str, base_url: str) -> PageExtract:
    soup = BeautifulSoup(html, "lxml")
    result = PageExtract()

    text = _norm_text(soup.get_text(separator=" "))
    lower_text = text.lower()

    result.practice_areas = _detect_practice_areas(lower_text)
    result.has_pi_terms = any(
        term in lower_text for term in ("personal injury", "injury attorney", "injury lawyer")
    )

    structured = _attorneys_from_jsonld(soup, base_url)
    if structured:
        result.attorneys = structured
    else:
        result.attorneys = _attorneys_heuristic(soup, base_url)

    result.last_post_at = _latest_post_date(soup)
    result.established_year = _established_year(text)
    result.same_origin_links = _same_origin_links(soup, base_url)
    return result


# ---- structured-data attorneys (JSON-LD) ----

def _attorneys_from_jsonld(soup: BeautifulSoup, base_url: str) -> list[ParsedAttorney]:
    """Pulls Person / Attorney entries from JSON-LD blocks if a site exposes them."""
    out: list[ParsedAttorney] = []
    for block in soup.find_all("script", {"type": "application/ld+json"}):
        raw = block.string or block.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _walk_jsonld(data):
            t = node.get("@type")
            types = {t} if isinstance(t, str) else set(t) if isinstance(t, list) else set()
            if not (types & {"Person", "Attorney"}):
                continue
            name = node.get("name")
            if not name:
                continue
            title = node.get("jobTitle")
            url = node.get("url")
            out.append(
                ParsedAttorney(
                    name=_norm_text(name),
                    title=_norm_text(title) if title else None,
                    bio_url=urljoin(base_url, url) if isinstance(url, str) else None,
                )
            )
    # Dedup by name (case-insensitive)
    seen: set[str] = set()
    deduped: list[ParsedAttorney] = []
    for a in out:
        key = a.name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(a)
    return deduped


def _walk_jsonld(node) -> list[dict]:
    """Yield every dict at any depth from a JSON-LD value."""
    found: list[dict] = []
    if isinstance(node, dict):
        found.append(node)
        for v in node.values():
            found.extend(_walk_jsonld(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_jsonld(item))
    return found


# ---- heuristic attorneys ----

# Order matters: longer / more-specific phrases come first so they match before
# their substrings (e.g. "founding partner" before "partner").
_TITLE_HINTS = (
    "founding partner", "managing partner", "name partner", "senior counsel",
    "trial lawyer", "of counsel",
    "shareholder", "principal", "partner",
    "attorney", "associate",
)
_NAME_RE = re.compile(
    # First Last, First M. Last, First Middle Last — keep it conservative
    r"^[A-Z][a-zA-Z'\-]+(?: [A-Z]\.?)?(?: [A-Z][a-zA-Z'\-]+){1,2}$"
)


def _attorneys_heuristic(soup: BeautifulSoup, base_url: str) -> list[ParsedAttorney]:
    """Look for cards: a heading that's a proper name plus a nearby title hint.

    To avoid false positives on common 2-word headings ("About Us", "Our Team",
    "Contact Us"), 2-token candidates must have a confirming title hint nearby;
    3+ token names pass without one.
    """
    out: list[ParsedAttorney] = []
    seen: set[str] = set()
    for hdr in soup.find_all(["h1", "h2", "h3", "h4"]):
        text = _norm_text(hdr.get_text())
        if not text or len(text) > 60:
            continue
        if not _NAME_RE.match(text):
            continue
        title = _find_title_near(hdr)
        tokens = text.split()
        if len(tokens) < 3 and title is None:
            continue
        bio_url = _nearest_link(hdr, base_url)
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ParsedAttorney(name=text, title=title, bio_url=bio_url))
    return out


def _find_title_near(hdr: Tag) -> str | None:
    """Scan the heading's immediate next sibling for a known title hint.

    Scoped narrowly on purpose: the bio-card pattern is `<hN>Name</hN><p>Title…</p>`
    or `<hN>Name</hN><div>…Title…</div>`. Walking up to `parent.get_text()` would
    pull in every sibling card's text and produce false positives on neutral
    headings like "About Us".
    """
    sib = hdr.find_next_sibling()
    if sib is None:
        return None
    lower = _norm_text(sib.get_text(separator=" ")).lower()
    for hint in _TITLE_HINTS:
        if hint in lower:
            return hint.title()
    return None


def _nearest_link(hdr: Tag, base_url: str) -> str | None:
    """If the heading is wrapped in or near an <a>, return absolute URL."""
    if hdr.name == "a" and hdr.get("href"):
        return urljoin(base_url, hdr["href"])
    a = hdr.find("a", href=True)
    if a and a.get("href"):
        return urljoin(base_url, a["href"])
    # Wrapping <a> case
    walker = hdr.parent
    depth = 0
    while walker is not None and depth < 3:
        if getattr(walker, "name", None) == "a" and walker.get("href"):
            return urljoin(base_url, walker["href"])
        walker = walker.parent
        depth += 1
    return None


# ---- practice areas ----

def _detect_practice_areas(lower_text: str) -> list[str]:
    found: list[str] = []
    for term in PI_PRACTICE_AREAS:
        if term in lower_text:
            found.append(term)
    # Collapse the "workers compensation" / "workers' compensation" pair.
    canonical_map = {"workers' compensation": "workers compensation"}
    collapsed: list[str] = []
    seen: set[str] = set()
    for t in found:
        c = canonical_map.get(t, t)
        if c in seen:
            continue
        seen.add(c)
        collapsed.append(c)
    return collapsed


# ---- last post date ----

_DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),                       # 2026-03-12
    re.compile(r"\b(\d{2})/(\d{2})/(\d{4})\b"),                       # 03/12/2026
    re.compile(r"\b(January|February|March|April|May|June|July|"
               r"August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b",
               re.IGNORECASE),
]
_MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1
)}


def _latest_post_date(soup: BeautifulSoup) -> datetime | None:
    """Best-effort: <time datetime="..."> first, then date-shaped text in articles."""
    dates: list[datetime] = []

    for t in soup.find_all("time"):
        dt = _parse_iso(t.get("datetime")) or _parse_iso(_norm_text(t.get_text()))
        if dt is not None:
            dates.append(dt)

    text = _norm_text(soup.get_text(separator=" "))
    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            dt = _parse_match(m)
            if dt is not None:
                dates.append(dt)

    if not dates:
        return None
    now = datetime.now(timezone.utc)
    plausible = [d for d in dates if d <= now and d.year >= 2000]
    if not plausible:
        return None
    return max(plausible)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parse_match(m: re.Match[str]) -> datetime | None:
    groups = m.groups()
    try:
        if len(groups) == 3 and groups[0].isdigit() and groups[1].isdigit() and groups[2].isdigit():
            if len(groups[0]) == 4:                           # 2026-03-12
                y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
            else:                                             # 03/12/2026
                mo, d, y = int(groups[0]), int(groups[1]), int(groups[2])
        else:                                                 # "March 12, 2026"
            mo = _MONTHS.get(groups[0].title())
            if mo is None:
                return None
            d, y = int(groups[1]), int(groups[2])
        return datetime(y, mo, d, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ---- established year ----

_ESTABLISHED_RE = re.compile(
    r"\b(?:est(?:ablished)?\.?|since|founded(?: in)?|serving\s+(?:clients\s+)?since)\s+"
    r"(\d{4})\b",
    re.IGNORECASE,
)


def _established_year(text: str) -> int | None:
    m = _ESTABLISHED_RE.search(text)
    if not m:
        return None
    year = int(m.group(1))
    current = datetime.now(timezone.utc).year
    if 1900 <= year <= current:
        return year
    return None


# ---- link discovery ----

def _same_origin_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    base = urlparse(base_url)
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        absolute = urljoin(base_url, a["href"])
        parsed = urlparse(absolute)
        if parsed.netloc != base.netloc:
            continue
        # Drop fragments and queries for crawl planning; keep canonical paths.
        canonical = parsed._replace(fragment="", query="").geturl()
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    return out


# ---- crawl planning ----

def select_candidate_paths(links: list[str], base_url: str, hint_paths: tuple[str, ...]) -> list[str]:
    """From discovered links, pick the small set worth visiting for enrichment."""
    base = urlparse(base_url).netloc
    chosen: list[str] = []
    seen: set[str] = set()
    for url in links:
        parsed = urlparse(url)
        if parsed.netloc != base:
            continue
        path_lower = parsed.path.lower().rstrip("/")
        if not any(path_lower.startswith(p) or path_lower == p for p in hint_paths):
            continue
        if url in seen:
            continue
        seen.add(url)
        chosen.append(url)
    return chosen
