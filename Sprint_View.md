# Sprint View — weightlifting-equipment

Last updated: 2026-08-23

## Sprint 1 — Cloudflare migration follow-through + images

| Task | Status | Owner | Notes |
|---|---|---|---|
| Add image_url column to Neon | backlog | — | **Blocking** — do first |
| Sync migrated scrapers into repo | backlog | — | Repo is stale vs ~/.hermes/scripts |
| First full image-fill pass | backlog | — | After Neon + sync |
| Dashboard product images | backlog | — | Needs image data |
| README refresh | backlog | — | Quick win, anytime |
| Digest deals with images | backlog | — | Blocked on deal history (5+ scrapes) |

## Done this sprint (pre-board)

| Task | Commit/Ref | Summary |
|---|---|---|
| Cloudflare → Playwright migration | skill v1.6.0 | 25 Shopify stores moved off HTTP (429s); 4 domains fixed |
| Image extraction system | equipment_db.py / browser_scraper.py | Two-pass: inline for HTTP stores, deferred fill via products.json fetch for Playwright |
| DB image_url column | local SQLite | Live ALTER TABLE + schema update; set-once semantics in ingest |
| Cron venv fix | job 2170cfe86c69 | Daily runner now uses venv Python (Playwright available) |

## Backlog (unscheduled)

See `SCRUM/Backlog/` for full task cards.
