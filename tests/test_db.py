"""Tests for the shared database layer and its migrations."""
import sqlite3

import pytest

import db as db_module
from db import Database, to_pg_placeholders, init_schema


# ── Placeholder translation ───────────────────────────────────────────────

def test_placeholders_translated_for_postgres():
    assert to_pg_placeholders('SELECT * FROM t WHERE a = ? AND b = ?') == \
        'SELECT * FROM t WHERE a = %s AND b = %s'


def test_question_marks_inside_string_literals_are_left_alone():
    sql = "SELECT * FROM t WHERE name = 'what?' AND id = ?"
    assert to_pg_placeholders(sql) == "SELECT * FROM t WHERE name = 'what?' AND id = %s"


def test_literal_percent_is_escaped_for_psycopg2():
    sql = "SELECT * FROM t WHERE name LIKE '%bar%' AND id = ?"
    out = to_pg_placeholders(sql)
    assert "'%%bar%%'" in out
    assert out.endswith('= %s')


# ── Dialect helpers ───────────────────────────────────────────────────────

def test_round_helper_casts_for_postgres():
    """ROUND(double precision, int) does not exist in Postgres — the cast is
    what stopped product matching from reporting its stats."""
    pg = Database(None, 'postgres')
    assert pg.round('AVG(price)', 1) == 'ROUND((AVG(price))::numeric, 1)::float'

    lite = Database(None, 'sqlite')
    assert lite.round('AVG(price)', 1) == 'ROUND(AVG(price), 1)'


def test_round_helper_runs_on_sqlite(sqlite_db):
    sqlite_db.execute("INSERT INTO products (site, name, url) VALUES ('s', 'n', 'u')")
    pid = sqlite_db.query_one('SELECT id FROM products')['id']
    for price in (10.0, 20.0):
        sqlite_db.execute(
            'INSERT INTO price_history (product_id, price, scraped_at) VALUES (?, ?, ?)',
            (pid, price, '2026-01-01T00:00:00Z'))
    row = sqlite_db.query_one(
        f'SELECT {sqlite_db.round("AVG(price)", 1)} AS avg FROM price_history')
    assert row['avg'] == 15.0


# ── Schema ────────────────────────────────────────────────────────────────

def test_schema_is_idempotent(sqlite_db):
    init_schema(sqlite_db)
    init_schema(sqlite_db)
    tables = {r['name'] for r in sqlite_db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'products', 'price_history', 'scrape_runs', 'product_matches'} <= tables


def test_site_url_uniqueness_is_enforced(sqlite_db):
    sqlite_db.execute("INSERT INTO products (site, name, url) VALUES ('s', 'a', 'u')")
    sqlite_db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_db.execute("INSERT INTO products (site, name, url) VALUES ('s', 'b', 'u')")
        sqlite_db.commit()


def test_same_name_different_url_is_allowed(sqlite_db):
    sqlite_db.execute("INSERT INTO products (site, name, url) VALUES ('s', 'Laces', 'u1')")
    sqlite_db.execute("INSERT INTO products (site, name, url) VALUES ('s', 'Laces', 'u2')")
    sqlite_db.commit()
    assert sqlite_db.query_one('SELECT COUNT(*) c FROM products')['c'] == 2


# ── Migration ─────────────────────────────────────────────────────────────

LEGACY_SCHEMA = """
    CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site TEXT NOT NULL, name TEXT NOT NULL, category TEXT,
        currency TEXT, url TEXT, image_url TEXT,
        first_seen TEXT, last_seen TEXT,
        UNIQUE(site, name)
    );
    CREATE TABLE price_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        price REAL, price_text TEXT, currency TEXT,
        scraped_at TEXT NOT NULL, source_url TEXT
    );
"""


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A database in the pre-(site, url) shape, before migration."""
    path = tmp_path / 'legacy.db'
    raw = sqlite3.connect(str(path))
    raw.executescript(LEGACY_SCHEMA)
    raw.commit()
    raw.close()
    monkeypatch.setattr(db_module, 'DB_PATH', path)
    conn = db_module.connect()
    yield conn
    conn.close()


def _seed(conn, rows):
    for site, name, url in rows:
        conn.execute('INSERT INTO products (site, name, url) VALUES (?, ?, ?)',
                     (site, name, url))
    conn.commit()
    for r in conn.query('SELECT id FROM products'):
        conn.execute('INSERT INTO price_history (product_id, price, scraped_at) '
                     'VALUES (?, ?, ?)', (r['id'], 100.0, '2026-01-01T00:00:00Z'))
    conn.commit()


def test_migration_merges_duplicate_urls_and_keeps_history(legacy_db):
    """Legacy databases hold several product rows per URL (a rename made a
    second row). Merging must preserve every price record."""
    _seed(legacy_db, [
        ('RepFit', 'Open Trap Bar', 'https://x.com/p/trap'),
        ('RepFit', 'Open Trap Bar v2', 'https://x.com/p/trap'),
    ])
    ids = [r['id'] for r in legacy_db.query('SELECT id FROM products ORDER BY id')]

    init_schema(legacy_db)

    assert legacy_db.query_one('SELECT COUNT(*) c FROM products')['c'] == 1
    assert legacy_db.query_one('SELECT COUNT(*) c FROM price_history')['c'] == 2
    survivor = legacy_db.query_one('SELECT id FROM products')['id']
    assert survivor == min(ids)
    owners = {r['product_id'] for r in legacy_db.query(
        'SELECT DISTINCT product_id FROM price_history')}
    assert owners == {survivor}


def test_migration_normalises_empty_urls_to_null(legacy_db):
    _seed(legacy_db, [('s', 'a', ''), ('s', 'b', '')])

    init_schema(legacy_db)

    # Both survive: NULL urls are distinct, so unrelated legacy rows aren't merged.
    assert legacy_db.query_one('SELECT COUNT(*) c FROM products')['c'] == 2
    assert legacy_db.query_one(
        'SELECT COUNT(*) c FROM products WHERE url IS NULL')['c'] == 2


def test_migration_drops_legacy_unique_site_name(legacy_db):
    """After migrating, two colourways sharing a display name can coexist."""
    _seed(legacy_db, [('s', 'Laces', 'u1')])

    init_schema(legacy_db)

    legacy_db.execute("INSERT INTO products (site, name, url) VALUES ('s', 'Laces', 'u2')")
    legacy_db.commit()
    assert legacy_db.query_one('SELECT COUNT(*) c FROM products')['c'] == 2


def test_migration_tolerates_null_timestamps(legacy_db):
    """Legacy rows can have NULL first_seen/last_seen; the rebuild must not
    fail the NOT NULL constraint on the new table."""
    _seed(legacy_db, [('s', 'a', 'u1')])
    assert legacy_db.query_one('SELECT first_seen FROM products')['first_seen'] is None

    init_schema(legacy_db)

    assert legacy_db.query_one('SELECT first_seen FROM products')['first_seen'] is not None


def test_migration_is_idempotent(legacy_db):
    _seed(legacy_db, [('s', 'a', 'u1'), ('s', 'b', 'u1')])
    init_schema(legacy_db)
    init_schema(legacy_db)
    assert legacy_db.query_one('SELECT COUNT(*) c FROM products')['c'] == 1
