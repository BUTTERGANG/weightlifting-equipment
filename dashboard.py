#!/usr/bin/env python3
"""
Weightlifting Equipment Price Dashboard — Plate Magnet.

Tracks barbell, plate, rack, belt, apparel & shoe prices across 26+ retailers.
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
import gzip
import secrets
import hashlib
import argparse
import threading
import subprocess
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone, timedelta
from functools import wraps
from collections import defaultdict

from flask import (Flask, g, request, jsonify, render_template, session,
                   redirect, url_for, abort)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR / 'scraper'))

from db import connect, init_schema, DB_PATH  # noqa: E402
from categories import CANONICAL_CATEGORIES, CATEGORY_ICONS  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL', '')
# AUTH_FILE/RESET_FILE live next to the script (inside the persisted workspace
# dir), not under Path.home() — on Replit, $HOME is an ephemeral overlay wiped
# on every container restart, which was locking everyone out of the dashboard.
AUTH_FILE = APP_DIR / '.equipment_dashboard_auth'
RESET_FILE = APP_DIR / '.equipment_dashboard_resets'
RESET_TOKEN_TTL = 30 * 60  # seconds
MIN_PASSWORD_LENGTH = int(os.environ.get('MIN_PASSWORD_LENGTH', '12'))
SESSION_LIFETIME = timedelta(days=int(os.environ.get('SESSION_DAYS', '14')))

AGENTMAIL_API_KEY = os.environ.get('AGENTMAIL_API_KEY', '')
AGENTMAIL_INBOX_ID = os.environ.get('AGENTMAIL_INBOX_ID', '')

# Number of proxies in front of the app whose X-Forwarded-For entries we trust.
# Anything above this is attacker-controlled — see client_ip().
TRUSTED_PROXY_HOPS = int(os.environ.get('TRUSTED_PROXY_HOPS', '1' if DATABASE_URL else '0'))

# ── Scraping ──────────────────────────────────────────────────────────────
# Scrapes run as a detached subprocess (scraper/run_scrape.py) launched from
# this app and tracked via the scrape_runs table. When RUN_SCHEDULER is on, a
# background thread also launches one every SCRAPE_INTERVAL_HOURS, guarded by a
# Postgres advisory lock so only one gunicorn worker ever fires it.
SCRAPER_SCRIPT = APP_DIR / 'scraper' / 'run_scrape.py'
SCRAPE_LOG_DIR = APP_DIR / 'data' / 'scrape_logs'
SCRAPE_INTERVAL_HOURS = float(os.environ.get('SCRAPE_INTERVAL_HOURS', '6'))
# A run whose heartbeat stopped this long ago is presumed dead (see reap_stale_runs).
SCRAPE_STALE_MINUTES = float(os.environ.get('SCRAPE_STALE_MINUTES', '30'))
_SCRAPE_SCHEDULER_LOCK_KEY = 872341  # arbitrary constant for pg_try_advisory_lock

# The in-process scheduler only makes sense on a deployment that is always
# running. On Replit Autoscale the container is recycled between requests, so
# the thread never reliably fires and any scrape it launched is killed mid-run;
# there, an external cron should drive scraping instead. Default off unless
# explicitly enabled.
RUN_SCHEDULER = os.environ.get('RUN_SCHEDULER', '0') not in ('0', 'false', 'False', '')

# How long /api/products may serve a cached payload.
PRODUCTS_CACHE_SECONDS = float(os.environ.get('PRODUCTS_CACHE_SECONDS', '120'))

app = Flask(__name__, template_folder=str(APP_DIR / 'templates'),
            static_folder=str(APP_DIR / 'static'))

# Trust exactly TRUSTED_PROXY_HOPS proxy hops for the client IP. Without this,
# werkzeug reports the proxy's address and our own X-Forwarded-For parsing
# accepted any value a client cared to send — which made the login rate limit
# trivially bypassable by rotating the header.
if TRUSTED_PROXY_HOPS:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXY_HOPS, x_proto=1, x_host=1)


def _load_secret_key():
    """Session signing key.

    Deriving it from the auth file (the old behaviour) meant every password
    change silently invalidated all sessions on the next restart, and gunicorn
    workers that restarted at different times disagreed about the key. Persist a
    random key instead, and let DASHBOARD_SECRET override it.
    """
    env = os.environ.get('DASHBOARD_SECRET')
    if env:
        return env
    key_file = APP_DIR / '.equipment_dashboard_secret'
    if key_file.exists():
        return key_file.read_text().strip()
    key = secrets.token_hex(32)
    key_file.write_text(key)
    key_file.chmod(0o600)
    return key


app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # Secure cookies require HTTPS; local dev over http would break if forced.
    SESSION_COOKIE_SECURE=bool(DATABASE_URL),
    PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
    MAX_CONTENT_LENGTH=1024 * 1024,
    JSON_SORT_KEYS=False,
)


# ── Database ──────────────────────────────────────────────────────────────
# One connection per request, closed on teardown. All SQL is written once,
# against scraper/db.py's dialect-neutral helpers.

def get_db():
    if 'db' not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db:
        db.close()


# ── Response hardening & compression ──────────────────────────────────────

CSP = "; ".join([
    "default-src 'self'",
    # Inline handlers remain in the markup, so 'unsafe-inline' is still needed
    # for scripts; the value of the policy here is that no external script host
    # is allowed at all (Chart.js is vendored under static/js/vendor/).
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    # Product images are hotlinked from arbitrary retailer CDNs.
    "img-src 'self' data: https:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

_GZIP_TYPES = ('application/json', 'text/html', 'text/css',
               'application/javascript', 'text/javascript')


@app.after_request
def finalize_response(response):
    response.headers.setdefault('Content-Security-Policy', CSP)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if DATABASE_URL:
        response.headers.setdefault(
            'Strict-Transport-Security', 'max-age=31536000; includeSubDomains')

    # gzip: /api/products is ~2.4 MB of JSON and was being shipped uncompressed
    # on every page load. It compresses to roughly 190 KB.
    if (response.direct_passthrough
            or response.status_code < 200 or response.status_code >= 300
            or 'Content-Encoding' in response.headers):
        return response
    if not response.content_type or not response.content_type.startswith(_GZIP_TYPES):
        return response
    if 'gzip' not in request.headers.get('Accept-Encoding', '').lower():
        return response
    data = response.get_data()
    if len(data) < 1024:
        return response
    buf = BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6, mtime=0) as f:
        f.write(data)
    response.set_data(buf.getvalue())
    response.headers['Content-Encoding'] = 'gzip'
    response.headers['Content-Length'] = str(response.content_length)
    response.headers.add('Vary', 'Accept-Encoding')
    return response


# ── CSRF ──────────────────────────────────────────────────────────────────
# SameSite=Lax already blocks most cross-site POSTs, but a token costs little
# and covers same-site-but-untrusted contexts.

CSRF_EXEMPT = set()


def csrf_token():
    token = session.get('_csrf')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf'] = token
    return token


@app.context_processor
def inject_csrf():
    return {'csrf_token': csrf_token()}


@app.before_request
def enforce_csrf():
    if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
        return None
    if request.endpoint in CSRF_EXEMPT:
        return None
    sent = (request.form.get('csrf_token')
            or request.headers.get('X-CSRF-Token', ''))
    expected = session.get('_csrf', '')
    if not expected or not secrets.compare_digest(str(sent), str(expected)):
        return jsonify({'error': 'Invalid or missing CSRF token. Reload the page.'}), 400
    return None


# ── Schema bootstrap ──────────────────────────────────────────────────────

def bootstrap_schema():
    try:
        with connect() as db:
            init_schema(db)
    except Exception as e:
        print(f'Could not run schema migrations: {e}', flush=True)


# ── Scraping ──────────────────────────────────────────────────────────────

def reap_stale_runs(db=None):
    """Mark runs whose process died as errored.

    A scrape launched as a subprocess dies with its container (Replit recycles
    them). Its scrape_runs row then stays 'running' forever, and because
    /api/scrape refuses to start while one is running, the button stayed dead
    with no way to recover. Runs heartbeat every 30s, so anything quiet for
    SCRAPE_STALE_MINUTES is presumed gone.
    """
    own = db is None
    db = db or connect()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=SCRAPE_STALE_MINUTES)
        cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
        cur = db.execute("""
            UPDATE scrape_runs
               SET status = 'error',
                   error = COALESCE(error, 'Scrape process died (no heartbeat)'),
                   finished_at = ?
             WHERE status = 'running'
               AND COALESCE(heartbeat_at, started_at) < ?
        """, (datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), cutoff_str))
        db.commit()
        return cur.rowcount or 0
    except Exception as e:
        print(f'[reaper] error: {e}', flush=True)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        if own:
            db.close()


def launch_scrape(trigger='manual', http_only=False, db=None):
    """Insert a scrape_runs row and launch scraper/run_scrape.py as a detached
    subprocess that updates that row itself as it progresses. Returns the run id."""
    own = db is None
    db = db or connect()
    try:
        if db.is_postgres:
            row = db.query_one(
                """INSERT INTO scrape_runs (status, trigger, http_only, heartbeat_at)
                   VALUES ('running', ?, ?, NOW()) RETURNING id""",
                (trigger, http_only))
            run_id = row['id']
        else:
            cur = db.execute(
                """INSERT INTO scrape_runs (status, trigger, http_only, heartbeat_at)
                   VALUES ('running', ?, ?, datetime('now'))""",
                (trigger, int(http_only)))
            run_id = cur.lastrowid
        db.commit()
    finally:
        if own:
            db.close()

    SCRAPE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SCRAPE_LOG_DIR / f'run_{run_id}.log'
    cmd = [sys.executable, str(SCRAPER_SCRIPT), '--run-id', str(run_id)]
    if http_only:
        cmd.append('--http-only')

    with open(log_path, 'w') as log_file:
        subprocess.Popen(cmd, cwd=str(APP_DIR), env=os.environ.copy(),
                         stdout=log_file, stderr=subprocess.STDOUT,
                         start_new_session=True)
    return run_id


def _maybe_run_scheduled_scrape():
    """Called periodically by the scheduler thread. Uses a Postgres advisory
    lock so that with multiple gunicorn workers, only one launches the scrape."""
    if not DATABASE_URL:
        return
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    locked = False
    try:
        cur.execute('SELECT pg_try_advisory_lock(%s)', (_SCRAPE_SCHEDULER_LOCK_KEY,))
        locked = cur.fetchone()[0]
        if not locked:
            return
        reap_stale_runs()
        cur.execute("SELECT COUNT(*) FROM scrape_runs WHERE status = 'running'")
        if cur.fetchone()[0] > 0:
            return
        cur.execute('SELECT MAX(started_at) FROM scrape_runs')
        last = cur.fetchone()[0]
        due = last is None or (datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)
                               >= timedelta(hours=SCRAPE_INTERVAL_HOURS))
        if due:
            print(f'[scheduler] launching scheduled scrape '
                  f'(interval={SCRAPE_INTERVAL_HOURS}h)', flush=True)
            launch_scrape(trigger='scheduled')
    finally:
        # Only unlock what we actually acquired.
        if locked:
            cur.execute('SELECT pg_advisory_unlock(%s)', (_SCRAPE_SCHEDULER_LOCK_KEY,))
        conn.close()


def _scheduler_loop():
    while True:
        time.sleep(300)
        try:
            _maybe_run_scheduled_scrape()
        except Exception as e:
            print(f'[scheduler] error: {e}', flush=True)


# ── Auth ──────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def is_valid_email(username):
    return bool(EMAIL_RE.match(username))


def validate_password(password):
    """Return an error string, or None when the password is acceptable."""
    if not password:
        return 'Password required'
    if len(password) < MIN_PASSWORD_LENGTH:
        return f'Password must be at least {MIN_PASSWORD_LENGTH} characters'
    return None


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
        raise ValueError(f'Username must be a valid email address: {username!r}')
    err = validate_password(password)
    if err:
        raise ValueError(err)
    pw_hash = generate_password_hash(password)
    users = load_users()
    users[username] = pw_hash
    AUTH_FILE.write_text(''.join(f'{u}:{h}\n' for u, h in users.items()))
    AUTH_FILE.chmod(0o600)


def _is_legacy_sha256(pw_hash):
    return len(pw_hash) == 64 and all(c in '0123456789abcdef' for c in pw_hash)


def check_auth(username, password):
    users = load_users()
    stored = users.get(username)
    if stored is None:
        # Spend comparable time on unknown users so response timing doesn't
        # reveal which addresses have accounts.
        check_password_hash(generate_password_hash('placeholder'), password)
        return False
    if _is_legacy_sha256(stored):
        if not secrets.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored):
            return False
        try:  # upgrade to a salted hash on successful login
            save_user(username, password)
        except ValueError:
            pass  # legacy password shorter than the current minimum — let them in, once
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
    """Return the inbox to send from, creating one on first use (idempotent)."""
    global _agentmail_inbox_id
    if _agentmail_inbox_id:
        return _agentmail_inbox_id
    client = get_agentmail_client()
    if client is None:
        return None
    inbox = client.inboxes.create(client_id='plate-magnet-dashboard')
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


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_reset_token(username):
    """Issue a reset token. Only its hash is stored, so a leaked token file
    can't be used to reset anyone's password."""
    now = time.time()
    tokens = {t: v for t, v in load_reset_tokens().items() if v['expires'] > now}
    token = secrets.token_urlsafe(32)
    tokens[_hash_token(token)] = {'username': username, 'expires': now + RESET_TOKEN_TTL}
    save_reset_tokens(tokens)
    return token


def peek_reset_token(token):
    """Return the username for a still-valid token without consuming it."""
    entry = load_reset_tokens().get(_hash_token(token or ''))
    if not entry or entry['expires'] < time.time():
        return None
    return entry['username']


def consume_reset_token(token):
    """Validate and burn a reset token, returning the username or None."""
    tokens = load_reset_tokens()
    entry = tokens.pop(_hash_token(token or ''), None)
    save_reset_tokens(tokens)
    if not entry or entry['expires'] < time.time():
        return None
    return entry['username']


def send_reset_email(to_email, reset_url):
    client = get_agentmail_client()
    inbox_id = get_agentmail_inbox_id()
    if not client or not inbox_id:
        print(f'[password reset] AGENTMAIL_API_KEY not configured — '
              f'reset link for {to_email}: {reset_url}')
        return
    client.inboxes.messages.send(
        inbox_id,
        to=to_email,
        subject='Reset your Plate Magnet password',
        text=(f'Click the link below to reset your Plate Magnet password. '
              f'This link expires in 30 minutes.\n\n{reset_url}\n\n'
              f"If you didn't request this, you can ignore this email."),
        html=(f'<p>Click the link below to reset your Plate Magnet password. '
              f'This link expires in 30 minutes.</p>'
              f'<p><a href="{reset_url}">{reset_url}</a></p>'
              f"<p>If you didn't request this, you can ignore this email.</p>"),
    )


def require_login(f):
    """Redirect unauthenticated users to the login page."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


