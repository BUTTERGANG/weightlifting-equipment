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
from urllib.parse import urljoin

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
            'Belts': 'https://store.weightliftinghouse.com/collections/belts',
            'Knee Sleeves': 'https://store.weightliftinghouse.com/collections/knee-sleeves',
            'Straps': 'https://store.weightliftinghouse.com/collections/straps',
            'Wrist Wraps': 'https://store.weightliftinghouse.com/collections/wrist-wraps',
            'Tape': 'https://store.weightliftinghouse.com/collections/tape',
            'Singlets': 'https://store.weightliftinghouse.com/collections/singlet-all',
            'Equipment': 'https://store.weightliftinghouse.com/collections/equipment',
        },
        'domain': 'store.weightliftinghouse.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'rivalsteel': {
        'name': 'Rival Steel',
        'urls': {
            'Barbells': 'https://rivalsteelfitness.com/collections/barbells',
            'Bumper Plates': 'https://rivalsteelfitness.com/collections/bumper-plates',
            'Plates': 'https://rivalsteelfitness.com/collections/plates',
            'Dumbbells': 'https://rivalsteelfitness.com/collections/dumbbells',
            'Racks': 'https://rivalsteelfitness.com/collections/racks',
            'Benches': 'https://rivalsteelfitness.com/collections/weight-lifitng-benches',
            'Rack Attachments': 'https://rivalsteelfitness.com/collections/rack-attachments',
            'Olympic Weight Sets': 'https://rivalsteelfitness.com/collections/olympic-weight-sets',
        },
        'domain': 'rivalsteelfitness.com',
        'currency': 'USD',
        'parser': 'shopify_preload',
    },
    'element26': {
        'name': 'Element 26',
        'urls': {
            'Powerlifting Belts': 'https://element26.co/collections/weightlifting-belts',
            'Special Edition Belts': 'https://element26.co/collections/special-edition-belts',
            'Knee Sleeves': 'https://element26.co/collections/sleeves',
            'Lifting Straps': 'https://element26.co/collections/weightlifting-straps',
            'Grips': 'https://element26.co/collections/grips',
            'Gym Equipment': 'https://element26.co/collections/gym-equipment',
        },
        'domain': 'element26.co',
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
        r = requests.get(f'{base}/products.json', params={'limit': 250, 'page': page},
                          headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            break
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
        elif parser == 'shopify_preload':
            # Hits the collection's /products.json API directly — no need to
            # fetch/parse the HTML page for this parser.
            products = extract_shopify_collection_json(url, config.get('currency', 'USD'))
        else:
            html = fetch_page(url)
            if not html:
                print(f'  {category}: failed ({url})')
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