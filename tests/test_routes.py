import pytest

import dashboard


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'AUTH_FILE', tmp_path / 'auth')
    monkeypatch.setattr(dashboard, 'RESET_FILE', tmp_path / 'resets')
    dashboard._rate_limit_buckets.clear()
    dashboard.save_user('a@b.com', 'secret')
    yield


@pytest.fixture
def client():
    dashboard.app.config['TESTING'] = True
    return dashboard.app.test_client()


def test_login_success(client):
    r = client.post('/login', data={'username': 'a@b.com', 'password': 'secret'})
    assert r.status_code == 200
    assert r.get_json() == {'ok': True}


def test_login_failure(client):
    r = client.post('/login', data={'username': 'a@b.com', 'password': 'wrong'})
    assert r.status_code == 401


def test_login_rate_limited_after_threshold(client):
    for _ in range(10):
        client.post('/login', data={'username': 'a@b.com', 'password': 'wrong'})
    r = client.post('/login', data={'username': 'a@b.com', 'password': 'wrong'})
    assert r.status_code == 429


def test_forgot_password_generic_response_for_unknown_user(client):
    r = client.post('/forgot-password', data={'username': 'nobody@nowhere.com'})
    assert r.status_code == 200
    assert 'message' in r.get_json()


def test_forgot_password_sends_reset_email(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(dashboard, 'send_reset_email',
                         lambda to, url: sent.update(to=to, url=url))

    r = client.post('/forgot-password', data={'username': 'a@b.com'})

    assert r.status_code == 200
    assert sent['to'] == 'a@b.com'
    assert '/reset-password?token=' in sent['url']


def test_full_reset_flow(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(dashboard, 'send_reset_email',
                         lambda to, url: sent.update(to=to, url=url))
    client.post('/forgot-password', data={'username': 'a@b.com'})
    token = sent['url'].split('token=')[1]

    r = client.get(f'/reset-password?token={token}')
    assert r.status_code == 200

    r = client.post(f'/reset-password?token={token}',
                     data={'password': 'newpw123', 'confirm': 'newpw123'})
    assert r.status_code == 200
    assert r.get_json() == {'ok': True}
    assert dashboard.check_auth('a@b.com', 'newpw123')

    # token is single-use
    r = client.get(f'/reset-password?token={token}')
    assert b'invalid or has expired' in r.data


def test_reset_password_mismatched_confirmation(client, monkeypatch):
    sent = {}
    monkeypatch.setattr(dashboard, 'send_reset_email',
                         lambda to, url: sent.update(to=to, url=url))
    client.post('/forgot-password', data={'username': 'a@b.com'})
    token = sent['url'].split('token=')[1]

    r = client.post(f'/reset-password?token={token}',
                     data={'password': 'a', 'confirm': 'b'})
    assert r.status_code == 400
