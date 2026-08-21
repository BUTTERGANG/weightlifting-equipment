#!/home/alex/.hermes/venv/bin/python3
"""
Weightlifting Equipment Price Dashboard — LiftTracker.

Tracks barbell, plate, rack, belt, apparel & shoe prices across 27+ retailers.
Supports SQLite (local dev) and PostgreSQL / Neon (Replit production).

Usage:
    python dashboard.py                          # Local SQLite, port 8080
    DATABASE_URL=postgres://... python dashboard.py  # Neon PostgreSQL
    python dashboard.py --port 5000
    python dashboard.py --host 0.0.0.0           # Network-accessible
    python dashboard.py --setup-auth              # Add dashboard users
"""
import os
import re
import sys
import json
import time
import secrets
import sqlite3
import hashlib
import argparse
import threading
from pathlib import Path
from datetime import datetime
from functools import wraps
from collections import defaultdict
from flask import Flask, g, request, jsonify, render_template_string, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

# ── Config ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / 'equipment_data' / 'equipment.db'
DATABASE_URL = os.environ.get('DATABASE_URL', '')
AUTH_FILE = Path.home() / '.equipment_dashboard_auth'
RESET_FILE = Path.home() / '.equipment_dashboard_resets'
RESET_TOKEN_TTL = 30 * 60  # seconds

AGENTMAIL_API_KEY = os.environ.get('AGENTMAIL_API_KEY', '')
AGENTMAIL_INBOX_ID = os.environ.get('AGENTMAIL_INBOX_ID', '')

app = Flask(__name__)
app.secret_key = os.environ.get('DASHBOARD_SECRET') or hashlib.sha256(
    (AUTH_FILE.read_text() if AUTH_FILE.exists() else 'liftracker').encode()
).hexdigest()


# ── Database abstraction ──────────────────────────────────────────────────
# Uses SQLite when DATABASE_URL is unset, PostgreSQL when set.
# All queries are SELECT-only (read from the dashboard).

def _dict_from_row(row, description):
    """Convert a DB row + cursor description to a dict."""
    return {description[i][0]: row[i] for i in range(len(description))}


def get_db():
    """Get the database connection for this request."""
    if 'db' not in g:
        if DATABASE_URL:
            import psycopg2
            conn = psycopg2.connect(DATABASE_URL)
            conn.autocommit = False
            g.db = conn
            g.db_type = 'postgres'
        else:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            g.db = conn
            g.db_type = 'sqlite'
    return g.db


def get_db_type():
    if 'db_type' not in g:
        get_db()
    return g.db_type


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db:
        db.close()


def fetch_dict(sql, params=()):
    """Execute query, return list of dicts."""
    db = get_db()
    cur = db.cursor()
    cur.execute(sql.replace('%', '%%') if get_db_type() == 'sqlite' and '%' in sql and '%s' not in sql else sql,
                params)
    rows = cur.fetchall()
    if get_db_type() == 'postgres':
        desc = cur.description
        return [_dict_from_row(r, desc) for r in rows]
    return [dict(r) for r in rows]


def fetch_one(sql, params=()):
    """Execute query, return single dict or None."""
    rows = fetch_dict(sql, params)
    return rows[0] if rows else None


# ── Auth ──────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def is_valid_email(username):
    return bool(EMAIL_RE.match(username))


def load_users():
    users = {}
    if AUTH_FILE.exists():
        for line in AUTH_FILE.read_text().strip().splitlines():
            line = line.strip()
            if ':' in line and not line.startswith('#'):
                u, pw = line.split(':', 1)
                users[u] = pw
    return users


def save_user(username, password):
    if not is_valid_email(username):
        raise ValueError(f"Username must be a valid email address: {username!r}")
    pw_hash = generate_password_hash(password)
    users = load_users()
    users[username] = pw_hash
    AUTH_FILE.write_text(''.join(f"{u}:{h}\n" for u, h in users.items()))
    AUTH_FILE.chmod(0o600)


def _is_legacy_sha256(pw_hash):
    return len(pw_hash) == 64 and all(c in '0123456789abcdef' for c in pw_hash)


def check_auth(username, password):
    users = load_users()
    if username not in users:
        return False
    stored = users[username]
    if _is_legacy_sha256(stored):
        if hashlib.sha256(password.encode()).hexdigest() != stored:
            return False
        save_user(username, password)  # upgrade to salted hash on successful login
        return True
    return check_password_hash(stored, password)


# ── Password Reset ───────────────────────────────────────────────────────

_agentmail_client = None
_agentmail_inbox_id = AGENTMAIL_INBOX_ID


def get_agentmail_client():
    """Lazily build the AgentMail client. Returns None if no API key is configured."""
    global _agentmail_client
    if _agentmail_client is None and AGENTMAIL_API_KEY:
        from agentmail import AgentMail
        _agentmail_client = AgentMail(api_key=AGENTMAIL_API_KEY)
    return _agentmail_client


def get_agentmail_inbox_id():
    """Return the inbox to send from, creating one on first use (idempotent via client_id)."""
    global _agentmail_inbox_id
    if _agentmail_inbox_id:
        return _agentmail_inbox_id
    client = get_agentmail_client()
    if client is None:
        return None
    inbox = client.inboxes.create(client_id='lifttracker-dashboard')
    _agentmail_inbox_id = inbox.inbox_id
    return _agentmail_inbox_id


