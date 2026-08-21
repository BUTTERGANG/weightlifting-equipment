#!/usr/bin/env python3
"""
The single ingest path for scrape results.

Replaces the two divergent implementations that used to exist
(``equipment_db.ingest_scrape`` for SQLite and ``run_scrape.push_to_neon`` for
Postgres) and which disagreed by 1,600 products on the same input.

Two things matter here:

1. **Identity is (site, url), not (site, name).** Retailers reuse a display name
   across distinct products, so name-keying merged unrelated items and made
   price history oscillate between their prices.
2. **One price_history row per product per run.** Duplicate rows within a single
   scrape are what poisoned AVG/MIN/MAX (and therefore deal detection).
"""
import json
from datetime import datetime, timezone

from categories import classify
from db import connect, init_schema

BATCH = 500


def dedupe(raw_products):
    """Collapse a scrape to one record per (site, url).

    The same product legitimately appears under several collections (a TYR shoe
    is in both "Lifters" and "Barefoot"). Those are identical apart from
    ``category``/``source_url``, so we keep the first and let the more specific
    category win, rather than writing a price row for each.
    """
    by_key, order = {}, []
    dropped_no_url = dropped_no_price = 0

    for p in raw_products:
        site = (p.get('site') or '').strip()
        name = (p.get('name') or '').strip()
        url = (p.get('url') or '').strip()
        if not site or not name or name == 'Unknown':
            continue
        if not url:
            dropped_no_url += 1
            continue
        price = p.get('price')
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None or price <= 0:
            dropped_no_price += 1
            continue

        key = (site, url)
        if key in by_key:
            # Keep the first sighting, but fill in anything it was missing.
            first = by_key[key]
            for field in ('category', 'image_url'):
                if not first.get(field) and p.get(field):
                    first[field] = p[field]
            # A more specific shelf may classify better than the first sighting.
            if first.get('group_name') == 'Other':
                regrouped = classify(first['name'], p.get('category'))
                if regrouped != 'Other':
                    first['group_name'] = regrouped
            continue

        by_key[key] = {
            'site': site,
            'name': name,
            'url': url,
            'category': (p.get('category') or '').strip() or None,
            # Canonical group, derived from the name and the store's label —
            # what the dashboard filters on. See scraper/categories.py.
            'group_name': classify(name, p.get('category')),
            'currency': p.get('currency') or 'USD',
            'image_url': (p.get('image_url') or '').strip() or None,
            'price': round(price, 2),
            'price_text': p.get('price_text'),
            'source_url': p.get('source_url') or '',
        }
        order.append(key)

    return [by_key[k] for k in order], {'no_url': dropped_no_url,
                                        'no_price': dropped_no_price}


def _upsert_products(db, products, scraped_at):
    """Insert/update every product, then return {(site, url): id} for all of them."""
    # COALESCE(NULLIF(...)) keeps a previously-known category/url/image when this
    # run happens not to have one, so a partial scrape never erases good data.
    if db.is_postgres:
        from psycopg2.extras import execute_values
        cur = db.conn.cursor()
        rows = [(p['site'], p['name'], p['url'], p['category'], p['group_name'],
                 p['currency'], p['image_url'], scraped_at) for p in products]
        for i in range(0, len(rows), BATCH):
            execute_values(cur, """
                INSERT INTO products
                    (site, name, url, category, group_name, currency, image_url, last_seen)
                VALUES %s
                ON CONFLICT (site, url) DO UPDATE SET
                    name       = EXCLUDED.name,
                    category   = COALESCE(EXCLUDED.category, products.category),
                    group_name = EXCLUDED.group_name,
                    image_url  = COALESCE(EXCLUDED.image_url, products.image_url),
                    last_seen  = EXCLUDED.last_seen
            """, rows[i:i + BATCH])
    else:
        db.executemany("""
            INSERT INTO products
                (site, name, url, category, group_name, currency, image_url, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (site, url) DO UPDATE SET
                name       = excluded.name,
                category   = COALESCE(excluded.category, products.category),
                group_name = excluded.group_name,
                image_url  = COALESCE(excluded.image_url, products.image_url),
                last_seen  = excluded.last_seen
        """, [(p['site'], p['name'], p['url'], p['category'], p['group_name'],
               p['currency'], p['image_url'], scraped_at) for p in products])
    db.commit()

    ids = {}
    sites = sorted({p['site'] for p in products})
    for site in sites:
        for row in db.query('SELECT id, site, url FROM products WHERE site = ?', (site,)):
            ids[(row['site'], row['url'])] = row['id']
    return ids


