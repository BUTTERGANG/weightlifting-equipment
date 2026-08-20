#!/usr/bin/env python3
"""
One-time migration: copy existing SQLite data to Neon PostgreSQL.

Usage:
    DATABASE_URL=postgres://user:pass@ep-xxx.us-east-2.aws.neon.tech/lifttracker
    python migrate_to_neon.py

This reads from ~/equipment_data/equipment.db and pushes all products
and price history to the PostgreSQL database specified in DATABASE_URL.
It's idempotent (upserts, won't duplicate).
"""
import os
import sys
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / 'equipment_data' / 'equipment.db'
DATABASE_URL = os.environ.get('DATABASE_URL', '')

if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable is required")
    print("Usage: DATABASE_URL=postgres://... python migrate_to_neon.py")
    sys.exit(1)

if not DB_PATH.exists():
    print(f"Error: SQLite database not found at {DB_PATH}")
    sys.exit(1)

import psycopg2
from psycopg2.extras import execute_values

# Connect to both databases
sqlite = sqlite3.connect(str(DB_PATH))
sqlite.row_factory = sqlite3.Row
pg = psycopg2.connect(DATABASE_URL)
pg.autocommit = False
cur = pg.cursor()

print("Connected to SQLite and PostgreSQL")

# Create schema on PG side
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
pg.commit()
print("Schema created / verified")

# Migrate products
sqlite_products = sqlite.execute("SELECT * FROM products ORDER BY id").fetchall()
print(f"Migrating {len(sqlite_products)} products...")

product_id_map = {}  # sqlite_id -> pg_id
products_migrated = 0

for row in sqlite_products:
    cur.execute("""
        INSERT INTO products (site, name, category, currency, url, first_seen, last_seen)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (site, name) DO UPDATE SET
            category = COALESCE(NULLIF(%s, ''), products.category),
            url = COALESCE(NULLIF(%s, ''), products.url),
            first_seen = LEAST(products.first_seen, %s),
            last_seen = GREATEST(products.last_seen, %s)
        RETURNING id
    """, (
        row['site'], row['name'], row['category'], row['currency'], row['url'],
        row['first_seen'], row['last_seen'],
        row['category'], row['url'], row['first_seen'], row['last_seen']
    ))
    pg_id = cur.fetchone()[0]
    product_id_map[row['id']] = pg_id
    products_migrated += 1

pg.commit()
print(f"  {products_migrated} products migrated")

# Migrate price history
sqlite_history = sqlite.execute("""
    SELECT ph.*, p.site, p.name
    FROM price_history ph
    JOIN products p ON p.id = ph.product_id
    ORDER BY ph.id
""").fetchall()
print(f"Migrating {len(sqlite_history)} price history records...")

history_migrated = 0
history_skipped = 0

for row in sqlite_history:
    pg_product_id = product_id_map.get(row['product_id'])
    if not pg_product_id:
        history_skipped += 1
        continue
    try:
        cur.execute("""
            INSERT INTO price_history (product_id, price, price_text, currency, scraped_at, source_url)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            pg_product_id, row['price'], row['price_text'],
            row.get('currency', 'USD'), row['scraped_at'], row.get('source_url', '')
        ))
        history_migrated += 1
    except Exception as e:
        history_skipped += 1

pg.commit()
print(f"  {history_migrated} records migrated, {history_skipped} skipped")

# Verify
cur.execute("SELECT COUNT(*) FROM products")
prod_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM price_history")
hist_count = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT site) FROM products")
site_count = cur.fetchone()[0]

print(f"\n✅ Migration complete!")
print(f"   PostgreSQL: {site_count} stores, {prod_count} products, {hist_count} price records")

cur.close()
pg.close()
sqlite.close()