def load_reset_tokens():
    if not RESET_FILE.exists():
        return {}
    try:
        return json.loads(RESET_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        return {}


def save_reset_tokens(tokens):
    RESET_FILE.write_text(json.dumps(tokens))
    RESET_FILE.chmod(0o600)


def create_reset_token(username):
    now = time.time()
    tokens = {t: v for t, v in load_reset_tokens().items() if v['expires'] > now}
    token = secrets.token_urlsafe(32)
    tokens[token] = {'username': username, 'expires': now + RESET_TOKEN_TTL}
    save_reset_tokens(tokens)
    return token


def consume_reset_token(token):
    """Validate and burn a reset token, returning the username or None if invalid/expired."""
    tokens = load_reset_tokens()
    entry = tokens.pop(token, None)
    save_reset_tokens(tokens)
    if not entry or entry['expires'] < time.time():
        return None
    return entry['username']


def send_reset_email(to_email, reset_url):
    client = get_agentmail_client()
    inbox_id = get_agentmail_inbox_id()
    if not client or not inbox_id:
        print(f"[password reset] AGENTMAIL_API_KEY not configured — reset link for {to_email}: {reset_url}")
        return
    client.inboxes.messages.send(
        inbox_id,
        to=to_email,
        subject="Reset your LiftTracker password",
        text=(f"Click the link below to reset your LiftTracker password. "
              f"This link expires in 30 minutes.\n\n{reset_url}\n\n"
              f"If you didn't request this, you can ignore this email."),
        html=(f'<p>Click the link below to reset your LiftTracker password. '
              f'This link expires in 30 minutes.</p>'
              f'<p><a href="{reset_url}">{reset_url}</a></p>'
              f'<p>If you didn\'t request this, you can ignore this email.</p>'),
    )


def require_login(f):
    """Redirect unauthenticated users to the login page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def setup_auth():
    if AUTH_FILE.exists() and load_users():
        print(f"Auth file exists at {AUTH_FILE}")
        yn = input("Add another user? (y/N): ")
        if yn.lower() != 'y':
            return
    print("Create a dashboard user")
    username = input("Email: ").strip()
    if not username:
        print("Email required")
        return
    if not is_valid_email(username):
        print("Username must be a valid email address")
        return
    password = input("Password: ").strip()
    if not password:
        print("Password required")
        return
    confirm = input("Confirm password: ").strip()
    if password != confirm:
        print("Passwords don't match")
        return
    save_user(username, password)
    print(f"User '{username}' created")
    print(f"Auth file: {AUTH_FILE}")


# ── Rate Limiting ────────────────────────────────────────────────────────
# In-process, per-worker sliding-window limiter. Good enough to blunt brute-force
# and reset-email-spam at this traffic level; not shared across gunicorn workers.

_rate_limit_lock = threading.Lock()
_rate_limit_buckets = defaultdict(list)


def rate_limited(key, max_attempts, window_seconds):
    """Record an attempt for `key` and return True if it has exceeded max_attempts
    within the trailing window_seconds."""
    now = time.time()
    with _rate_limit_lock:
        attempts = [t for t in _rate_limit_buckets[key] if t > now - window_seconds]
        attempts.append(now)
        _rate_limit_buckets[key] = attempts
        return len(attempts) > max_attempts


def client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()


# ── Login Page & Routes ─────────────────────────────────────────────────────

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiftTracker — Login</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-base: #090b10;
  --bg-surface: #11141d;
  --bg-card: #161b26;
  --border: #232938;
  --border-focus: #38bdf8;
  --text-main: #f1f5f9;
  --text-muted: #8e9db4;
  --accent-cyan: #38bdf8;
  --accent-green: #10b981;
  --accent-orange: #f97316;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  background: radial-gradient(circle at 50% 20%, #151b2a 0%, var(--bg-base) 80%);
  color: var(--text-main);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.login-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 40px 36px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.7), 0 0 40px rgba(56, 189, 248, 0.05);
  position: relative;
  overflow: hidden;
}
.login-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #f97316, #38bdf8, #10b981);
}
.brand {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 8px;
}
.brand-icon {
  width: 44px;
  height: 44px;
  background: linear-gradient(135deg, #1e293b, #0f172a);
  border: 1px solid var(--border);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.brand-name {
  font-size: 24px;
  font-weight: 800;
  letter-spacing: -0.5px;
  background: linear-gradient(135deg, #ffffff 40%, var(--text-muted) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.brand-name span {
  color: var(--accent-cyan);
  -webkit-text-fill-color: var(--accent-cyan);
}
.subtitle {
  text-align: center;
  color: var(--text-muted);
  font-size: 13.5px;
  margin-bottom: 28px;
}
.error-box {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  padding: 12px 14px;
  border-radius: 10px;
  font-size: 13px;
  margin-bottom: 20px;
  display: none;
  font-weight: 500;
}
.field {
  margin-bottom: 18px;
}
label {
  display: block;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
input {
  width: 100%;
  padding: 13px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 14.5px;
  color: var(--text-main);
  outline: none;
  transition: all 0.2s ease;
  font-family: inherit;
}
input:focus {
  border-color: var(--accent-cyan);
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15);
  background: #19202e;
}
button {
  width: 100%;
  padding: 14px;
  margin-top: 10px;
  background: linear-gradient(135deg, #0284c7, #0369a1);
  color: #fff;
  border: none;
  border-radius: 10px;
  font-size: 14.5px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(2, 132, 199, 0.35);
  transition: all 0.2s ease;
}
button:hover {
  background: linear-gradient(135deg, #38bdf8, #0284c7);
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(56, 189, 248, 0.4);
}
.footer-text {
  text-align: center;
  margin-top: 24px;
  font-size: 12.5px;
  color: var(--text-muted);
}
.footer-text a {
  color: var(--accent-cyan);
  text-decoration: none;
  font-weight: 600;
}
.footer-text a:hover {
  text-decoration: underline;
}
</style>
</head>
<body>
<div class="login-card">
  <div class="brand">
    <div class="brand-icon">⚡</div>
    <div class="brand-name">LIFT<span>TRACKER</span></div>
  </div>
  <div class="subtitle">Real-time equipment intelligence & deal radar</div>
  <div class="error-box" id="errorMsg"></div>
  <form id="loginForm" onsubmit="return handleLogin(event)">
    <div class="field">
      <label for="username">Email</label>
      <input type="email" id="username" name="username" autocomplete="username" placeholder="alex@buttergang.dev" required autofocus>
    </div>
    <div class="field">
      <label for="password">Password</label>
      <input type="password" id="password" name="password" autocomplete="current-password" placeholder="••••••••" required>
    </div>
    <button type="submit">Sign In →</button>
  </form>
  <div class="footer-text"><a href="/forgot-password">Forgot your password?</a></div>
</div>
<script>
async function handleLogin(e) {
  e.preventDefault();
  const err = document.getElementById('errorMsg');
  const u = document.getElementById('username').value.trim();
  const p = document.getElementById('password').value;
  if (!u || !p) {
    err.textContent = 'Please enter email and password';
    err.style.display = 'block';
    return false;
  }
  try {
    const r = await fetch('/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'username=' + encodeURIComponent(u) + '&password=' + encodeURIComponent(p)
    });
    if (r.ok) {
      window.location.href = '/';
      return false;
    }
    const data = await r.json();
    err.textContent = data.error || 'Invalid credentials';
    err.style.display = 'block';
  } catch(e) {
    err.textContent = 'Connection error. Please try again.';
    err.style.display = 'block';
  }
  return false;
}
</script>
</body>
</html>"""


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        if rate_limited(f'login:{client_ip()}', max_attempts=10, window_seconds=300):
            return jsonify({'error': 'Too many login attempts. Try again in a few minutes.'}), 429
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if check_auth(username, password):
            session['user'] = username
            return jsonify({'ok': True})
        return jsonify({'error': 'Invalid username or password'}), 401
    # GET — show login page
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template_string(LOGIN_HTML)


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login_page'))


FORGOT_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiftTracker — Reset Password</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-base: #090b10;
  --bg-surface: #11141d;
  --bg-card: #161b26;
  --border: #232938;
  --text-main: #f1f5f9;
  --text-muted: #8e9db4;
  --accent-cyan: #38bdf8;
  --accent-green: #10b981;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  background: radial-gradient(circle at 50% 20%, #151b2a 0%, var(--bg-base) 80%);
  color: var(--text-main);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 40px 36px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.7);
  position: relative;
  overflow: hidden;
}
.card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #38bdf8, #10b981);
}
h1 { font-size: 22px; font-weight: 700; text-align: center; margin-bottom: 4px; }
.subtitle { text-align: center; color: var(--text-muted); font-size: 13.5px; margin-bottom: 24px; }
.msg { padding: 12px 14px; border-radius: 10px; font-size: 13px; margin-bottom: 16px; display: none; font-weight: 500; }
.msg.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.msg.ok { background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); color: #6ee7b7; }
.field { margin-bottom: 18px; }
label { display: block; font-size: 12.5px; font-weight: 600; color: var(--text-muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
input { width: 100%; padding: 13px 16px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; font-size: 14.5px; color: var(--text-main); outline: none; transition: all 0.2s ease; }
input:focus { border-color: var(--accent-cyan); box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15); }
button { width: 100%; padding: 14px; margin-top: 10px; background: linear-gradient(135deg, #0284c7, #0369a1); color: #fff; border: none; border-radius: 10px; font-size: 14.5px; font-weight: 700; cursor: pointer; transition: all 0.2s ease; }
button:hover { background: linear-gradient(135deg, #38bdf8, #0284c7); }
.footer { text-align: center; margin-top: 24px; font-size: 12.5px; color: var(--text-muted); }
.footer a { color: var(--accent-cyan); text-decoration: none; font-weight: 600; }
.footer a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="card">
  <h1>Reset Password</h1>
  <div class="subtitle">Enter your email and we'll send a secure reset link</div>
  <div class="msg" id="msg"></div>
  <form id="forgotForm" onsubmit="return handleForgot(event)">
    <div class="field">
      <label for="username">Email Address</label>
      <input type="email" id="username" name="username" autocomplete="username" placeholder="alex@buttergang.dev" required autofocus>
    </div>
    <button type="submit">Send Reset Link →</button>
  </form>
  <div class="footer"><a href="/login">← Back to sign in</a></div>
</div>
<script>
async function handleForgot(e) {
  e.preventDefault();
  const msg = document.getElementById('msg');
  const username = document.getElementById('username').value.trim();
  msg.style.display = 'none';
  try {
    const r = await fetch('/forgot-password', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'username='+encodeURIComponent(username)
    });
    const data = await r.json();
    msg.className = 'msg ok';
    msg.textContent = data.message || 'If that account exists, a reset link is on its way.';
    msg.style.display = 'block';
  } catch (e) {
    msg.className = 'msg error';
    msg.textContent = 'Connection error. Try again.';
    msg.style.display = 'block';
  }
  return false;
}
</script>
</body>
</html>"""


RESET_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiftTracker — Set New Password</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-base: #090b10;
  --bg-surface: #11141d;
  --bg-card: #161b26;
  --border: #232938;
  --text-main: #f1f5f9;
  --text-muted: #8e9db4;
  --accent-cyan: #38bdf8;
  --accent-green: #10b981;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  background: radial-gradient(circle at 50% 20%, #151b2a 0%, var(--bg-base) 80%);
  color: var(--text-main);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 40px 36px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.7);
  position: relative;
  overflow: hidden;
}
.card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #38bdf8, #10b981);
}
h1 { font-size: 22px; font-weight: 700; text-align: center; margin-bottom: 20px; }
.msg { padding: 12px 14px; border-radius: 10px; font-size: 13px; margin-bottom: 16px; display: none; font-weight: 500; }
.msg.error { background: rgba(239, 68, 68, 0.12); border: 1px solid rgba(239, 68, 68, 0.3); color: #fca5a5; }
.msg.ok { background: rgba(16, 185, 129, 0.12); border: 1px solid rgba(16, 185, 129, 0.3); color: #6ee7b7; }
.field { margin-bottom: 18px; }
label { display: block; font-size: 12.5px; font-weight: 600; color: var(--text-muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
input { width: 100%; padding: 13px 16px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; font-size: 14.5px; color: var(--text-main); outline: none; transition: all 0.2s ease; }
input:focus { border-color: var(--accent-cyan); box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15); }
button { width: 100%; padding: 14px; margin-top: 10px; background: linear-gradient(135deg, #0284c7, #0369a1); color: #fff; border: none; border-radius: 10px; font-size: 14.5px; font-weight: 700; cursor: pointer; transition: all 0.2s ease; }
button:hover { background: linear-gradient(135deg, #38bdf8, #0284c7); }
</style>
</head>
<body>
<div class="card">
  <h1>Set New Password</h1>
  <div class="msg" id="msg"></div>
  <form id="resetForm" onsubmit="return handleReset(event)">
    <div class="field">
      <label for="password">New password</label>
      <input type="password" id="password" name="password" autocomplete="new-password" placeholder="••••••••" required>
    </div>
    <div class="field">
      <label for="confirm">Confirm password</label>
      <input type="password" id="confirm" name="confirm" autocomplete="new-password" placeholder="••••••••" required>
    </div>
    <button type="submit">Update Password →</button>
  </form>
</div>
<script>
async function handleReset(e) {
  e.preventDefault();
  const msg = document.getElementById('msg');
  const password = document.getElementById('password').value;
  const confirm = document.getElementById('confirm').value;
  msg.style.display = 'none';
  if (password !== confirm) {
    msg.className = 'msg error'; msg.textContent = "Passwords don't match"; msg.style.display = 'block';
    return false;
  }
  try {
    const r = await fetch(window.location.pathname + window.location.search, {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'password='+encodeURIComponent(password)+'&confirm='+encodeURIComponent(confirm)
    });
    const data = await r.json();
    if (r.ok) {
      msg.className = 'msg ok';
      msg.textContent = 'Password updated. Redirecting to sign in…';
      msg.style.display = 'block';
      setTimeout(() => { window.location.href = '/login'; }, 1500);
    } else {
      msg.className = 'msg error';
      msg.textContent = data.error || 'Something went wrong';
      msg.style.display = 'block';
    }
  } catch (e) {
    msg.className = 'msg error';
    msg.textContent = 'Connection error. Try again.';
    msg.style.display = 'block';
  }
  return false;
}
</script>
</body>
</html>"""


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        if rate_limited(f'forgot:{client_ip()}', max_attempts=5, window_seconds=300):
            return jsonify({'error': 'Too many requests. Try again in a few minutes.'}), 429
        username = request.form.get('username', '').strip()
        generic = {'message': "If that account exists, a reset link is on its way."}
        # Per-email throttle is silent (still returns `generic`) so it can't be used to enumerate accounts.
        if (is_valid_email(username) and username in load_users()
                and not rate_limited(f'forgot-email:{username}', max_attempts=3, window_seconds=900)):
            token = create_reset_token(username)
            reset_url = url_for('reset_password', token=token, _external=True)
            send_reset_email(username, reset_url)
        return jsonify(generic)
    return render_template_string(FORGOT_HTML)


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    token = request.args.get('token', '')
    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        confirm = request.form.get('confirm', '').strip()
        if not password or password != confirm:
            return jsonify({'error': "Passwords don't match"}), 400
        username = consume_reset_token(token)
        if not username:
            return jsonify({'error': 'This reset link is invalid or has expired'}), 400
        save_user(username, password)
        return jsonify({'ok': True})
    # GET — show the form only if the token still looks valid
    tokens = load_reset_tokens()
    entry = tokens.get(token)
    if not entry or entry['expires'] < time.time():
        return render_template_string(
            FORGOT_HTML.replace(
                '<div class="msg" id="msg"></div>',
                '<div class="msg error" style="display:block">This reset link is invalid or has expired. Request a new one below.</div>'
            )
        )
    return render_template_string(RESET_HTML)


# ── Error Handlers ────────────────────────────────────────────────────────

ERROR_404_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiftTracker — 404 Not Found</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-base: #090b10;
  --bg-surface: #10141d;
  --border: #22293b;
  --cyan: #38bdf8;
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  background: radial-gradient(circle at 50% 30%, #151b2a 0%, var(--bg-base) 80%);
  color: var(--text-main);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  text-align: center;
}
.error-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 48px 36px;
  max-width: 440px;
  width: 100%;
  box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.7);
}
.error-code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 56px;
  font-weight: 800;
  color: var(--cyan);
  line-height: 1;
  margin-bottom: 12px;
}
h1 { font-size: 20px; font-weight: 700; margin-bottom: 8px; }
p { color: var(--text-muted); font-size: 14px; margin-bottom: 24px; }
.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: linear-gradient(135deg, #0284c7, #0369a1);
  color: #fff;
  text-decoration: none;
  font-size: 14px;
  font-weight: 600;
  padding: 12px 24px;
  border-radius: 10px;
  transition: all 0.2s ease;
}
.btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(56, 189, 248, 0.35);
}
</style>
</head>
<body>
<div class="error-card">
  <div class="error-code">404</div>
  <h1>Page Not Found</h1>
  <p>The equipment page or endpoint you are looking for does not exist or has been moved.</p>
  <a href="/" class="btn">← Back to Dashboard</a>
