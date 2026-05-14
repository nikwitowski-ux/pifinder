# pifinder

PI law firm lead-gen + buying-signal intelligence. Given a geography, returns ranked firms enriched with active buying signals (Meta ads, news, hiring, attorney roster, review velocity).

## Stack

- Python 3.12, `uv` for env management
- SQLite (`data/firms.db`) — no ORM, stdlib `sqlite3`
- `httpx` async + `playwright` for JS-heavy pages
- `typer` CLI
- `fastapi` + Jinja2 + HTMX + Leaflet + Tabulator for the dashboard
- `pydantic` v2 everywhere

## Quickstart

```bash
uv sync
uv run playwright install chromium   # one-time, ~150MB download for the scraper
cp .env.example .env                  # fill in GOOGLE_PLACES_API_KEY
uv run pifinder discover --location "Orange County, CA" --radius 25 --output firms.csv
uv run pifinder enrich --all --limit 5
```

## CLI

```
pifinder discover --location "Orange County, CA" --radius 25 [--query "personal injury law firm"] [--output firms.csv]
pifinder enrich [--firm-id ID | --all]
pifinder score --recompute
pifinder export --min-score 60 --format csv|xlsx
pifinder dashboard      # http://127.0.0.1:8000
```

## Layout

```
src/pifinder/
  cli.py            typer app
  models.py         pydantic types
  db.py             sqlite + migrations
  config.py         settings + config.yaml loader
  cache.py          disk-backed http response cache
  scoring.py        weighted lead score
  logging_setup.py  loguru
  sources/
    base.py
    google_places.py
    firm_website.py
    meta_ads.py
    news.py
    directories.py
  web/
    app.py          FastAPI
    templates/
    static/
tasks/
  todo.md           rolling build log (Nik's discipline)
  lessons.md        gotchas / decisions
tests/
data/
  firms.db
  cache/
```

## Scoring

Weights live in `config.yaml` so tuning needs no code change. See `scoring.py` for application.

## Politeness

- Single user-agent identifying the tool (see `.env.example`).
- 1 req/s per host default; tighter on directory scrapers.
- Every external response is cached on disk; re-runs during dev cost nothing.
- `robots.txt` respected on firm-website scrapes.
