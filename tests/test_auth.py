import hashlib
import time

import pytest

import dashboard


@pytest.fixture(autouse=True)
def isolated_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'AUTH_FILE', tmp_path / 'auth')
    monkeypatch.setattr(dashboard, 'RESET_FILE', tmp_path / 'resets')
    dashboard._rate_limit_buckets.clear()
    yield


def test_is_valid_email():
    assert dashboard.is_valid_email('a@b.com')
    assert not dashboard.is_valid_email('not-an-email')
    assert not dashboard.is_valid_email('a@b')


def test_save_user_rejects_non_email():
    with pytest.raises(ValueError):
        dashboard.save_user('admin', 'pw')


def test_save_and_check_auth_roundtrip():
    dashboard.save_user('a@b.com', 'secret')
    assert dashboard.check_auth('a@b.com', 'secret')
    assert not dashboard.check_auth('a@b.com', 'wrong')
    assert not dashboard.check_auth('nobody@b.com', 'secret')


def test_passwords_are_salted():
    dashboard.save_user('a@b.com', 'secret')
    dashboard.save_user('c@d.com', 'secret')
    users = dashboard.load_users()
    assert users['a@b.com'] != users['c@d.com']  # same password, different salt
    assert not dashboard._is_legacy_sha256(users['a@b.com'])


def test_legacy_sha256_hash_upgrades_on_login():
    legacy_hash = hashlib.sha256('secret'.encode()).hexdigest()
    dashboard.AUTH_FILE.write_text(f'a@b.com:{legacy_hash}\n')

    assert dashboard.check_auth('a@b.com', 'secret')

    upgraded = dashboard.load_users()['a@b.com']
    assert not dashboard._is_legacy_sha256(upgraded)
    assert dashboard.check_auth('a@b.com', 'secret')  # still works post-upgrade


def test_reset_token_roundtrip():
    dashboard.save_user('a@b.com', 'old-pw')
    token = dashboard.create_reset_token('a@b.com')

    assert dashboard.consume_reset_token(token) == 'a@b.com'
    assert dashboard.consume_reset_token(token) is None  # single use


def test_reset_token_expires():
    dashboard.save_user('a@b.com', 'old-pw')
    token = dashboard.create_reset_token('a@b.com')

    tokens = dashboard.load_reset_tokens()
    tokens[token]['expires'] = time.time() - 1
    dashboard.save_reset_tokens(tokens)

    assert dashboard.consume_reset_token(token) is None


def test_rate_limited_allows_then_blocks():
    key = 'test-key'
    for _ in range(5):
        assert dashboard.rate_limited(key, max_attempts=5, window_seconds=60) is False
    assert dashboard.rate_limited(key, max_attempts=5, window_seconds=60) is True


def test_rate_limited_window_resets(monkeypatch):
    key = 'test-key-2'
    now = [1000.0]
    monkeypatch.setattr(dashboard.time, 'time', lambda: now[0])

    for _ in range(3):
        dashboard.rate_limited(key, max_attempts=3, window_seconds=10)
    assert dashboard.rate_limited(key, max_attempts=3, window_seconds=10) is True

    now[0] += 11  # past the window
    assert dashboard.rate_limited(key, max_attempts=3, window_seconds=10) is False
