let allProducts = [], stores = [], categories = [], groups = [];
let currentPage = 1, pageSize = 50;
let sortField = 'deal', sortDir = 'desc', chartInstance = null;
let onlyDeals = false;
// The canonical category currently being browsed ('' = everything).
let activeGroup = '';

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

// ── Category rail ────────────────────────────────────────────────────────
// The rail is the primary navigation: pick a category, and the table renders
// only that slice. Search still spans the whole catalogue (see getFiltered).

function renderRail() {
  const list = document.getElementById('railList');
  const total = allProducts.length;

  const row = (value, icon, name, count) => `
    <button class="rail-item${activeGroup === value ? ' active' : ''}"
            data-group="${esc(value)}" onclick="selectGroup('${esc(value).replace(/'/g, "\\'")}')"
            title="${esc(name)} — ${count.toLocaleString()} items">
      <span class="rail-icon">${icon}</span>
      <span class="rail-name">${esc(name)}</span>
      <span class="rail-count">${count.toLocaleString()}</span>
    </button>`;

  list.innerHTML =
    row('', '◆', 'All Equipment', total) +
    '<div class="rail-divider"></div>' +
    groups.map(g => row(g.name, g.icon || '•', g.name, g.count)).join('');
}

function selectGroup(value) {
  activeGroup = value === activeGroup ? '' : value;
  // A store label from a different category would leave zero results.
  document.getElementById('filterCategory').value = '';
  populateStoreLabels();
  currentPage = 1;
  syncHash();
  renderRail();
  render();
}

function toggleRail() {
  const ws = document.querySelector('.workspace');
  const collapsed = ws.classList.toggle('rail-collapsed');
  document.getElementById('railToggle').setAttribute('aria-expanded', String(!collapsed));
  try { localStorage.setItem('pm_rail_collapsed', collapsed ? '1' : '0'); } catch (e) {}
}

// Store labels are scoped to the active category so the dropdown only offers
// options that can actually return rows.
function populateStoreLabels() {
  const sel = document.getElementById('filterCategory');
  const pool = activeGroup
    ? allProducts.filter(p => (p.group_name || 'Other') === activeGroup)
    : allProducts;
  const labels = [...new Set(pool.map(p => p.category).filter(Boolean))].sort();
  const current = sel.value;
  sel.innerHTML = '<option value="">All Store Labels</option>';
  labels.forEach(c => {
    const o = document.createElement('option');
    o.value = c;
    o.textContent = c;
    sel.appendChild(o);
  });
  if (labels.includes(current)) sel.value = current;
}

// ── Deep linking ─────────────────────────────────────────────────────────
// The category lives in the URL hash so a filtered view can be bookmarked,
// shared, and survives a reload.

function syncHash() {
  const parts = [];
  if (activeGroup) parts.push('c=' + encodeURIComponent(activeGroup));
  const q = document.getElementById('searchBox').value.trim();
  if (q) parts.push('q=' + encodeURIComponent(q));
  const store = document.getElementById('filterStore').value;
  if (store) parts.push('s=' + encodeURIComponent(store));
  if (onlyDeals) parts.push('deals=1');
  const hash = parts.join('&');
  history.replaceState(null, '', hash ? '#' + hash : window.location.pathname);
}

