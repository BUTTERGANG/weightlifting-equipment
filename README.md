# Weightlifting Equipment Price Tracker (LiftTracker)

A price tracking dashboard for weightlifting gear across 27+ retailers. Scrapes pricing data, tracks historical trends, and highlights deal opportunities.

## Stack

- **Backend:** Python + Flask
- **Database:** PostgreSQL (Neon) in production, SQLite locally
- **Scraping:** requests + BeautifulSoup for HTTP sites, Playwright for rate-limited / JS-rendered stores
- **Frontend:** Server-rendered HTML + Chart.js
- **Auth:** HTTP Basic Auth (SHA-256 hashed passwords)

## Repo structure

```
weightlifting-equipment/
├── dashboard.py          # Flask web app
├── scraper/
│   ├── equipment_scraper.py   # HTTP scraper (18 stores)
│   ├── browser_scraper.py     # Playwright scraper (9 stores)
│   ├── equipment_db.py        # SQLite DB layer (ingest, query, export)
│   └── run_scrape.py          # Orchestrator: scrape → save → ingest → push to Neon
├── migrate_to_neon.py    # One-time SQLite → PostgreSQL migration
├── .replit               # Replit config
├── requirements.txt
├── AGENTS.md
└── README.md
```

## Quick start (local)

```bash
pip install -r requirements.txt
python scraper/run_scrape.py --http-only          # Scrape 18 HTTP stores
python dashboard.py                                 # Start UI at http://127.0.0.1:8080
```

## Replit

1. **Create repo** from this directory on Replit
2. **Set secrets:** `DATABASE_URL` (Neon connection string)
3. **Set up auth:** Run `python dashboard.py --setup-auth` in the Replit shell
4. **Deploy:** Push to `main`, Replit Autoscale picks up the `.replit` config

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | On Replit | Neon PostgreSQL connection string. Omit for local SQLite. |
| `PORT` | No | Server port (default 8080). On Replit set via `.replit` ports. |

## Auth

User credentials stored in `~/.equipment_dashboard_auth`:

```bash
python dashboard.py --setup-auth     # Interactive setup
```

Passwords are SHA-256 hashed. Add multiple users by re-running `--setup-auth`.

## Deployment notes (from AGENT-PLAYBOOK lessons)

- **Preview port:** Dashboard binds to 8080; `.replit` maps external 80 → local 8080
- **CLAUDE_CONFIG_DIR:** Set in `.replit` to persist Claude Code auth across resets
- **Playwright:** Chromium is installed via nix + `playwright install chromium --with-deps` in the build step
- **Data persistence:** Price history lives in Neon. The scraper can run on Replit (workflow) or externally, both pushing to the same database.