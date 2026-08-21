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
import os
import json
import sys
import time
import random
import argparse
import csv
import io
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

class ScrapeBlocked(RuntimeError):
    """The store refused or throttled us. Distinct from "the store has no
    products", so a blocked run is reported as an error instead of silently
    shipping an empty catalogue."""


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
        'urls': {
            'Lifters': 'https://tyr.com/collections/lifters',
            'L-1 Lifters': 'https://tyr.com/collections/mens-l-1-lifters',
            'L-2 Lifters': 'https://tyr.com/collections/mens-l-2-lifters',
            "Women's Lifters": 'https://tyr.com/collections/womens-lifters',
            'Trainers': 'https://tyr.com/collections/footwear-trainers',
            'Barefoot': 'https://tyr.com/collections/footwear-barefoot',
            'Accessories': 'https://tyr.com/collections/accessories',
        },
        'domain': 'tyr.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'luxiaojun': {
        'name': 'LUXIAOJUN',
        'urls': {
            'Weightlifting Shoes': 'https://luxiaojun.com/collections/weightlifting-shoes',
            'Shoes': 'https://luxiaojun.com/collections/shoes',
            'Barefoot': 'https://luxiaojun.com/collections/barefoot-shoes',
            'Apparel': 'https://luxiaojun.com/collections/apparel',
            'Gear': 'https://luxiaojun.com/collections/gear',
        },
        'domain': 'luxiaojun.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'sbd': {
        'name': 'SBD Apparel',
        'urls': {
            'All': 'https://sbdapparel.com/collections/all',
        },
        'domain': 'sbdapparel.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
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
        'urls': {
            'Belts': 'https://www.gymreapers.com/collections/10mm-lever-belts',
            'Knee Sleeves': 'https://www.gymreapers.com/collections/powerlifting-knee-sleeves',
            'Elbow Sleeves': 'https://www.gymreapers.com/collections/elbow-sleeves',
            'Wrist Wraps': 'https://www.gymreapers.com/collections/wrist-wraps',
            'Straps': 'https://www.gymreapers.com/collections/lifting-straps',
            'Accessories': 'https://www.gymreapers.com/collections/accessories',
            'Apparel': 'https://www.gymreapers.com/collections/shirts',
            'Equipment': 'https://www.gymreapers.com/collections/equipment',
        },
        'domain': 'www.gymreapers.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
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
        'urls': {
            'Racks': 'https://www.againfaster.com/collections/freestanding-racks-and-rigs',
            'Barbells': 'https://www.againfaster.com/collections/barbells',
            'Plates': 'https://www.againfaster.com/collections/competition-plates-kg',
            'Benches': 'https://www.againfaster.com/collections/benches',
            'Dumbbells': 'https://www.againfaster.com/collections/dumbbells',
            'Accessories': 'https://www.againfaster.com/collections/comp-accessories',
            'Footwear': 'https://www.againfaster.com/collections/footwear',
        },
        'domain': 'www.againfaster.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
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
        'urls': {
            'Bench Shirts': 'https://inzernet.com/collections/bench-shirts-1',
            'Squat Suits': 'https://inzernet.com/collections/squat-suits',
            'DL Suits': 'https://inzernet.com/collections/dl-suits-dl-shirts',
            'Belts': 'https://inzernet.com/collections/power-belts',
            'Knee Wraps': 'https://inzernet.com/collections/knee-wraps-1',
            'Knee Sleeves': 'https://inzernet.com/collections/knee-sleeves-1',
            'Wrist Wraps': 'https://inzernet.com/collections/wrist-wraps-1',
            'Singlets': 'https://inzernet.com/collections/singlets',
            'Shoes': 'https://inzernet.com/collections/shoes',
            'Accessories': 'https://inzernet.com/collections/accessories-1',
        },
        'domain': 'inzernet.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'bornprimitive': {
        'name': 'Born Primitive',
        'urls': {
            'Men': 'https://bornprimitive.com/collections/born-primitive-all-men',
            'Women': 'https://bornprimitive.com/collections/born-primitive-all-women',
            'Accessories': 'https://bornprimitive.com/collections/accessories',
        },
        'domain': 'bornprimitive.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'virus': {
        'name': 'Virus International',
        'urls': {
            'Singlets': 'https://virusintl.com/collections/mens-and-womens-singlets',
            "Men's Shorts": 'https://virusintl.com/collections/mens-active-shorts',
            "Women's Shorts": 'https://virusintl.com/collections/womens-shorts',
            "Men's Compression": 'https://virusintl.com/collections/mens-compression-pant',
            "Women's Compression": 'https://virusintl.com/collections/womens-compression-pants',
            'Jackets': 'https://virusintl.com/collections/mens-womens-jackets',
            "Men's Tops": 'https://virusintl.com/collections/mens-shirts-tanks',
            "Women's Tops": 'https://virusintl.com/collections/womens-shirts-tanks',
            'Bags': 'https://virusintl.com/collections/backpack',
            'Accessories': 'https://virusintl.com/collections/accessories',
            'USAW Gear': 'https://virusintl.com/collections/usaw-x-virus',
        },
        'domain': 'virusintl.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
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