</div>
</body>
</html>"""

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template_string(ERROR_404_HTML), 404


# ── HTML Template ─────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiftTracker — Equipment Price Intelligence</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
:root {
  --bg-base: #090b10;
  --bg-surface: #10141d;
  --bg-card: #151a26;
  --bg-card-hover: #1c2233;
  --bg-input: #111622;
  --border: #22293b;
  --border-light: #2d364d;
  
  --text-main: #f8fafc;
  --text-muted: #94a3b8;
  --text-dim: #64748b;
  
  --cyan: #38bdf8;
  --cyan-glow: rgba(56, 189, 248, 0.15);
  --emerald: #10b981;
  --emerald-glow: rgba(16, 185, 129, 0.15);
  --amber: #f59e0b;
  --rose: #f43f5e;
  --purple: #a855f7;
  
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;
  --radius-xl: 22px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
  background-color: var(--bg-base);
  color: var(--text-main);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

/* ── Top Navbar ── */
header {
  background: rgba(16, 20, 29, 0.85);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 40;
}

.nav-container {
  max-width: 1600px;
  margin: 0 auto;
  padding: 14px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  text-decoration: none;
}

.brand-icon {
  width: 38px;
  height: 38px;
  background: linear-gradient(135deg, #1e293b, #0f172a);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  box-shadow: 0 0 16px rgba(56, 189, 248, 0.12);
}

.brand-title {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.5px;
  color: #fff;
}

.brand-title span {
  color: var(--cyan);
}

.brand-badge {
  font-size: 11px;
  font-weight: 700;
  background: rgba(56, 189, 248, 0.12);
  color: var(--cyan);
  border: 1px solid rgba(56, 189, 248, 0.3);
  padding: 2px 7px;
  border-radius: 20px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.scrape-pulse {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text-muted);
  background: var(--bg-card);
  padding: 6px 14px;
  border-radius: 30px;
  border: 1px solid var(--border);
}

.pulse-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--emerald);
  box-shadow: 0 0 10px var(--emerald);
  animation: pulse-ring 2s infinite;
}

@keyframes pulse-ring {
  0% { transform: scale(0.95); opacity: 0.8; }
  50% { transform: scale(1.3); opacity: 1; filter: drop-shadow(0 0 4px var(--emerald)); }
  100% { transform: scale(0.95); opacity: 0.8; }
}

.user-pill {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  background: var(--bg-card);
  border: 1px solid var(--border);
  padding: 6px 14px;
  border-radius: 30px;
}

.user-avatar {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--cyan), #0284c7);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 800;
  color: #fff;
}

.btn-ghost {
  background: transparent;
  color: var(--text-dim);
  border: none;
  font-size: 13px;
  font-weight: 600;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s ease;
  text-decoration: none;
}
.btn-ghost:hover {
  color: var(--rose);
}

/* ── Main Layout ── */
.app-layout {
  max-width: 1600px;
  margin: 0 auto;
  padding: 24px;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 24px;
  flex: 1;
}

/* ── Metrics Grid ── */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}

.metric-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 20px;
  position: relative;
  overflow: hidden;
  transition: transform 0.2s ease, border-color 0.2s ease;
}

.metric-card:hover {
  transform: translateY(-2px);
  border-color: var(--border-light);
}

.metric-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.metric-label {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  color: var(--text-dim);
}

.metric-icon {
  font-size: 18px;
  opacity: 0.8;
}

.metric-val {
  font-family: 'JetBrains Mono', monospace;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: #fff;
}

.metric-sub {
  font-size: 12.5px;
  color: var(--text-muted);
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.metric-sub.good { color: var(--emerald); }
.metric-sub.highlight { color: var(--cyan); }

/* Card Accent Overlays */
.card-deals::after {
  content: '';
  position: absolute;
  top: 0; right: 0; bottom: 0; width: 4px;
  background: linear-gradient(180deg, var(--emerald), transparent);
}
.card-prods::after {
  content: '';
  position: absolute;
  top: 0; right: 0; bottom: 0; width: 4px;
  background: linear-gradient(180deg, var(--cyan), transparent);
}
.card-stores::after {
  content: '';
  position: absolute;
  top: 0; right: 0; bottom: 0; width: 4px;
  background: linear-gradient(180deg, var(--purple), transparent);
}
.card-avg::after {
  content: '';
  position: absolute;
  top: 0; right: 0; bottom: 0; width: 4px;
  background: linear-gradient(180deg, var(--amber), transparent);
}

/* ── Control Bar / Filters ── */
.control-bar {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.search-group {
  position: relative;
  flex: 1 1 320px;
  min-width: 260px;
}

.search-icon {
  position: absolute;
  left: 14px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 15px;
  color: var(--text-dim);
  pointer-events: none;
}

.search-input {
  width: 100%;
  padding: 10px 14px 10px 38px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: #fff;
  font-size: 13.5px;
  outline: none;
  transition: all 0.15s ease;
  font-family: inherit;
}

.search-input:focus {
  border-color: var(--cyan);
  box-shadow: 0 0 0 3px var(--cyan-glow);
}

.filter-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

select {
  background: var(--bg-input);
  color: var(--text-main);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 9.5px 32px 9.5px 12px;
  font-size: 13px;
  font-weight: 500;
  outline: none;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2394a3b8'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'%3E%3C/path%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  background-size: 14px;
  min-width: 140px;
  transition: border-color 0.15s ease;
  font-family: inherit;
}

select:focus {
  border-color: var(--cyan);
}

.toggle-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
  transition: all 0.15s ease;
}

.toggle-chip:hover {
  border-color: var(--border-light);
  color: #fff;
}

.toggle-chip.active {
  background: rgba(16, 185, 129, 0.12);
  border-color: rgba(16, 185, 129, 0.4);
  color: #34d399;
}

.toggle-chip.active .chip-icon {
  transform: scale(1.15);
}

.action-btn {
  background: var(--bg-input);
  color: var(--text-main);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 9.5px 14px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s ease;
  text-decoration: none;
}

.action-btn:hover {
  background: var(--bg-card);
  border-color: var(--border-light);
}

/* ── Content Table Area ── */
.table-panel {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
  display: flex;
  flex-direction: column;
}

.table-stats-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 20px;
  background: rgba(21, 26, 38, 0.4);
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  color: var(--text-muted);
}

.table-responsive {
  overflow-x: auto;
  width: 100%;
}

table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  font-size: 13.5px;
}

thead th {
  background: #131722;
  color: var(--text-dim);
  font-size: 11.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  user-select: none;
  cursor: pointer;
  transition: color 0.15s ease;
}

thead th:hover {
  color: var(--cyan);
}

thead th .sort-arrow {
  display: inline-block;
  margin-left: 4px;
  font-size: 10px;
  color: var(--text-dim);
}

tbody tr {
  border-bottom: 1px solid rgba(34, 41, 59, 0.7);
  transition: background 0.12s ease;
}

tbody tr:hover {
  background: rgba(30, 41, 59, 0.35);
}

tbody td {
  padding: 13px 18px;
  vertical-align: middle;
  color: var(--text-main);
}

.prod-name-col {
  max-width: 440px;
}

.prod-link {
  color: #fff;
  font-weight: 600;
  text-decoration: none;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.15s ease;
  cursor: pointer;
}

.prod-link:hover {
  color: var(--cyan);
}

.store-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 6px;
  font-size: 11.5px;
  font-weight: 700;
  letter-spacing: 0.2px;
  white-space: nowrap;
}

.cat-pill {
  font-size: 12px;
  color: var(--text-muted);
  background: rgba(255, 255, 255, 0.04);
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(255, 255, 255, 0.05);
  white-space: nowrap;
}

.price-box {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
  font-size: 14.5px;
  white-space: nowrap;
}

.price-box.deal {
  color: var(--emerald);
}

.deal-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: rgba(16, 185, 129, 0.12);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.3);
  padding: 2px 7px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.action-icon-btn {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--border);
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
  text-decoration: none;
}

.action-icon-btn:hover {
  background: var(--cyan);
  border-color: var(--cyan);
  color: #000;
  transform: scale(1.05);
}

/* ── Pagination ── */
.table-pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 20px;
  background: rgba(21, 26, 38, 0.4);
  border-top: 1px solid var(--border);
  font-size: 13px;
  color: var(--text-muted);
}

.page-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.page-btn {
  padding: 6px 14px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  color: var(--text-main);
  border-radius: var(--radius-md);
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}

.page-btn:hover:not(.disabled) {
  border-color: var(--cyan);
  color: var(--cyan);
}

.page-btn.disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* ── Modern Modal Overlay ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(4, 6, 10, 0.75);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  z-index: 100;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 20px;
  opacity: 0;
  transition: opacity 0.2s ease;
}

.modal-overlay.active {
  display: flex;
  opacity: 1;
}

.modal-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-xl);
  width: 100%;
  max-width: 820px;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 25px 60px -10px rgba(0, 0, 0, 0.8), 0 0 40px rgba(56, 189, 248, 0.08);
  display: flex;
  flex-direction: column;
  position: relative;
  transform: scale(0.95);
  transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.modal-overlay.active .modal-card {
  transform: scale(1);
}

.modal-header {
  padding: 22px 26px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  position: sticky;
  top: 0;
  background: var(--bg-surface);
  z-index: 2;
}

.modal-title-area h2 {
  font-size: 19px;
  font-weight: 700;
  color: #fff;
  line-height: 1.35;
}

.modal-meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.modal-close-btn {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  color: var(--text-muted);
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.modal-close-btn:hover {
  background: var(--rose);
  border-color: var(--rose);
  color: #fff;
}

.modal-body {
  padding: 24px 26px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.deal-banner {
  background: linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(5, 150, 105, 0.08));
  border: 1px solid rgba(16, 185, 129, 0.35);
  border-radius: var(--radius-md);
  padding: 16px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.deal-banner-left {
  display: flex;
  align-items: center;
  gap: 14px;
}

.deal-pct-huge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 32px;
  font-weight: 800;
  color: #34d399;
  line-height: 1;
}

.chart-wrapper {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 18px;
  position: relative;
}

.chart-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
  display: flex;
  justify-content: space-between;
}

.matches-box {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 16px;
}

.section-head {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.mini-table th {
  background: transparent;
  color: var(--text-dim);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
}

.mini-table td {
  padding: 9px 12px;
  border-bottom: 1px solid rgba(34, 41, 59, 0.5);
}

/* ── Responsive ── */
@media (max-width: 900px) {
  .nav-container { flex-direction: column; align-items: flex-start; }
  .nav-actions { width: 100%; justify-content: space-between; }
  .control-bar { flex-direction: column; align-items: stretch; }
  .filter-group { width: 100%; }
  .filter-group select { flex: 1; }
}
</style>
</head>
<body>

<header>
  <div class="nav-container">
    <a href="/" class="brand">
      <div class="brand-icon">⚡</div>
      <div class="brand-title">LIFT<span>TRACKER</span></div>
      <div class="brand-badge">PRO RADAR</div>
    </a>
    <div class="nav-actions">
      <div class="scrape-pulse">
        <div class="pulse-dot"></div>
        <span>Sync: <strong id="lastScrape" style="color:#fff">—</strong></span>
      </div>
      <div class="user-pill">
        <div class="user-avatar" id="userInitial">A</div>
        <span id="userName">admin</span>
      </div>
      <a href="/logout" class="btn-ghost">Sign Out</a>
    </div>
  </div>
</header>

<main class="app-layout">

  <!-- Metrics Overview -->
  <section class="metrics-grid">
    <div class="metric-card card-prods">
      <div class="metric-header">
        <span class="metric-label">Tracked Catalog</span>
        <span class="metric-icon">📦</span>
      </div>
      <div class="metric-val" id="metricTotalProds">—</div>
      <div class="metric-sub highlight">Across all major equipment brands</div>
    </div>

    <div class="metric-card card-deals">
      <div class="metric-header">
        <span class="metric-label">Active Price Drops</span>
        <span class="metric-icon">🔥</span>
      </div>
      <div class="metric-val" id="metricTotalDeals" style="color:var(--emerald)">—</div>
      <div class="metric-sub good">Items &gt;10% below historical avg</div>
    </div>

    <div class="metric-card card-stores">
      <div class="metric-header">
        <span class="metric-label">Retailer Network</span>
        <span class="metric-icon">🏪</span>
      </div>
      <div class="metric-val" id="metricTotalStores">—</div>
      <div class="metric-sub" id="metricCategoryCount">— categories indexed</div>
    </div>

    <div class="metric-card card-avg">
      <div class="metric-header">
        <span class="metric-label">Catalog Price Range</span>
        <span class="metric-icon">🏷️</span>
      </div>
      <div class="metric-val" id="metricPriceRange" style="font-size:22px">—</div>
      <div class="metric-sub" id="metricAvgPrice">Avg price: —</div>
    </div>
  </section>

  <!-- Filter & Control Bar -->
  <section class="control-bar">
    <div class="search-group">
      <span class="search-icon">🔍</span>
      <input type="search" class="search-input" id="searchBox" placeholder="Filter barbells, plates, racks, belts, shoes, brands..." oninput="applyFilters()">
    </div>

    <div class="filter-group">
      <select id="filterStore" onchange="applyFilters()">
        <option value="">All Retailers (28)</option>
      </select>

      <select id="filterCategory" onchange="applyFilters()">
        <option value="">All Categories</option>
      </select>

      <div class="toggle-chip" id="chipDeals" onclick="toggleDealsFilter()">
        <span class="chip-icon">🔥</span>
        <span>Deals Only</span>
      </div>

      <button class="action-btn" onclick="exportFilteredCSV()" title="Export current results as CSV">
        <span>📥</span> Export CSV
      </button>
    </div>
  </section>

  <!-- Products Data Table -->
  <section class="table-panel">
    <div class="table-stats-bar">
      <div>Showing <strong id="resultCount" style="color:#fff">0</strong> items</div>
      <div id="filterSummary">Viewing all equipment</div>
    </div>

    <div class="table-responsive">
      <table>
        <thead>
          <tr>
            <th onclick="sortBy('name')" style="min-width:320px">
              Equipment / Item <span class="sort-arrow" id="s-name">↓</span>
            </th>
            <th onclick="sortBy('site')">
              Store <span class="sort-arrow" id="s-site">↓</span>
            </th>
            <th onclick="sortBy('category')">
              Category <span class="sort-arrow" id="s-category">↓</span>
            </th>
            <th onclick="sortBy('price')">
              Current Price <span class="sort-arrow" id="s-price">↓</span>
            </th>
            <th onclick="sortBy('deal')">
              Deal Status <span class="sort-arrow" id="s-deal">↓</span>
            </th>
            <th style="text-align:right">Detail</th>
          </tr>
        </thead>
        <tbody id="productBody">
          <!-- Rows inserted via JS -->
        </tbody>
      </table>
    </div>

    <div class="table-pagination">
      <span id="pageInfo">Page 1 of 1</span>
      <div class="page-controls">
        <button class="page-btn" id="prevPage" onclick="goPage(-1)">← Previous</button>
        <button class="page-btn" id="nextPage" onclick="goPage(1)">Next →</button>
      </div>
    </div>
  </section>

</main>

<!-- Modern Detail Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="handleOverlayClick(event)">
  <div class="modal-card" id="modalCard">
    <div class="modal-header">
      <div class="modal-title-area">
        <h2 id="detailName">Product Title</h2>
        <div class="modal-meta-row">
          <span id="detailStoreBadge" class="store-badge">Store</span>
          <span id="detailCatPill" class="cat-pill">Category</span>
          <a id="detailExternalLink" href="#" target="_blank" class="action-btn" style="padding:3px 10px;font-size:12px">
            Official Store ↗
          </a>
        </div>
      </div>
      <button class="modal-close-btn" onclick="closeModal()">✕</button>
    </div>

    <div class="modal-body">
      <!-- Hot Deal Banner if applicable -->
      <div id="detailDealBanner" class="deal-banner" style="display:none">
        <div class="deal-banner-left">
          <div class="deal-pct-huge" id="detailDealPct">-25%</div>
          <div>
            <div style="font-weight:700;color:#fff;font-size:15px">Verified Price Drop</div>
            <div style="color:var(--text-muted);font-size:13px" id="detailDealStats">Current $150 vs Average $200</div>
          </div>
        </div>
        <div class="deal-badge" style="padding:6px 12px;font-size:13px">🔥 HOT DEAL</div>
      </div>

      <!-- Price History Chart -->
      <div class="chart-wrapper">
        <div class="chart-title">
          <span>Price Timeline & Historical Fluctuations</span>
          <span id="detailDataPointsCount" style="color:var(--text-muted)">0 data points</span>
        </div>
        <canvas id="priceChart" style="max-height:260px;width:100%"></canvas>
      </div>

      <!-- Cross Store Matches -->
      <div id="matchesSection" class="matches-box" style="display:none">
        <div class="section-head">
          <span>🔗 Cross-Store Alternatives & Competing Gear</span>
        </div>
        <table class="mini-table">
          <thead>
            <tr>
              <th>Store</th>
              <th>Alternative Product</th>
              <th>Price</th>
              <th>Match Confidence</th>
            </tr>
          </thead>
          <tbody id="matchesBody"></tbody>
        </table>
      </div>

      <!-- Price History Raw Table -->
      <div class="matches-box">
        <div class="section-head">
          <span>📜 Scrape Audit Log</span>
        </div>
        <table class="mini-table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Recorded Price</th>
              <th>Direct Link</th>
            </tr>
          </thead>
          <tbody id="historyBody"></tbody>
        </table>
      </div>

    </div>
  </div>
</div>

<script>
let allProducts = [], stores = [], categories = [], currentPage = 1, pageSize = 50;
let sortField = 'deal', sortDir = 'desc', chartInstance = null;
let onlyDeals = false;

const STORE_COLORS = {
  "Rogue Fitness": "#ef4444",
  "EliteFTS": "#0284c7",
  "REP Fitness": "#10b981",
  "Titan Fitness": "#f97316",
  "Bells of Steel": "#a855f7",
  "LiftingLarge": "#06b6d4",
  "Weightlifting House": "#64748b",
  "LUXIAOJUN": "#ec4899",
  "TYR Sport": "#3b82f6",
  "SBD Apparel": "#475569",
  "Onyx Straps": "#854d0e",
  "2POOD": "#0284c7",
  "American Barbell": "#b91c1c",
  "Fringe Sport": "#eab308",
  "Cerberus Strength": "#15803d",
  "Pioneer Fitness": "#78350f",
  "Hookgrip": "#7e22ce",
  "NoBull": "#334155",
  "Mark Bell": "#c2410c",
  "Slingshot": "#0f766e",
  "Force USA": "#a16207",
  "Get Rx'd": "#1d4ed8",
  "Virus International": "#475569",
  "Born Primitive": "#7c2d12",
  "Gymreapers": "#ea580c",
  "Again Faster": "#0891b2",
  "Inzer Advance Designs": "#1e40af",
};

function storeColor(s) {
  return STORE_COLORS[s] || '#0284c7';
}

async function loadData() {
  try {
    const [pr, mr, userRes] = await Promise.all([
      fetch('/api/products'),
      fetch('/api/meta'),
      fetch('/api/me')
    ]);
    
    if (userRes.ok) {
      const u = await userRes.json();
      if (u.user) {
        document.getElementById('userName').textContent = u.user;
        document.getElementById('userInitial').textContent = u.user[0].toUpperCase();
      }
    }

    allProducts = await pr.json();
    const meta = await mr.json();

    stores = meta.stores || [];
    categories = meta.categories || [];

    document.getElementById('metricTotalProds').textContent = (meta.total_products || allProducts.length).toLocaleString();
    document.getElementById('metricTotalStores').textContent = meta.total_stores || stores.length;
    document.getElementById('metricCategoryCount').textContent = `${meta.category_count || categories.length} categories indexed`;
    document.getElementById('lastScrape').textContent = meta.last_scrape || 'Active';

    // Populate dropdowns
    const ss = document.getElementById('filterStore');
    stores.forEach(s => {
      let o = document.createElement('option');
      o.value = s;
      o.textContent = s;
      ss.appendChild(o);
    });

    const cs = document.getElementById('filterCategory');
    categories.forEach(c => {
      let o = document.createElement('option');
      o.value = c;
      o.textContent = c;
      cs.appendChild(o);
    });

    // Metrics math
    const prices = allProducts.filter(p => p.price && p.price > 0).map(p => p.price);
    const avg = prices.length ? (prices.reduce((a, b) => a + b, 0) / prices.length) : 0;
    const mn = prices.length ? Math.min(...prices) : 0;
    const mx = prices.length ? Math.max(...prices) : 0;
    const deals = allProducts.filter(p => p.deal_pct && p.deal_pct > 10);

    document.getElementById('metricTotalDeals').textContent = deals.length.toLocaleString();
    document.getElementById('metricPriceRange').textContent = `$${mn.toFixed(0)} – $${mx.toFixed(0)}`;
    document.getElementById('metricAvgPrice').textContent = `Avg item price: $${avg.toFixed(2)}`;

    render();
  } catch(e) {
    console.error('Failed to load dashboard data:', e);
  }
}

function toggleDealsFilter() {
  onlyDeals = !onlyDeals;
  const chip = document.getElementById('chipDeals');
  if (onlyDeals) {
    chip.classList.add('active');
  } else {
    chip.classList.remove('active');
  }
  currentPage = 1;
  render();
}

function getFiltered() {
  const store = document.getElementById('filterStore').value;
  const cat = document.getElementById('filterCategory').value;
  const q = document.getElementById('searchBox').value.toLowerCase().trim();

  let prods = allProducts.filter(p => {
    if (store && p.site !== store) return false;
    if (cat && p.category !== cat) return false;
    if (onlyDeals && (!p.deal_pct || p.deal_pct <= 10)) return false;
    if (q) {
      const matchName = p.name && p.name.toLowerCase().includes(q);
      const matchSite = p.site && p.site.toLowerCase().includes(q);
      const matchCat = p.category && p.category.toLowerCase().includes(q);
      if (!matchName && !matchSite && !matchCat) return false;
    }
    return true;
  });

  prods.sort((a, b) => {
    let va, vb;
    if (sortField === 'name') {
      va = (a.name || '').toLowerCase();
      vb = (b.name || '').toLowerCase();
    } else if (sortField === 'site') {
      va = a.site || '';
      vb = b.site || '';
    } else if (sortField === 'category') {
      va = a.category || '';
      vb = b.category || '';
    } else if (sortField === 'price') {
      va = a.price || 999999;
      vb = b.price || 999999;
    } else if (sortField === 'deal') {
      va = -(a.deal_pct || 0);
      vb = -(b.deal_pct || 0);
    } else {
      va = (a.name || '').toLowerCase();
      vb = (b.name || '').toLowerCase();
    }
    return va < vb ? (sortDir === 'asc' ? -1 : 1) : va > vb ? (sortDir === 'asc' ? 1 : -1) : 0;
  });

  return prods;
}

function render() {
  const prods = getFiltered();
  const totalPages = Math.ceil(prods.length / pageSize) || 1;
  if (currentPage > totalPages) currentPage = totalPages;
  const start = (currentPage - 1) * pageSize;
  const page = prods.slice(start, start + pageSize);
  const body = document.getElementById('productBody');

  if (!page.length) {
    body.innerHTML = `<tr><td colspan="6" style="text-align:center;padding:40px;color:var(--text-dim);">No equipment matches the selected filters.</td></tr>`;
  } else {
    body.innerHTML = page.map(p => {
      const c = storeColor(p.site);
      const hasDeal = p.deal_pct && p.deal_pct > 10;
      const dealBadge = hasDeal
        ? `<span class="deal-badge">🔥 -${p.deal_pct.toFixed(0)}%</span>`
        : `<span style="color:var(--text-dim);font-size:12px;">Normal</span>`;
      
      const priceClass = hasDeal ? 'price-box deal' : 'price-box';
      const formattedPrice = p.price ? `$${p.price.toFixed(2)}` : (p.price_text || '—');

      return `<tr>
        <td class="prod-name-col">
          <a class="prod-link" onclick="showDetail(${p.id})">${esc(p.name)}</a>
        </td>
        <td>
          <span class="store-badge" style="background:${c}22;color:${c};border:1px solid ${c}55">
            ${esc(p.site)}
          </span>
        </td>
        <td><span class="cat-pill">${esc(p.category || 'General')}</span></td>
        <td><span class="${priceClass}">${formattedPrice}</span></td>
        <td>${dealBadge}</td>
        <td style="text-align:right">
          <button class="action-icon-btn" onclick="showDetail(${p.id})" title="View Price History">
            📊
          </button>
        </td>
      </tr>`;
    }).join('');
  }

  document.getElementById('resultCount').textContent = prods.length.toLocaleString();
  document.getElementById('pageInfo').textContent = `Page ${currentPage} of ${totalPages}`;
  
  const prevBtn = document.getElementById('prevPage');
  const nextBtn = document.getElementById('nextPage');
  if (currentPage <= 1) prevBtn.classList.add('disabled'); else prevBtn.classList.remove('disabled');
  if (currentPage >= totalPages) nextBtn.classList.add('disabled'); else nextBtn.classList.remove('disabled');

  ['name', 'site', 'category', 'price', 'deal'].forEach(f => {
    const el = document.getElementById('s-' + f);
    if (el) {
      el.textContent = sortField === f ? (sortDir === 'asc' ? '↑' : '↓') : '↓';
      el.style.color = sortField === f ? 'var(--cyan)' : 'var(--text-dim)';
    }
  });
}

function applyFilters() {
  currentPage = 1;
  render();
}

function goPage(d) {
  const t = Math.ceil(getFiltered().length / pageSize) || 1;
  const n = currentPage + d;
  if (n >= 1 && n <= t) {
    currentPage = n;
    render();
    window.scrollTo({ top: 300, behavior: 'smooth' });
  }
}

function sortBy(f) {
  if (sortField === f) {
    sortDir = sortDir === 'asc' ? 'desc' : 'asc';
  } else {
    sortField = f;
    sortDir = f === 'deal' || f === 'price' ? 'asc' : 'asc';
  }
  render();
}

function esc(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function showDetail(id) {
  try {
    const r = await fetch(`/api/product/${id}`);
    if (!r.ok) return;
    const prod = await r.json();

    document.getElementById('detailName').textContent = prod.name;
    const c = storeColor(prod.site);
    const storeBadge = document.getElementById('detailStoreBadge');
    storeBadge.textContent = prod.site;
    storeBadge.style.background = `${c}25`;
    storeBadge.style.color = c;
    storeBadge.style.border = `1px solid ${c}66`;

    document.getElementById('detailCatPill').textContent = prod.category || 'General';

    const extLink = document.getElementById('detailExternalLink');
    if (prod.url) {
      extLink.href = prod.url;
      extLink.style.display = 'inline-flex';
    } else {
      extLink.style.display = 'none';
    }

    // Deal banner
    const dealBanner = document.getElementById('detailDealBanner');
    if (prod.deal_pct && prod.deal_pct > 5) {
      dealBanner.style.display = 'flex';
      document.getElementById('detailDealPct').textContent = `-${prod.deal_pct.toFixed(0)}%`;
      document.getElementById('detailDealStats').textContent = 
        `Current: $${(prod.price || 0).toFixed(2)} vs Historical Avg: $${(prod.avg_price || 0).toFixed(2)} (Lowest seen: $${(prod.min_price || prod.price || 0).toFixed(2)})`;
    } else {
      dealBanner.style.display = 'none';
    }

    // Chart
    const hist = prod.history || [];
    document.getElementById('detailDataPointsCount').textContent = `${hist.length} data points recorded`;

    const ctx = document.getElementById('priceChart').getContext('2d');
    if (chartInstance) chartInstance.destroy();

    const chartDates = hist.map(h => (h.scraped_at || '').slice(0, 10)).reverse();
    const chartPrices = hist.map(h => h.price).reverse();

    chartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        labels: chartDates,
        datasets: [{
          label: 'Price (USD)',
          data: chartPrices,
          borderColor: '#38bdf8',
          backgroundColor: 'rgba(56, 189, 248, 0.08)',
          borderWidth: 2.5,
          fill: true,
          tension: 0.3,
          pointRadius: 4,
          pointBackgroundColor: '#38bdf8',
          pointHoverRadius: 7,
          pointHoverBackgroundColor: '#fff',
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#10141d',
            titleColor: '#94a3b8',
            bodyColor: '#fff',
            borderColor: '#22293b',
            borderWidth: 1,
            padding: 10,
            callbacks: {
              label: (context) => ` Price: $${context.parsed.y.toFixed(2)}`
            }
          }
        },
        scales: {
          x: {
            grid: { color: 'rgba(255, 255, 255, 0.04)' },
            ticks: { color: '#64748b', font: { family: "'Plus Jakarta Sans', sans-serif" } }
          },
          y: {
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#94a3b8',
              font: { family: "'JetBrains Mono', monospace" },
              callback: v => '$' + v.toFixed(0)
            }
          }
        }
      }
    });

    // Cross-store Matches
    const ms = document.getElementById('matchesSection');
    const mb = document.getElementById('matchesBody');
    if (prod.matches && prod.matches.length) {
      ms.style.display = 'block';
      mb.innerHTML = prod.matches.map(m => {
        const mc = storeColor(m.site);
        const link = m.url ? `<a href="${esc(m.url)}" target="_blank" style="color:#fff;text-decoration:none">${esc(m.name)} ↗</a>` : esc(m.name);
        return `<tr>
          <td><span class="store-badge" style="background:${mc}22;color:${mc};border:1px solid ${mc}55">${esc(m.site)}</span></td>
          <td>${link}</td>
          <td><strong style="font-family:'JetBrains Mono';color:#34d399">$${(m.price || 0).toFixed(2)}</strong></td>
          <td><span class="cat-pill">${m.similarity.toFixed(0)}% match</span></td>
        </tr>`;
      }).join('');
    } else {
      ms.style.display = 'none';
    }

    // Raw History Table
    const hb = document.getElementById('historyBody');
    hb.innerHTML = hist.map(h => {
      const l = h.source_url ? `<a href="${esc(h.source_url)}" target="_blank" style="color:var(--cyan);text-decoration:none">View Source ↗</a>` : '—';
      const d = (h.scraped_at || '').slice(0, 19).replace('T', ' ');
      return `<tr>
        <td style="color:var(--text-muted);font-family:'JetBrains Mono';font-size:12px">${d}</td>
        <td><strong style="font-family:'JetBrains Mono'">$${(h.price || 0).toFixed(2)}</strong></td>
        <td>${l}</td>
      </tr>`;
    }).join('');

    const overlay = document.getElementById('modalOverlay');
    overlay.classList.add('active');
  } catch(e) {
    console.error('Failed to show detail modal:', e);
  }
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('active');
  if (chartInstance) {
    chartInstance.destroy();
    chartInstance = null;
  }
}

function handleOverlayClick(e) {
  if (e.target.id === 'modalOverlay') {
    closeModal();
  }
}

function exportFilteredCSV() {
  const prods = getFiltered();
  if (!prods.length) return;
  const headers = ['ID', 'Site', 'Name', 'Category', 'Price', 'Avg Price', 'Deal Pct', 'URL'];
  const rows = prods.map(p => [
    p.id,
    `"${(p.site || '').replace(/"/g, '""')}"`,
    `"${(p.name || '').replace(/"/g, '""')}"`,
    `"${(p.category || '').replace(/"/g, '""')}"`,
    p.price || 0,
    p.avg_price || 0,
    p.deal_pct || 0,
    `"${(p.url || '').replace(/"/g, '""')}"`
  ]);

  const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows.map(e => e.join(','))].join('\n');
  const encodedUri = encodeURI(csvContent);
  const link = document.createElement('a');
  link.setAttribute('href', encodedUri);
  link.setAttribute('download', `lifttracker_export_${new Date().toISOString().slice(0,10)}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

loadData();
</script>
</body>
</html>"""