def setup_auth():
    import getpass
    if AUTH_FILE.exists() and load_users():
        print(f'Auth file exists at {AUTH_FILE}')
        if input('Add another user? (y/N): ').lower() != 'y':
            return
    print('Create a dashboard user')
    username = input('Email: ').strip()
    if not username:
        print('Email required')
        return
    if not is_valid_email(username):
        print('Username must be a valid email address')
        return
    password = getpass.getpass('Password: ')
    err = validate_password(password)
    if err:
        print(err)
        return
    if password != getpass.getpass('Confirm password: '):
        print("Passwords don't match")
        return
    save_user(username, password)
    print(f"User '{username}' created")
    print(f'Auth file: {AUTH_FILE}')


# ── Rate Limiting ────────────────────────────────────────────────────────
# In-process, per-worker sliding-window limiter. Good enough to blunt
# brute-force and reset-email spam at this traffic level; not shared across
# gunicorn workers.

_rate_limit_lock = threading.Lock()
_rate_limit_buckets = defaultdict(list)


def rate_limited(key, max_attempts, window_seconds):
    """Record an attempt for `key`; True if it exceeded max_attempts in the window."""
    now = time.time()
    with _rate_limit_lock:
        attempts = [t for t in _rate_limit_buckets[key] if t > now - window_seconds]
        attempts.append(now)
        _rate_limit_buckets[key] = attempts
        if len(_rate_limit_buckets) > 10000:  # bound memory against IP churn
            for k in [k for k, v in _rate_limit_buckets.items()
                      if not v or v[-1] < now - window_seconds][:5000]:
                del _rate_limit_buckets[k]
        return len(attempts) > max_attempts


