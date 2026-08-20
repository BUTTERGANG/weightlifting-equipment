#!/usr/bin/env python3
"""
Scrape orchestrator: runs all equipment sites, saves JSON, ingests into DB,
and pushes to Neon if DATABASE_URL is set.

Usage:
    python scraper/run_scrape.py              # Full scrape (HTTP + browser)
    python scraper/run_scrape.py --http-only   # HTTP stores only
    python scraper/run_scrape.py --site rogue  # Single site
"""
import sys, os, json, time, argparse
from pathlib import Path
from datetime import datetime, timezone

# Add scraper directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from equipment_scraper import scrape_all, to_json, to_csv

OUTPUT_DIR = Path(__file__).resolve().parent.parent / 'data'
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Weightlifting equipment price scraper')
    parser.add_argument('--http-only', action='store_true', help='Skip Playwright sites')
    parser.add_argument('--site', '-s', help='Scrape a single site')
    parser.add_argument('--output', '-o', help='Output JSON path')
    args = parser.parse_args()

    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M')
    print(f'Scrape starting at {date_str}', flush=True)

    # Check if browser scraping is available
    has_browser = False
    if not args.http_only:
        try:
            import playwright
            has_browser = True
        except ImportError:
            print('Playwright not available, skipping browser sites', flush=True)

    if args.site:
        results, products = scrape_all(site_filter=args.site, use_browser=has_browser)
    else:
        results, products = scrape_all(use_browser=has_browser)

    if not products:
        print('No products scraped', flush=True)
        return

    # Save JSON
    output_path = args.output or (OUTPUT_DIR / f'scrape_{date_str}.json')
    output_path = Path(output_path)
    output_path.write_text(to_json(products))
    print(f'{len(products)} products -> {output_path}', flush=True)

    # Also save latest
    latest = OUTPUT_DIR / 'scrape_latest.json'
    latest.write_text(to_json(products))

    # Ingest into local SQLite
    try:
        from equipment_db import init_db, ingest_scrape
        init_db()
        print('Ingesting into local SQLite...', flush=True)
        ingest_scrape(str(latest))
    except Exception as e:
        print(f'Local DB ingest error: {e}', flush=True)

    # Push to Neon if DATABASE_URL set
    db_url = os.environ.get('DATABASE_URL', '')
    if db_url:
        try:
            push_to_neon(products, db_url)
        except Exception as e:
            print(f'Neon push error: {e}', flush=True)

    # Run product matching
    try:
        from product_matching import compute_matches
        print('Running product matching...', flush=True)
        compute_matches(threshold=65)
    except ImportError as e:
        print(f'Product matching skipped (rapidfuzz not installed?): {e}', flush=True)
    except Exception as e:
        print(f'Product matching error: {e}', flush=True)

    print('Done', flush=True)


def push_to_neon(products, db_url):
    """Push scrape results to Neon PostgreSQL."""
    import psycopg2
    from psycopg2.extras import execute_values

    conn = psycopg2.connect(db_url)
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
    inserted = 0
    history = 0

    for p in products:
        site = p.get('site', 'Unknown')
        name = p.get('name', 'Unknown')
        category = p.get('category')
        currency = p.get('currency', 'USD')
        url = p.get('url', '')
        price = p.get('price')
        price_text = p.get('price_text')
        source_url = p.get('source_url', '')

        if not name or not price:
            continue

        # Upsert product
        cur.execute("""
            INSERT INTO products (site, name, category, currency, url)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (site, name) DO UPDATE SET
                category = COALESCE(NULLIF(%s, ''), products.category),
                url = COALESCE(NULLIF(%s, ''), products.url),
                last_seen = NOW()
        """, (site, name, category, currency, url, category, url))

        if cur.rowcount == 0 or cur.rowcount == 1:
            inserted += 1

        # Get product ID
        cur.execute("SELECT id FROM products WHERE site = %s AND name = %s", (site, name))
        row = cur.fetchone()
        if not row:
            continue
        pid = row[0]

        # Insert price history
        try:
            price_float = float(price)
            if price_float > 0:
                cur.execute("""
                    INSERT INTO price_history (product_id, price, price_text, currency, scraped_at, source_url)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (pid, price_float, price_text, currency, scraped_at, source_url))
                history += 1
        except (ValueError, TypeError):
            pass

    conn.commit()
    cur.close()
    conn.close()
    print(f'Pushed to Neon: {inserted} products, {history} price records', flush=True)


if __name__ == '__main__':
    main()