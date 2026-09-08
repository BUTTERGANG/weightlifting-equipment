# Sprint View — weightlifting-equipment

Last updated: 2026-08-23

## Sprint 1 — Cloudflare migration follow-through + images

| Task | Status | Owner | Notes |
|---|---|---|---|
| Add image_url column to Neon | backlog | — | **Blocking** — do first |
| Sync migrated scrapers into repo | backlog | — | ✅ DONE 2026-09-08 (b2d39e0) — merged w/ Replit side, products.json approach adopted |
| First full image-fill pass | backlog | — | Mostly obsolete — images now inline via products.json |
| Dashboard product images | backlog | — | Needs image data |
| README refresh | backlog | — | ✅ DONE 2026-09-08 — AGENTS.md/README updated in sync commit |
| Digest deals with images | backlog | — | Blocked on deal history (5+ scrapes) |
| **Store threshold discounts** | backlog | — | NEW — TYR 20%/$99 verified; banners + checkout probes |

## Done this sprint (pre-board)

| Task | Commit/Ref | Summary |
|---|---|---|
| Cloudflare → Playwright migration | skill v1.6.0 | 25 Shopify stores moved off HTTP (429s); 4 domains fixed |
| Image extraction system | equipment_db.py / browser_scraper.py | Two-pass: inline for HTTP stores, deferred fill via products.json fetch for Playwright |
| DB image_url column | local SQLite | Live ALTER TABLE + schema update; set-once semantics in ingest |
| Cron venv fix | job 2170cfe86c69 | Daily runner now uses venv Python (Playwright available) |

## Backlog (unscheduled)

See `SCRUM/Backlog/` for full task cards.
