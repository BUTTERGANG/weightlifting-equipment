# Weightlifting Equipment Price Tracker (Plate Magnet)

A price tracking dashboard for weightlifting gear across 27 retailers. Scrapes pricing
data, tracks historical trends, and highlights deal opportunities.

## Stack

- **Backend:** Python 3.12 + Flask
- **Database:** PostgreSQL (Neon) in production, SQLite locally — one query layer for both
- **Scraping:** requests + BeautifulSoup for HTTP stores, Playwright for Rogue
- **Frontend:** Server-rendered Jinja templates + vendored Chart.js, dark-mode-first
  "cyber-athletic" design system
- **Auth:** Session login with salted password hashes, CSRF tokens, self-service reset

## Repo structure

```
weightlifting-equipment/
├── dashboard.py               # Flask app (routes + auth only; no markup)
├── templates/                 # Jinja templates
│   ├── base_auth.html         #   shared shell for login/forgot/reset/404
│   ├── dashboard.html
│   ├── login.html  forgot.html  reset.html  404.html
├── static/
│   ├── css/dashboard.css      # main app styles
│   ├── css/auth.css           # shared auth-page styles
│   ├── js/dashboard.js        # client app
│   └── js/vendor/chart.min.js # vendored — no external script hosts
├── scraper/
│   ├── db.py                  # shared DB layer + schema + migrations
│   ├── categories.py          # canonical category taxonomy
│   ├── ingest.py              # the single ingest path
│   ├── equipment_scraper.py   # HTTP scraper engine (26 stores)
│   ├── browser_scraper.py     # Playwright scraper engine (Rogue)
│   ├── product_matching.py    # cross-store fuzzy matching
│   ├── equipment_db.py        # reporting CLI
│   └── run_scrape.py          # orchestrator
├── migrate_to_neon.py         # One-time SQLite → PostgreSQL migration
├── tests/                     # pytest suite
└── .replit
```

## Quick start (local)

