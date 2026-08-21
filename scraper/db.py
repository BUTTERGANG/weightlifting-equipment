#!/usr/bin/env python3
"""
Single database abstraction shared by the scraper, the matcher and the dashboard.

Before this module every query was written twice — once for PostgreSQL and once
for SQLite — which doubled the surface area for dialect bugs (and produced at
least one: ``ROUND(AVG(x), 1)`` is valid SQLite but raises
``function round(double precision, integer) does not exist`` on Postgres).

Callers now write each query exactly once using:
  * ``?`` placeholders (translated to ``%s`` for psycopg2), and
  * ``db.round(expr, places)`` / ``db.now()`` for the handful of expressions
    that genuinely differ between backends.
"""
import os
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get('EQUIPMENT_DB_PATH',
                              Path.home() / 'equipment_data' / 'equipment.db'))


def database_url():
    """Read DATABASE_URL at call time so tests can monkeypatch the environment."""
    return os.environ.get('DATABASE_URL', '')


# ── Placeholder translation ───────────────────────────────────────────────
# psycopg2 wants %s; sqlite3 wants ?. We standardise on ? and rewrite for
# Postgres, taking care not to touch ? inside string literals, and escaping
# any literal % so psycopg2's own interpolation doesn't choke on it.

_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")


def to_pg_placeholders(sql):
    out, last = [], 0
    for m in _LITERAL_RE.finditer(sql):
        out.append(sql[last:m.start()].replace('%', '%%').replace('?', '%s'))
        out.append(m.group(0).replace('%', '%%'))
        last = m.end()
    out.append(sql[last:].replace('%', '%%').replace('?', '%s'))
    return ''.join(out)


class Database:
    """A connection plus the dialect-specific bits, with dict-returning queries."""

    def __init__(self, conn, dialect):
        self.conn = conn
        self.dialect = dialect

    # -- dialect helpers ---------------------------------------------------
    @property
    def is_postgres(self):
        return self.dialect == 'postgres'

    def round(self, expr, places=2):
        """ROUND() that works on a float column in both backends."""
        if self.is_postgres:
            return f'ROUND(({expr})::numeric, {places})::float'
        return f'ROUND({expr}, {places})'

    def now(self):
        return 'NOW()' if self.is_postgres else "datetime('now')"

    def prepare(self, sql):
        return to_pg_placeholders(sql) if self.is_postgres else sql

    # -- execution ---------------------------------------------------------
    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(self.prepare(sql), params)
        return cur

    def executemany(self, sql, seq):
        cur = self.conn.cursor()
        cur.executemany(self.prepare(sql), seq)
        return cur

    def executescript(self, sql):
        """Run a multi-statement DDL script."""
        if self.is_postgres:
            self.conn.cursor().execute(sql)
        else:
            self.conn.executescript(sql)

    def query(self, sql, params=()):
        cur = self.execute(sql, params)
        rows = cur.fetchall()
        if self.is_postgres:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        return [dict(r) for r in rows]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
        return False


def connect(url=None):
    """Open a Database. Uses PostgreSQL when DATABASE_URL is set, else SQLite."""
    url = database_url() if url is None else url
    if url:
        import psycopg2
        conn = psycopg2.connect(url)
        conn.autocommit = False
        return Database(conn, 'postgres')
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return Database(conn, 'sqlite')


