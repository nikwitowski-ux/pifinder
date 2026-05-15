# Lessons / gotchas

Append-only. New entries on top.

---

## 2026-05-14 — Attorney heuristic: every candidate now requires a title hint + stoplist + all-caps reject

Real-world enrichment (TORKLAW, DK Law, etc.) exposed two leak classes the
original heuristic missed:

1. **3-token marketing copy**: "Who We Are", "Practice Areas", "Free Case Evaluation", "Christmas Gift Giveaway". The earlier "3+ tokens skips the title-hint requirement" bypass let any title-cased 3-word phrase through.
2. **All-caps brand fragments**: "TORKLAW Action Center", "FOO Holdings Group". The regex `[a-zA-Z]+` happily matches ALL-CAPS tokens.

**Three layers of defense, all required:**
1. `_NAME_STOPWORDS` — frozenset of ~120 tokens that never appear in human names (pronouns, articles, "lawyers", "free", "consultation", "injury", "law", etc.). Any candidate containing one of these tokens is rejected.
2. `_has_uppercase_brand_token` — any token >2 chars that is fully uppercase is a firm/brand marker. Middle initials ("A.") are still fine because the length check is >2.
3. **All candidates now require a confirming title hint** in the immediate next sibling (Partner / Attorney / Associate / Of Counsel etc.). The old "3+ token bypass" is gone. JSON-LD attorneys are unaffected — that path is structured-data-driven.

**Why precision > recall here:** the dashboard shows a "PI" column and surfaces firms by attorney count. A handful of fake attorneys per firm corrupts the lead-quality signal far more than missing one real attorney on a poorly-marked-up bio page does.