def _insert_prices(db, products, ids, scraped_at):
    rows = []
    for p in products:
        pid = ids.get((p['site'], p['url']))
        if pid is None:
            continue
        rows.append((pid, p['price'], p['price_text'], p['currency'],
                     scraped_at, p['source_url']))

    sql = """INSERT INTO price_history
                 (product_id, price, price_text, currency, scraped_at, source_url)
             VALUES (?, ?, ?, ?, ?, ?)"""
    if db.is_postgres:
        from psycopg2.extras import execute_values
        cur = db.conn.cursor()
        for i in range(0, len(rows), BATCH):
            execute_values(cur, """INSERT INTO price_history
                    (product_id, price, price_text, currency, scraped_at, source_url)
                VALUES %s""", rows[i:i + BATCH])
    else:
        db.executemany(sql, rows)
    db.commit()
    return len(rows)


def ingest(raw_products, db=None, scraped_at=None, quiet=False):
    """Ingest a list of scraped product dicts. Returns a stats dict."""
    products, dropped = dedupe(raw_products)
    scraped_at = scraped_at or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    own_db = db is None
    db = db or connect()
    try:
        if own_db:
            init_schema(db)
        before = db.query_one('SELECT COUNT(*) AS c FROM products')['c']
        ids = _upsert_products(db, products, scraped_at)
        after = db.query_one('SELECT COUNT(*) AS c FROM products')['c']
        prices = _insert_prices(db, products, ids, scraped_at)
    finally:
        if own_db:
            db.close()

    stats = {
        'seen': len(raw_products),
        'products': len(products),
        'new': after - before,
        'updated': len(products) - (after - before),
        'price_records': prices,
        'dropped_no_url': dropped['no_url'],
        'dropped_no_price': dropped['no_price'],
    }
    if not quiet:
        print(f"  Ingested {stats['products']} products from {stats['seen']} scraped rows "
              f"({stats['new']} new, {stats['updated']} existing)")
        print(f"  Price records: {stats['price_records']}")
        if dropped['no_price'] or dropped['no_url']:
            print(f"  Skipped: {dropped['no_price']} without a price, "
                  f"{dropped['no_url']} without a URL")
    return stats


def ingest_file(json_path, **kw):
    with open(json_path) as f:
        return ingest(json.load(f), **kw)


def backfill_groups(db=None, quiet=False):
    """Recompute group_name for every product already in the database.

    Run after changing scraper/categories.py — the taxonomy is derived data, so
    it can be rebuilt at any time without re-scraping.
    """
    own_db = db is None
    db = db or connect()
    try:
        rows = db.query('SELECT id, name, category, group_name FROM products')
        updates = []
        for r in rows:
            group = classify(r['name'], r['category'])
            if group != r.get('group_name'):
                updates.append((group, r['id']))
        for i in range(0, len(updates), BATCH):
            db.executemany('UPDATE products SET group_name = ? WHERE id = ?',
                           updates[i:i + BATCH])
        db.commit()
        if not quiet:
            print(f'  Regrouped {len(updates)} of {len(rows)} products')
        return len(updates)
    finally:
        if own_db:
            db.close()


if __name__ == '__main__':
    import sys
    if '--backfill-groups' in sys.argv:
        with connect() as _db:
            init_schema(_db)
            backfill_groups(db=_db)
    else:
        print(__doc__)
        print('Usage: python scraper/ingest.py --backfill-groups')