function readHash() {
  const hash = (window.location.hash || '').replace(/^#/, '');
  if (!hash) return;
  const params = new URLSearchParams(hash);
  activeGroup = params.get('c') || '';
  if (params.get('q')) document.getElementById('searchBox').value = params.get('q');
  if (params.get('s')) document.getElementById('filterStore').value = params.get('s');
  onlyDeals = params.get('deals') === '1';
  if (onlyDeals) document.getElementById('chipDeals').classList.add('active');
}

function clearAllFilters() {
  activeGroup = '';
  onlyDeals = false;
  document.getElementById('searchBox').value = '';
  document.getElementById('filterStore').value = '';
  document.getElementById('filterCategory').value = '';
  document.getElementById('chipDeals').classList.remove('active');
  currentPage = 1;
  populateStoreLabels();
  syncHash();
  renderRail();
  render();
}

function changePageSize(value) {
  pageSize = parseInt(value, 10) || 50;
  currentPage = 1;
  try { localStorage.setItem('pm_page_size', String(pageSize)); } catch (e) {}
  render();
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
    groups = meta.groups || [];

    document.getElementById('metricTotalProds').textContent = (meta.total_products || allProducts.length).toLocaleString();
    document.getElementById('metricTotalStores').textContent = meta.total_stores || stores.length;
    document.getElementById('metricCategoryCount').textContent =
      `${groups.length} categories · ${meta.category_count || categories.length} store labels`;
    document.getElementById('lastScrape').textContent = meta.last_scrape || 'Active';

    // Populate dropdowns
    const ss = document.getElementById('filterStore');
    stores.forEach(s => {
      let o = document.createElement('option');
      o.value = s;
      o.textContent = s;
      ss.appendChild(o);
    });

    // Restore prior view, then build the rail and the scoped label dropdown.
    try {
      const savedSize = parseInt(localStorage.getItem('pm_page_size'), 10);
      if (savedSize) {
        pageSize = savedSize;
        document.getElementById('pageSizeSelect').value = String(savedSize);
      }
      if (localStorage.getItem('pm_rail_collapsed') === '1') {
        document.querySelector('.workspace').classList.add('rail-collapsed');
        document.getElementById('railToggle').setAttribute('aria-expanded', 'false');
      }
    } catch (e) { /* storage unavailable — defaults are fine */ }

    readHash();
    if (document.getElementById('filterStore').value === '' && stores.length) {
      document.getElementById('filterStore').options[0].textContent =
        `All Retailers (${stores.length})`;
    }
    renderRail();
    populateStoreLabels();

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

  // Every search term must appear somewhere in the row, so "rogue bar" narrows
  // rather than widening the way a single substring match would.
  const terms = q ? q.split(/\s+/).filter(Boolean) : [];

  let prods = allProducts.filter(p => {
    if (activeGroup && (p.group_name || 'Other') !== activeGroup) return false;
    if (store && p.site !== store) return false;
    if (cat && p.category !== cat) return false;
    if (onlyDeals && (!p.deal_pct || p.deal_pct <= 10)) return false;
    if (terms.length) {
      const haystack = [p.name, p.site, p.category, p.group_name]
        .filter(Boolean).join(' ').toLowerCase();
      if (!terms.every(t => haystack.includes(t))) return false;
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
      va = (a.group_name || 'Other') + (a.category || '');
      vb = (b.group_name || 'Other') + (b.category || '');
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
    const scope = activeGroup ? ` in ${esc(activeGroup)}` : '';
    body.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:44px;color:var(--text-dim);">
        <div style="font-size:15px;font-weight:600;color:var(--text-muted);margin-bottom:6px;">
          No equipment matches these filters${scope}.</div>
        <div style="font-size:13px;">Try a different category, or
          <button class="clear-filters" onclick="clearAllFilters()">clear all filters</button>
        </div>
      </td></tr>`;
  } else {
    body.innerHTML = page.map(p => {
      const c = storeColor(p.site);
      const hasDeal = p.deal_pct && p.deal_pct > 10;
      const dealBadge = hasDeal
        ? `<span class="deal-badge">🔥 -${p.deal_pct.toFixed(0)}%</span>`
        : `<span style="color:var(--text-dim);font-size:12px;">Normal</span>`;

      const priceClass = hasDeal ? 'price-box deal' : 'price-box';
      const formattedPrice = p.price ? `$${p.price.toFixed(2)}` : (p.price_text || '—');
      const thumb = p.image_url
        ? `<img class="prod-thumb" src="${safeUrl(p.image_url)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=prod-thumb-placeholder>📦</div>'">`
        : `<div class="prod-thumb-placeholder">📦</div>`;

      return `<tr>
        <td class="prod-thumb-col">${thumb}</td>
        <td class="prod-name-col">
          <a class="prod-link" onclick="showDetail(${p.id})">${esc(p.name)}</a>
        </td>
        <td>
          <span class="store-badge" style="background:${c}22;color:${c};border:1px solid ${c}55">
            ${esc(p.site)}
          </span>
        </td>
        <td><span class="cat-pill" title="${esc(p.category || '')}">${esc(p.group_name || 'Other')}</span></td>
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
  document.getElementById('pageInfo').textContent =
    `Page ${currentPage} of ${totalPages}` +
    (prods.length ? ` · ${(start + 1).toLocaleString()}–${Math.min(start + pageSize, prods.length).toLocaleString()} of ${prods.length.toLocaleString()}` : '');

  document.getElementById('activeCrumb').textContent = activeGroup || 'All Equipment';

  const store = document.getElementById('filterStore').value;
  const label = document.getElementById('filterCategory').value;
  const q = document.getElementById('searchBox').value.trim();
  const bits = [];
  if (store) bits.push(store);
  if (label) bits.push(`“${label}”`);
  if (q) bits.push(`matching “${q}”`);
  if (onlyDeals) bits.push('deals only');
  document.getElementById('filterSummary').textContent =
    bits.length ? bits.join(' · ')
                : (activeGroup ? `All ${activeGroup.toLowerCase()}` : 'Viewing all equipment');

  const anyFilter = !!(activeGroup || store || label || q || onlyDeals);
  document.getElementById('clearFilters').style.display = anyFilter ? '' : 'none';
  
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
  syncHash();
  render();
}

function goPage(d) {
  const t = Math.ceil(getFiltered().length / pageSize) || 1;
  const n = currentPage + d;
  if (n >= 1 && n <= t) {
    currentPage = n;
    render();
    const panel = document.querySelector('.table-panel');
    if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

// Product names, URLs and image URLs all come from third-party retailer sites,
// so everything interpolated into markup has to be escaped — including quotes,
// which the previous version missed while being used inside src="" and href=""
// attributes. That was a stored-XSS path straight from a scraped page.
function esc(s) {
  return (s == null ? '' : String(s))
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Only http(s) URLs may reach an href/src. A scraped `javascript:` URL would
// otherwise execute on click. sanitizeUrl returns the raw URL (for assigning to
// a DOM property); safeUrl additionally escapes it for interpolation into markup.
function sanitizeUrl(u) {
  if (!u) return '';
  try {
    const parsed = new URL(u, window.location.origin);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return '';
    return parsed.href;
  } catch (e) {
    return '';
  }
}

function safeUrl(u) {
  return esc(sanitizeUrl(u));
}

// Give the price axis a sensible window: pad a flat series so its ticks are
// distinct, and otherwise leave a little headroom around the observed range.
function priceAxisBounds(prices) {
  const valid = (prices || []).filter(v => typeof v === 'number' && isFinite(v));
  if (!valid.length) return {};
  const lo = Math.min(...valid), hi = Math.max(...valid);
  if (hi - lo < 0.01) {
    const pad = Math.max(1, Math.abs(hi) * 0.1);
    return {min: Math.max(0, lo - pad), max: hi + pad};
  }
  const pad = (hi - lo) * 0.15;
  return {min: Math.max(0, lo - pad), max: hi + pad};
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

    const hero = document.getElementById('detailHero');
    hero.innerHTML = prod.image_url
      ? `<img src="${safeUrl(prod.image_url)}" alt="" onerror="this.parentElement.innerHTML='<div class=detail-hero-placeholder>📦</div>'">`
      : `<div class="detail-hero-placeholder">📦</div>`;

    const extLink = document.getElementById('detailExternalLink');
    const productUrl = sanitizeUrl(prod.url);
    if (productUrl) {
      extLink.href = productUrl;
      extLink.style.display = 'inline-flex';
    } else {
      extLink.removeAttribute('href');
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
    document.getElementById('detailDataPointsCount').textContent =
      hist.length === 1 ? '1 data point recorded'
                        : `${hist.length} data points recorded`;

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
            // A flat or single-point series gave every gridline the same value
            // ("$9" eleven times) because the axis had no range to spread over.
            ...priceAxisBounds(chartPrices),
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: {
              color: '#94a3b8',
              font: { family: "'JetBrains Mono', monospace" },
              maxTicksLimit: 6,
              callback: v => '$' + (v >= 100 ? v.toFixed(0) : v.toFixed(2))
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
        const link = m.url ? `<a href="${safeUrl(m.url)}" target="_blank" rel="noopener noreferrer" style="color:#fff;text-decoration:none">${esc(m.name)} ↗</a>` : esc(m.name);
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
      const l = h.source_url ? `<a href="${safeUrl(h.source_url)}" target="_blank" rel="noopener noreferrer" style="color:var(--cyan);text-decoration:none">View Source ↗</a>` : '—';
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
  const headers = ['ID', 'Site', 'Name', 'Category', 'Store Label', 'Price',
                   'Avg Price', 'Deal Pct', 'URL'];
  const rows = prods.map(p => [
    p.id,
    `"${(p.site || '').replace(/"/g, '""')}"`,
    `"${(p.name || '').replace(/"/g, '""')}"`,
    `"${(p.group_name || 'Other').replace(/"/g, '""')}"`,
    `"${(p.category || '').replace(/"/g, '""')}"`,
    p.price || 0,
    p.avg_price || 0,
    p.deal_pct || 0,
    `"${(p.url || '').replace(/"/g, '""')}"`
  ]);

  // A Blob, not a data: URI — exporting the full catalogue produces ~1 MB of
  // CSV, which exceeds the data: URL length several browsers accept.
  // \ufeff is a BOM so Excel reads the UTF-8 product names correctly.
  const csv = [headers.join(','), ...rows.map(e => e.join(','))].join('\r\n');
  const blob = new Blob(['\ufeff' + csv], {type: 'text/csv;charset=utf-8;'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  const scope = activeGroup ? '_' + activeGroup.replace(/[^a-z0-9]+/gi, '-').toLowerCase() : '';
  link.href = url;
  link.download = `plate_magnet${scope}_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── Scrape Activity ──────────────────────────────────────────────────────

let scrapePollTimer = null;

const escapeHtml = esc;

function openScrapeModal() {
  document.getElementById('scrapeModalOverlay').classList.add('active');
  refreshScrapeRuns();
  if (!scrapePollTimer) {
    scrapePollTimer = setInterval(refreshScrapeRuns, 4000);
  }
}

function closeScrapeModal() {
  document.getElementById('scrapeModalOverlay').classList.remove('active');
  if (scrapePollTimer) {
    clearInterval(scrapePollTimer);
    scrapePollTimer = null;
  }
}

function handleScrapeOverlayClick(e) {
  if (e.target.id === 'scrapeModalOverlay') {
    closeScrapeModal();
  }
}

async function refreshScrapeRuns() {
  try {
    const res = await fetch('/api/scrape/runs');
    const data = await res.json();
    const runs = data.runs || [];
    const running = runs.some(r => r.status === 'running');

    const btn = document.getElementById('runScrapeBtn');
    const label = document.getElementById('runScrapeBtnLabel');
    btn.disabled = running;
    btn.style.opacity = running ? 0.6 : 1;
    btn.style.cursor = running ? 'default' : 'pointer';
    label.textContent = running ? 'Scrape running…' : 'Run Scrape Now';

    document.getElementById('pulseDot').classList.toggle('running', running);

    document.getElementById('scrapeScheduleNote').textContent = data.auto_scrape
      ? `Auto-scrapes every ${data.interval_hours}h`
      : 'Manual trigger only';

    const body = document.getElementById('scrapeRunsBody');
    if (!runs.length) {
      body.innerHTML = '<tr><td colspan="4" class="scrape-empty">No scrapes yet — run one to populate the dashboard.</td></tr>';
      return;
    }
    body.innerHTML = runs.map(r => `
      <tr>
        <td>${escapeHtml(r.started_at || '—')}</td>
        <td style="color:var(--text-muted)">${escapeHtml(r.trigger)}</td>
        <td><span class="status-pill ${r.status}">${escapeHtml(r.status)}</span>${r.error ? ` <span style="color:var(--text-dim);font-size:11.5px" title="${escapeHtml(r.error)}">⚠</span>` : ''}</td>
        <td>${r.products_scraped != null ? r.products_scraped.toLocaleString() : '—'}</td>
      </tr>
    `).join('');

    // If a scrape just finished, refresh the underlying product data too.
    if (!running && window._lastScrapeWasRunning) {
      loadData();
    }
    window._lastScrapeWasRunning = running;
  } catch (e) {
    console.error('Failed to load scrape runs:', e);
  }
}

async function triggerScrape() {
  const errBox = document.getElementById('scrapeError');
  errBox.style.display = 'none';
  try {
    const res = await fetch('/api/scrape', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN},
      body: JSON.stringify({})
    });
    const data = await res.json();
    if (!res.ok) {
      errBox.textContent = data.error || 'Failed to start scrape';
      errBox.style.display = 'block';
      return;
    }
    refreshScrapeRuns();
  } catch (e) {
    errBox.textContent = 'Failed to start scrape';
    errBox.style.display = 'block';
  }
}

loadData();
setInterval(refreshScrapeRuns, 30000);
