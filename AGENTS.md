# Weightlifting Equipment Price Tracker

**Scraper + Dashboard** — tracks barbell, plate, rack, belt, apparel, shoe &
accessory prices across 27 retailers. Highlights deals via historical price
comparison. Runs on Replit with Neon PostgreSQL.

## Stack

- Python 3.12 · Flask · Playwright · BeautifulSoup
- PostgreSQL (Neon) in production, SQLite locally
- Jinja templates + vendored Chart.js (no external script hosts)
- Dark-mode-first "cyber-athletic" UI: forced `color-scheme: dark` across every
  route — no light theme

## Project structure

```
weightlifting-equipment/
├── dashboard.py               # Flask app — routes and auth only, no markup
├── templates/                 # Jinja templates (base_auth.html is the shared shell)
├── static/css, static/js      # extracted styles/scripts; chart.js vendored
├── scraper/
│   ├── db.py                  # shared DB layer, schema, migrations
│   ├── categories.py          # canonical category taxonomy
│   ├── ingest.py              # the single ingest path
│   ├── equipment_scraper.py   # HTTP scraper engine
│   ├── browser_scraper.py     # Playwright scraper engine
│   ├── product_matching.py    # cross-store fuzzy matching
│   ├── equipment_db.py        # reporting CLI
│   └── run_scrape.py          # orchestrator
├── migrate_to_neon.py
├── tests/
└── .replit
```

## Architecture notes

These are the decisions that are easy to undo by accident. Each exists because
the alternative was tried and broke something.

### Products are keyed on `(site, url)`

**Not `(site, name)`.** Retailers reuse a display name across distinct products —
NoBull lists 28 colourways of "NOBULL Laces" at two prices. Under the old
name-based key they collapsed onto one row, and each scrape wrote 28
`price_history` rows against it with alternating prices. That made `AVG`/`MIN`/
`MAX`, deal detection, and the trend chart meaningless. `db.py` migrates old
databases automatically (drops the constraint, merges colliding rows, repoints
their history).

Invariant worth preserving: **one `price_history` row per product per run.**

### One database layer, one ingest path

`scraper/db.py` is the only place that knows about dialects. Queries are written
once with `?` placeholders and `db.round()` / `db.now()` for the expressions that
genuinely differ. Before this, every query existed twice, which is exactly how
`ROUND(AVG(similarity), 1)` shipped — valid SQLite, but Postgres raises
`function round(double precision, integer) does not exist`.

Similarly, `scraper/ingest.py` is the only ingest path. There used to be two
(one per backend) and they disagreed by 1,600 products on the same input.

### Category taxonomy

Stores use 82 labels for the same product types, and ~45% of rows land in a
bucket that says nothing ("All", "Accessories", "Apparel"). `categories.py`
classifies from the product name first, then the store's label. Two things to
know when editing it:

- Patterns are built with `words()` / `phrase()`, never by hand. A hand-written
  `\b(alternatives)\b` silently fails on plurals: after matching "resistance
  band" in "Resistance Bands" the trailing `\b` sits between "d" and "s".
- `_EARLY_RULES` runs first, for decisive nouns. The equipment rules match bare
  "bar"/"barbell"/"plate", so without it a "Load The Bar T-Shirt" is a barbell
  and a "2POOD Barbell Patch" is a barbell. Bare "shirt" is deliberately *not* an
  early apparel noun — a "Bench Shirt" is supportive gear.

`products.group_name` is derived data. After editing the taxonomy run
`python scraper/ingest.py --backfill-groups` rather than re-scraping.

### Scraping is parallel but rate-limited

**Shopify rate-limits per IP across every storefront on the platform.** Scraping
several Shopify stores concurrently trips a shared budget: at 6 workers, a second
full run within a couple of minutes returned 429 for 23 of 26 stores. Default is
3 workers plus per-host spacing in `http_get()`, which retries 429/5xx with
exponential backoff and backs the whole host off, not just the one request.

Every extractor must go through `http_get`. The failure mode being avoided is
silent: the extractors used to call `requests.get` directly and treat any non-200
as "no more pages", so a throttled store produced zero products and the run still
reported success. A blocked store now returns status `blocked` and lands in the
run's summary.

### Scrape runs heartbeat

`run_scrape.py` touches `scrape_runs.heartbeat_at` every 30s, and the dashboard
reaps runs that go quiet for `SCRAPE_STALE_MINUTES`. Without it, a scrape killed
with its container left a row stuck in `running` forever, and `/api/scrape`
refuses to start while one is running — the Sync button was permanently dead with
no recovery path.