# ── API Routes ────────────────────────────────────────────────────────────

@app.route('/')
@require_login
def index():
    return render_template_string(HTML)


@app.route('/api/products')
@require_login
def api_products():
    """All products with latest price, historical stats, and deal detection."""
    db_type = get_db_type()

    if db_type == 'postgres':
        rows = fetch_dict("""
            WITH latest AS (
                SELECT DISTINCT ON (product_id) product_id, price, price_text, scraped_at, source_url
                FROM price_history
                ORDER BY product_id, scraped_at DESC
            ),
            stats AS (
                SELECT product_id,
                       ROUND(AVG(price)::numeric, 2)::float as avg_price,
                       ROUND(MIN(price)::numeric, 2)::float as min_price,
                       ROUND(MAX(price)::numeric, 2)::float as max_price,
                       ROUND((MAX(price) - MIN(price))::numeric, 2)::float as price_range,
                       COUNT(*) as history_count
                FROM price_history
                GROUP BY product_id
            )
            SELECT p.id, p.site, p.name, p.category, p.currency, p.url,
                   l.price, l.price_text, l.scraped_at,
                   s.avg_price, s.min_price, s.max_price, s.price_range, s.history_count
            FROM products p
            LEFT JOIN latest l ON l.product_id = p.id
            LEFT JOIN stats s ON s.product_id = p.id
            ORDER BY p.site, p.name
        """)
    else:
        rows = fetch_dict("""
            WITH latest AS (
                SELECT product_id, price, price_text, scraped_at, source_url
                FROM price_history
                WHERE (product_id, scraped_at) IN (
                    SELECT product_id, MAX(scraped_at)
                    FROM price_history
                    GROUP BY product_id
                )
            ),
            stats AS (
                SELECT product_id,
                       ROUND(AVG(price), 2) as avg_price,
                       ROUND(MIN(price), 2) as min_price,
                       ROUND(MAX(price), 2) as max_price,
                       ROUND(MAX(price) - MIN(price), 2) as price_range,
                       COUNT(*) as history_count
                FROM price_history
                GROUP BY product_id
            )
            SELECT p.id, p.site, p.name, p.category, p.currency, p.url,
                   l.price, l.price_text, l.scraped_at,
                   s.avg_price, s.min_price, s.max_price, s.price_range, s.history_count
            FROM products p
            LEFT JOIN latest l ON l.product_id = p.id
            LEFT JOIN stats s ON s.product_id = p.id
            ORDER BY p.site, p.name
        """)

    for r in rows:
        if r.get('avg_price') and r.get('price') and r['avg_price'] > 0:
            pct = round((1 - r['price'] / r['avg_price']) * 100, 1)
            r['deal_pct'] = pct if pct > 0 else 0
        else:
            r['deal_pct'] = 0
    return jsonify(rows)


