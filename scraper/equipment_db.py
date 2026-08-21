#!/usr/bin/env python3
"""
Equipment price database — SQLite backend for price tracking.

Usage:
    equipment-db.py init                         # Create schema
    equipment-db.py ingest <scrape.json>          # Load scrape results
    equipment-db.py summary                       # Current market snapshot
    equipment-db.py history "Barbell"             # Price history for matching products
    equipment-db.py drops "20"                    # Products that dropped >20%
    equipment-db.py cheapest barbells             # Cheapest barbells across all sites
    equipment-db.py query "SELECT * FROM products WHERE category='Barbells' ORDER BY price LIMIT 10"
    equipment-db.py export                        # Export as spreadsheet
"""

import sqlite3
import json
import sys
import os
import re
import argparse
import csv
import io
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path.home() / 'equipment_data' / 'equipment.db'


# ── Schema ────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site        TEXT NOT NULL,
    name        TEXT NOT NULL,
    category    TEXT,
    currency    TEXT DEFAULT 'USD',
    url         TEXT,
    image_url   TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(site, name)
);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    price       REAL,
    price_text  TEXT,
    currency    TEXT DEFAULT 'USD',
    scraped_at  TEXT NOT NULL,
    source_url  TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_product
    ON price_history(product_id, scraped_at DESC);

CREATE INDEX IF NOT EXISTS idx_products_site
    ON products(site, category);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at       TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    trigger           TEXT NOT NULL DEFAULT 'manual',
    http_only         INTEGER NOT NULL DEFAULT 0,
    products_scraped  INTEGER,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_started
    ON scrape_runs(started_at DESC);
"""


# ── DB helpers ────────────────────────────────────────────────────────────

def get_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_schema(conn):
    """Add columns to pre-existing DBs that predate them (CREATE TABLE IF NOT
    EXISTS in SCHEMA_SQL only helps brand-new databases)."""
    cols = {row['name'] for row in conn.execute("PRAGMA table_info(products)")}
    if 'image_url' not in cols:
        conn.execute("ALTER TABLE products ADD COLUMN image_url TEXT")


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA_SQL)
    _migrate_schema(conn)
    conn.commit()
    conn.close()
    print(f'Database initialized: {DB_PATH}')


# ── Ingest ────────────────────────────────────────────────────────────────

def ingest_scrape(json_path):
    with open(json_path) as f:
        raw_products = json.load(f)

    # Deduplicate by (site, name), keeping first occurrence (most specific category)
    seen = set()
    products = []
    for p in raw_products:
        key = (p.get('site', ''), p.get('name', ''))
        if key not in seen:
            seen.add(key)
            products.append(p)

    scraped_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    conn = get_db()
    _migrate_schema(conn)

    inserted = 0
    updated = 0
    history = 0

    for p in products:
        site = p.get('site', 'Unknown')
        name = p.get('name', 'Unknown')
        category = p.get('category')
        currency = p.get('currency', 'USD')
        url = p.get('url', '')
        image_url = p.get('image_url', '')
        price = p.get('price')
        price_text = p.get('price_text')
        source_url = p.get('source_url', '')

        if not name or name == 'Unknown':
            continue

        # Upsert into products
        cur = conn.execute("""
            INSERT INTO products (site, name, category, currency, url, image_url)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(site, name) DO UPDATE SET
                category  = COALESCE(NULLIF(?, ''), category),
                url       = COALESCE(NULLIF(?, ''), url),
                image_url = COALESCE(NULLIF(?, ''), image_url),
                last_seen = datetime('now')
        """, (site, name, category, currency, url, image_url, category, url, image_url))

        if cur.rowcount == 0:
            updated += 1
        else:
            # Check if it was an update or insert
            if cur.lastrowid:
                inserted += 1
            else:
                updated += 1

        # Get product ID
        row = conn.execute(
            "SELECT id FROM products WHERE site = ? AND name = ?",
            (site, name)
        ).fetchone()
        if not row:
            continue
        pid = row['id']

        # Append to price_history (only if price is meaningful)
        if price is not None:
            try:
                price_float = float(price)
                if price_float > 0:
                    conn.execute("""
                        INSERT INTO price_history (product_id, price, price_text, currency, scraped_at, source_url)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (pid, price_float, price_text, currency, scraped_at, source_url))
                    history += 1
            except (ValueError, TypeError):
                pass

    conn.commit()
    conn.close()

    print(f'Ingested {len(products)} products from {json_path}')
    print(f'  Products: {inserted} new, {updated} existing')
    print(f'  Price records: {history}')