```bash
pip install -r requirements.txt
python scraper/run_scrape.py --http-only     # Scrape the 26 HTTP stores
python dashboard.py --setup-auth             # Create the first user
python dashboard.py                          # http://127.0.0.1:8080
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

Tests always run against a temporary SQLite file; `tests/conftest.py` clears
`DATABASE_URL` so a stray environment variable can't point them at a live database.

## Data model

Products are keyed on **`(site, url)`**, not `(site, name)`.

Retailers reuse one display name across genuinely distinct products — NoBull lists 28
colourways of "NOBULL Laces" at two different prices. Under a name-based key those
collapsed onto one row and every scrape wrote 28 price records against it with
alternating prices, which made `AVG`/`MIN`/`MAX` — and therefore deal detection and the
trend chart — meaningless. The product URL is the retailer's own identity for an item.

`scraper/db.py` migrates older databases automatically on startup: it drops the legacy
`UNIQUE(site, name)` constraint, merges rows that now collide on `(site, url)`
(repointing their price history), and adds new columns.

## Categories

Stores use 82 different labels for the same handful of product types, and nearly half of
all rows land in a bucket that says nothing about the product ("All", "Accessories",
"Apparel", "Clearance"). `scraper/categories.py` maps every product to one of 19
canonical categories from its name and the store's label; `products.group_name` holds
the result and the dashboard's category rail navigates by it. About 4% end up in "Other".

The taxonomy is derived data — after editing `categories.py`, rebuild it without
re-scraping:

```bash
python scraper/ingest.py --backfill-groups
```

## Scraping

```bash
python scraper/run_scrape.py                 # Full scrape (HTTP + Rogue)
python scraper/run_scrape.py --http-only     # Skip Playwright
python scraper/run_scrape.py --site rogue    # One store
```

Sites are scraped in parallel (default 3 workers). **Shopify rate-limits per IP across
every storefront on the platform**, so more workers means more 429s rather than more
throughput; `http_get()` in `equipment_scraper.py` adds per-host spacing and retries
429/5xx with exponential backoff. A store that is throttled reports status `blocked` and
fails the run's summary line instead of silently contributing zero products.

Tune with `SCRAPE_WORKERS` and `SCRAPE_HOST_INTERVAL` if needed.

### Scheduling

The app can run scrapes three ways:

| How | When to use | Setup |
|---|---|---|
| **Manual** | Ad-hoc | The "Sync" button in the dashboard header |
| **In-app scheduler** | Always-on deployments (Reserved VM) | `RUN_SCHEDULER=1`, `SCRAPE_INTERVAL_HOURS=6` |
| **External cron** | Replit Autoscale (the current target) | A Scheduled Deployment running `python scraper/run_scrape.py` |

**The in-app scheduler is off by default and should stay off on Autoscale.** Autoscale
recycles the container between requests, so the background thread fires unpredictably and
any scrape it launches is killed mid-run. Use a Scheduled Deployment there instead.

Runs are tracked in `scrape_runs` and heartbeat every 30s. A run whose process dies is
reaped after `SCRAPE_STALE_MINUTES` (default 30) — without that, a killed scrape left a
row stuck in `running` forever and the Sync button returned 409 permanently.

## Replit

1. **Create repo** from this directory on Replit
2. **Set secrets:** `DATABASE_URL` (Neon connection string)
3. **Set up auth:** `python dashboard.py --setup-auth` in the shell
4. **Deploy:** push to `main`; Autoscale picks up `.replit`
5. **Schedule scrapes:** add a Scheduled Deployment running `python scraper/run_scrape.py`

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | On Replit | PostgreSQL connection string. Omit for local SQLite. |
| `EQUIPMENT_DB_PATH` | No | Local SQLite path (default `~/equipment_data/equipment.db`). |
| `PORT` | No | Server port (default 8080). |
| `DASHBOARD_SECRET` | No | Flask session secret. Otherwise persisted in `.equipment_dashboard_secret`. |
| `MIN_PASSWORD_LENGTH` | No | Default 12. |
| `SESSION_DAYS` | No | Session lifetime, default 14. |
| `TRUSTED_PROXY_HOPS` | No | Proxy hops to trust for the client IP (default 1 in production, 0 locally). |
| `RUN_SCHEDULER` | No | `1` enables the in-app scrape scheduler. Off by default. |
| `SCRAPE_INTERVAL_HOURS` | No | Scheduler interval, default 6. |
| `SCRAPE_STALE_MINUTES` | No | When a heartbeat-less run is presumed dead, default 30. |
| `SCRAPE_WORKERS` | No | Parallel site workers, default 3. |
| `SCRAPE_HOST_INTERVAL` | No | Minimum seconds between requests to one host, default 0.7. |
| `PRODUCTS_CACHE_SECONDS` | No | `/api/products` cache window, default 120. |
| `AGENTMAIL_API_KEY` | No | Enables password-reset emails. Without it, links are logged to stdout. |
| `AGENTMAIL_INBOX_ID` | No | Pin the AgentMail inbox; otherwise one is auto-created. |

## Auth & security

Usernames must be email addresses. Passwords are salted (werkzeug scrypt), at least
`MIN_PASSWORD_LENGTH` characters, and stored in `.equipment_dashboard_auth` (mode 600)
next to the app — **not** under `$HOME`, which Replit wipes on every container restart.
Legacy unsalted SHA-256 entries upgrade transparently on next login. There's no default
account; the app refuses to start with zero users.

```bash
python dashboard.py --setup-auth     # re-run to add more users
```

- **Sessions:** HttpOnly, SameSite=Lax, Secure in production, 14-day lifetime, and the
  session is cleared on login so a pre-auth value can't survive. The signing key is
  persisted rather than derived from the auth file — deriving it meant every password
  change silently logged everyone out.
- **CSRF:** every POST requires a token (form field or `X-CSRF-Token`).
- **Reset tokens:** single-use, 30-minute expiry, stored **hashed** in
  `.equipment_dashboard_resets` (600), and not consumed by a failed password validation.
- **Rate limits:** `/login` 10/5min, `/forgot-password` 5/5min per IP plus a silent
  3-emails/15min per address, `/api/scrape` 5/hour. The limiter is per-worker, not shared
  across gunicorn processes. The client IP comes from `ProxyFix` with a fixed number of
  trusted hops — reading `X-Forwarded-For` directly let anyone bypass the limit by
  rotating the header.
- **Headers:** CSP (no external script hosts), `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`, HSTS in production.
- **Output escaping:** everything scraped is third-party content, so the client escapes
  quotes as well as angle brackets, and only `http(s)` URLs may reach an `href`/`src`.

## Price history & deals

Each scrape appends one row per product to `price_history` — exactly one, which is the
point of the `(site, url)` key. A product is flagged as a deal when its current price is
>10% below its running average **and** it has at least two data points; with a single
observation the average is the current price, so everything would look like a 0% deal.

## Performance

- `/api/products` is gzipped (2.8 MB → ~240 KB), memoised for `PRODUCTS_CACHE_SECONDS`,
  and served with an ETag so repeat loads get a 304.
- Ingest batches its writes; the previous implementation issued three queries per product
  in a loop (~19,000 round-trips per scrape).

## Product images

`products.image_url` holds a hotlinked CDN URL from the retailer — nothing is cached
locally. It's preserved on re-scrape (`COALESCE`), so a partial scrape never erases a
known image. Images can 404 as retailers reorganise; the dashboard falls back to a
placeholder client-side.

## Data flow

```
Scraper → JSON file → ingest.py → SQLite or PostgreSQL ← Dashboard reads
```
