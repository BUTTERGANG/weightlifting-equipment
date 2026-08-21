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
import os
import shutil
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def chromium_executable():
    """Locate a usable Chromium.

    Playwright defaults to a browser it downloads into PLAYWRIGHT_BROWSERS_PATH,
    which on Replit points at an ephemeral cache that is routinely empty — the
    symptom is ``Executable doesn't exist at .../chromium_headless_shell-.../``
    and a silent zero-product scrape for every browser site. Replit publishes a
    working build via REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE, and a nix chromium
    is usually on PATH, so prefer those and fall back to Playwright's bundled
    download only if neither is present.
    """
    candidates = [
        os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),
        os.environ.get('REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE'),
        shutil.which('chromium'),
        shutil.which('chromium-browser'),
        shutil.which('google-chrome'),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


# ── Rogue (Vue SPA, DOM extraction) ───────────────────────────────────────

# Verified live. The previous 'Plates' and 'Racks & Rigs' URLs had started
# 404ing after a Rogue site reorganisation, silently contributing zero products.
ROGUE_CATEGORIES = {
    'Barbells': 'https://www.roguefitness.com/weightlifting-bars-plates/barbells',
    'Plates': 'https://www.roguefitness.com/weightlifting-bars-plates/bumpers/bumper-plates',
    'Competition Plates': 'https://www.roguefitness.com/weightlifting-bars-plates/bumpers/competition-bumpers',
    'Steel Plates': 'https://www.roguefitness.com/weightlifting-bars-plates/bumpers/steel-plates',
    'Racks': 'https://www.roguefitness.com/rogue-rigs-racks/power-racks',
    'Collars': 'https://www.roguefitness.com/weightlifting-bars-plates/collars',
}


# The category grid. `a.product` looked right but is the "popular products"
# carousel — it returned the same 12 items (Echo Bike, kettlebells…) on every
# category page. The real tiles are hover-cards inside .products-wrapper.
GRID_SELECTOR = '.products-wrapper a.hover-card'
JSON_GRID_SELECTOR = repr(GRID_SELECTOR)


def _autoscroll(page, max_steps=12):
    """Rogue lazy-loads its grid on scroll; step down until the count settles."""
    previous = -1
    for _ in range(max_steps):
        count = page.evaluate(
            "() => document.querySelectorAll(%s).length" % JSON_GRID_SELECTOR)
        if count == previous:
            break
        previous = count
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(600)
    return previous


def extract_products_from_page(page):
    """Extract the products in the category grid.

    Scoped to GRID_SELECTOR — the category grid's own tiles. The original
    selector (`a[href*="/rogue-"]`) matched every Rogue link on the page, so it
    swept up nav entries and carousels: on the Barbells page that turned 70 real
    barbells into 105 rows including kettlebells, shirts and an Echo Bike, all
    labelled "Barbells", plus ~40 price-less nav links.
    """
    return page.evaluate(r"""(GRID) => {
        const cards = Array.from(document.querySelectorAll(GRID));
        const seen = new Map();

        cards.forEach(a => {
            const priceEl = a.querySelector('[class*="price"], [class*="amount"]');
            const rawText = (a.textContent || '').trim();
            const priceSource = priceEl ? priceEl.textContent : rawText;
            const priceMatch = (priceSource || '').match(/\$[\d,]+(?:\.\d{2})?/);
            if (!priceMatch) return;                       // nav/junk link
            const priceText = priceMatch[0];
            const price = parseFloat(priceText.replace(/[$,]/g, ''));
            if (!price || price <= 0) return;

            // The card's text is "Name$123.00" — strip the price and any rating.
            let name = rawText
                .replace(/\$[\d,]+(?:\.\d{2})?[\s\S]*$/, '')
                .replace(/[★☆]+.*$/, '')
                .replace(/\s+/g, ' ')
                .trim();
            if (!name || name.length < 3) return;

            const url = a.href || '';
            if (!url || seen.has(url)) return;

            let imageUrl = null;
            const imgEl = a.querySelector('img');
            if (imgEl) {
                imageUrl = imgEl.currentSrc || imgEl.src
                    || imgEl.getAttribute('data-src') || null;
                if (imageUrl && imageUrl.startsWith('data:')) imageUrl = null;
                if (imageUrl && imageUrl.startsWith('//')) imageUrl = 'https:' + imageUrl;
            }

            seen.set(url, {
                name: name,
                price: price,
                price_text: priceText,
                url: url,
                image_url: imageUrl,
            });
        });

        return Array.from(seen.values());
    }""", GRID_SELECTOR)


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

    executable = chromium_executable()
    with sync_playwright() as p:
        launch_kwargs = {'headless': headless}
        if executable:
            launch_kwargs['executable_path'] = executable
        else:
            print('  (no system chromium found — using Playwright bundled build)', flush=True)
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
            viewport={'width': 1920, 'height': 1080},
        )
        page = context.new_page()

        for cat_name, url in config['categories'].items():
            try:
                print(f'  {cat_name}...', end=' ', flush=True)
                page.goto(url, wait_until='load', timeout=45000)
                # Wait for the grid itself rather than guessing at a hydration
                # delay — slower category pages were being read while empty.
                try:
                    page.wait_for_selector(GRID_SELECTOR, timeout=20000)
                except Exception:
                    print('(grid never appeared)', end=' ', flush=True)
                _autoscroll(page)

                items = extract_products_from_page(page)

                print(f'{len(items)} products', flush=True)

                for item in items:
                    item['site'] = config['name']
                    item['category'] = cat_name
                    item['currency'] = config['currency']
                    item['source_url'] = url

                all_products.extend(items)

            except Exception as e:
                print(f'  ERROR: {e}', flush=True)

        browser.close()

    # Deduplicate by product URL — the retailer's own identity for the item.
    # Keying on name would merge distinct variants that share a display name.
    seen = set()
    deduped = []
    for p in all_products:
        key = p.get('url') or p.get('name', '').lower()
        if key and key not in seen:
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