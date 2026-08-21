#!/usr/bin/env python3
"""
One-time migration: copy existing SQLite data to PostgreSQL (Neon).

Usage:
    DATABASE_URL=postgres://user:pass@host/db python migrate_to_neon.py

Reads from the local SQLite database (EQUIPMENT_DB_PATH, default
~/equipment_data/equipment.db) and pushes all products and price history to
DATABASE_URL. Idempotent: products upsert on (site, url), and price rows are
skipped when an identical (product, timestamp) row already exists.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'scraper'))

from db import connect, init_schema, DB_PATH  # noqa: E402

BATCH = 500


def main():
    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url:
        print('Error: DATABASE_URL is not set.')
        print('Usage: DATABASE_URL=postgres://... python migrate_to_neon.py')
        return 1
    if not DB_PATH.exists():
        print(f'Error: no local SQLite database at {DB_PATH}')
        return 1

    # connect() picks its backend from DATABASE_URL, so open the source
    # explicitly with an empty URL to force the SQLite path.
    src = connect(url='')
    dst = connect(url=database_url)
    try:
        print(f'Source: {DB_PATH}')
        print('Target: PostgreSQL')
        init_schema(dst)

        products = src.query("""
            SELECT id, site, name, url, category, group_name, currency,
                   image_url, first_seen, last_seen
            FROM products WHERE url IS NOT NULL
        """)
        print(f'  {len(products)} products to migrate')

        from psycopg2.extras import execute_values
        cur = dst.conn.cursor()
        rows = [(p['site'], p['name'], p['url'], p['category'], p['group_name'],
                 p['currency'], p['image_url'], p['first_seen'], p['last_seen'])
                for p in products]
        for i in range(0, len(rows), BATCH):
            execute_values(cur, """
                INSERT INTO products (site, name, url, category, group_name,
                                      currency, image_url, first_seen, last_seen)
                VALUES %s
                ON CONFLICT (site, url) DO UPDATE SET
                    name       = EXCLUDED.name,
                    category   = COALESCE(EXCLUDED.category, products.category),
                    group_name = EXCLUDED.group_name,
                    image_url  = COALESCE(EXCLUDED.image_url, products.image_url),
                    last_seen  = EXCLUDED.last_seen
            """, rows[i:i + BATCH])
        dst.commit()

        # Map source ids to target ids via the natural key.
        target_ids = {(r['site'], r['url']): r['id']
                      for r in dst.query('SELECT id, site, url FROM products')}
        src_key = {p['id']: (p['site'], p['url']) for p in products}

        history = src.query("""
            SELECT product_id, price, price_text, currency, scraped_at, source_url
            FROM price_history
        """)
        print(f'  {len(history)} price records to migrate')

        hrows = []
        for h in history:
            key = src_key.get(h['product_id'])
            pid = target_ids.get(key) if key else None
            if pid is None:
                continue
            hrows.append((pid, h['price'], h['price_text'], h['currency'],
                          h['scraped_at'], h['source_url']))

        # Skip rows already present, so re-running doesn't duplicate history.
        existing = {(r['product_id'], str(r['scraped_at'])[:19])
                    for r in dst.query('SELECT product_id, scraped_at FROM price_history')}
        hrows = [r for r in hrows if (r[0], str(r[4])[:19]) not in existing]

        for i in range(0, len(hrows), BATCH):
            execute_values(cur, """
                INSERT INTO price_history (product_id, price, price_text,
                                           currency, scraped_at, source_url)
                VALUES %s
            """, hrows[i:i + BATCH])
        dst.commit()

        print(f'Done: {len(rows)} products, {len(hrows)} new price records')
        return 0
    finally:
        src.close()
        dst.close()


if __name__ == '__main__':
    sys.exit(main())
