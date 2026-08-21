# Weightlifting Equipment Price Tracker

**Scraper + Dashboard** — tracks barbell, plate, rack, belt, apparel, shoe &
accessory prices across 27+ retailers. Highlights deals via historical price
comparison. Runs on Replit with Neon PostgreSQL.

## Stack

- Python 3.12 · Flask · Playwright · BeautifulSoup
- PostgreSQL (Neon) in production, SQLite locally
- Chart.js on the frontend for price history

## What it monitors

**HTTP (18 stores, no browser needed):**
EliteFTS · Pioneer Fitness · Fringe Sport · Cerberus Strength ·
Onyx Straps · LiftingLarge · 2POOD · American Barbell · REP Fitness ·
Bells of Steel · Weightlifting House · Titan Fitness · Get Rx'd ·
Hookgrip · Force USA · NoBull · Slingshot · Mark Bell

**Playwright (9 stores, rate-limited / JS-rendered):**
Rogue Fitness · TYR Sport · LUXIAOJUN · SBD Apparel · Virus Intl ·
Born Primitive · Gymreapers · Again Faster · Inzer Advance Designs

## Project structure

```
weightlifting-equipment/
├── dashboard.py              # Flask app (SQLite local, Neon on Replit)
├── scraper/
│   ├── __init__.py
│   ├── equipment_scraper.py  # HTTP scraper engine
│   ├── browser_scraper.py    # Playwright scraper engine
│   ├── equipment_db.py       # SQLite DB + CLI tools
│   └── run_scrape.py         # Orchestrator
├── migrate_to_neon.py        # One-time SQLite → PostgreSQL migration
├── tests/                    # pytest suite (auth, reset, rate limiting)
├── .replit                   # Replit platform config
├── requirements.txt
├── requirements-dev.txt      # + pytest, for running tests/
├── AGENTS.md
└── README.md
```

## Development

```bash
# Local — scrapes HTTP stores, uses SQLite
pip install -r requirements.txt
python scraper/run_scrape.py --http-only
python dashboard.py           # http://127.0.0.1:8080

# Full scrape (needs chromium)
python scraper/run_scrape.py

# Auth
python dashboard.py --setup-auth

# Database CLI
python scraper/equipment_db.py summary
python scraper/equipment_db.py cheapest barbells
python scraper/equipment_db.py history "Ohio Bar"
python scraper/equipment_db.py drops 10

# Tests
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

## Replit deployment

1. Create Replit project from this repo
2. Set `DATABASE_URL` secret (Neon connection string)
3. Run `python dashboard.py --setup-auth` to create the first user
4. Push to `main` — Autoscale picks up `.replit`

On every deploy the build step installs deps + Chromium for Playwright.

## Environment

| Variable | Required? | Purpose |
|---|---|---|
| `DATABASE_URL` | Replit only | Neon PostgreSQL. Omit for local SQLite. |
| `PORT` | No | Default 8080 — .replit maps 80→8080 |
| `DASHBOARD_SECRET` | No | Flask session secret key. Falls back to a hash of the auth file. |
| `AGENTMAIL_API_KEY` | No | Enables password-reset emails via AgentMail. Without it, reset links are logged to stdout instead of emailed. |
| `AGENTMAIL_INBOX_ID` | No | Pin a specific AgentMail inbox to send from. Without it, one is auto-created (idempotent) on first reset request. |

## Auth

Login is by email — usernames must be valid email addresses (enforced by `is_valid_email()`
in `dashboard.py`). Passwords are salted (werkzeug `generate_password_hash`, scrypt) and
stored in `~/.equipment_dashboard_auth` as `email:hash` per line, permissions 600.
Legacy unsalted SHA-256 entries (pre-hardening) are transparently upgraded on next login.

```bash
python dashboard.py --setup-auth      # create/update a user from the CLI
```

There's no auto-created default account — the app refuses to start with zero users configured.

**Password reset:** `/forgot-password` → `/reset-password?token=...` in `dashboard.py`.
Tokens are single-use, expire after 30 minutes, and are stored separately in
`~/.equipment_dashboard_resets` (600). Delivery goes through AgentMail (see Environment table).

**Rate limiting:** `/login` (10 attempts / 5 min per IP) and `/forgot-password`
(5 requests / 5 min per IP, plus a silent 3-emails / 15 min per-address throttle) use an
in-process sliding-window limiter (`rate_limited()` in `dashboard.py`). It's per-worker, not
shared across gunicorn's processes — fine at current traffic, revisit if abuse shows up.

## Price history

Each scrape run appends a row to `price_history` per product. After 5+ scrapes
the dashboard shows meaningful trend lines. Deals are flagged when current
price is >10% below the product's running historical average.

## Data flow

```
Scraper → JSON file → SQLite (local) or Neon (Replit) ← Dashboard reads
```

The VPS cron runs daily at 6am UTC, scrapes all stores, and pushes to the
same Neon database the Replit dashboard reads from.

## Lessons learned (cross-project)

See AGENT-PLAYBOOK for the full archive. Relevant to this project:
- Replit lockfile rewrites: `package-firewall.replit.local` → `registry.npmjs.org`
- Chrome on Replit: install via nix channel + `playwright install chromium --with-deps`
- Auth persistence: pin `CLAUDE_CONFIG_DIR` in `.replit`
- Preview port: Replit only exposes 80 and 443; map via `[[ports]]` in `.replit`