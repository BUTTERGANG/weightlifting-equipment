#!/usr/bin/env python3
"""
Scheduled runner: scrapes all equipment sites, saves to JSON/CSV/DB.
"""
import json, sys, os, time, sqlite3
from pathlib import Path
from datetime import datetime, timezone

scripts_dir = os.path.expanduser('~/.hermes/scripts')
sys.path.insert(0, scripts_dir)

from equipment_scraper import scrape_all, to_json, to_csv

OUTPUT_DIR = Path.home() / 'equipment_data'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')
print(f'Equipment price scrape starting at {date_str}', flush=True)

# Determine if browser scraping is available
has_browser = False
try:
    import playwright
    has_browser = True
except ImportError:
    print('Playwright not available, skipping browser sites', flush=True)

# Run with browser if available
results, products = scrape_all(use_browser=has_browser)

# Current files
latest_json = OUTPUT_DIR / 'equipment_prices_latest.json'
latest_csv = OUTPUT_DIR / 'equipment_prices_latest.csv'
archive_json = OUTPUT_DIR / f'equipment_prices_{date_str}.json'

# Save current
latest_json.write_text(to_json(products))
latest_csv.write_text(to_csv(products))
archive_json.write_text(to_json(products))

print(f'{len(products)} products -> {latest_json}, {latest_csv}', flush=True)

# Ingest into SQLite database
try:
    from equipment_db import init_db, ingest_scrape
    init_db()
    print('Ingesting into SQLite...', flush=True)
    ingest_scrape(str(latest_json))
except Exception as e:
    print(f'DB ingest error: {e}', flush=True)

# Cleanup old archives (keep 60 days)
now_ts = time.time()
removed = 0
for f in OUTPUT_DIR.glob('equipment_prices_*.json'):
    if f.name != 'equipment_prices_latest.json' and f.stat().st_mtime < now_ts - 60 * 86400:
        f.unlink()
        removed += 1
if removed:
    print(f'Cleaned {removed} old archives', flush=True)

# Push to Neon if DATABASE_URL is set
neon_url = os.environ.get('DATABASE_URL', '')
if neon_url:
    print('Pushing to Neon PostgreSQL...', flush=True)
    try:
        import psycopg2
        conn = psycopg2.connect(neon_url)
        cur = conn.cursor()

        # Ensure tables exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id          SERIAL PRIMARY KEY,
                site        TEXT NOT NULL,
                name        TEXT NOT NULL,
                category    TEXT,
                currency    TEXT DEFAULT 'USD',
                url         TEXT,
                first_seen  TIMESTAMP NOT NULL DEFAULT NOW(),
                last_seen   TIMESTAMP NOT NULL DEFAULT NOW(),
                UNIQUE(site, name)
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id          SERIAL PRIMARY KEY,
                product_id  INTEGER NOT NULL REFERENCES products(id),
                price       REAL,
                price_text  TEXT,
                currency    TEXT DEFAULT 'USD',
                scraped_at  TIMESTAMP NOT NULL,
                source_url  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ph_product ON price_history(product_id, scraped_at DESC);
            CREATE INDEX IF NOT EXISTS idx_p_site ON products(site, category);
        """)
        conn.commit()

        scraped_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        pushed_p = 0
        pushed_h = 0

        for p in products:
            site = p.get('site', 'Unknown')
            name = p.get('name', 'Unknown')
            if not name or not p.get('price'):
                continue

            cur.execute("""
                INSERT INTO products (site, name, category, currency, url)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (site, name) DO UPDATE SET
                    category = COALESCE(NULLIF(%s, ''), products.category),
                    url = COALESCE(NULLIF(%s, ''), products.url),
                    last_seen = NOW()
            """, (site, name, p.get('category'), p.get('currency', 'USD'),
                  p.get('url', ''), p.get('category'), p.get('url', '')))
            pushed_p += 1

            cur.execute("SELECT id FROM products WHERE site = %s AND name = %s", (site, name))
            row = cur.fetchone()
            if row:
                try:
                    price_float = float(p['price'])
                    if price_float > 0:
                        cur.execute("""
                            INSERT INTO price_history (product_id, price, price_text, currency, scraped_at, source_url)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (row[0], price_float, p.get('price_text'), p.get('currency', 'USD'),
                              scraped_at, p.get('source_url', '')))
                        pushed_h += 1
                except (ValueError, TypeError):
                    pass

        conn.commit()
        cur.close()
        conn.close()
        print(f'Neon push: {pushed_p} products, {pushed_h} price records', flush=True)
    except ImportError:
        print('psycopg2 not available, skipping Neon push', flush=True)
    except Exception as e:
        print(f'Neon push error: {e}', flush=True)

print('Equipment scrape complete.', flush=True)

# ── Image fill pass: only for products still missing images ──
print('Checking for products missing images...', flush=True)
try:
    conn = sqlite3.connect(str(OUTPUT_DIR / 'equipment.db'))
    conn.row_factory = sqlite3.Row
    missing = conn.execute("""
        SELECT p.id, p.site, p.name, p.url
        FROM products p
        WHERE (p.image_url IS NULL OR p.image_url = '')
          AND p.url IS NOT NULL AND p.url != ''
        ORDER BY p.last_seen DESC
        LIMIT 500
    """).fetchall()
    conn.close()

    if missing:
        print(f'{len(missing)} products need images', flush=True)

        # Group by site
        import collections
        by_site = collections.defaultdict(list)
        for r in missing:
            by_site[r['site']].append((r['name'], r['url']))

        # Import browser scraper and equipment scraper configs
        sys.path.insert(0, scripts_dir)
        import browser_scraper as bs
        import equipment_scraper as es
        
        # Map site name back to browser key
        site_name_to_key = {v['name']: k for k, v in bs.SITES.items()}

        total_fetched = 0
        for site_name, products in by_site.items():
            browser_key = site_name_to_key.get(site_name)
            if not browser_key:
                print(f'  {site_name}: no Playwright config (HTTP site), skipping', flush=True)
                continue

            print(f'  {site_name}: fetching {len(products)} product images...', flush=True)
            results = bs.fill_missing_images(bs.SITES[browser_key], products)
            
            # Update DB with fetched images
            if results:
                conn2 = sqlite3.connect(str(OUTPUT_DIR / 'equipment.db'))
                for url, img_url in results.items():
                    conn2.execute(
                        "UPDATE products SET image_url = ? WHERE url = ? AND (image_url IS NULL OR image_url = '')",
                        (img_url, url)
                    )
                conn2.commit()
                conn2.close()
                total_fetched += len(results)
                print(f'    got {len(results)} images', flush=True)

        print(f'Total images fetched: {total_fetched}', flush=True)
    else:
        print('All products already have images!', flush=True)
except Exception as e:
    print(f'Image fill error: {e}', flush=True)

print('Equipment scrape fully complete.', flush=True)