# ── Queries ───────────────────────────────────────────────────────────────

def query_raw(sql, params=None):
    conn = get_db()
    try:
        cur = conn.execute(sql, params or [])
        rows = cur.fetchall()
        if not rows:
            print('No results.')
            return
        # Print header
        headers = [d[0] for d in cur.description]
        print(' | '.join(f'{h:<20s}' for h in headers))
        print('-' * (22 * len(headers)))
        for row in rows:
            vals = [str(row[h] or '')[:20] for h in headers]
            print(' | '.join(f'{v:<20s}' for v in vals))
        print(f'\n{len(rows)} rows returned')
    except sqlite3.Error as e:
        print(f'SQL error: {e}')
    finally:
        conn.close()


def summary():
    conn = get_db()
    cur = conn.execute("""
        SELECT p.site, 
               COUNT(DISTINCT p.id) as products,
               COUNT(ph.id) as price_records,
               ROUND(AVG(ph.price), 2) as avg_price,
               ROUND(MIN(ph.price), 2) as min_price,
               ROUND(MAX(ph.price), 2) as max_price,
               COUNT(DISTINCT p.category) as categories
        FROM products p
        LEFT JOIN price_history ph ON ph.product_id = p.id
            AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history)
        GROUP BY p.site
        ORDER BY products DESC
    """)
    rows = cur.fetchall()

    total_products = sum(r['products'] for r in rows)
    total_records = sum(r['price_records'] for r in rows)

    print(f'\n{"SITE":20s} {"PRODUCTS":>8s} {"PRICES":>8s} {"AVG $":>8s} {"MIN $":>8s} {"MAX $":>8s} {"CATS":>5s}')
    print('─' * 62)
    for r in rows:
        avg = f'${r["avg_price"]:.2f}' if r['avg_price'] else '-'
        mn = f'${r["min_price"]:.2f}' if r['min_price'] else '-'
        mx = f'${r["max_price"]:.2f}' if r['max_price'] else '-'
        print(f'{r["site"]:20s} {r["products"]:>8d} {r["price_records"]:>8d} {avg:>8s} {mn:>8s} {mx:>8s} {r["categories"]:>5d}')

    print(f'\nTotal: {total_products} products, {total_records} price records')

    # Most recent scrape
    cur2 = conn.execute("SELECT MAX(scraped_at) as last FROM price_history")
    last = cur2.fetchone()['last']
    if last:
        print(f'Last scrape: {last[:19]}')

    conn.close()


def history(search_term, limit=10):
    conn = get_db()
    cur = conn.execute("""
        SELECT p.site, p.name, p.category, ph.price, ph.price_text, ph.currency, ph.scraped_at
        FROM products p
        JOIN price_history ph ON ph.product_id = p.id
        WHERE p.name LIKE ?
        ORDER BY ph.scraped_at DESC
        LIMIT ?
    """, (f'%{search_term}%', limit * 5))  # Over-fetch to filter unique

    rows = cur.fetchall()
    if not rows:
        print(f'No history found for "{search_term}"')
        return

    # Group by product name
    from collections import defaultdict
    by_product = defaultdict(list)
    for r in rows:
        key = (r['site'], r['name'])
        by_product[key].append(r)

    for (site, name), entries in sorted(by_product.items())[:limit]:
        print(f'\n{site} — {name[:55]}')
        print(f'  {"DATE":22s} {"PRICE":>10s}')
        print(f'  {"─"*32}')
        for e in entries[:15]:
            dt = e['scraped_at'][:19] if e['scraped_at'] else '?'
            pr = f'${e["price"]:.2f}' if e['price'] else e.get('price_text', '?')
            print(f'  {dt:22s} {pr:>10s}')

        if len(entries) > 15:
            print(f'  ... ({len(entries)} total records)')

    conn.close()