def client_ip():
    """The client address, trusting only proxies we've been told about.

    ProxyFix has already resolved remote_addr from the rightmost
    TRUSTED_PROXY_HOPS entries of X-Forwarded-For. Reading the raw header here
    (the old behaviour) let a client claim any address it liked and sail past
    the login rate limit.
    """
    return request.remote_addr or 'unknown'


# ── Login & Password Routes ───────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        if rate_limited(f'login:{client_ip()}', max_attempts=10, window_seconds=300):
            return jsonify({'error': 'Too many login attempts. '
                                     'Try again in a few minutes.'}), 429
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if check_auth(username, password):
            session.clear()          # rotate the session id on privilege change
            session.permanent = True
            session['user'] = username
            csrf_token()
            return jsonify({'ok': True})
        return jsonify({'error': 'Invalid username or password'}), 401
    if 'user' in session:
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        if rate_limited(f'forgot:{client_ip()}', max_attempts=5, window_seconds=300):
            return jsonify({'error': 'Too many requests. Try again in a few minutes.'}), 429
        username = request.form.get('username', '').strip()
        generic = {'message': 'If that account exists, a reset link is on its way.'}
        # The per-email throttle is silent (still returns `generic`) so it can't
        # be used to enumerate accounts.
        if (is_valid_email(username) and username in load_users()
                and not rate_limited(f'forgot-email:{username}',
                                     max_attempts=3, window_seconds=900)):
            token = create_reset_token(username)
            reset_url = url_for('reset_password', token=token, _external=True)
            try:
                send_reset_email(username, reset_url)
            except Exception as e:
                print(f'[password reset] send failed for {username}: {e}', flush=True)
        return jsonify(generic)
    return render_template('forgot.html', notice=None)