### The in-app scheduler is off by default

`RUN_SCHEDULER=0`. It only makes sense on an always-on deployment. `.replit` uses
`deploymentTarget = "autoscale"`, which recycles the container between requests:
the thread fires unpredictably and any scrape it launches is SIGKILLed mid-run.
On Autoscale, drive scraping from a Scheduled Deployment instead.

### Rogue is the only browser-scraped store

- Chromium is located via `chromium_executable()`. Playwright's own download path
  points at an ephemeral Replit cache that is routinely empty — the symptom is
  `Executable doesn't exist at .../chromium_headless_shell-.../` and a silent
  zero-product scrape. Prefer `REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE` or a nix
  chromium on PATH.
- The grid selector is `.products-wrapper a.hover-card`. `a[href*="/rogue-"]`
  matched every Rogue link on the page (nav + carousels), turning 70 barbells
  into 105 rows including kettlebells, shirts and an Echo Bike. `a.product` is
  the "popular products" carousel and returns the same 12 items on every page.
- Rogue reorganises its category URLs; the ones in `ROGUE_CATEGORIES` are
  verified live. Two of the originals had started 404ing.

### A `--site` run must not overwrite `scrape_latest.json`

It holds one store's products and would masquerade as a complete scrape.

## Security posture

Everything scraped is third-party content, so it's treated as hostile:

- `esc()` escapes quotes as well as angle brackets — it's interpolated into
  `src=""`/`href=""`, and the quote-less version was a stored-XSS path from any
  scraped page. `sanitizeUrl()` allows only `http(s)`, so a scraped
  `javascript:` URL can't execute.
- CSRF token on every POST; sessions are HttpOnly/SameSite=Lax/Secure, cleared on
  login, with a persisted (not auth-file-derived) signing key.
- Reset tokens are stored hashed and aren't burned by a failed validation.
- Client IP comes from `ProxyFix` with a fixed trusted-hop count. Reading
  `X-Forwarded-For` directly let anyone bypass the login rate limit by rotating
  the header.
- CSP forbids external script hosts, which is why Chart.js is vendored.

## Development

```bash
pip install -r requirements.txt
python scraper/run_scrape.py --http-only
python dashboard.py                    # http://127.0.0.1:8080

python scraper/run_scrape.py           # full scrape (needs chromium)
python dashboard.py --setup-auth

python scraper/equipment_db.py summary
python scraper/equipment_db.py cheapest barbells
python scraper/ingest.py --backfill-groups

pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

Tests run against a temporary SQLite file; `conftest.py` clears `DATABASE_URL` so
a stray environment variable can't point them at a live database.

## Lessons learned (cross-project)

See AGENT-PLAYBOOK for the full archive. Relevant to this project:
- Replit lockfile rewrites: `package-firewall.replit.local` → `registry.npmjs.org`
- Chrome on Replit: prefer `REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE` over
  `playwright install`
- Preview port: Replit only exposes 80 and 443; map via `[[ports]]` in `.replit`
- Claude Code CLI "disappearing" after a restart: `/home/runner` is an ephemeral
  overlay fs, only `/home/runner/workspace` persists. The installer's binaries
  already land in the persisted path, but the `~/.local/bin/claude` PATH symlink
  it creates does not survive a restart, and neither does anything written to
  `~/.bashrc` (it's a symlink into `/nix/store`, not a real file — don't edit it
  directly). Fix has two independent, redundant layers, both driven from
  repo-tracked/persisted files so they're reapplied on every boot regardless of
  what happened to `$HOME`:
  1. A self-healing launcher at `workspace/.local/bin/claude` (execs the newest
     dir under `workspace/.local/share/claude/versions/`), with `PATH` extended
     to include it via `.replit`'s `[env]` block. This is what agent/workflow-mode
     shells pick up (Replit rebuilds `/run/replit/env/latest` from `.replit` on
     boot and sources it).
  2. `workspace/.config/bashrc` — Nix's `replit-bashrc` sources
     `${REPL_HOME}/.config/bashrc` for every *interactive* Shell tab (when
     `REPLIT_MODE` is unset, i.e. a human-opened shell, not agent/workflow mode).
     This file sets the same `PATH` + `CLAUDE_CONFIG_DIR` directly, independent
     of the `.replit`-env rebuild pipeline.
- **Same-host rate limits are shared across a platform's tenants.** Shopify's
  budget is per-IP across every storefront, so "scrape N independent stores in
  parallel" is not actually independent.