**Validated against real data**: re-ran enrichment on 10 Irvine firms before & after. TORKLAW dropped from 15 → 10 → 8 attorneys across the two rounds of tightening; all 8 remaining have valid titles ("Attorney", "Partner"). DK Law dropped from 21 → 14 → 11. RMD Law dropped from 8 → 3 → 1 (their bio markup doesn't surface titles in adjacent siblings — known recall loss).

---

## 2026-05-14 — Cluster bubbles vs. apartments.com pattern

Design feedback from the user: don't bundle firms into "+N" cluster bubbles —
show them individually with a multi-card popup when pins are clustered
geographically.

Dropped `leaflet.markercluster` entirely. Each firm gets its own `circleMarker`.
On click/hover, JS computes pixel-distance neighbors (`PROXIMITY_PX = 26`) at
the current zoom and renders a single popup containing cards for every nearby
firm. Each card has the firm name (link to detail), score chip, city, phone.

**Why it matters:** for a sales analyst, "5 firms are clustered here" is less
useful than "here's a card for each of those 5 firms with names + scores you
can click directly." The cluster bubble was hiding the information he came to
the map for.

---

## 2026-05-14 — Scoring engine: weight keys can lead implementation

`scoring.score()` silently ignores weight keys in `config.yaml` that have no
matching evaluator in `COMPONENT_EVALUATORS`. This means we can land
`meta_ads_running: 25` in config the moment we start working on the Meta Ads
source — no scoring code changes until the evaluator function is added. The
test `test_unknown_weight_keys_in_config_are_ignored_not_fatal` locks this
contract.

**Why it matters:** future enrichers can be enabled gradually without
breaking active scoring runs.

---

## 2026-05-14 — `last_website_post_at` lives on the firm, not derived from signals

The scoring engine reads `last_website_post_at` directly from the firm row.
When I seeded test firms by inserting a `signal` with `observed_at` but
*not* updating the firm's column, `recent_activity` didn't fire — the
scorer was correct; the seed was incomplete. Real enrichment writes both,
via `apply_firm_patch(... last_website_post_at=...)`. Worth remembering when
hand-crafting test data: signals are observations; firm columns are
materialized state, and the scorer reads materialized state only.

---

## 2026-05-14 — Starlette 1.0 `TemplateResponse` signature: request is positional, not a context key

We bundled `starlette==1.0.0` (via FastAPI). In 1.0, `Jinja2Templates.TemplateResponse` requires `request` as the first positional arg; passing `request` inside the context dict makes Jinja2 try to use the context as the cache key, which fails with `TypeError: unhashable type: 'dict'` on the second render attempt. Fixed by switching every call site to `TEMPLATES.TemplateResponse(request, "name.html", {…})`.

**Why it matters:** templated routes appeared to "work" on first request and crashed on the second, only caught by an end-to-end test that hit `/` twice. Worth keeping the dashboard tests covering both new and repeated renders.

---

## 2026-05-14 — Heuristic attorney scan: scope title search to next sibling only

First cut of `_find_title_near` walked `hdr.parent.get_text(...)` which on a page with multiple bio cards meant any heading that loosely matched `_NAME_RE` (like "About Us") pulled in title hints from far-away cards and got mis-classified as an attorney. Scoping to `hdr.find_next_sibling()` only is enough for the common bio-card pattern (`<hN>Name</hN><p>Title…</p>`) and eliminates the cross-card leakage.

**Also:** order `_TITLE_HINTS` longest-phrase first ("founding partner" before "partner") since we iterate and return the first match, not the longest.

**Also:** 2-token name candidates ("Jane Doe") require a confirming title hint nearby. 3+ token names ("Jane A. Doe") pass on their own. This blocks the residual false positives ("Our Team", "Contact Us") while still capturing real attorneys with simple names whose cards include a partner/associate tag.

---

## 2026-05-14 — EnrichmentResult.patch carries a private `_attorneys` payload

The pure-parser produces attorney structs but the patch shape on `EnrichmentResult` is a flat dict. To avoid bloating `models.py` with a new field while parsers are still evolving, the scraper stashes attorney dicts under `patch["_attorneys"]` and `apply_firm_patch` whitelists only real column names, silently dropping unknown / private keys. The CLI's `_persist_enrichment` pops `_attorneys` before applying the patch and routes them through `upsert_attorney`.

**Why it matters:** any future enricher can use the same convention (`patch["_some_collection"]`) without risking accidental writes to the `firms` table.

---

## 2026-05-14 — Playwright browser binary is a separate install

`uv add playwright` only installs the Python package. To actually run the scraper you need `uv run playwright install chromium` (one-time, ~150 MB). Documented in README quickstart so a fresh clone doesn't fail mysteriously.

---

## 2026-05-14 — Suffix stripping must run AFTER canonicalizing punctuation

The original `normalize_firm_name` ran `re.sub(r"[\s,\.\-&/]+", " ", name)` BEFORE the suffix regex. That stripped the dots in "P.C." to "p c " before the regex `\bp\.c\.\b` could match it, so "Smith & Jones, P.C." normalized to "smith jones p c" instead of "smith jones".

**Fix:** turn punctuation into spaces first, then match suffixes with `\bp\s*c\b` (handles both "pc" and "p c"). Tests now lock both the punctuated and unpunctuated forms.

**Why it matters:** dedup is the whole point of normalization. A miss here would create duplicate rows for the same firm whenever its scraped record had different punctuation than its Places record.

---

## 2026-05-14 — Places API v1 requires X-Goog-FieldMask

Without `X-Goog-FieldMask`, the new Places API returns nothing useful and you can't tell the request worked. We send a precise mask listing exactly the fields we hydrate into `FirmRecord`, plus `nextPageToken`. Billing is per requested field, so the mask is also a cost lever — don't add fields we don't read.

---

## 2026-05-14 — Pagination has a token-validity delay

Places API requires a brief delay (~2s) before `nextPageToken` is valid for the next request. The source sleeps for `max(per_host_sleep, 2.0)` between pages. The pagination test monkeypatches `asyncio.sleep` to skip this so it runs fast.

---

## 2026-05-14 — `pydantic-settings` caches; tests must `cache_clear()`

`get_settings()` is `@lru_cache`'d so the rest of the app gets one canonical Settings instance. Tests that monkeypatch env vars must call `cache_clear()` before AND after, otherwise a prior test's settings leak into the current run.