@app.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    token = request.args.get('token', '')
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if password != confirm:
            return jsonify({'error': "Passwords don't match"}), 400
        err = validate_password(password)
        if err:
            return jsonify({'error': err}), 400
        # Check the token only after validation so a bad password doesn't burn it.
        if not peek_reset_token(token):
            return jsonify({'error': 'This reset link is invalid or has expired'}), 400
        username = consume_reset_token(token)
        if not username:
            return jsonify({'error': 'This reset link is invalid or has expired'}), 400
        save_user(username, password)
        return jsonify({'ok': True})

    if not peek_reset_token(token):
        return render_template('forgot.html',
                               notice='This reset link is invalid or has expired. '
                                      'Request a new one below.')
    return render_template('reset.html', min_password_length=MIN_PASSWORD_LENGTH)


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('404.html'), 404


@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'Request too large'}), 413


# ── API Routes ────────────────────────────────────────────────────────────

@app.route('/')
@require_login
def index():
    return render_template('dashboard.html')


# /api/products aggregates the whole of price_history, which is expensive and
# identical for every user, so the result is memoised briefly. Scrapes run at
# most every few hours; a two-minute window costs nothing in freshness.
_products_cache = {'body': None, 'at': 0.0, 'etag': ''}
_products_cache_lock = threading.Lock()


