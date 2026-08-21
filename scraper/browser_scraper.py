#!/usr/bin/env python3
"""
Scrape browser-required weightlifting equipment sites via Playwright.

Rogue Fitness is the only site left here: it's a Vue SPA with no public JSON
API, so it needs a real rendered DOM. Every other site that used to live here
(TYR, LUXIAOJUN, SBD, Virus, Gymreapers, Inzer, Born Primitive, Again Faster)
turned out to expose Shopify's public /products.json API even though their
HTML collection pages block plain requests — so they moved to
equipment_scraper.py's shopify_preload parser, which is faster, more
reliable, and gets product images for free. See equipment_scraper.py's
extract_shopify_collection_json().
"""

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

                // Find an image within the card — prefer loaded src, fall back
                // to common lazy-load attributes.
                let imageUrl = null;
                const imgEl = card ? card.querySelector('img') : null;
                if (imgEl) {
                    imageUrl = imgEl.currentSrc || imgEl.src || imgEl.getAttribute('data-src')
                        || imgEl.getAttribute('data-srcset') || null;
                    if (imageUrl && imageUrl.startsWith('data:')) imageUrl = null;
                    if (imageUrl && imageUrl.startsWith('//')) imageUrl = 'https:' + imageUrl;
                }

                seen.set(cleanName, {
                    name: cleanName,
                    price: priceNum,
                    price_text: cleanPrice,
                    url: a.href || '',
                    image_url: imageUrl,
                });
            }
        });

        return Array.from(seen.values());
    }""")
    return data


# ── Site configs ──────────────────────────────────────────────────────────

SITES = {
    'rogue': {
        'name': 'Rogue Fitness',
        'categories': ROGUE_CATEGORIES,
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

                items = extract_products_from_page(page)

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
    parser = argparse.ArgumentParser(description='Browser scraper for Rogue Fitness')
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