# ── Schema ────────────────────────────────────────────────────────────────
# Products are keyed on (site, url), NOT (site, name).
#
# Retailers reuse one display name across genuinely distinct products — NoBull
# lists 28 different colourways of "NOBULL Laces" at two different prices. Under
# the old UNIQUE(site, name) those all collapsed onto one row, and every scrape
# wrote 28 price_history rows against it with alternating prices, which made
# avg/min/max — and therefore deal detection and the trend chart — meaningless.
# The product URL is the retailer's own identity for the item, so we key on that.

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    site        TEXT NOT NULL,
    name        TEXT NOT NULL,
    url         TEXT,
    category    TEXT,
    group_name  TEXT,
    currency    TEXT DEFAULT 'USD',
    image_url   TEXT,
    first_seen  TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS price_history (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price       REAL,
    price_text  TEXT,
    currency    TEXT DEFAULT 'USD',
    scraped_at  TIMESTAMP NOT NULL,
    source_url  TEXT
);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                SERIAL PRIMARY KEY,
    started_at        TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at       TIMESTAMP,
    status            TEXT NOT NULL DEFAULT 'running',
    trigger           TEXT NOT NULL DEFAULT 'manual',
    http_only         BOOLEAN NOT NULL DEFAULT FALSE,
    products_scraped  INTEGER,
    error             TEXT,
    heartbeat_at      TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_matches (
    product_id         INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    matched_product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    similarity         REAL NOT NULL,
    updated_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (product_id, matched_product_id)
);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site        TEXT NOT NULL,
    name        TEXT NOT NULL,
    url         TEXT,
    category    TEXT,
    group_name  TEXT,
    currency    TEXT DEFAULT 'USD',
    image_url   TEXT,
    first_seen  TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price       REAL,
    price_text  TEXT,
    currency    TEXT DEFAULT 'USD',
    scraped_at  TEXT NOT NULL,
    source_url  TEXT
);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at        TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at       TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    trigger           TEXT NOT NULL DEFAULT 'manual',
    http_only         INTEGER NOT NULL DEFAULT 0,
    products_scraped  INTEGER,
    error             TEXT,
    heartbeat_at      TEXT
);
CREATE TABLE IF NOT EXISTS product_matches (
    product_id         INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    matched_product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    similarity         REAL NOT NULL,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (product_id, matched_product_id)
);
"""

_INDEXES = [
    'CREATE UNIQUE INDEX IF NOT EXISTS idx_products_identity ON products(site, url)',
    'CREATE INDEX IF NOT EXISTS idx_products_site ON products(site, category)',
    'CREATE INDEX IF NOT EXISTS idx_products_group ON products(group_name)',
    'CREATE INDEX IF NOT EXISTS idx_history_product ON price_history(product_id, scraped_at DESC)',
    'CREATE INDEX IF NOT EXISTS idx_history_scraped ON price_history(scraped_at DESC)',
    'CREATE INDEX IF NOT EXISTS idx_scrape_runs_started ON scrape_runs(started_at DESC)',
    'CREATE INDEX IF NOT EXISTS idx_matches_product ON product_matches(product_id, similarity DESC)',
]


def init_schema(db):
    """Create the schema and bring an older database up to date. Idempotent."""
    db.executescript(_PG_SCHEMA if db.is_postgres else _SQLITE_SCHEMA)
    _migrate(db)
    merge_duplicate_products(db)
    for stmt in _INDEXES:
        try:
            db.execute(stmt)
            db.commit()
        except Exception as e:
            print(f'  [schema] {stmt.split(" ON ")[0].strip()}: {e}')
            db.rollback()
    db.commit()


def merge_duplicate_products(db):
    """Collapse rows that describe the same product under the old name-based key.

    Databases created before (site, url) became the identity can hold several
    product rows pointing at one retailer URL — a rename produced a second row,
    and price history was then split across both. Keep the lowest id, repoint its
    history, and drop the rest. Runs before the unique index is created.
    """
    if not _table_columns(db, 'products'):
        return
    db.execute("""UPDATE products SET url = NULL WHERE url = ''""")
    db.commit()

    dupes = db.query("""
        SELECT site, url, COUNT(*) AS n, MIN(id) AS keep_id
        FROM products
        WHERE url IS NOT NULL
        GROUP BY site, url
        HAVING COUNT(*) > 1
    """)
    if not dupes:
        return

    merged = 0
    for row in dupes:
        losers = [r['id'] for r in db.query(
            'SELECT id FROM products WHERE site = ? AND url = ? AND id <> ?',
            (row['site'], row['url'], row['keep_id']))]
        if not losers:
            continue
        marks = ','.join('?' for _ in losers)
        db.execute(f'UPDATE price_history SET product_id = ? WHERE product_id IN ({marks})',
                   [row['keep_id']] + losers)
        # Matches are recomputed from scratch each run; just drop the stale rows.
        db.execute(f'DELETE FROM product_matches WHERE product_id IN ({marks}) '
                   f'OR matched_product_id IN ({marks})', losers + losers)
        db.execute(f'DELETE FROM products WHERE id IN ({marks})', losers)
        merged += len(losers)
    db.commit()
    print(f'  [schema] merged {merged} duplicate product rows into '
          f'{len(dupes)} canonical products')


def _table_columns(db, table):
    if db.is_postgres:
        rows = db.query(
            'SELECT column_name FROM information_schema.columns WHERE table_name = ?',
            (table,))
        return {r['column_name'] for r in rows}
    return {r['name'] for r in db.query(f'PRAGMA table_info({table})')}


def _migrate(db):
    """Retrofit columns and constraints onto databases created by earlier versions."""
    cols = _table_columns(db, 'products')
    if not cols:
        return

    for table, column, coltype in (('products', 'image_url', 'TEXT'),
                                   ('products', 'url', 'TEXT'),
                                   ('products', 'group_name', 'TEXT'),
                                   ('scrape_runs', 'heartbeat_at',
                                    'TIMESTAMP' if db.is_postgres else 'TEXT')):
        existing = cols if table == 'products' else _table_columns(db, table)
        if existing and column not in existing:
            try:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {coltype}')
                db.commit()
            except Exception:
                db.rollback()

    # Drop the old UNIQUE(site, name) constraint — it is the variant-collision bug.
    if not db.is_postgres:
        ddl = db.query_one(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'products'")
        if ddl and 'UNIQUE' in (ddl['sql'] or '').upper():
            # SQLite cannot ALTER away a table-level constraint; rebuild the table.
            print('  [schema] rebuilding products to drop legacy UNIQUE(site, name)')
            db.execute('PRAGMA foreign_keys=OFF')
            db.executescript(_SQLITE_SCHEMA.replace('products', 'products_new', 1))
            db.execute("""
                INSERT INTO products_new (id, site, name, url, category, currency,
                                          image_url, first_seen, last_seen)
                SELECT id, site, name, url, category, currency, image_url,
                       COALESCE(first_seen, datetime('now')),
                       COALESCE(last_seen, datetime('now'))
                FROM products
            """)
            db.execute('DROP TABLE products')
            db.execute('ALTER TABLE products_new RENAME TO products')
            db.commit()
            db.execute('PRAGMA foreign_keys=ON')
        return

    if db.is_postgres:
        for row in db.query("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'products'::regclass AND contype = 'u'
        """):
            name = row['conname']
            cols_in = db.query("""
                SELECT a.attname FROM pg_constraint c
                JOIN unnest(c.conkey) k(attnum) ON TRUE
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
                WHERE c.conname = ?
            """, (name,))
            names = {r['attname'] for r in cols_in}
            if names == {'site', 'name'}:
                try:
                    db.execute(f'ALTER TABLE products DROP CONSTRAINT "{name}"')
                    db.commit()
                    print(f'  [schema] dropped legacy UNIQUE(site, name) constraint "{name}"')
                except Exception:
                    db.rollback()
