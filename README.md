# Weightlifting Equipment Price Tracker (Plate Magnet)

A price tracking dashboard for weightlifting gear across 27+ retailers. Scrapes pricing data, tracks historical trends, and highlights deal opportunities.

## Stack

- **Backend:** Python + Flask
- **Database:** PostgreSQL (Neon) in production, SQLite locally
- **Scraping:** requests + BeautifulSoup for HTTP sites, Playwright for rate-limited / JS-rendered stores
- **Frontend:** Server-rendered HTML + Chart.js, dark-mode-first "cyber-athletic" design system
- **Auth:** Session login with salted password hashes (email-based accounts, self-service password reset via AgentMail)

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
├── tests/                # pytest suite (auth, reset, rate limiting)
├── .replit               # Replit config
├── requirements.txt
├── requirements-dev.txt  # + pytest
├── AGENTS.md
└── README.md
```

## Quick start (local)

```bash
pip install -r requirements.txt
python scraper/run_scrape.py --http-only          # Scrape 18 HTTP stores
python dashboard.py --setup-auth                    # Create the first user (email + password)
python dashboard.py                                 # Start UI at http://127.0.0.1:8080
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
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
| `DASHBOARD_SECRET` | No | Flask session secret. Falls back to a hash of the auth file. |
| `AGENTMAIL_API_KEY` | No | Enables password-reset emails. Without it, reset links are logged to stdout instead. |
| `AGENTMAIL_INBOX_ID` | No | Pin the AgentMail inbox to send from; otherwise one is auto-created. |

## Auth

Usernames must be email addresses. Credentials are stored in `~/.equipment_dashboard_auth`
as `email:hash` (permissions 600), with passwords salted via werkzeug's `generate_password_hash`.
There's no default account — the app won't start until at least one user exists.

```bash
python dashboard.py --setup-auth     # Interactive setup; re-run to add more users
```

Forgot your password? Use the "Forgot password?" link on the login page — it emails a
30-minute single-use reset link via AgentMail (or logs it to stdout if no API key is set).
`/login` and `/forgot-password` are rate-limited per IP to blunt brute-force and spam.

## Deployment notes (from AGENT-PLAYBOOK lessons)

- **Preview port:** Dashboard binds to 8080; `.replit` maps external 80 → local 8080
- **CLAUDE_CONFIG_DIR:** Set in `.replit` to persist Claude Code auth across resets
- **Playwright:** Chromium is installed via nix + `playwright install chromium --with-deps` in the build step
- **Data persistence:** Price history lives in Neon. The scraper can run on Replit (workflow) or externally, both pushing to the same database.