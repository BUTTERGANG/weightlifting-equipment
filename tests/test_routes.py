import pytest

import dashboard
from conftest import PASSWORD

NEW_PASSWORD = 'a-brand-new-passphrase'


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'AUTH_FILE', tmp_path / 'auth')
    monkeypatch.setattr(dashboard, 'RESET_FILE', tmp_path / 'resets')
    dashboard._rate_limit_buckets.clear()
    dashboard.save_user('a@b.com', PASSWORD)
    yield


@pytest.fixture
def client():
    dashboard.app.config['TESTING'] = True
    return dashboard.app.test_client()


def csrf(client):
    """Prime the session with a CSRF token and return it."""
    client.get('/login')
    with client.session_transaction() as sess:
        return sess['_csrf']


def post(client, url, data):
    return client.post(url, data={**data, 'csrf_token': csrf(client)})


# ── Login ─────────────────────────────────────────────────────────────────

def test_login_success(client):
    r = post(client, '/login', {'username': 'a@b.com', 'password': PASSWORD})
    assert r.status_code == 200
    assert r.get_json() == {'ok': True}


def test_login_failure(client):
    r = post(client, '/login', {'username': 'a@b.com', 'password': 'wrong'})
    assert r.status_code == 401


def test_login_rate_limited_after_threshold(client):
    token = csrf(client)
    for _ in range(10):
        client.post('/login', data={'username': 'a@b.com', 'password': 'wrong',
                                    'csrf_token': token})
    r = client.post('/login', data={'username': 'a@b.com', 'password': 'wrong',
                                    'csrf_token': token})
    assert r.status_code == 429


def test_login_rejects_missing_csrf_token(client):
    client.get('/login')
    r = client.post('/login', data={'username': 'a@b.com', 'password': PASSWORD})
    assert r.status_code == 400
    assert 'CSRF' in r.get_json()['error']


def test_login_rejects_forged_csrf_token(client):
    csrf(client)
    r = client.post('/login', data={'username': 'a@b.com', 'password': PASSWORD,
                                    'csrf_token': 'not-the-real-token'})
    assert r.status_code == 400


def test_session_id_rotates_on_login(client):
    """A pre-login session value must not survive authentication."""
    csrf(client)
    with client.session_transaction() as sess:
        sess['planted'] = 'value'
    post(client, '/login', {'username': 'a@b.com', 'password': PASSWORD})
    with client.session_transaction() as sess:
        assert 'planted' not in sess
        assert sess['user'] == 'a@b.com'


def test_logout_clears_session(client):
    post(client, '/login', {'username': 'a@b.com', 'password': PASSWORD})
    client.get('/logout')
    with client.session_transaction() as sess:
        assert 'user' not in sess


# ── Authorization ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('path', ['/', '/api/products', '/api/meta', '/api/me',
                                  '/api/scrape/runs'])
def test_protected_routes_require_login(client, path):
    r = client.get(path)
    if path.startswith('/api/'):
        assert r.status_code == 401
    else:
        assert r.status_code == 302
        assert '/login' in r.headers['Location']


def test_healthz_is_public(client):
    assert client.get('/healthz').status_code in (200, 503)


# ── Password reset ────────────────────────────────────────────────────────

def test_forgot_password_generic_response_for_unknown_user(client):
    r = post(client, '/forgot-password', {'username': 'nobody@nowhere.com'})
    assert r.status_code == 200
    assert 'message' in r.get_json()


def test_forgot_password_sends_reset_email(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(dashboard, 'send_reset_email',
                        lambda to, url: sent.update(to=to, url=url))

    r = post(client, '/forgot-password', {'username': 'a@b.com'})

    assert r.status_code == 200
    assert sent['to'] == 'a@b.com'
    assert '/reset-password?token=' in sent['url']


def _request_reset(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(dashboard, 'send_reset_email',
                        lambda to, url: sent.update(to=to, url=url))
    post(client, '/forgot-password', {'username': 'a@b.com'})
    return sent['url'].split('token=')[1]


def test_full_reset_flow(client, monkeypatch):
    token = _request_reset(client, monkeypatch)

    assert client.get(f'/reset-password?token={token}').status_code == 200

    r = post(client, f'/reset-password?token={token}',
             {'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert r.status_code == 200
    assert r.get_json() == {'ok': True}
    assert dashboard.check_auth('a@b.com', NEW_PASSWORD)

    # token is single-use
    assert b'invalid or has expired' in client.get(f'/reset-password?token={token}').data


def test_reset_password_mismatched_confirmation(client, monkeypatch):
    token = _request_reset(client, monkeypatch)
    r = post(client, f'/reset-password?token={token}',
             {'password': NEW_PASSWORD, 'confirm': 'something-else-entirely'})
    assert r.status_code == 400


def test_reset_password_enforces_minimum_length(client, monkeypatch):
    token = _request_reset(client, monkeypatch)
    r = post(client, f'/reset-password?token={token}',
             {'password': 'short', 'confirm': 'short'})
    assert r.status_code == 400
    assert 'at least' in r.get_json()['error']


def test_rejected_password_does_not_burn_the_token(client, monkeypatch):
    """A too-short password must leave the reset link usable for a retry."""
    token = _request_reset(client, monkeypatch)
    post(client, f'/reset-password?token={token}', {'password': 'short', 'confirm': 'short'})

    r = post(client, f'/reset-password?token={token}',
             {'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert r.status_code == 200
    assert dashboard.check_auth('a@b.com', NEW_PASSWORD)


def test_reset_with_unknown_token_is_rejected(client):
    r = post(client, '/reset-password?token=made-up',
             {'password': NEW_PASSWORD, 'confirm': NEW_PASSWORD})
    assert r.status_code == 400


# ── Response hardening ────────────────────────────────────────────────────

def test_security_headers_present(client):
    r = client.get('/login')
    assert r.headers['X-Content-Type-Options'] == 'nosniff'
    assert r.headers['X-Frame-Options'] == 'DENY'
    assert "frame-ancestors 'none'" in r.headers['Content-Security-Policy']
    # No external script host may be whitelisted — Chart.js is vendored.
    assert 'cdn.jsdelivr.net' not in r.headers['Content-Security-Policy']


def test_session_cookie_is_hardened(client):
    r = post(client, '/login', {'username': 'a@b.com', 'password': PASSWORD})
    cookies = [c for c in r.headers.getlist('Set-Cookie') if c.startswith('session=')]
    assert cookies, 'login should set a session cookie'
    assert 'HttpOnly' in cookies[0]
    assert 'SameSite=Lax' in cookies[0]
    assert dashboard.app.config['PERMANENT_SESSION_LIFETIME'].days > 0


def test_404_returns_json_for_api_paths(client):
    r = client.get('/api/does-not-exist')
    assert r.status_code == 404
    assert r.get_json()['error'] == 'Not found'