def drops(threshold_pct=20):
    """Find products that dropped more than threshold% (comparing first and last price records)."""
    conn = get_db()
    cur = conn.execute("""
        WITH first_price AS (
            SELECT product_id, price as first_price, scraped_at as first_date
            FROM price_history
            WHERE scraped_at = (SELECT MIN(scraped_at) FROM price_history ph2 WHERE ph2.product_id = price_history.product_id)
            GROUP BY product_id
        ),
        last_price AS (
            SELECT product_id, price as last_price, scraped_at as last_date
            FROM price_history
            WHERE scraped_at = (SELECT MAX(scraped_at) FROM price_history ph2 WHERE ph2.product_id = price_history.product_id)
            GROUP BY product_id
        )
        SELECT p.site, p.name, p.category, p.url,
               f.first_price, f.first_date,
               l.last_price, l.last_date,
               ROUND((f.first_price - l.last_price) / f.first_price * 100, 1) as drop_pct
        FROM products p
        JOIN first_price f ON f.product_id = p.id
        JOIN last_price l ON l.product_id = p.id
        WHERE f.first_price > 0 AND l.last_price > 0
          AND f.first_price > l.last_price
          AND (f.first_price - l.last_price) / f.first_price * 100 > ?
        ORDER BY drop_pct DESC
    """, (threshold_pct,))

    rows = cur.fetchall()
    if not rows:
        print(f'No products with price drops > {threshold_pct}%')
        return

    print(f'\n{"SITE":18s} {"DROP":>7s} {"OLD $":>8s} {"NEW $":>8s} {"PRODUCT":45s}')
    print('─' * 86)
    for r in rows:
        site = r['site'][:17]
        drop = f'{r["drop_pct"]:.0f}%'
        old = f'${r["first_price"]:.2f}'
        new = f'${r["last_price"]:.2f}'
        name = r['name'][:44]
        print(f'{site:18s} {drop:>7s} {old:>8s} {new:>8s} {name:45s}')

    conn.close()


def cheapest(category='Barbells', limit=15):
    conn = get_db()
    cur = conn.execute("""
        SELECT p.site, p.name, p.category, ph.price, ph.price_text, ph.currency, ph.scraped_at
        FROM products p
        JOIN price_history ph ON ph.product_id = p.id
            AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history ph2 WHERE ph2.product_id = p.id)
        WHERE p.category LIKE ? AND ph.price > 0
        ORDER BY ph.price ASC
        LIMIT ?
    """, (f'%{category}%', limit))

    rows = cur.fetchall()
    if not rows:
        print(f'No products found matching category "{category}"')
        return

    print(f'\nCheapest {category}:\n')
    print(f'{"SITE":20s} {"PRICE":>10s} {"PRODUCT":55s}')
    print('─' * 85)
    for r in rows:
        site = r['site'][:19]
        pr = f'${r["price"]:.2f}'
        name = r['name'][:54]
        print(f'{site:20s} {pr:>10s} {name:55s}')

    conn.close()


# ── XLSX Export ──────────────────────────────────────────────────────────