# ── Polite HTTP ───────────────────────────────────────────────────────────
# Shopify rate-limits per IP across every store on the platform, so scraping
# several Shopify storefronts in parallel trips a shared budget: a full run
# followed too closely by another returned 429 for 23 of 26 stores. The old
# code made this invisible — the Shopify/Magento/WooCommerce extractors called
# requests.get directly and treated any non-200 as "no more pages", so a
# throttled store silently produced zero products and the scrape still reported
# success. Everything now goes through http_get, which retries 429/5xx with
# exponential backoff and keeps a minimum gap between requests to one host.

_host_lock = threading.Lock()
_host_next_allowed = {}

MIN_HOST_INTERVAL = float(os.environ.get('SCRAPE_HOST_INTERVAL', '0.7'))  # seconds
MAX_RETRIES = 4


def _throttle(host):
    """Block until this host's minimum request interval has elapsed."""
    while True:
        with _host_lock:
            now = time.monotonic()
            ready_at = _host_next_allowed.get(host, 0.0)
            if now >= ready_at:
                _host_next_allowed[host] = now + MIN_HOST_INTERVAL
                return
            wait = ready_at - now
        time.sleep(wait)


def http_get(url, params=None, timeout=TIMEOUT, headers=None):
    """GET with per-host throttling and backoff. Returns a Response or None.

    Returns None only after the retries are exhausted or the server gave a
    definite non-retryable answer, so callers can distinguish "no more data"
    (200 with an empty body) from "we were blocked" (None).
    """
    host = urlparse(url).netloc
    delay = 2.0
    for attempt in range(MAX_RETRIES):
        _throttle(host)
        try:
            r = requests.get(url, params=params, headers=headers or HEADERS,
                             timeout=timeout)
        except requests.RequestException:
            time.sleep(delay + random.uniform(0, 0.5))
            delay *= 2
            continue

        if r.status_code == 200:
            return r
        if r.status_code in (429, 503):
            try:
                wait = float(r.headers.get('Retry-After', delay))
            except (TypeError, ValueError):
                wait = delay
            wait = min(max(wait, 1.0), 60.0)
            # Back the whole host off, not just this request.
            with _host_lock:
                _host_next_allowed[host] = time.monotonic() + wait
            time.sleep(wait + random.uniform(0, 0.5))
            delay *= 2
            continue
        if 500 <= r.status_code < 600:
            time.sleep(delay + random.uniform(0, 0.5))
            delay *= 2
            continue
        return None       # 404 and friends: no point retrying
    return None


def fetch_page(url):
    r = http_get(url)
    return r.text if r is not None else None


# ── Parser: Shopify public /products.json API ────────────────────────────
# Every Shopify storefront exposes its collections as JSON by appending
# /products.json to the collection URL (e.g. .../collections/barbells ->
# .../collections/barbells/products.json), paginated via ?limit=250&page=N.
# This is far more reliable than scraping the rendered HTML/preloaded JSON
# blob (themes vary, markup changes), and it comes with real product images
# and handles for free. Also works for a handful of sites that block the
# plain HTML collection page but not this API (TYR, LUXIAOJUN, SBD, Virus,
# Gymreapers, Inzer, Born Primitive, Again Faster) — verified live, so those
# no longer need Playwright.

