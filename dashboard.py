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
import sqlite3
import hashlib
import argparse
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, g, request, jsonify, render_template_string, Response

# ── Config ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / 'equipment_data' / 'equipment.db'
DATABASE_URL = os.environ.get('DATABASE_URL', '')
AUTH_FILE = Path.home() / '.equipment_dashboard_auth'

app = Flask(__name__)


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
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    users = load_users()
    users[username] = pw_hash
    AUTH_FILE.write_text(''.join(f"{u}:{h}\n" for u, h in users.items()))
    AUTH_FILE.chmod(0o600)


def check_auth(username, password):
    users = load_users()
    if username in users:
        return hashlib.sha256(password.encode()).hexdigest() == users[username]
    return False


def authenticate():
    return Response('Authentication required', 401,
                    {'WWW-Authenticate': 'Basic realm="LiftTracker Dashboard"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


def setup_auth():
    if AUTH_FILE.exists() and load_users():
        print(f"Auth file exists at {AUTH_FILE}")
        yn = input("Add another user? (y/N): ")
        if yn.lower() != 'y':
            return
    print("Create a dashboard user")
    username = input("Username: ").strip()
    if not username:
        print("Username required")
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


# ── HTML Template ─────────────────────────────────────────────────────────

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LiftTracker — Equipment Price Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #f5f5f5; color: #222; }
header { background: #1a1a2e; color: #eee; padding: 20px 24px; display: flex;
         justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
header h1 { font-size: 20px; font-weight: 600; }
header h1 span { color: #4fc3f7; }
header .stats { font-size: 13px; color: #aaa; }
header .stats strong { color: #eee; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px 16px; }
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px; align-items: center; }
.filters select, .filters input { padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px;
    font-size: 14px; background: #fff; min-width: 140px; }
.filters input[type=search] { min-width: 200px; flex: 1; }
.filters .badge { background: #e3f2fd; color: #1565c0; padding: 4px 10px; border-radius: 20px;
    font-size: 12px; font-weight: 600; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
           gap: 12px; margin-bottom: 20px; }
.metric { background: #fff; border-radius: 10px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.metric .label { font-size: 12px; color: #666; text-transform: uppercase; letter-spacing: .5px; }
.metric .value { font-size: 22px; font-weight: 700; margin-top: 4px; }
.metric .sub { font-size: 12px; color: #888; margin-top: 2px; }
.table-wrap { background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
              overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #f8f9fa; padding: 10px 12px; text-align: left; font-weight: 600;
     font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: #555;
     border-bottom: 2px solid #e0e0e0; cursor: pointer; white-space: nowrap; }
th:hover { color: #1565c0; }
td { padding: 9px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }
tr:hover { background: #fafbff; }
.store-badge { display: inline-block; padding: 2px 8px; border-radius: 4px;
               font-size: 11px; font-weight: 600; color: #fff; }
.price { font-weight: 700; font-size: 14px; }
.price.down { color: #2e7d32; }
.price.up { color: #c62828; }
.deal-tag { display: inline-block; background: #e8f5e9; color: #2e7d32; padding: 2px 8px;
            border-radius: 4px; font-size: 11px; font-weight: 700; }
.paginate { display: flex; justify-content: space-between; align-items: center;
            padding: 12px 16px; font-size: 13px; color: #555; }
.paginate a { color: #1565c0; text-decoration: none; padding: 4px 12px;
              border: 1px solid #ddd; border-radius: 4px; }
.paginate a:hover { background: #e3f2fd; }
.paginate a.disabled { color: #ccc; pointer-events: none; }
.modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.4);
                 z-index: 100; }
.modal { display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%);
         background: #fff; border-radius: 12px; padding: 24px; z-index: 101;
         max-width: 700px; width: 90%; max-height: 85vh; overflow-y: auto;
         box-shadow: 0 8px 32px rgba(0,0,0,.2); }
.modal.active, .modal-overlay.active { display: block; }
.modal h2 { font-size: 18px; margin-bottom: 4px; }
.modal .sub { color: #666; font-size: 14px; margin-bottom: 16px; }
.modal .close { float: right; cursor: pointer; font-size: 20px; color: #999; }
.modal .close:hover { color: #333; }
.history-table { width: 100%; font-size: 13px; margin-top: 12px; }
.history-table th { font-size: 11px; }
#priceChart { max-height: 250px; margin: 16px 0; }
.deal-info { background: #e8f5e9; border: 1px solid #a5d6a7; border-radius: 8px;
             padding: 12px 16px; margin-bottom: 16px; }
.deal-info .pct { font-size: 24px; font-weight: 700; color: #2e7d32; }
@media (max-width: 768px) {
    header { flex-direction: column; align-items: flex-start; }
    .filters input[type=search] { min-width: auto; width: 100%; }
}
</style>
</head>
<body>
<header>
  <div><h1>🏋️ <span>LiftTracker</span> — Equipment Price Dashboard</h1></div>
  <div class="stats">
    <strong id="totalProducts">—</strong> products ·
    <strong id="totalStores">—</strong> stores ·
    last scrape: <strong id="lastScrape">—</strong>
  </div>
</header>
<div class="container">
  <div class="metrics" id="metrics"></div>
  <div class="filters">
    <select id="filterStore" onchange="applyFilters()"><option value="">All Stores</option></select>
    <select id="filterCategory" onchange="applyFilters()"><option value="">All Categories</option></select>
    <select id="filterDeal" onchange="applyFilters()">
      <option value="">All Products</option>
      <option value="deal">🔥 Deals Only</option>
    </select>
    <input type="search" id="searchBox" placeholder="Search products..." oninput="applyFilters()">
    <span class="badge" id="resultCount">0 results</span>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th onclick="sortBy('name')">Product <span id="s-name">↓</span></th>
          <th onclick="sortBy('site')">Store <span id="s-site">↓</span></th>
          <th onclick="sortBy('category')">Category <span id="s-category">↓</span></th>
          <th onclick="sortBy('price')">Price <span id="s-price">↓</span></th>
          <th onclick="sortBy('deal')">Deal <span id="s-deal">↓</span></th>
          <th>History</th>
        </tr>
      </thead>
      <tbody id="productBody"></tbody>
    </table>
    <div class="paginate">
      <span id="pageInfo">Page 1 of 1</span>
      <div>
        <a href="#" id="prevPage" onclick="goPage(-1)">← Prev</a>
        <a href="#" id="nextPage" onclick="goPage(1)">Next →</a>
      </div>
    </div>
  </div>
</div>
<div class="modal-overlay" id="modalOverlay" onclick="closeModal()"></div>
<div class="modal" id="detailModal">
  <span class="close" onclick="closeModal()">✕</span>
  <h2 id="detailName"></h2>
  <div class="sub" id="detailStore"></div>
  <div id="dealInfo" class="deal-info" style="display:none"></div>
  <canvas id="priceChart"></canvas>
  <table class="history-table">
    <thead><tr><th>Date</th><th>Price</th><th>Source</th></tr></thead>
    <tbody id="historyBody"></tbody>
  </table>
  <div id="matchesSection" style="margin-top:16px;display:none">
    <h3 style="font-size:14px;margin-bottom:8px;color:#555">🔗 Similar from other stores</h3>
    <table class="history-table">
      <thead><tr><th>Store</th><th>Product</th><th>Price</th><th>Match</th></tr></thead>
      <tbody id="matchesBody"></tbody>
    </table>
  </div>
</div>
<script>
let allProducts = [], stores = [], categories = [], currentPage = 1, pageSize = 50;
let sortField = 'price', sortDir = 'asc', chartInstance = null;
const STORE_COLORS = {
  "Rogue Fitness":"#c62828","EliteFTS":"#1565c0","REP Fitness":"#2e7d32",
  "Titan Fitness":"#e65100","Bells of Steel":"#6a1b9a","LiftingLarge":"#00838f",
  "Weightlifting House":"#37474f","LUXIAOJUN":"#ad1457","TYR Sport":"#1a237e",
  "SBD Apparel":"#263238","Onyx Straps":"#4e342e","2POOD":"#01579b",
  "American Barbell":"#b71c1c","Fringe Sport":"#f57f17","Cerberus Strength":"#1b5e20",
  "Pioneer Fitness":"#3e2723","Hookgrip":"#4a148c","NoBull":"#212121",
  "Mark Bell":"#bf360c","Slingshot":"#004d40","Force USA":"#827717",
  "Get Rx'd":"#0d47a1","Virus International":"#37474f","Born Primitive":"#4e342e",
  "Gymreapers":"#e65100","Again Faster":"#00838f","Inzer Advance Designs":"#1a237e",
};
function storeColor(s) { return STORE_COLORS[s]||'#'+Math.floor(Math.random()*0xFFFFFF).toString(16).padStart(6,'0'); }
async function loadData() {
  const [pr, mr] = await Promise.all([fetch('/api/products'), fetch('/api/meta')]);
  allProducts = await pr.json(); const meta = await mr.json();
  stores = meta.stores; categories = meta.categories;
  document.getElementById('totalProducts').textContent = meta.total_products;
  document.getElementById('totalStores').textContent = meta.total_stores;
  document.getElementById('lastScrape').textContent = meta.last_scrape||'never';
  const ss = document.getElementById('filterStore');
  stores.forEach(s => { let o = document.createElement('option'); o.value = s; o.text = s; ss.appendChild(o); });
  const cs = document.getElementById('filterCategory');
  categories.forEach(c => { let o = document.createElement('option'); o.value = c; o.text = c; cs.appendChild(o); });
  const prices = allProducts.filter(p=>p.price).map(p=>p.price);
  const avg = prices.length ? (prices.reduce((a,b)=>a+b,0)/prices.length) : 0;
  const mn = prices.length ? Math.min(...prices) : 0;
  const mx = prices.length ? Math.max(...prices) : 0;
  const deals = allProducts.filter(p=>p.deal_pct&&p.deal_pct>10);
  document.getElementById('metrics').innerHTML =
    `<div class="metric"><div class="label">Avg Price</div><div class="value">$${avg.toFixed(2)}</div></div>
     <div class="metric"><div class="label">Price Range</div><div class="value">$${mn.toFixed(2)} – $${mx.toFixed(2)}</div></div>
     <div class="metric"><div class="label">Categories</div><div class="value">${meta.category_count}</div></div>
     <div class="metric"><div class="label">🔥 Deals</div><div class="value">${deals.length}</div><div class="sub">>10% below avg</div></div>`;
  render();
}
function getFiltered() {
  const store = document.getElementById('filterStore').value;
  const cat = document.getElementById('filterCategory').value;
  const deal = document.getElementById('filterDeal').value;
  const q = document.getElementById('searchBox').value.toLowerCase().trim();
  let prods = allProducts.filter(p => {
    if(store && p.site!==store) return false;
    if(cat && p.category!==cat) return false;
    if(deal==='deal' && (!p.deal_pct||p.deal_pct<=10)) return false;
    if(q && !p.name.toLowerCase().includes(q) && !p.site.toLowerCase().includes(q)) return false;
    return true;
  });
  prods.sort((a,b)=>{
    let va,vb;
    if(sortField==='name'){va=a.name.toLowerCase();vb=b.name.toLowerCase();}
    else if(sortField==='site'){va=a.site;vb=b.site;}
    else if(sortField==='category'){va=a.category||'';vb=b.category||'';}
    else if(sortField==='price'){va=a.price||999999;vb=b.price||999999;}
    else if(sortField==='deal'){va=-(a.deal_pct||0);vb=-(b.deal_pct||0);}
    else{va=a.name.toLowerCase();vb=b.name.toLowerCase();}
    return va<vb?sortDir==='asc'?-1:1:va>vb?sortDir==='asc'?1:-1:0;
  });
  return prods;
}
function render() {
  const prods = getFiltered();
  const totalPages = Math.ceil(prods.length/pageSize)||1;
  if(currentPage>totalPages) currentPage=totalPages;
  const start=(currentPage-1)*pageSize;
  const page = prods.slice(start,start+pageSize);
  const body = document.getElementById('productBody');
  body.innerHTML = page.map(p=>{
    const c = storeColor(p.site);
    const db = p.deal_pct&&p.deal_pct>10?`<span class="deal-tag">-${p.deal_pct.toFixed(0)}%</span>`:'';
    const pc = p.deal_pct&&p.deal_pct>10?'price down':'price';
    return `<tr><td><a href="javascript:void(0)" onclick="showDetail(${p.id})">${esc(p.name)}</a></td>
      <td><span class="store-badge" style="background:${c}">${esc(p.site)}</span></td>
      <td>${esc(p.category||'-')}</td>
      <td class="${pc}">${p.price_text||'-'}</td>
      <td>${db}</td>
      <td><a href="javascript:void(0)" onclick="showDetail(${p.id})">📈</a></td></tr>`;
  }).join('');
  document.getElementById('resultCount').textContent = `${prods.length} results`;
  document.getElementById('pageInfo').textContent = `Page ${currentPage} of ${totalPages}`;
  document.getElementById('prevPage').className = currentPage<=1?'disabled':'';
  document.getElementById('nextPage').className = currentPage>=totalPages?'disabled':'';
  ['name','site','category','price','deal'].forEach(f=>{
    document.getElementById('s-'+f).textContent=sortField===f?(sortDir==='asc'?'↑':'↓'):'↓';
  });
}
function applyFilters(){currentPage=1;render();}
function goPage(d){const t=Math.ceil(getFiltered().length/pageSize)||1;const n=currentPage+d;if(n>=1&&n<=t){currentPage=n;render();}}
function sortBy(f){if(sortField===f)sortDir=sortDir==='asc'?'desc':'asc';else{sortField=f;sortDir='asc';}render();}
function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
async function showDetail(id) {
  const r=await fetch(`/api/product/${id}`);const prod=await r.json();
  document.getElementById('detailName').textContent=prod.name;
  document.getElementById('detailStore').textContent=`${prod.site} · ${prod.category||'No category'}`+(prod.price_text?` · Current: ${prod.price_text}`:'');
  const di=document.getElementById('dealInfo');
  if(prod.deal_pct&&prod.deal_pct>5){
    di.style.display='block';
    di.innerHTML=`<div class="pct">🔥 ${prod.deal_pct.toFixed(0)}% below average</div>
      <div style="margin-top:4px">Current: ${prod.price_text} vs avg: $${(prod.avg_price||0).toFixed(2)}
      ${prod.min_price?` · Lowest: $${prod.min_price.toFixed(2)}`:''}
      ${prod.max_price?` · Highest: $${prod.max_price.toFixed(2)}`:''}</div>`;
  } else di.style.display='none';
  const hist=prod.history||[];
  const ctx=document.getElementById('priceChart').getContext('2d');
  if(chartInstance)chartInstance.destroy();
  chartInstance=new Chart(ctx,{
    type:'line',
    data:{labels:hist.map(h=>h.date.slice(0,10)),datasets:[{label:prod.name.slice(0,35),data:hist.map(h=>h.price),borderColor:'#1565c0',backgroundColor:'rgba(21,101,192,0.1)',fill:true,tension:0.3,pointRadius:4,pointHoverRadius:7}]},
    options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{ticks:{callback:v=>'$'+v.toFixed(2)}}}}
  });
  document.getElementById('historyBody').innerHTML=hist.map(h=>{
    const l=h.source_url?`<a href="${esc(h.source_url)}" target="_blank">view</a>`:'';
    return `<tr><td>${h.date.slice(0,19).replace('T',' ')}</td><td>$${h.price.toFixed(2)}</td><td>${l}</td></tr>`;
  }).join('');

  // Matches
  const ms=document.getElementById('matchesSection');
  const mb=document.getElementById('matchesBody');
  if(prod.matches&&prod.matches.length){
    ms.style.display='block';
    mb.innerHTML=prod.matches.map(m=>{
      const c=storeColor(m.site);
      const link=m.url?`<a href="${esc(m.url)}" target="_blank">${esc(m.name)}</a>`:esc(m.name);
      return `<tr><td><span class="store-badge" style="background:${c}">${esc(m.site)}</span></td>
        <td>${link}</td><td class="price">${m.price_text||'-'}</td>
        <td>${m.similarity.toFixed(0)}%</td></tr>`;
    }).join('');
  } else {
    ms.style.display='none';
  }
  document.getElementById('detailModal').classList.add('active');
  document.getElementById('modalOverlay').classList.add('active');
}
function closeModal(){document.getElementById('detailModal').classList.remove('active');document.getElementById('modalOverlay').classList.remove('active');if(chartInstance){chartInstance.destroy();chartInstance=null;}}
loadData();
</script>
</body>
</html>
"""


# ── API Routes ────────────────────────────────────────────────────────────

@app.route('/')
@requires_auth
def index():
    return render_template_string(HTML)


@app.route('/api/products')
@requires_auth
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
@requires_auth
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
@requires_auth
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

    # Auto-create default admin/admin on first run (Replit/ephemeral friendly)
    if not AUTH_FILE.exists():
        save_user('admin', 'admin')
        print("   Created default credentials: admin / admin")

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