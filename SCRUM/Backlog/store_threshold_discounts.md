---
title: "Store threshold discounts: discover, document, surface in dashboard"
status: backlog
priority: P2
project: weightlifting-equipment
type: dev
agent_claimed: null
claimed_at: null
created: 2026-09-08T00:00:00Z
updated: 2026-09-08T00:00:00Z
tags: [discounts, dashboard, research]
due: null
estimate: large

# WSJF Value Scoring (Product Owner fills in)
business_value: 7
time_criticality: 4
risk_reduction: 3
wsjf_score: null
po_notes: "Verified live: TYR auto-applies 20% over $99 with no code. Most suppliers run free-ship or %-off thresholds — this data changes real purchase decisions."
po_accepted: null
---

# Store threshold discounts: discover, document, surface in dashboard

## Summary
Retailers increasingly use automatic threshold benefits (free shipping over $X, %-off over $Y, bulk pricing) rather than coupon codes. Verified example: TYR auto-applies 20% to any cart over $99 — no code, invisible until checkout. Discover these per store, store them as data, and surface them in the dashboard + daily digest.

## Value Proposition
A scraped price of $160 is misleading if the real cart price is $128 (TYR). Threshold data makes the tracker's prices *comparable across stores* — one store's "expensive" bar may be cheapest after its auto-discount. This is the difference between listing prices and actual cost.

## Context
- **Empirically verified (Sep 2026):** TYR — orders $99+ get automatic 20% off cart, no code required. Found by driving a real checkout session ($160 item → $128 cart).
- **Seen in site banners during scraping:** Element 26 — "FREE U.S. SHIPPING ON ORDERS OVER $50"
- Coupon aggregators are noise: all 6 "verified" TYR codes tested dead in real carts (Sep 2026). Don't build on them.
- Checkout-session verification pattern exists: `/tmp/tyr_verify3.py` (Playwright: add item → hit /discount/CODE or read cart.js → compare totals). Reusable per store.

## Scope

### 1. Discovery (per store)
Two-tier approach, cheap-first:
- **Tier 1 (free):** Scrape homepage/collection banners for "free shipping over $X" / "% off over $Y" text. Most Shopify stores put this in announcement bars.
- **Tier 2 (targeted):** For stores where banners are silent, drive a Playwright checkout session with a cheap item, read cart.js for auto-applied discounts at 1–2 quantity levels. Respect rate limits (TYR taught us: 429s last 20+ min after ~20 requests).

### 2. Data model
New table `store_thresholds`:
```sql
CREATE TABLE store_thresholds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site        TEXT NOT NULL,
    kind        TEXT NOT NULL,        -- 'pct_off' | 'free_shipping' | 'bulk_price'
    threshold   REAL NOT NULL,        -- dollar amount that triggers the benefit
    benefit     TEXT NOT NULL,        -- '20% off cart' / 'free shipping' / etc
    pct_off     REAL,                 -- nullable, machine-readable for pct_off kind
    source      TEXT NOT NULL,        -- 'banner_scrape' | 'checkout_verified' | 'manual'
    verified_at TEXT,
    UNIQUE(site, kind, threshold)
);
```

### 3. Surfaces
- **Dashboard header:** per-store badge on the products table ("Auto 20% over $99") via tooltip or filter chip
- **Product detail modal:** "Real cart price" line — product price adjusted for the store's best threshold the cart could realistically hit
- **Daily digest:** "Threshold watch" section — only when a store's threshold is newly discovered/changed (don't repeat daily)

## Acceptance Criteria
- [ ] store_thresholds table created (SQLite + Neon schema)
- [ ] Banner scraper pass runs over all 29 stores, extracts shipping/% thresholds where present
- [ ] TYR 20%/$99 documented with source=checkout_verified
- [ ] Element 26 free-ship/$50 documented
- [ ] Dashboard shows threshold badge on products table (only for stores with data)
- [ ] Digest includes threshold changes, not repeated static info
- [ ] Checkout-verification script generalized into scraper/checkout_threshold_probe.py (per-store, rate-limit-aware)

## Notes
- DO NOT scrape coupon aggregators (SimplyCodes/RetailMeNot/Wethrift) — empirically dead ends.
- Rate-limit budget: one probe per store per week is plenty; thresholds change quarterly at most.
- Some stores gate thresholds behind account creation (e.g., newsletter-locked). Mark those source=manual, unverified if we can't automate past the gate.
