"""Tests for scrape-run bookkeeping and the API's response handling."""
from datetime import datetime, timedelta, timezone

import pytest

import dashboard
from conftest import PASSWORD


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, sqlite_db):
    monkeypatch.setattr(dashboard, 'AUTH_FILE', tmp_path / 'auth')
    monkeypatch.setattr(dashboard, 'RESET_FILE', tmp_path / 'resets')
    monkeypatch.setattr(dashboard, 'connect', lambda *a, **k: sqlite_db)
    monkeypatch.setattr(sqlite_db, 'close', lambda: None)
    dashboard._rate_limit_buckets.clear()
    with dashboard._products_cache_lock:
        dashboard._products_cache['body'] = None
    dashboard.save_user('a@b.com', PASSWORD)
    yield


@pytest.fixture
def client():
    dashboard.app.config['TESTING'] = True
    c = dashboard.app.test_client()
    c.get('/login')
    with c.session_transaction() as sess:
        token = sess['_csrf']
    c.post('/login', data={'username': 'a@b.com', 'password': PASSWORD,
                           'csrf_token': token})
    # Login rotates the session (and therefore the CSRF token) on purpose, so
    # re-read it the way a browser does when it reloads the page afterwards.
    with c.session_transaction() as sess:
        c.csrf = sess['_csrf']
    return c


def _insert_run(db, status='running', heartbeat_minutes_ago=0):
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=heartbeat_minutes_ago)
             ).strftime('%Y-%m-%d %H:%M:%S')
    db.execute("""INSERT INTO scrape_runs (status, trigger, started_at, heartbeat_at)
                  VALUES (?, 'manual', ?, ?)""", (status, stamp, stamp))
    db.commit()


# ── Stale-run reaping ─────────────────────────────────────────────────────

def test_stale_run_is_reaped(sqlite_db):
    """A scrape killed with its container leaves a row stuck in 'running'.
    /api/scrape then returns 409 forever, so the Sync button never recovers."""
    _insert_run(sqlite_db, heartbeat_minutes_ago=120)

    reaped = dashboard.reap_stale_runs(sqlite_db)

    assert reaped == 1
    row = sqlite_db.query_one('SELECT status, error, finished_at FROM scrape_runs')
    assert row['status'] == 'error'
    assert 'heartbeat' in row['error']
    assert row['finished_at'] is not None


def test_live_run_is_not_reaped(sqlite_db):
    _insert_run(sqlite_db, heartbeat_minutes_ago=1)
    assert dashboard.reap_stale_runs(sqlite_db) == 0
    assert sqlite_db.query_one('SELECT status FROM scrape_runs')['status'] == 'running'


def test_finished_run_is_left_alone(sqlite_db):
    _insert_run(sqlite_db, status='success', heartbeat_minutes_ago=500)
    assert dashboard.reap_stale_runs(sqlite_db) == 0
    assert sqlite_db.query_one('SELECT status FROM scrape_runs')['status'] == 'success'


def test_scrape_endpoint_refuses_while_a_live_run_exists(client, sqlite_db):
    _insert_run(sqlite_db, heartbeat_minutes_ago=0)
    r = client.post('/api/scrape', json={},
                    headers={'X-CSRF-Token': client.csrf})
    assert r.status_code == 409


def test_scrape_endpoint_recovers_after_a_dead_run(client, sqlite_db, monkeypatch):
    """The dead run should be reaped and a new scrape allowed to start."""
    _insert_run(sqlite_db, heartbeat_minutes_ago=120)
    launched = {}
    monkeypatch.setattr(dashboard, 'launch_scrape',
                        lambda **kw: launched.setdefault('run_id', 99) or 99)

    r = client.post('/api/scrape', json={}, headers={'X-CSRF-Token': client.csrf})

    assert r.status_code == 200
    assert launched['run_id'] == 99


# ── /api/products response handling ───────────────────────────────────────

def _seed_product(db, price=100.0, name='Ohio Bar'):
    db.execute("""INSERT INTO products (site, name, url, category, group_name, currency)
                  VALUES ('RepFit', ?, 'https://x.com/p/1', 'Barbells', 'Barbells', 'USD')""",
               (name,))
    db.commit()
    pid = db.query_one('SELECT id FROM products')['id']
    db.execute("""INSERT INTO price_history (product_id, price, scraped_at)
                  VALUES (?, ?, '2026-01-01T00:00:00Z')""", (pid, price))
    db.commit()
    return pid


def test_products_are_gzipped(client, sqlite_db):
    for i in range(200):      # exceed the 1 KB floor
        sqlite_db.execute(
            """INSERT INTO products (site, name, url, group_name)
               VALUES ('RepFit', ?, ?, 'Barbells')""",
            (f'Bar number {i} with a reasonably long name', f'https://x.com/p/{i}'))
    sqlite_db.commit()

    r = client.get('/api/products', headers={'Accept-Encoding': 'gzip'})

    assert r.status_code == 200
    assert r.headers['Content-Encoding'] == 'gzip'
    assert 'Accept-Encoding' in r.headers.get('Vary', '')


def test_products_etag_returns_304(client, sqlite_db):
    _seed_product(sqlite_db)
    first = client.get('/api/products')
    etag = first.headers['ETag']

    second = client.get('/api/products', headers={'If-None-Match': etag})

    assert second.status_code == 304


def test_deal_needs_more_than_one_observation(client, sqlite_db):
    """With a single data point the average IS the current price, so a naive
    calculation reports a deal on a product that has never changed price."""
    pid = _seed_product(sqlite_db, price=100.0)
    body = client.get('/api/products').get_json()
    assert body[0]['deal_pct'] == 0

    # A genuine drop, with history behind it.
    with dashboard._products_cache_lock:
        dashboard._products_cache['body'] = None
    sqlite_db.execute("""INSERT INTO price_history (product_id, price, scraped_at)
                         VALUES (?, 50.0, '2026-01-02T00:00:00Z')""", (pid,))
    sqlite_db.commit()

    body = client.get('/api/products').get_json()
    assert body[0]['deal_pct'] > 0


def test_meta_reports_canonical_groups(client, sqlite_db):
    _seed_product(sqlite_db)
    meta = client.get('/api/meta').get_json()
    groups = {g['name']: g for g in meta['groups']}
    assert 'Barbells' in groups
    assert groups['Barbells']['count'] == 1
    assert groups['Barbells']['icon']