def extract_shopify_collection_json(collection_url, currency='USD', min_price=1.0):
    products = []
    seen = set()
    base = collection_url.split('?')[0].rstrip('/')
    domain = re.sub(r'^https?://', '', base).split('/')[0]
    page = 1

    while page <= 20:  # safety cap — no collection realistically has 5000+ items
        r = http_get(f'{base}/products.json', params={'limit': 250, 'page': page})
        if r is None:
            # Blocked or unreachable — distinct from "collection is empty".
            raise ScrapeBlocked(f'{domain} did not answer /products.json (page {page})')
        try:
            items = r.json().get('products', [])
        except ValueError:
            break
        if not items:
            break

        for item in items:
            title = (item.get('title') or '').strip()
            handle = item.get('handle', '')
            key = handle or title
            if not title or key in seen:
                continue
            seen.add(key)

            price = None
            for v in item.get('variants', []) or []:
                vp = safe_price(v.get('price'))
                if vp and vp >= min_price and (price is None or vp < price):
                    price = vp

            images = item.get('images') or []
            image_url = images[0].get('src') if images else None

            products.append({
                'name': title,
                'price': price,
                'price_text': f'${price:.2f}' if price else None,
                'currency': currency,
                'url': f'https://{domain}/products/{handle}' if handle else None,
                'image_url': image_url,
            })

        if len(items) < 250:
            break
        page += 1

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
        r = http_get(url)
        if r is None:
            if page == 1:
                raise ScrapeBlocked(f'{base_url} did not answer')
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
            img_el = item.select_one('img.product-image-photo') or item.select_one('img')
            image_url = img_el.get('src') if img_el else None
            products.append({
                'name': name,
                'price': round(price, 2),
                'price_text': f'${price:.2f}',
                'currency': currency,
                'url': href,
                'image_url': image_url,
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
        r = http_get(url)
        if r is None:
            if page == 1:
                raise ScrapeBlocked(f'{url_base} did not answer')
            break
        try:
            items = r.json()
        except ValueError:
            break
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
            images = item.get('images') or []
            image_url = images[0].get('src') if images else None

            products.append({
                'name': name,
                'price': round(price, 2),
                'price_text': f'${price:.2f}',
                'currency': currency,
                'url': url,
                'image_url': image_url,
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

        img_el = item.find('img')
        image_url = urljoin('https://www.liftinglarge.com/', img_el['src']) if img_el and img_el.get('src') else None

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
    # Buffered so parallel workers don't interleave their output.
    log = []
    empty_categories = []
    blocked = []

    # Special case: WooCommerce Store API (no category iteration)
    if parser == 'woocommerce_store':
        api_url = config.get('api_url', '')
        try:
            products = extract_woocommerce_store(api_url, config.get('currency', 'USD'))
        except ScrapeBlocked as e:
            return {'site': config['name'], 'status': 'blocked', 'error': str(e),
                    'products': [], 'log': [f'  BLOCKED — {e}']}
        for p in products:
            p['site'] = config['name']
            p['category'] = 'All'
            p['source_url'] = api_url
        all_products.extend(products)
        log.append(f'  All products: {len(products)}')
        return {
            'site': config['name'],
            'status': 'ok' if all_products else 'empty',
            'products': all_products,
            'log': log,
            'empty_categories': [] if all_products else ['All'],
            'scraped_at': datetime.now(timezone.utc).isoformat(),
        }

    urls = config.get('urls', {})

    for category, url in urls.items():
        # LiftingLarge: use viewall=1 to get all products on one page
        if parser == 'liftinglarge' and '?' not in url:
            url = url + '?viewall=1'

        try:
            if parser == 'magento':
                # Magento handles fetching + pagination internally
                products = extract_magento(url, config.get('currency', 'USD'))
            elif parser == 'shopify_preload':
                # Hits the collection's /products.json API directly — no need to
                # fetch/parse the HTML page for this parser.
                products = extract_shopify_collection_json(
                    url, config.get('currency', 'USD'))
            else:
                products = None
        except ScrapeBlocked as e:
            log.append(f'  {category}: BLOCKED — {e}')
            blocked.append(category)
            continue

        if products is None:
            html = fetch_page(url)
            if not html:
                log.append(f'  {category}: FAILED to fetch ({url})')
                empty_categories.append(category)
                continue

            if parser == 'liftinglarge':
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
        log.append(f'  {category}: {len(products)} products')
        if not products:
            empty_categories.append(category)

    if blocked and not all_products:
        # Every category we tried was refused — report it as an error rather
        # than an empty store, so the run surfaces it.
        return {
            'site': config['name'],
            'log': log,
            'status': 'blocked',
            'error': f'rate-limited or blocked on {len(blocked)} categories',
            'empty_categories': empty_categories,
            'blocked_categories': blocked,
            'products': [],
        }

    return {
        'site': config['name'],
        'log': log,
        'empty_categories': empty_categories,
        'blocked_categories': blocked,
        'status': 'ok' if all_products else 'empty',
        'products': all_products,
        'scraped_at': datetime.now(timezone.utc).isoformat(),
    }


def _scrape_one(key, config, use_browser):
    """Scrape a single site. Always returns a result dict, never raises."""
    name = config['name']
    if config.get('requires_browser') and not use_browser:
        return {'site': name, 'status': 'requires_browser', 'products': []}
    try:
        if config.get('requires_browser'):
            import importlib
            browser_mod = importlib.import_module('browser_scraper')
            browser_key = config.get('browser_key')
            items = browser_mod.scrape_site_playwright(
                browser_key, browser_mod.SITES[browser_key])
            return {
                'site': name,
                'status': 'ok',
                'products': items,
                'scraped_at': datetime.now(timezone.utc).isoformat(),
            }
        return scrape_site(key, config)
    except Exception as e:
        return {'site': name, 'status': 'error', 'error': str(e), 'products': []}


def scrape_all(site_filter=None, category_filter=None, use_browser=False, workers=3):
    """Scrape every configured site, in parallel.

    Sites are independent and the work is almost entirely network-bound, so a
    thread pool cuts a full run from the sum of all sites to roughly the slowest
    one. Playwright's sync API is not thread-safe, so browser sites are scraped
    on the calling thread after the HTTP pool drains.
    """
    targets = [(k, c) for k, c in SITES.items() if not site_filter or k == site_filter]
    http_targets = [(k, c) for k, c in targets if not c.get('requires_browser')]
    browser_targets = [(k, c) for k, c in targets if c.get('requires_browser')]

    results = []
    if http_targets:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_scrape_one, k, c, use_browser): c['name']
                       for k, c in http_targets}
            for fut in as_completed(futures):
                r = fut.result()
                results.append(r)
                count = len(r.get('products', []))
                mark = 'ok' if r['status'] == 'ok' and count else r['status'].upper()
                print(f'  {r["site"]:26s} {count:5d} products  [{mark}]', flush=True)
                if r.get('error'):
                    print(f'      ERROR: {r["error"]}', flush=True)
                for line in r.get('log', []):
                    if 'FAILED' in line or ': 0 products' in line:
                        print(f'    {line.strip()}', flush=True)

    for k, c in browser_targets:
        print(f'\n── {c["name"]} ──', flush=True)
        r = _scrape_one(k, c, use_browser)
        results.append(r)
        print(f'  {len(r.get("products", []))} products  [{r["status"]}]', flush=True)
        if r.get('error'):
            print(f'  ERROR: {r["error"]}', flush=True)

    # Keep output order stable regardless of completion order.
    order = {c['name']: i for i, (_, c) in enumerate(targets)}
    results.sort(key=lambda r: order.get(r['site'], 999))

    all_products = []
    for r in results:
        all_products.extend(r.get('products', []))

    if category_filter:
        all_products = [
            p for p in all_products
            if category_filter.lower() in (p.get('category') or '').lower()
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