def export_xlsx(output_path=None):
    """Export to Excel spreadsheet (requires openpyxl)."""
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.chart import BarChart, Reference
    except ImportError:
        print('openpyxl required. Install: pip3 install openpyxl --break-system-packages')
        return

    if output_path is None:
        output_path = Path.home() / 'equipment_data' / 'equipment_report.xlsx'

    conn = get_db()

    wb = openpyxl.Workbook()

    # ── Sheet 1: Current Prices ──
    ws1 = wb.active
    ws1.title = 'Current Prices'

    cur = conn.execute("""
        SELECT p.site, p.name, p.category, ph.price, ph.price_text, ph.currency, p.url, ph.scraped_at
        FROM products p
        JOIN price_history ph ON ph.product_id = p.id
            AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history ph2 WHERE ph2.product_id = p.id)
        WHERE ph.price > 0
        ORDER BY p.site, ph.price
    """)

    headers = ['Site', 'Product', 'Category', 'Price', 'Price Text', 'Currency', 'URL', 'Last Seen']
    ws1.append(headers)
    for r in cur.fetchall():
        ws1.append([r['site'], r['name'], r['category'], r['price'],
                     r['price_text'], r['currency'], r['url'], r['scraped_at'][:19]])

    # Style header
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='2F5496', end_color='2F5496', fill_type='solid')
    for cell in ws1[1]:
        cell.font = header_font
        cell.fill = header_fill

    # Auto-width
    for col in ws1.columns:
        max_len = max(len(str(c.value or '')) for c in col[:50])
        ws1.column_dimensions[col[0].column_letter].width = min(max_len + 3, 50)

    # ── Sheet 2: Price Drops ──
    ws2 = wb.create_sheet('Price Drops')
    ws2.append(['Site', 'Product', 'Category', 'Previous $', 'Current $', 'Drop %'])

    cur2 = conn.execute("""
        WITH first_price AS (
            SELECT product_id, price as first_price, scraped_at as first_date
            FROM price_history
            WHERE scraped_at = (SELECT MIN(scraped_at) FROM price_history ph2 WHERE ph2.product_id = price_history.product_id)
            GROUP BY product_id
        ),
        last_price AS (
            SELECT product_id, price as last_price, scraped_at as last_date
            FROM price_history
            WHERE scraped_at = (SELECT MAX(scraped_at) FROM price_history ph2 WHERE ph2.product_id = price_history.product_id)
            GROUP BY product_id
        )
        SELECT p.site, p.name, p.category,
               f.first_price, l.last_price,
               ROUND((f.first_price - l.last_price) / f.first_price * 100, 1) as drop_pct
        FROM products p
        JOIN first_price f ON f.product_id = p.id
        JOIN last_price l ON l.product_id = p.id
        WHERE f.first_price > 0 AND l.last_price > 0
          AND f.first_price > l.last_price
        ORDER BY drop_pct DESC
        LIMIT 50
    """)

    for r in cur2.fetchall():
        ws2.append([r['site'], r['name'], r['category'],
                     f'${r["first_price"]:.2f}', f'${r["last_price"]:.2f}', f'{r["drop_pct"]:.0f}%'])

    for cell in ws2[1]:
        cell.font = header_font
        cell.fill = header_fill

    # ── Sheet 3: Price History (last 30 records) ──
    ws3 = wb.create_sheet('Price History')
    ws3.append(['Site', 'Product', 'Price', 'Currency', 'Date'])

    cur3 = conn.execute("""
        SELECT p.site, p.name, ph.price, ph.currency, ph.scraped_at
        FROM price_history ph
        JOIN products p ON p.id = ph.product_id
        ORDER BY ph.scraped_at DESC
        LIMIT 500
    """)
    for r in cur3.fetchall():
        ws3.append([r['site'], r['name'], r['price'], r['currency'], r['scraped_at'][:19]])

    for cell in ws3[1]:
        cell.font = header_font
        cell.fill = header_fill

    # ── Sheet 4: Market Overview (Avg price by site + category) ──
    ws4 = wb.create_sheet('Market Overview')
    ws4.append(['Site', 'Category', 'Products', 'Avg Price', 'Min Price', 'Max Price'])

    cur4 = conn.execute("""
        SELECT p.site, p.category,
               COUNT(DISTINCT p.id) as products,
               ROUND(AVG(ph.price), 2) as avg_price,
               ROUND(MIN(ph.price), 2) as min_price,
               ROUND(MAX(ph.price), 2) as max_price
        FROM products p
        JOIN price_history ph ON ph.product_id = p.id
            AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history ph2 WHERE ph2.product_id = p.id)
        WHERE ph.price > 0
        GROUP BY p.site, p.category
        ORDER BY p.site, p.category
    """)
    for r in cur4.fetchall():
        ws4.append([r['site'], r['category'], r['products'],
                     r['avg_price'], r['min_price'], r['max_price']])

    for cell in ws4[1]:
        cell.font = header_font
        cell.fill = header_fill

    wb.save(output_path)
    conn.close()
    print(f'Spreadsheet saved: {output_path}')


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Equipment price database')
    default_db = str(DB_PATH)
    parser.add_argument('command', nargs='?', help='init | ingest | summary | history | drops | cheapest | query | export')
    parser.add_argument('args', nargs='*', help='Command arguments')
    parser.add_argument('--db', default=default_db, help=f'Sqlite DB path (default: {default_db})')
    args = parser.parse_args()

    db_path = Path(args.db)

    # Override module-level DB_PATH for all handler functions
    import sys
    this_module = sys.modules[__name__]
    this_module.DB_PATH = db_path

    cmd = args.command or 'summary'

    if cmd == 'init':
        init_db()
    elif cmd == 'ingest':
        for path in args.args:
            ingest_scrape(path)
    elif cmd == 'summary':
        summary()
    elif cmd == 'history':
        term = ' '.join(args.args) if args.args else 'Barbell'
        history(term)
    elif cmd == 'drops':
        threshold = int(args.args[0]) if args.args else 20
        drops(threshold)
    elif cmd == 'cheapest':
        cat = ' '.join(args.args) if args.args else 'Barbells'
        cheapest(cat)
    elif cmd == 'query':
        sql = ' '.join(args.args)
        query_raw(sql)
    elif cmd == 'export':
        export_xlsx()
    else:
        print(f'Unknown command: {cmd}')
        parser.print_help()


if __name__ == '__main__':
    main()