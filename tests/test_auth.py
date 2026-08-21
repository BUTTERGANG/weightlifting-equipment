import hashlib
import time

import pytest

import dashboard
from conftest import PASSWORD


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
        dashboard.save_user('admin', PASSWORD)


def test_save_user_rejects_short_password():
    with pytest.raises(ValueError, match='at least'):
        dashboard.save_user('a@b.com', 'short')


def test_validate_password_enforces_minimum():
    assert dashboard.validate_password('') == 'Password required'
    assert 'at least' in dashboard.validate_password('a' * (dashboard.MIN_PASSWORD_LENGTH - 1))
    assert dashboard.validate_password('a' * dashboard.MIN_PASSWORD_LENGTH) is None


def test_save_and_check_auth_roundtrip():
    dashboard.save_user('a@b.com', PASSWORD)
    assert dashboard.check_auth('a@b.com', PASSWORD)
    assert not dashboard.check_auth('a@b.com', 'wrong-password-entirely')
    assert not dashboard.check_auth('nobody@b.com', PASSWORD)


def test_passwords_are_salted():
    dashboard.save_user('a@b.com', PASSWORD)
    dashboard.save_user('c@d.com', PASSWORD)
    users = dashboard.load_users()
    assert users['a@b.com'] != users['c@d.com']  # same password, different salt
    assert not dashboard._is_legacy_sha256(users['a@b.com'])


def test_legacy_sha256_hash_upgrades_on_login():
    legacy_hash = hashlib.sha256(PASSWORD.encode()).hexdigest()
    dashboard.AUTH_FILE.write_text(f'a@b.com:{legacy_hash}\n')

    assert dashboard.check_auth('a@b.com', PASSWORD)

    upgraded = dashboard.load_users()['a@b.com']
    assert not dashboard._is_legacy_sha256(upgraded)
    assert dashboard.check_auth('a@b.com', PASSWORD)  # still works post-upgrade


def test_legacy_short_password_still_logs_in():
    """A pre-policy password shorter than the minimum must not lock the user out."""
    legacy_hash = hashlib.sha256('old'.encode()).hexdigest()
    dashboard.AUTH_FILE.write_text(f'a@b.com:{legacy_hash}\n')
    assert dashboard.check_auth('a@b.com', 'old')


def test_reset_token_roundtrip():
    dashboard.save_user('a@b.com', PASSWORD)
    token = dashboard.create_reset_token('a@b.com')

    assert dashboard.peek_reset_token(token) == 'a@b.com'
    assert dashboard.consume_reset_token(token) == 'a@b.com'
    assert dashboard.consume_reset_token(token) is None  # single use


def test_reset_tokens_are_stored_hashed():
    """The token file must not contain anything usable as a reset link."""
    dashboard.save_user('a@b.com', PASSWORD)
    token = dashboard.create_reset_token('a@b.com')
    assert token not in dashboard.RESET_FILE.read_text()
    assert hashlib.sha256(token.encode()).hexdigest() in dashboard.RESET_FILE.read_text()


def test_reset_token_expires():
    dashboard.save_user('a@b.com', PASSWORD)
    token = dashboard.create_reset_token('a@b.com')

    tokens = dashboard.load_reset_tokens()
    tokens[dashboard._hash_token(token)]['expires'] = time.time() - 1
    dashboard.save_reset_tokens(tokens)

    assert dashboard.peek_reset_token(token) is None
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
