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

## Next (build order from the master prompt)

- [ ] Firm-website scraper (`sources/firm_website.py`) using Playwright; politeness rules (robots.txt + 2s delay); extract attorneys, practice areas, recent posts
- [ ] Meta Ad Library source (`sources/meta_ads.py`) — official API, search by page name → is_running_ads + active_ad_count + ad_themes + regions
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