def _build_products_payload():
    db = get_db()
    rows = db.query(f"""
        WITH latest AS (
            SELECT ph.product_id, ph.price, ph.price_text, ph.scraped_at, ph.source_url
            FROM price_history ph
            JOIN (
                SELECT product_id, MAX(scraped_at) AS scraped_at
                FROM price_history GROUP BY product_id
            ) m ON m.product_id = ph.product_id AND m.scraped_at = ph.scraped_at
        ),
        stats AS (
            SELECT product_id,
                   {db.round('AVG(price)', 2)} AS avg_price,
                   {db.round('MIN(price)', 2)} AS min_price,
                   {db.round('MAX(price)', 2)} AS max_price,
                   {db.round('MAX(price) - MIN(price)', 2)} AS price_range,
                   COUNT(*) AS history_count
            FROM price_history
            GROUP BY product_id
        )
        SELECT p.id, p.site, p.name, p.category, p.group_name, p.currency,
               p.url, p.image_url,
               l.price, l.price_text, l.scraped_at,
               s.avg_price, s.min_price, s.max_price, s.price_range, s.history_count
        FROM products p
        LEFT JOIN latest l ON l.product_id = p.id
        LEFT JOIN stats s ON s.product_id = p.id
        ORDER BY p.site, p.name
    """)
    for r in rows:
        r['scraped_at'] = _fmt_ts(r.get('scraped_at'))
        avg, price = r.get('avg_price'), r.get('price')
        # A "deal" is only meaningful once there's history to compare against;
        # with a single data point the average IS the current price.
        if avg and price and avg > 0 and (r.get('history_count') or 0) >= 2:
            pct = round((1 - price / avg) * 100, 1)
            r['deal_pct'] = pct if pct > 0 else 0
        else:
            r['deal_pct'] = 0
    return rows