@app.route('/api/product/<int:pid>')
@require_login
def api_product(pid):
    """Single product detail with full price history."""
    db_type = get_db_type()

    if db_type == 'postgres':
        prod = fetch_one("""
            SELECT p.*, ph.price, ph.price_text, ph.source_url
            FROM products p
            LEFT JOIN price_history ph ON ph.product_id = p.id
                AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)
            WHERE p.id = %s
        """, (pid,))
        stats = fetch_one("""
            SELECT ROUND(AVG(price)::numeric, 2)::float as avg_price,
                   ROUND(MIN(price)::numeric, 2)::float as min_price,
                   ROUND(MAX(price)::numeric, 2)::float as max_price,
                   COUNT(*) as history_count
            FROM price_history WHERE product_id = %s
        """, (pid,))
        history = fetch_dict("""
            SELECT price, price_text, scraped_at, source_url
            FROM price_history
            WHERE product_id = %s
            ORDER BY scraped_at DESC
            LIMIT 100
        """, (pid,))
    else:
        prod = fetch_one("""
            SELECT p.*, ph.price, ph.price_text, ph.source_url
            FROM products p
            LEFT JOIN price_history ph ON ph.product_id = p.id
                AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)
            WHERE p.id = ?
        """, (pid,))
        stats = fetch_one("""
            SELECT ROUND(AVG(price), 2) as avg_price,
                   ROUND(MIN(price), 2) as min_price,
                   ROUND(MAX(price), 2) as max_price,
                   COUNT(*) as history_count
            FROM price_history WHERE product_id = ?
        """, (pid,))
        history = fetch_dict("""
            SELECT price, price_text, scraped_at, source_url
            FROM price_history
            WHERE product_id = ?
            ORDER BY scraped_at DESC
            LIMIT 100
        """, (pid,))

    if not prod:
        return jsonify({'error': 'not found'}), 404

    deal_pct = 0
    if prod.get('price') and stats and stats.get('avg_price') and stats['avg_price'] > 0:
        pct = round((1 - prod['price'] / stats['avg_price']) * 100, 1)
        deal_pct = pct if pct > 0 else 0

    # Matches — similar products from other stores
    matches = []
    try:
        if db_type == 'postgres':
            matches = fetch_dict("""
                SELECT p.id, p.site, p.name, p.category, p.url,
                       ph.price, ph.price_text,
                       pm.similarity
                FROM product_matches pm
                JOIN products p ON p.id = pm.matched_product_id
                LEFT JOIN price_history ph ON ph.product_id = p.id
                    AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)
                WHERE pm.product_id = %s
                ORDER BY pm.similarity DESC
                LIMIT 5
            """, (pid,))
        else:
            matches = fetch_dict("""
                SELECT p.id, p.site, p.name, p.category, p.url,
                       ph.price, ph.price_text,
                       pm.similarity
                FROM product_matches pm
                JOIN products p ON p.id = pm.matched_product_id
                LEFT JOIN price_history ph ON ph.product_id = p.id
                    AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)
                WHERE pm.product_id = ?
                ORDER BY pm.similarity DESC
                LIMIT 5
            """, (pid,))
    except Exception:
        matches = []

    return jsonify({**prod, **(stats or {}), 'deal_pct': deal_pct, 'history': history, 'matches': matches})


