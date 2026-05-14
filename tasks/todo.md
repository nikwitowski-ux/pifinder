# pifinder — build log

Rolling task log. Each session: tick off what shipped, add what's next, write any gotchas to `lessons.md`.

## Done (v0.1 scaffold + discover slice)

- [x] Project scaffold: `uv` env, `src/` layout, `pyproject.toml`, `.env.example`, `.gitignore`, `config.yaml`, `README.md`
- [x] Pydantic v2 models in `models.py` (FirmRecord, Attorney, Signal, EnrichmentResult, ScoreBreakdown, DiscoverRun)
- [x] SQLite schema (`migrations/0001_init.sql`) with WAL, FK constraints, indices, `cached_responses`, `runs`, `scores`
- [x] DB layer in `db.py` with idempotent migrations, transactions, `upsert_firm` keyed on place_id then normalized name
- [x] Config (`pydantic-settings` + `config.yaml`), `loguru` logging, disk-backed JSON response cache
- [x] Source base Protocols (`DiscoverySource`, `EnrichmentSource`)
- [x] Google Places API v1 source (`searchText` + pagination + field mask + retry with `tenacity` + on-disk caching)
- [x] Typer CLI: `discover`, `dbinit`, `info`
- [x] End-to-end test against mocked Places API: HTTP → parse → upsert → CSV → idempotent re-run
- [x] Pagination test (multi-page nextPageToken)
- [x] Normalizer unit test (handles "P.C.", "LLP", "PLLC", "The Law Offices of", casing)

## Done (v0.2 firm-website enrichment)

- [x] Pure HTML parser (`sources/_html_parser.py`) — JSON-LD attorneys + heuristic cards, practice areas, post dates, established year, same-origin link discovery
- [x] Playwright scraper (`sources/firm_website.py`) — robots.txt enforcement, max_pages + per-host delay, candidate-path planning (`/attorneys`, `/our-team`, `/blog`, …), aggregator that rolls up multiple pages
- [x] DB helpers: `apply_firm_patch` (whitelist of patchable columns), `upsert_attorney` (idempotent on firm_id+name), `insert_signal`
- [x] CLI `enrich --firm-id N | --all [--limit N]` with soft-fail per firm
- [x] Tests: parser (9), aggregator (6), persistence round-trip (4)

## Done (v0.4 scoring engine)

- [x] Aggregation-focused weight set in `config.yaml` (max 100, no buying-intent components yet)
- [x] Pure scoring engine (`scoring.py`): `ScoringInputs` + 8 component evaluators + `score()` orchestrator. No I/O.
- [x] Each component takes `now` from inputs so tests are deterministic
- [x] Mutually exclusive review tiers (high ≥100 vs moderate 50–99) enforced + tested
- [x] Weight keys without an evaluator are silently dropped — config can lead implementation
- [x] DB: `upsert_score` (idempotent), `firms_needing_score` (skips already-scored unless --recompute)
- [x] CLI: `pifinder score --firm-id N | --all [--recompute]`
- [x] Tests: 26 unit tests on the pure engine + 3 persistence tests = 29 new
- [x] Verified live: scored 6 seeded firms, dashboard pins/grid/detail-page all reflect new scores correctly

## Done (v0.3 dashboard)

- [x] FastAPI app + routes (`/`, `/firm/{id}`, `/api/firms`, `/api/firms/{id}`, `/api/export.csv`, `/healthz`)
- [x] Templates: base / index / firm_detail (Jinja2; Leaflet 1.9 + markercluster + Tabulator 6 via CDN)
- [x] Design system: paper / ink / signal-orange. Fraunces (display) + Manrope (body) + IBM Plex Mono (data)
- [x] Map / grid bidirectional sync (row click → fly + popup; pin click → scroll + highlight)
- [x] Filters: search, min-score, has-website, enriched-only — server-side query params
- [x] CSV export of filtered set
- [x] CLI `pifinder dashboard --host --port [--reload]`
- [x] API smoke + filter tests via httpx.ASGITransport (7 new)
- [x] Visual verification: seeded 6 firms, captured screenshots of index + detail

## Aggregation-focus pivot (decided 2026-05-14)

Deferring buying-signal depth (Meta Ads / news scoring) until base aggregation
is operationally useful. Tool's value right now is being a clean aggregator
across Places + firm-website data.

## Next

- [ ] Real-API smoke test: live `discover` against a real `GOOGLE_PLACES_API_KEY` for a small Irvine radius. Verify the dashboard against actual data, not synthetic.
- [ ] xlsx export with conditional formatting on `score`
- [ ] Avvo / Justia directory scrapers (only if Places coverage proves insufficient on first real run)
- [ ] Street View embed on firm detail page (needs `GOOGLE_MAPS_EMBED_API_KEY`)
- [ ] Territory drawing (Leaflet.draw polygon/radius)
- [ ] (later) Meta Ad Library, news signals, scoring engine — when intent buying becomes the focus
- [ ] News signals (`sources/news.py`) — Google News RSS keywords (verdict / settlement / hires / expands) past 90d
- [ ] Avvo / Justia fallback (`sources/directories.py`) — Playwright with realistic timing
- [ ] Scoring engine (`scoring.py`) with weights from `config.yaml`; tests for each component
- [ ] CLI: `enrich [--firm-id ID | --all]`, `score --recompute`, `export --min-score --format csv|xlsx`
- [ ] FastAPI dashboard scaffolding: `/` (Leaflet + Tabulator split view), `/firm/{id}` detail, `/api/firms` JSON
- [ ] Filtering + bidirectional map↔grid sync
- [ ] Export buttons (CSV + xlsx with conditional formatting on score)
- [ ] Street View embed on firm detail page
- [ ] STRETCH: Leaflet.draw polygon/radius territory + persistence

## Known constraints

- Python 3.12 (no 3.11 installed locally; spec is "3.11+")
- Politeness: 1 rps/host default, configurable in `config.yaml`
- Every external call is cached on disk in `data/cache/` — dev reruns are free
- No-op gracefully when keys/sources missing; dashboard must work day-one on Places-only data

## Definition of "done" for a module

1. Source file exists and is importable
2. There's at least one test that exercises the parser / logic (not just thin API wrapping)
3. CLI integration works against a small real or cassette sample
4. Failure modes log clearly and don't kill the run
5. A line added under "Done" here and any gotchas in `lessons.md`