@app.route('/api/products')
@require_login
def api_products():
    """All products with latest price, historical stats, and deal detection."""
    now = time.time()
    with _products_cache_lock:
        fresh = (_products_cache['body'] is not None
                 and now - _products_cache['at'] < PRODUCTS_CACHE_SECONDS)
        if not fresh:
            body = json.dumps(_build_products_payload(), default=str)
            _products_cache.update(body=body, at=now,
                                   etag='W/"%s"' % hashlib.sha256(
                                       body.encode()).hexdigest()[:32])
        body, etag = _products_cache['body'], _products_cache['etag']

    if request.headers.get('If-None-Match') == etag:
        return '', 304
    response = app.response_class(body, mimetype='application/json')
    response.headers['ETag'] = etag
    response.headers['Cache-Control'] = f'private, max-age={int(PRODUCTS_CACHE_SECONDS)}'
    return response


@app.route('/api/product/<int:pid>')
@require_login
def api_product(pid):
    """Single product detail with full price history."""
    db = get_db()
    prod = db.query_one("""
        SELECT p.*, ph.price, ph.price_text, ph.source_url
        FROM products p
        LEFT JOIN price_history ph ON ph.product_id = p.id
            AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history
                                 WHERE product_id = p.id)
        WHERE p.id = ?
    """, (pid,))
    if not prod:
        return jsonify({'error': 'not found'}), 404

    stats = db.query_one(f"""
        SELECT {db.round('AVG(price)', 2)} AS avg_price,
               {db.round('MIN(price)', 2)} AS min_price,
               {db.round('MAX(price)', 2)} AS max_price,
               COUNT(*) AS history_count
        FROM price_history WHERE product_id = ?
    """, (pid,))
    history = db.query("""
        SELECT price, price_text, scraped_at, source_url
        FROM price_history
        WHERE product_id = ?
        ORDER BY scraped_at DESC
        LIMIT 100
    """, (pid,))
    for h in history:
        h['scraped_at'] = _fmt_ts(h.get('scraped_at'))

    deal_pct = 0
    if (prod.get('price') and stats and stats.get('avg_price')
            and stats['avg_price'] > 0 and (stats.get('history_count') or 0) >= 2):
        pct = round((1 - prod['price'] / stats['avg_price']) * 100, 1)
        deal_pct = pct if pct > 0 else 0

    try:
        matches = db.query("""
            SELECT p.id, p.site, p.name, p.category, p.url,
                   ph.price, ph.price_text, pm.similarity
            FROM product_matches pm
            JOIN products p ON p.id = pm.matched_product_id
            LEFT JOIN price_history ph ON ph.product_id = p.id
                AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history
                                     WHERE product_id = p.id)
            WHERE pm.product_id = ?
            ORDER BY pm.similarity DESC
            LIMIT 5
        """, (pid,))
    except Exception:
        db.rollback()
        matches = []

    for key in ('first_seen', 'last_seen'):
        if key in prod:
            prod[key] = _fmt_ts(prod[key])

    return jsonify({**prod, **(stats or {}), 'deal_pct': deal_pct,
                    'history': history, 'matches': matches})


@app.route('/api/meta')
@require_login
def api_meta():
    db = get_db()
    stores = db.query('SELECT DISTINCT site FROM products ORDER BY site')
    cats = db.query("""SELECT DISTINCT category FROM products
                       WHERE category IS NOT NULL AND category != ''
                       ORDER BY category""")
    counts = {r['group_name']: r['c'] for r in db.query(
        """SELECT COALESCE(group_name, 'Other') AS group_name, COUNT(*) AS c
           FROM products GROUP BY COALESCE(group_name, 'Other')""")}
    totals = db.query_one("""
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT site) AS stores,
               COUNT(DISTINCT category) AS cats
        FROM products
    """)
    last = db.query_one('SELECT MAX(scraped_at) AS last FROM price_history')
    # Canonical order, and only groups that actually hold products.
    groups = [{'name': name, 'icon': CATEGORY_ICONS.get(name, '•'),
               'count': counts.get(name, 0)}
              for name in CANONICAL_CATEGORIES if counts.get(name)]
    return jsonify({
        'stores': [s['site'] for s in stores],
        'categories': [c['category'] for c in cats],
        'groups': groups,
        'total_products': totals['total'] if totals else 0,
        'total_stores': totals['stores'] if totals else 0,
        'category_count': totals['cats'] if totals else 0,
        'last_scrape': _fmt_ts(last['last']) if last else None,
    })


@app.route('/api/me')
@require_login
def api_me():
    return jsonify({'user': session.get('user', '')})


