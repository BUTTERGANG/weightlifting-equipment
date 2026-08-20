#!/usr/bin/env python3
"""
Weightlifting equipment price scraper.
Collects barbell, plate, rack & powerlifting gear prices from major retailers.

Usage:
    python3 equipment_scraper.py                    # All HTTP-scrapable sites
    python3 equipment_scraper.py --browser          # Include Rogue (needs Playwright)
    python3 equipment_scraper.py --site titan       # Single site
    python3 equipment_scraper.py --category barbells # Filter results
    python3 equipment_scraper.py --output prices.json
"""

import re
import json
import sys
import argparse
import csv
import io
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
}
TIMEOUT = 20

# ── Site configs ──────────────────────────────────────────────────────────

SITES = {
    'rogue': {
        'name': 'Rogue Fitness',
        'browser_key': 'rogue',
        'note': 'Vue SPA — requires --browser flag',
        'requires_browser': True,
    },
    'tyr': {
        'name': 'TYR Sport',
        'browser_key': 'tyr',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'luxiaojun': {
        'name': 'LUXIAOJUN',
        'browser_key': 'luxiaojun',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'sbd': {
        'name': 'SBD Apparel',
        'browser_key': 'sbd',
        'note': 'Shopify — requires --browser flag',
        'requires_browser': True,
    },
    'rep': {
        'name': 'REP Fitness',
        'urls': {
            'Barbells': 'https://www.repfitness.com/collections/barbells',
            'Plates': 'https://www.repfitness.com/collections/bumper-plates',
            'Racks': 'https://www.repfitness.com/collections/power-racks',
        },
        'domain': 'repfitness.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'titan': {
        'name': 'Titan Fitness',
        'urls': {
            'Barbells': 'https://www.titan.fitness/collections/barbells',
            'Racks': 'https://www.titan.fitness/collections/power-racks',
        },
        'domain': 'titan.fitness',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'bos': {
        'name': 'Bells of Steel',
        'urls': {
            'Barbells': 'https://bellsofsteel.com/collections/barbells',
            'Plates': 'https://bellsofsteel.com/collections/bumper-plates',
            'Racks': 'https://bellsofsteel.com/collections/racks',
        },
        'domain': 'bellsofsteel.com',
        'currency': 'CAD',
        'parser': 'shopify_preload',
    },
    'elitefts': {
        'name': 'EliteFTS',
        'urls': {
            'Barbells': 'https://elitefts.com/collections/barbells',
            'Knee Sleeves': 'https://elitefts.com/collections/knee-sleeves',
            'Plates': 'https://elitefts.com/collections/plates',
            'Racks': 'https://elitefts.com/collections/power-racks',
            'Apparel': 'https://elitefts.com/collections/apparel',
            'Footwear': 'https://elitefts.com/collections/footwear',
            'Accessories': 'https://elitefts.com/collections/accessories',
            'Support Gear': 'https://elitefts.com/collections/support-gear',
        },
        'domain': 'elitefts.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'liftinglarge': {
        'name': 'LiftingLarge',
        'urls': {
            'All Items': 'https://www.liftinglarge.com/liftinglarge-new-items',
            'Belts': 'https://www.liftinglarge.com/Competition-Powerlifting-Belts',
            'Bench Shirts': 'https://www.liftinglarge.com/titan-support-systems-bench-press-shirts',
            'Squat Suits': 'https://www.liftinglarge.com/titan-support-systems-squat-suits',
            'Deadlift Suits': 'https://www.liftinglarge.com/titan-support-systems-deadlift-suits',
            'Singlets': 'https://www.liftinglarge.com/powerlifting-singlets',
            'Wrist Wraps': 'https://www.liftinglarge.com/Powerlifting-Wrist-Wraps',
            'Knee Wraps': 'https://www.liftinglarge.com/Powerlifting-Knee-Wraps',
            'Knee Sleeves': 'https://www.liftinglarge.com/knee-and-elbow-sleeves',
            'Strength Tools': 'https://www.liftinglarge.com/powerlifting-and-strength-training-tools',
            'Bands': 'https://www.liftinglarge.com/kilo-powerlifting-bands',
            'Bench Boards': 'https://www.liftinglarge.com/Powerlifting-Benchpress-Boards',
            'Grip & Straps': 'https://www.liftinglarge.com/Grip-Accessories',
            'Strongman': 'https://www.liftinglarge.com/Strongman-Training-Tools',
            'Recovery': 'https://www.liftinglarge.com/Recovery-Rehab-Tools',
            'Shoes': 'https://www.liftinglarge.com/Deadlift-Slippers-Squat-Shoes',
            'Clearance': 'https://www.liftinglarge.com/clearance-powerlifting-gear',
            'IPF Equipment': 'https://www.liftinglarge.com/ipf-powerlifting-approved-competition-equipment',
            'USPA Gear': 'https://www.liftinglarge.com/USPA-IPL-Approved-Powerlifting-Gear',
        },
        'parser': 'liftinglarge',
    },
    'onyx': {
        'name': 'Onyx Straps',
        'urls': {
            'Lifting Straps': 'https://www.onyxstraps.com/collections/lifting-straps',
            'Wrist Wraps': 'https://www.onyxstraps.com/collections/wrist-wraps',
            'High-top Wraps': 'https://www.onyxstraps.com/collections/high-top-wrist-wraps',
            'Belts': 'https://www.onyxstraps.com/collections/the-belt',
            'Apparel': 'https://www.onyxstraps.com/collections/apparel',
            'Accessories': 'https://www.onyxstraps.com/collections/accessories',
            'Leather Care': 'https://www.onyxstraps.com/collections/leather-care',
            'Bags & Wallets': 'https://www.onyxstraps.com/collections/wallets-bags',
        },
        'domain': 'onyxstraps.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'twopood': {
        'name': '2POOD',
        'urls': {
            "4\" Belts": 'https://2pood.com/collections/weightlifting-belts',
            "3\" Belts": 'https://2pood.com/collections/petite-weightlifting-belts',
            'Lever Belts': 'https://2pood.com/collections/10mm-lever-belt',
            'Knee Sleeves': 'https://2pood.com/collections/knee-sleeves',
            'Apparel': 'https://2pood.com/collections/apparel',
            'Bags': 'https://2pood.com/collections/backpacks-duffels',
            'Wrist Wraps': 'https://2pood.com/collections/wrist-wraps',
            'Accessories': 'https://2pood.com/collections/accessories-1',
            'Tape': 'https://2pood.com/collections/weightlifting-tape',
        },
        'domain': '2pood.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'gymreapers': {
        'name': 'Gymreapers',
        'browser_key': 'gymreapers',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'americanbarbell': {
        'name': 'American Barbell',
        'urls': {
            'Barbells': 'https://www.americanbarbell.com/collections/bars',
            'Plates': 'https://www.americanbarbell.com/collections/bumper-and-olympic-plates',
            'Benches': 'https://www.americanbarbell.com/collections/benches-1',
            'Specialty Bars': 'https://www.americanbarbell.com/collections/all-specialty-bars',
            'Accessories': 'https://www.americanbarbell.com/collections/accessories',
        },
        'domain': 'americanbarbell.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'fringesport': {
        'name': 'Fringe Sport',
        'urls': {
            'Barbells': 'https://www.fringesport.com/collections/barbells',
            'Plates': 'https://www.fringesport.com/collections/bumper-plates',
            'Racks': 'https://www.fringesport.com/collections/squat-racks',
            'Benches': 'https://www.fringesport.com/collections/weight-benches',
            'Accessories': 'https://www.fringesport.com/collections/accessories',
            'Apparel': 'https://www.fringesport.com/collections/apparel',
        },
        'domain': 'fringesport.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'againfaster': {
        'name': 'Again Faster',
        'browser_key': 'againfaster',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'cerberus': {
        'name': 'Cerberus Strength',
        'urls': {
            'Belts': 'https://cerberus-strength.com/collections/powerlifting-belts',
            'Wraps': 'https://cerberus-strength.com/collections/powerlifting-wraps',
            'Sleeves': 'https://cerberus-strength.com/collections/powerlifting-sleeves',
            'Deadlift Suits': 'https://cerberus-strength.com/collections/deadlift-suits',
            'Singlets': 'https://cerberus-strength.com/collections/powerlifting-singlets',
            'Plates': 'https://cerberus-strength.com/collections/weight-plates',
            'Apparel': 'https://cerberus-strength.com/collections/apparel',
            'Accessories': 'https://cerberus-strength.com/collections/gym-accessories',
        },
        'domain': 'cerberus-strength.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'pioneerfit': {
        'name': 'Pioneer Fitness',
        'urls': {
            'Powerlifting Belts': 'https://pioneerfit.com/collections/pioneer-powerlifting-belts',
            'Lever Belts': 'https://pioneerfit.com/collections/pioneer-lever-lifting-belts',
            'Deadlift Belts': 'https://pioneerfit.com/collections/pioneer-deadlift-belts',
            'Straps': 'https://pioneerfit.com/collections/lifting-straps-by-pioneer',
            'Knee Wraps': 'https://pioneerfit.com/collections/knee-wraps-by-pioneer',
            'Accessories': 'https://pioneerfit.com/collections/leather-weight-lifting-accessories-by-pioneer',
            'Apparel': 'https://pioneerfit.com/collections/hats-apparel',
            'Singlets': 'https://pioneerfit.com/collections/powerlifting-weightlifting-singlets',
        },
        'domain': 'pioneerfit.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'inzer': {
        'name': 'Inzer Advance Designs',
        'browser_key': 'inzer',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'bornprimitive': {
        'name': 'Born Primitive',
        'browser_key': 'bornprimitive',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'virus': {
        'name': 'Virus International',
        'browser_key': 'virus',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'hookgrip': {
        'name': 'Hookgrip',
        'api_url': 'https://www.hookgrip.com/wp-json/wc/store/products',
        'currency': 'USD',
        'parser': 'woocommerce_store',
    },
    'nobullproject': {
        'name': 'NoBull',
        'urls': {
            "Men's Shoes & Apparel": 'https://nobullproject.com/collections/all-mens',
            "Women's Shoes & Apparel": 'https://nobullproject.com/collections/all-womens',
            'Accessories': 'https://nobullproject.com/collections/accessories',
            'Bags': 'https://nobullproject.com/collections/bags',
        },
        'domain': 'nobullproject.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'getrxd': {
        'name': 'Get Rx\'d',
        'urls': {
            'Weightlifting': 'https://www.getrxd.com/weightlifting.html',
            'Conditioning & Agility': 'https://www.getrxd.com/conditioning-agility.html',
            'Racks & GHDs': 'https://www.getrxd.com/racks-ghd-s.html',
            'Gymnastics & Climbing': 'https://www.getrxd.com/gymnastics-climbing.html',
            'Mobility & Gear': 'https://www.getrxd.com/mobility-gear.html',
            'Pull-Up Rigs': 'https://www.getrxd.com/pulluprigs.html',
            'Xebex Fitness': 'https://www.getrxd.com/xebex-fitness.html',
        },
        'currency': 'USD',
        'parser': 'magento',
    },
    'markbell': {
        'name': 'Mark Bell',
        'urls': {
            'Hip Circles': 'https://markbell.com/collections/hip-circles',
            'Knee Wraps': 'https://markbell.com/collections/knee-wraps',
            'Wrist Wraps': 'https://markbell.com/collections/wrist-wraps',
            'Sleeves': 'https://markbell.com/collections/sleeves',
            'Lifting Straps': 'https://markbell.com/collections/lifting-straps',
            'Accessories': 'https://markbell.com/collections/accessories',
        },
        'domain': 'markbell.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'slingshot': {
        'name': 'Slingshot',
        'urls': {
            'Hip Circles': 'https://markbellslingshot.com/collections/hip-circle',
            'Knee Sleeves': 'https://markbellslingshot.com/collections/knee-sleeves',
            'Knee Wraps': 'https://markbellslingshot.com/collections/knee-wraps',
            'Wrist Wraps': 'https://markbellslingshot.com/collections/gangsta-wrist-wraps',
            'Elbow Sleeves': 'https://markbellslingshot.com/collections/elbow-sleeves',
            'Shake Straps': 'https://markbellslingshot.com/collections/shake-straps',
            'Apparel': 'https://markbellslingshot.com/collections/apparel',
            'Accessories': 'https://markbellslingshot.com/collections/accessories',
        },
        'domain': 'markbellslingshot.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'forceusa': {
        'name': 'Force USA',
        'urls': {
            'All-In-One Trainers': 'https://forceusa.com/collections/all-in-one',
            'Power Racks': 'https://forceusa.com/collections/power-racks',
            'Barbells': 'https://forceusa.com/collections/barbells',
            'Benches': 'https://forceusa.com/collections/benches',
            'Functional Trainers': 'https://forceusa.com/collections/functional-trainers',
            'Leg Machines': 'https://forceusa.com/collections/leg-machines',
            'Dual Station': 'https://forceusa.com/collections/dual-station-machines',
            'Accessories': 'https://forceusa.com/collections/accessories',
        },
        'domain': 'forceusa.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'wh': {
        'name': 'Weightlifting House',
        'urls': {
            'Belts': 'https://weightliftinghouse.com/collections/belts',
            'Knee Sleeves': 'https://weightliftinghouse.com/collections/knee-sleeves',
            'Straps': 'https://weightliftinghouse.com/collections/straps',
            'Wrist Wraps': 'https://weightliftinghouse.com/collections/wrist-wraps',
            'Tape': 'https://weightliftinghouse.com/collections/tape',
            'New': 'https://weightliftinghouse.com/collections/new-equipment',
            'Books': 'https://weightliftinghouse.com/collections/books',
            'Flags': 'https://weightliftinghouse.com/collections/flags',
        },
        'domain': 'weightliftinghouse.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
}


# ── Helpers ───────────────────────────────────────────────────────────────

def safe_price(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    s = re.sub(r'[^0-9.]', '', str(val))
    try:
        return round(float(s), 2)
    except (ValueError, TypeError):
        return None


def fetch_page(url):
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.text
            elif r.status_code == 429:
                import time
                wait = int(r.headers.get('Retry-After', 5))
                time.sleep(wait)
                continue
            else:
                return None
        except requests.RequestException:
            import time
            time.sleep(2)
    return None


# ── Parser: Shopify preloaded JSON ────────────────────────────────────────
# Pattern found across Titan, EliteFTS, REP, Bells of Steel:
#   "price":12345,"name":"Product Name"
# where price is in cents.

def extract_shopify_preload(html_text, domain, currency='USD'):
    products = []
    seen = set()

    # Pattern 1: variant-level data with name and price in cents
    pattern1 = re.compile(r'\{"id":(\d+),"price":(\d+),"name":"([^"]+)"')
    for vid, price_cents, name in pattern1.findall(html_text):
        key = (name, price_cents)
        if key in seen:
            continue
        seen.add(key)

        price = safe_price(price_cents) / 100 if price_cents else None
        price = round(price, 2) if price else None

        # Build product URL from handle if present later
        products.append({
            'name': name.strip(),
            'price': price,
            'price_text': f'${price:.2f}' if price else None,
            'currency': currency,
            'url': None,
            'handle': None,
        })

    # Pattern 2: handles for product links
    handle_pattern = re.compile(r'"handle":"([^"]+)"')
    handles = handle_pattern.findall(html_text)
    handles = list(dict.fromkeys(handles))  # dedup, preserve order

    # Match handles to products by name similarity
    if handles and products:
        for p in products:
            # Find matching handle by normalized name
            name_slug = re.sub(r'[^a-z0-9]+', '-', p['name'].lower()).strip('-')
            for h in handles:
                if h == name_slug or h in name_slug or name_slug in h:
                    p['handle'] = h
                    p['url'] = f'https://{domain}/products/{h}'
                    break
            # Fallback: partial match
            if not p['url'] and handles:
                for h in handles:
                    # Remove color/variant modifiers for loose matching
                    base_h = re.sub(r'--?\s*\w+$', '', h)
                    base_n = re.sub(r'[-–—].+$', '', name_slug)
                    if base_h in base_n or base_n in base_h:
                        p['handle'] = h
                        p['url'] = f'https://{domain}/products/{h}'
                        break

    return products


# ── Parser: Magento HTML (e.g. Get Rx'd) ────────────────────────────────
# Magento server-rendered category pages with pagination.
# Products in .product-item elements with .product-item-link (name/url)
# and data-price-amount attribute.

def extract_magento(base_url, currency='USD'):
    products = []
    seen = set()
    page = 1

    while True:
        url = f"{base_url}?p={page}"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, 'lxml')
        items = soup.select('.product-item')
        if not items:
            break

        found = False
        for item in items:
            name_el = item.select_one('.product-item-link')
            price_el = item.select_one('[data-price-amount]')
            if not name_el or not price_el:
                continue
            name = name_el.get_text(strip=True)
            price = float(price_el.get('data-price-amount', 0))
            if not name or price < 1.0:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            href = name_el.get('href', '')
            products.append({
                'name': name,
                'price': round(price, 2),
                'price_text': f'${price:.2f}',
                'currency': currency,
                'url': href,
            })
            found = True

        if not found:
            break
        page += 1

    return products


# ── Parser: WooCommerce Store API ────────────────────────────────────────────
# For WooCommerce sites with an open Store REST API (e.g. Hookgrip).
# Paginates /wp-json/wc/store/products?per_page=100&page=N

def extract_woocommerce_store(url_base, currency='USD'):
    products = []
    page = 1
    seen = set()

    while True:
        url = f"{url_base}?per_page=100&page={page}"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            break
        items = r.json()
        if not items:
            break

        for item in items:
            name = item.get('name', '').strip()
            if not name:
                continue
            # Skip order artifacts
            if name.lower().startswith('order #'):
                continue

            prices = item.get('prices', {})
            price_cents = prices.get('price')
            if price_cents is None:
                continue
            price = float(price_cents) / 100

            # Skip add-on / customization items
            if price < 1.0:
                continue

            key = name.lower()
            if key in seen:
                continue
            seen.add(key)

            # Build URL from permalink
            url = item.get('permalink', '') or item.get('slug', '')

            products.append({
                'name': name,
                'price': round(price, 2),
                'price_text': f'${price:.2f}',
                'currency': currency,
                'url': url,
            })

        total_pages = int(r.headers.get('X-WP-TotalPages', 0))
        if page >= total_pages:
            break
        page += 1

    return products


# ── Parser: DOM hydrate (no preload JSON) ────────────────────────────────
# For Shopify stores whose themes don't embed product JSON (Born Primitive,
# Again Faster). Extracts product names, prices, and URLs from rendered HTML
# by finding <a> tags with product links and nearby price text.

def extract_dom_hydrate(html_text, domain, currency='USD'):
    products = []
    soup = BeautifulSoup(html_text, 'lxml')
    seen = set()

    # Find product cards: look for links containing product slug patterns
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        name = link.get_text(strip=True)
        if not name or len(name) < 4:
            continue
        # Normalize to product URL
        if '/products/' not in href:
            continue
        if name.lower() in ['shop now', 'view product', 'quick view', 'learn more', 'read more', 'add to cart']:
            continue

        # Build full URL
        if href.startswith('http'):
            url = href
        elif href.startswith('/'):
            url = f'https://{domain}{href}'
        else:
            url = f'https://{domain}/{href}'

        # Find nearest price text within this card
        card = link.find_parent(['div', 'li', 'article'], class_=lambda c: c and 'product' in c.lower()) or link.parent
        price_text = ''
        price = None
        if card:
            price_el = card.find(['span', 'div', 'p'], class_=lambda c: c and 'price' in c.lower()) if card else None
            if not price_el:
                price_el = card.find(['span', 'div', 'p'], string=re.compile(r'\$\d'))
            if price_el:
                price_text = price_el.get_text(strip=True)
        if not price_text:
            # Fallback: scan nearby siblings/children for $
            for el in [link, link.parent] if link.parent else [link]:
                txt = el.get_text()
                m = re.search(r'\$(\d+(?:\.\d{2})?)', txt)
                if m:
                    price_text = f'${m.group(1)}'
                    break

        if price_text:
            pm = re.search(r'\$([\d,]+\.?\d*)', price_text)
            if pm:
                price = float(pm.group(1).replace(',', ''))

        key = name.lower().strip()
        if key and key not in seen:
            seen.add(key)
            products.append({
                'name': name.strip(),
                'price': price,
                'price_text': f'${price:.2f}' if price else price_text[:50] or None,
                'currency': currency,
                'url': url,
            })

    return products


# ── Parser: LiftingLarge (ASP classic) ────────────────────────────────────

def extract_liftinglarge(html_text):
    """Extract from LiftingLarge ASP classic HTML using div.product-item structure."""
    products = []
    soup = BeautifulSoup(html_text, 'lxml')

    for item in soup.find_all('div', class_='product-item'):
        name_el = item.find('div', class_='name')
        if not name_el:
            continue
        link = name_el.find('a', href=True)
        if not link:
            continue

        name = link.get_text(strip=True)
        href = link.get('href', '')

        if not name or len(name) < 4:
            continue

        # Build full URL
        if href.startswith('http'):
            url = href
        elif href.startswith('/'):
            url = f'https://www.liftinglarge.com{href}'
        else:
            url = f'https://www.liftinglarge.com/{href}'

        # Price
        price_el = item.find('div', class_='price')
        price_text = ''
        if price_el:
            span = price_el.find('span', class_=re.compile(r'price|sale', re.I))
            if span:
                price_text = span.get_text(strip=True)
            if not price_text:
                price_text = price_el.get_text(strip=True)

        # Extract numeric price
        price_match = re.search(r'\$([\d,]+\.?\d*)', price_text)
        price = float(price_match.group(1).replace(',', '')) if price_match else None

        products.append({
            'name': name.strip(),
            'price': price,
            'price_text': f'${price:.2f}' if price else price_text[:50] or None,
            'currency': 'USD',
            'url': url,
        })

    return products


# ── Scraper engine ────────────────────────────────────────────────────────

def scrape_site(site_key, config):
    if config.get('requires_browser'):
        return {'site': config['name'], 'status': 'requires_browser', 'products': []}

    parser = config.get('parser', '')
    all_products = []

    # Special case: WooCommerce Store API (no category iteration)
    if parser == 'woocommerce_store':
        api_url = config.get('api_url', '')
        products = extract_woocommerce_store(api_url, config.get('currency', 'USD'))
        for p in products:
            p['site'] = config['name']
            p['category'] = 'All'
            p['source_url'] = api_url
        all_products.extend(products)
        print(f'  All products: {len(products)}')
        return {
            'site': config['name'],
            'status': 'ok' if all_products else 'empty',
            'products': all_products,
            'scraped_at': datetime.now(timezone.utc).isoformat(),
        }

    urls = config.get('urls', {})

    for category, url in urls.items():
        # LiftingLarge: use viewall=1 to get all products on one page
        if parser == 'liftinglarge' and '?' not in url:
            url = url + '?viewall=1'
        
        if parser == 'magento':
            # Magento handles fetching + pagination internally
            products = extract_magento(url, config.get('currency', 'USD'))
        else:
            html = fetch_page(url)
            if not html:
                print(f'  {category}: failed ({url})')
                continue

            if parser == 'shopify_preload':
                products = extract_shopify_preload(html, config.get('domain', ''), config.get('currency', 'USD'))
            elif parser == 'liftinglarge':
                products = extract_liftinglarge(html)
            elif parser == 'dom_hydrate':
                products = extract_dom_hydrate(html, config.get('domain', ''), config.get('currency', 'USD'))
            else:
                products = []

        for p in products:
            p['site'] = config['name']
            p['category'] = category
            p['source_url'] = url

        all_products.extend(products)
        print(f'  {category}: {len(products)} products')

    return {
        'site': config['name'],
        'status': 'ok' if all_products else 'empty',
        'products': all_products,
        'scraped_at': datetime.now(timezone.utc).isoformat(),
    }


def scrape_all(site_filter=None, category_filter=None, use_browser=False):
    results = []

    for key, config in SITES.items():
        if site_filter and key != site_filter:
            continue
        if config.get('requires_browser') and not use_browser:
            results.append({'site': config['name'], 'status': 'requires_browser', 'products': []})
            print(f'\n── {config["name"]} (requires --browser) ──')
            continue

        print(f'\n── {config["name"]} ──')
        try:
            if config.get('requires_browser'):
                # Use Playwright-based browser scraper for all browser sites
                import importlib
                browser_mod = importlib.import_module('browser_scraper')
                browser_key = config.get('browser_key')
                rogue_products = browser_mod.scrape_site_playwright(
                    browser_key,
                    browser_mod.SITES[browser_key],
                )
                result = {
                    'site': config['name'],
                    'status': 'ok',
                    'products': rogue_products,
                    'scraped_at': datetime.now(timezone.utc).isoformat(),
                }
                print(f'  Total: {len(rogue_products)} products')
            else:
                result = scrape_site(key, config)
            results.append(result)
        except Exception as e:
            print(f'  ERROR: {e}')
            results.append({'site': config['name'], 'status': 'error', 'error': str(e)})

    # Flatten
    all_products = []
    for r in results:
        all_products.extend(r.get('products', []))

    if category_filter:
        all_products = [
            p for p in all_products
            if category_filter.lower() in p.get('category', '').lower()
        ]

    return results, all_products


# ── Output ────────────────────────────────────────────────────────────────

def print_table(products):
    if not products:
        print('\nNo products found.')
        return

    print(f'\n{"SITE":24s} {"CAT":18s} {"PRICE":10s} {"NAME":55s}')
    print('─' * 107)
    for p in sorted(products, key=lambda x: (
        x.get('site', ''),
        x.get('price', 999999) or 999999
    )):
        site = p.get('site', '')[:23]
        cat = p.get('category', '')[:17]
        price_str = p.get('price_text', '') or 'N/A'
        name = p.get('name', '')[:54]
        print(f'{site:24s} {cat:18s} {price_str:10s} {name:55s}')


def to_json(products):
    return json.dumps(products, indent=2, default=str)


def to_csv(products):
    if not products:
        return ''
    output = io.StringIO()
    fields = ['site', 'category', 'name', 'price', 'price_text', 'currency', 'url', 'handle', 'source_url']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for p in products:
        writer.writerow({k: p.get(k, '') for k in fields})
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description='Weightlifting equipment price scraper')
    parser.add_argument('--site', '-s', choices=list(SITES.keys()), help='Scrape only this site')
    parser.add_argument('--browser', '-b', action='store_true', help='Include browser-required sites (Rogue)')
    parser.add_argument('--category', '-c', help='Filter results by category keyword')
    parser.add_argument('--output', '-o', help='Output file (.json or .csv)')
    parser.add_argument('--format', '-f', choices=['table', 'json', 'csv'], default='table')
    args = parser.parse_args()

    print(f'Weightlifting Equipment Price Scraper')
    print(f'  {datetime.now(timezone.utc).isoformat()}')
    if args.site:
        print(f'  Site: {args.site}')
    if args.category:
        print(f'  Filter: {args.category}')

    results, products = scrape_all(site_filter=args.site, category_filter=args.category, use_browser=args.browser)

    if args.output:
        path = Path(args.output)
        if path.suffix == '.json':
            path.write_text(to_json(products))
        elif path.suffix == '.csv':
            path.write_text(to_csv(products))
        else:
            path.write_text(to_json(products))
        print(f'\nSaved {len(products)} products to {args.output}')
    elif args.format == 'json':
        print(to_json(products))
    elif args.format == 'csv':
        print(to_csv(products))
    else:
        print_table(products)

    # Summary
    counts = {}
    for r in results:
        n = len(r.get('products', []))
        counts[r['site']] = (n, r.get('status', '?'))
    print(f'\n── Summary ──')
    for site, (n, status) in counts.items():
        s = '*' if status == 'requires_browser' else ''
        print(f'  {site:20s} {n:4d} products{s}')
    print(f'  {"TOTAL":20s} {sum(n for n,_ in counts.values())} products')

    browser_sites = [r for r in results if r.get('status') == 'requires_browser']
    if browser_sites:
        print(f'\n* Requires browser (use --browser or browser_exec)')
        for r in browser_sites:
            print(f'   - {r["site"]}')


if __name__ == '__main__':
    main()