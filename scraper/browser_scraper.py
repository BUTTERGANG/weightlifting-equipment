#!/usr/bin/env python3
"""
Scrape browser-required weightlifting equipment sites via Playwright.
Handles sites that rate-limit or block plain HTTP requests:
- Rogue Fitness (Vue SPA - DOM extraction)
- TYR Sport (Shopify, rate-limited)
- LUXIAOJUN (Shopify, rate-limited)
"""

import re
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


# ── Rogue (Vue SPA, DOM extraction) ───────────────────────────────────────

ROGUE_CATEGORIES = {
    'Barbells': 'https://www.roguefitness.com/weightlifting-bars-plates/barbells',
    'Plates': 'https://www.roguefitness.com/weightlifting-bars-plates/barbell-plates',
    'Racks & Rigs': 'https://www.roguefitness.com/strength-training/racks-rigs',
}


def extract_products_from_page(page):
    """Extract all products visible on the current Rogue page."""
    data = page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll('a[href*="/rogue-"]'));
        const seen = new Map();

        links.forEach(a => {
            const name = (a.textContent || a.innerText || '').trim();
            if (!name || name.length < 5) return;
            if (name.includes('Gym Tour') || name.includes('Equipped')) return;

            if (!seen.has(name)) {
                const card = a.closest('[class*="product"], [class*="item"], li, div') || a.parentElement;

                // Find price - look for spans/divs with price classes or $ signs
                let price = '';
                const priceEl = card ? card.querySelector(
                    '[class*="price"], [class*="amount"], [class*="sale"]'
                ) : null;
                if (priceEl) {
                    price = (priceEl.textContent || priceEl.innerText || '').trim();
                }

                // Extract numeric price - clean up any trailing junk
                const priceMatch = (price || name).match(/\\$[\\d,]+(?:\\.\\d+)?/);
                const cleanPrice = priceMatch ? priceMatch[0] : '';
                // Extract numeric value
                const priceNum = cleanPrice ? parseFloat(cleanPrice.replace(/[$,]/g, '')) : null;
                // Clean up name - remove trailing price and ratings
                let cleanName = name.replace(/\\$[\\d,.]+/, '').replace(/[★☆]+.*$/, '').trim();

                seen.set(cleanName, {
                    name: cleanName,
                    price: priceNum,
                    price_text: cleanPrice,
                    url: a.href || '',
                });
            }
        });

        return Array.from(seen.values());
    }""")
    return data


# ── Shopify preloaded JSON extraction (TYR, LUXIAOJUN) ────────────────────

def extract_shopify_preload(page, min_price=1.0):
    """
    Extract products from Shopify preloaded JSON:
    "price":12345,"name":"Product Name" (price in cents)
    """
    data = page.evaluate("""() => {
        const html = document.documentElement.innerHTML;
        const regex = /\\"price\\":(\\d+),\\"name\\":\\"([^\\"]+)\\"/g;
        const items = [];
        const seen = new Set();
        let m;
        while ((m = regex.exec(html)) !== null) {
            const price = parseInt(m[1]) / 100;
            const name = m[2];
            // Clean up escaped unicode
            const cleanName = name.replace(/\\\\u0026/g, '&').replace(/\\\\u0027/g, "'");
            const key = cleanName + '|' + m[1];
            if (!seen.has(key)) {
                seen.add(key);
                items.push({
                    name: cleanName,
                    price: price,
                    price_text: '$' + price.toFixed(2),
                    url: '',
                });
            }
        }
        return items;
    }""")

    # Filter out variants: collapse to base product name + min price
    products = {}
    for item in data:
        if item['price'] < min_price:
            continue
        # Base name = everything before " - " (variant separator)
        base = item['name'].split(' - ')[0].strip()
        # Also handle "/" variant separators in names like "Black / XS"
        if ' / ' in base:
            base = base.split(' / ')[0].strip()
        if base not in products or item['price'] < products[base]['price']:
            products[base] = {
                'name': base,
                'price': item['price'],
                'price_text': '$' + f"{item['price']:.2f}",
                'currency': 'USD',
                'url': item.get('url', ''),
            }

    return list(products.values())


# ── Site configs ──────────────────────────────────────────────────────────

SITES = {
    'rogue': {
        'name': 'Rogue Fitness',
        'mode': 'dom',
        'categories': ROGUE_CATEGORIES,
        'currency': 'USD',
    },
    'tyr': {
        'name': 'TYR Sport',
        'mode': 'shopify_preload',
        'categories': {
            'Lifters': 'https://tyr.com/collections/lifters',
            'L-1 Lifters': 'https://tyr.com/collections/mens-l-1-lifters',
            'L-2 Lifters': 'https://tyr.com/collections/mens-l-2-lifters',
            'Women\'s Lifters': 'https://tyr.com/collections/womens-lifters',
            'Trainers': 'https://tyr.com/collections/footwear-trainers',
            'Barefoot': 'https://tyr.com/collections/footwear-barefoot',
            'Accessories': 'https://tyr.com/collections/accessories',
        },
        'currency': 'USD',
    },
    'luxiaojun': {
        'name': 'LUXIAOJUN',
        'mode': 'shopify_preload',
        'categories': {
            'Weightlifting Shoes': 'https://luxiaojun.com/collections/weightlifting-shoes',
            'Shoes': 'https://luxiaojun.com/collections/shoes',
            'Barefoot': 'https://luxiaojun.com/collections/barefoot-shoes',
            'Apparel': 'https://luxiaojun.com/collections/apparel',
            'Gear': 'https://luxiaojun.com/collections/gear',
        },
        'currency': 'USD',
    },
    'sbd': {
        'name': 'SBD Apparel',
        'mode': 'shopify_preload',
        'categories': {
            'All': 'https://sbdapparel.com/collections/all',
        },
        'currency': 'USD',
    },
    'virus': {
        'name': 'Virus International',
        'mode': 'shopify_preload',
        'categories': {
            "Singlets": 'https://virusintl.com/collections/mens-and-womens-singlets',
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
        'currency': 'USD',
    },
    'gymreapers': {
        'name': 'Gymreapers',
        'mode': 'shopify_preload',
        'categories': {
            'Belts': 'https://www.gymreapers.com/collections/10mm-lever-belts',
            'Knee Sleeves': 'https://www.gymreapers.com/collections/powerlifting-knee-sleeves',
            'Elbow Sleeves': 'https://www.gymreapers.com/collections/elbow-sleeves',
            'Wrist Wraps': 'https://www.gymreapers.com/collections/wrist-wraps',
            'Straps': 'https://www.gymreapers.com/collections/lifting-straps',
            'Accessories': 'https://www.gymreapers.com/collections/accessories',
            'Apparel': 'https://www.gymreapers.com/collections/shirts',
            'Equipment': 'https://www.gymreapers.com/collections/equipment',
        },
        'currency': 'USD',
    },
    'inzer': {
        'name': 'Inzer Advance Designs',
        'mode': 'shopify_preload',
        'categories': {
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
        'currency': 'USD',
    },
    'bornprimitive': {
        'name': 'Born Primitive',
        'mode': 'shopify_preload',
        'categories': {
            'Men': 'https://bornprimitive.com/collections/born-primitive-all-men',
            'Women': 'https://bornprimitive.com/collections/born-primitive-all-women',
            'Accessories': 'https://bornprimitive.com/collections/accessories',
        },
        'currency': 'USD',
    },
    'againfaster': {
        'name': 'Again Faster',
        'mode': 'shopify_preload',
        'categories': {
            'Racks': 'https://www.againfaster.com/collections/freestanding-racks-and-rigs',
            'Barbells': 'https://www.againfaster.com/collections/barbells',
            'Plates': 'https://www.againfaster.com/collections/competition-plates-kg',
            'Benches': 'https://www.againfaster.com/collections/benches',
            'Dumbbells': 'https://www.againfaster.com/collections/dumbbells',
            'Accessories': 'https://www.againfaster.com/collections/comp-accessories',
            'Footwear': 'https://www.againfaster.com/collections/footwear',
        },
        'currency': 'USD',
    },
}


def scrape_site_playwright(site_key, config, headless=True):
    """Scrape one browser-required site."""
    all_products = []
    start_time = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
            viewport={'width': 1920, 'height': 1080},
        )
        page = context.new_page()

        for cat_name, url in config['categories'].items():
            try:
                print(f'  {cat_name}...', end=' ', flush=True)
                page.goto(url, wait_until='load', timeout=30000)
                page.wait_for_timeout(2500)  # Let JS hydrate

                if config['mode'] == 'dom':
                    items = extract_products_from_page(page)
                else:
                    items = extract_shopify_preload(page)

                print(f'{len(items)} products', flush=True)

                for item in items:
                    item['site'] = config['name']
                    item['category'] = cat_name
                    item['currency'] = config['currency']

                all_products.extend(items)

            except Exception as e:
                print(f'  ERROR: {e}', flush=True)

        browser.close()

    # Deduplicate by name
    seen = set()
    deduped = []
    for p in all_products:
        key = (p.get('name', '').lower(), p.get('category', ''))
        if key[0] and key not in seen:
            seen.add(key)
            deduped.append(p)

    elapsed = time.time() - start_time
    print(f'  {config["name"]} total: {len(deduped)} unique products in {elapsed:.1f}s')
    return deduped


def scrape_all_browser(site_filter=None, headless=True):
    """Scrape all browser-required sites."""
    results = {}
    for key, config in SITES.items():
        if site_filter and key != site_filter:
            continue
        print(f'\n── {config["name"]} ──')
        try:
            products = scrape_site_playwright(key, config, headless=headless)
            results[key] = products
        except Exception as e:
            print(f'  ERROR: {e}')
            results[key] = []
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Browser scraper for Rogue/TYR/LUXIAOJUN')
    parser.add_argument('--site', '-s', choices=list(SITES.keys()), help='Scrape only this site')
    parser.add_argument('--visible', action='store_true', help='Run with visible browser')
    parser.add_argument('--output', '-o', help='Output file')
    args = parser.parse_args()

    results = scrape_all_browser(site_filter=args.site, headless=not args.visible)

    all_products = []
    for site_products in results.values():
        all_products.extend(site_products)

    if args.output:
        Path(args.output).write_text(json.dumps(all_products, indent=2))
        print(f'\nSaved {len(all_products)} products to {args.output}')
    else:
        # Print summary
        for key, products in results.items():
            print(f'{SITES[key]["name"]}: {len(products)} products')
        print(f'TOTAL: {len(all_products)} products')


if __name__ == '__main__':
    main()