def _fmt_ts(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v[:19].replace('T', ' ')
    return v.strftime('%Y-%m-%d %H:%M:%S')


@app.route('/api/scrape', methods=['POST'])
@require_login
def api_scrape_trigger():
    if not DATABASE_URL and not DB_PATH.exists():
        return jsonify({'error': 'No database available to scrape into'}), 400
    if rate_limited(f'scrape:{client_ip()}', max_attempts=5, window_seconds=3600):
        return jsonify({'error': 'Too many scrape requests. Try again later.'}), 429

    db = get_db()
    reap_stale_runs(db)
    running = db.query_one("SELECT id FROM scrape_runs WHERE status = 'running' LIMIT 1")
    if running:
        return jsonify({'error': 'A scrape is already running',
                        'run_id': running['id']}), 409
    body = request.get_json(silent=True) or {}
    run_id = launch_scrape(trigger='manual', http_only=bool(body.get('http_only')), db=db)
    with _products_cache_lock:
        _products_cache['body'] = None
    return jsonify({'run_id': run_id})


@app.route('/api/scrape/runs')
@require_login
def api_scrape_runs():
    db = get_db()
    reap_stale_runs(db)
    rows = db.query("""
        SELECT id, started_at, finished_at, status, trigger, http_only,
               products_scraped, error
        FROM scrape_runs ORDER BY started_at DESC LIMIT 30
    """)
    for r in rows:
        r['started_at'] = _fmt_ts(r['started_at'])
        r['finished_at'] = _fmt_ts(r['finished_at'])
    return jsonify({'runs': rows,
                    'auto_scrape': RUN_SCHEDULER and bool(DATABASE_URL),
                    'interval_hours': SCRAPE_INTERVAL_HOURS})


@app.route('/favicon.ico')
def favicon():
    return redirect(url_for('static', filename='favicon.svg'))


@app.route('/healthz')
def healthz():
    """Liveness + DB reachability, for uptime checks. No auth (no data exposed)."""
    try:
        with connect() as db:
            db.query_one('SELECT 1 AS ok')
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'degraded', 'error': str(e)[:200]}), 503


# ── Startup ───────────────────────────────────────────────────────────────

def start_background_workers():
    bootstrap_schema()
    reap_stale_runs()
    if DATABASE_URL and RUN_SCHEDULER:
        threading.Thread(target=_scheduler_loop, daemon=True).start()
        print(f'[scheduler] enabled, every {SCRAPE_INTERVAL_HOURS}h', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Plate Magnet - Equipment Price Dashboard')
    parser.add_argument('--host', default='0.0.0.0' if DATABASE_URL else '127.0.0.1')
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8080)))
    parser.add_argument('--debug', action='store_true', help='Debug mode')
    parser.add_argument('--setup-auth', action='store_true',
                        help='Create/update dashboard users')
    parser.add_argument('--workers', type=int,
                        default=int(os.environ.get('WEB_CONCURRENCY', '2')))
    args = parser.parse_args()

    if args.setup_auth:
        setup_auth()
        return

    if not DATABASE_URL and not DB_PATH.exists():
        print(f'Error: no database found at {DB_PATH}')
        print('Run a scrape first:  python scraper/run_scrape.py')
        sys.exit(1)

    if not AUTH_FILE.exists() or not load_users():
        print('Error: no dashboard users configured. '
              'Run:  python dashboard.py --setup-auth')
        sys.exit(1)

    start_background_workers()

    db_source = 'PostgreSQL' if DATABASE_URL else f'SQLite ({DB_PATH})'
    print('🏋️  Plate Magnet Dashboard')
    print(f'   Database: {db_source}')
    print(f'   URL:      http://{args.host}:{args.port}')
    print(f'   Auth:     enabled (credentials in {AUTH_FILE})')

    if DATABASE_URL and not args.debug:
        try:
            from gunicorn.app.wsgiapp import run
            sys.argv = ['gunicorn', 'dashboard:app', '-b', f'{args.host}:{args.port}',
                        '--workers', str(args.workers), '--threads', '4',
                        '--access-logfile', '-', '--error-logfile', '-']
            run()
            return
        except ImportError:
            print('   (gunicorn not installed — falling back to the dev server)')
    app.run(host=args.host, port=args.port, debug=args.debug)


# Under gunicorn, main() never runs — start the workers at import time instead.
if os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn'):
    start_background_workers()

if __name__ == '__main__':
    main()
