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
        'browser_key': 'rep',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'titan': {
        'name': 'Titan Fitness',
        'browser_key': 'titan',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'rivalsteel': {
        'name': 'Rival Steel',
        'browser_key': 'rivalsteel',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'bos': {
        'name': 'Bells of Steel',
        'browser_key': 'bos',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'elitefts': {
        'name': 'EliteFTS',
        'browser_key': 'elitefts',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
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
        'browser_key': 'onyx',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'twopood': {
        'name': '2POOD',
        'browser_key': 'twopood',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'gymreapers': {
        'name': 'Gymreapers',
        'browser_key': 'gymreapers',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'americanbarbell': {
        'name': 'American Barbell',
        'browser_key': 'americanbarbell',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'fringesport': {
        'name': 'Fringe Sport',
        'browser_key': 'fringesport',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'againfaster': {
        'name': 'Again Faster',
        'browser_key': 'againfaster',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'cerberus': {
        'name': 'Cerberus Strength',
        'browser_key': 'cerberus',
        'note': 'Migrated to cerberus-strength.us — HTTP rate-limited, requires --browser flag',
        'requires_browser': True,
    },
    'pioneerfit': {
        'name': 'Pioneer Fitness',
        'browser_key': 'pioneerfit',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
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
        'browser_key': 'nobullproject',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
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
        'browser_key': 'markbell',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'slingshot': {
        'name': 'Slingshot',
        'browser_key': 'slingshot',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'forceusa': {
        'name': 'Force USA',
        'browser_key': 'forceusa',
        'note': 'Shopify rate-limited — requires --browser flag',
        'requires_browser': True,
    },
    'wh': {
        'name': 'Weightlifting House',
        'browser_key': 'wh',
        'note': 'Moved to store.weightliftinghouse.com — HTTP rate-limited, requires --browser flag',
        'requires_browser': True,
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

# ── Parser: Shopify products.json API ────────────────────────────────────
# For Shopify stores with thin/no preload data. Paginates /products.json.

def extract_shopify_json(api_url, currency='USD', collections=None):
    import json as _json
    import time
    base = re.match(r'(https://[^/]+)/', api_url)
    base_url = base.group(1) if base else ''
    products = []
    page = 1
    seen_handles = set()
    # Build handle/variant-id -> category map from collection endpoints
    cat_map = {}
    if collections:
        for cat, handle in collections.items():
            cpage = 1
            while cpage <= 5:
                text = fetch_page(f'{base_url}/collections/{handle}/products.json?limit=250&page={cpage}')
                if not text:
                    break
                try:
                    items = _json.loads(text).get('products', [])
                except ValueError:
                    break
                if not items:
                    break
                for prod in items:
                    cat_map[prod.get('handle', '')] = cat
                    for v in prod.get('variants', []):
                        cat_map[str(v.get('id'))] = cat
                cpage += 1
                time.sleep(0.4)
            time.sleep(0.4)
    while page <= 20:
        text = fetch_page(api_url.format(page=page))
        if not text:
            break
        try:
            data = _json.loads(text)
        except ValueError:
            break
        items = data.get('products', [])
        if not items:
            break
        for prod in items:
            handle = prod.get('handle', '')
            if handle in seen_handles:
                continue
            seen_handles.add(handle)
            title = (prod.get('title') or '').strip()
            url = f'{base_url}/products/{handle}'
            for variant in prod.get('variants', []):
                price = safe_price(variant.get('price'))
                if price is None or price < 1.00:
                    continue
                vname = (variant.get('title') or '').strip()
                name = title if vname in ('Default Title', '') else f'{title} - {vname}'
                vhandle = variant.get('id')
                products.append({
                    'name': name,
                    'price': price,
                    'price_text': f'${price:,.2f}',
                    'currency': currency,
                    'url': url,
                    'handle': str(vhandle) if vhandle else handle,
                    'category': cat_map.get(str(vhandle)) or cat_map.get(handle)
                                or (prod.get('product_type') or '').strip() or 'All',
                })
        page += 1
        time.sleep(1)
    return products


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
            # Image
            img_url = None
            img_el = item.find('img')
            if img_el:
                src = img_el.get('src') or img_el.get('data-src', '')
                if src and src.startswith('http'):
                    img_url = src[:250]
            products.append({
                'name': name,
                'price': round(price, 2),
                'price_text': f'${price:.2f}',
                'currency': currency,
                'url': href,
                'image_url': img_url,
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

            # Image from WooCommerce images array
            img_url = None
            imgs = item.get('images', [])
            if imgs and isinstance(imgs, list) and len(imgs) > 0:
                img_url = (imgs[0].get('src', '') or imgs[0].get('sizes', {}).get('full', '') or '')[:250]

            products.append({
                'name': name,
                'price': round(price, 2),
                'price_text': f'${price:.2f}',
                'currency': currency,
                'url': url,
                'image_url': img_url,
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

        # Image
        image_url = None
        img_el = item.find('img')
        if img_el:
            img_src = img_el.get('src') or img_el.get('data-src', '')
            if img_src:
                if img_src.startswith('/'):
                    img_src = 'https://www.liftinglarge.com' + img_src
                elif not img_src.startswith('http'):
                    img_src = 'https://www.liftinglarge.com/' + img_src.lstrip('/')
                image_url = img_src[:250] if img_src else None

        products.append({
            'name': name.strip(),
            'price': price,
            'price_text': f'${price:.2f}' if price else price_text[:50] or None,
            'currency': 'USD',
            'url': url,
            'image_url': image_url,
        })

    return products


# ── Scraper engine ────────────────────────────────────────────────────────

def scrape_site(site_key, config):
    if config.get('requires_browser'):
        return {'site': config['name'], 'status': 'requires_browser', 'products': []}

    parser = config.get('parser', '')
    all_products = []

    # Special case: Shopify products.json API (no category iteration)
    if parser == 'shopify_json':
        api_url = config.get('api_url', '')
        products = extract_shopify_json(api_url, config.get('currency', 'USD'), config.get('collections'))
        for p in products:
            p['site'] = config['name']
            p.setdefault('category', 'All')
            p['source_url'] = api_url
        all_products.extend(products)
        print(f'  All products: {len(products)}')
        return {
            'site': config['name'],
            'status': 'ok' if all_products else 'empty',
            'products': all_products,
            'scraped_at': datetime.now(timezone.utc).isoformat(),
        }

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
    parser.add_argument('--browser', '-b', action='store_true', help='Include browser-required sites (all 25 Shopify stores)')
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