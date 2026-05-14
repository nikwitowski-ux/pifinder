# Lessons / gotchas

Append-only. New entries on top.

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