@app.route('/api/meta')
@require_login
def api_meta():
    stores = fetch_dict("SELECT DISTINCT site FROM products ORDER BY site")
    cats = fetch_dict("SELECT DISTINCT category FROM products WHERE category IS NOT NULL AND category != '' ORDER BY category")
    total = fetch_one("SELECT COUNT(*) as c FROM products")
    store_count = fetch_one("SELECT COUNT(DISTINCT site) as c FROM products")
    cat_count = fetch_one("SELECT COUNT(DISTINCT category) as c FROM products WHERE category IS NOT NULL AND category != ''")
    last = fetch_one("SELECT MAX(scraped_at) as last FROM price_history")

    return jsonify({
        'stores': [s['site'] for s in stores],
        'categories': [c['category'] for c in cats],
        'total_products': total['c'] if total else 0,
        'total_stores': store_count['c'] if store_count else 0,
        'category_count': cat_count['c'] if cat_count else 0,
        'last_scrape': last['last'][:19].replace('T', ' ') if last and last['last'] else None,
    })


@app.route('/api/me')
@require_login
def api_me():
    return jsonify({'user': session.get('user', '')})


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='LiftTracker - Equipment Price Dashboard')
    parser.add_argument('--host', default='0.0.0.0' if DATABASE_URL else '127.0.0.1',
                        help=f'Host (default: {"0.0.0.0" if DATABASE_URL else "127.0.0.1"})')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8080)),
                        help='Port (default: 8080 or $PORT)')
    parser.add_argument('--debug', action='store_true', help='Debug mode')
    parser.add_argument('--setup-auth', action='store_true', help='Create/update dashboard users')
    args = parser.parse_args()

    if args.setup_auth:
        setup_auth()
        return

    if not DATABASE_URL and not DB_PATH.exists():
        print(f"Error: no database found at {DB_PATH}")
        print("Run a scrape first:  python scraper/run_scrape.py")
        sys.exit(1)

    if not AUTH_FILE.exists() or not load_users():
        print(f"Error: no dashboard users configured. Run:  python dashboard.py --setup-auth")
        sys.exit(1)

    db_source = 'Neon PostgreSQL' if DATABASE_URL else f'SQLite ({DB_PATH})'
    print(f"🏋️  LiftTracker Dashboard")
    print(f"   Database: {db_source}")
    print(f"   URL:      http://{args.host}:{args.port}")
    print(f"   Auth:     enabled (credentials in {AUTH_FILE})")
    print(f"   Press Ctrl+C to stop")

    if DATABASE_URL:
        # Production — use gunicorn if available
        try:
            from gunicorn.app.wsgiapp import run
            sys.argv = ['gunicorn', 'dashboard:app', '-b', f'{args.host}:{args.port}',
                        '--workers', '2', '--threads', '4',
                        '--access-logfile', '-', '--error-logfile', '-']
            run()
        except ImportError:
            app.run(host=args.host, port=args.port, debug=args.debug)
    else:
        app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()