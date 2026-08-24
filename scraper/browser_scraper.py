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

                // Find image in the product card
                let imgSrc = '';
                const productImg = card ? card.querySelector('img[src*="rogue"], img[src*="cdn"], img') : null;
                if (productImg) {
                    imgSrc = productImg.getAttribute('src') || productImg.getAttribute('data-src') || '';
                    if (imgSrc && imgSrc.startsWith('//')) imgSrc = 'https:' + imgSrc;
                    const cleanMatch = imgSrc.match(new RegExp('https?://[^?]+'));
                    if (cleanMatch) imgSrc = cleanMatch[1].substring(0, 250);
                }

                seen.set(cleanName, {
                    name: cleanName,
                    price: priceNum,
                    price_text: cleanPrice,
                    url: a.href || '',
                    image_url: imgSrc || '',
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

    Also extracts product images from the rendered DOM.
    """
    data = page.evaluate("""() => {
        const html = document.documentElement.innerHTML;
        const regex = /\\\"price\\\":(\\d+),\\\"name\\\":\\\"([^\\\"]+)\\\"/g;
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


def fill_missing_images(site, handles_and_urls, headless=True):
    """Batch-fetch product images for one Playwright site.
    
    Args:
        site: site config dict from SITES
        handles_and_urls: list of (product_handle, product_url) tuples
    
    Returns:
        dict mapping handle -> image_url
    """
    from urllib.parse import urlparse
    import time
    
    if not handles_and_urls:
        return {}
    
    domain = urlparse(handles_and_urls[0][1]).netloc if handles_and_urls[0][1] else ''
    results = {}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'),
            viewport={'width': 1920, 'height': 1080},
        )
        page = context.new_page()
        
        # Visit one page to establish the session, then use fetch() for products
        first_url = handles_and_urls[0][1]
        try:
            page.goto(first_url, wait_until='domcontentloaded', timeout=15000)
        except:
            pass
        
        batch_size = 20
        for i in range(0, len(handles_and_urls), batch_size):
            batch = handles_and_urls[i:i+batch_size]
            
            # Fetch product JSON via in-page fetch
            json_results = page.evaluate("""(urls) => {
                return Promise.all(urls.map(async (url) => {
                    try {
                        const jsonUrl = url.replace(/\\/products\\//, '/products/').replace(/\\?.*$/, '') + '.json';
                        const resp = await fetch(jsonUrl, {signal: AbortSignal.timeout(8000)});
                        if (!resp.ok) return null;
                        const data = await resp.json();
                        const product = data.product || data;
                        const img = product.featured_image || (product.images && product.images[0] ? product.images[0].src : null) || null;
                        return {url: url, image: img ? (img.startsWith('//') ? 'https:' + img : img) : null};
                    } catch(e) {
                        return null;
                    }
                }));
            }""", [u for _, u in batch])
            
            for entry in json_results:
                if entry and entry.get('image'):
                    results[entry['url']] = entry['image']
            
            if i + batch_size < len(handles_and_urls):
                time.sleep(1)
        
        browser.close()
    
    return results


def extract_product_images(page):
    """Extract product name -> image URL mappings from rendered DOM.
    Scrolls down to trigger lazy-loaded images, then finds product cards
    and their associated images. Handles <img>, <picture>/<source>,
    and CSS background-image patterns."""
    images = page.evaluate("""() => {
        // Scroll down gradually to trigger lazy loading (up to 10 viewports)
        const viewportH = window.innerHeight;
        for (let i = 0; i < 15; i++) {
            window.scrollBy(0, viewportH);
        }

        // Now find product images near product links
        const results = [];
        const seenNames = new Set();

        // Look for links pointing to product pages
        const links = document.querySelectorAll('a[href*="/products/"]');
        for (const link of links) {
            const name = (link.textContent || link.innerText || '').trim();
            if (!name || name.length < 5) continue;
            if (['Learn More', 'Quick View', 'Add to Cart', 'Shop Now', 'Read More', 'View Product'].includes(name)) continue;

            const key = name.substring(0, 30);
            if (seenNames.has(key)) continue;
            seenNames.add(key);

            // Walk up to find a container
            let container = link.closest('[class*="product"], [class*="card"], [class*="item"], [class*="grid"], li, div');
            if (!container) container = link.parentElement;

            let imgSrc = null;

            if (container) {
                // Method 1: <img> tags
                const img = container.querySelector('img');
                if (img) {
                    imgSrc = img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || '';
                }

                // Method 2: <picture> / <source> srcset
                if (!imgSrc || !imgSrc.includes('//')) {
                    const source = container.querySelector('source[srcset]');
                    if (source) {
                        const srcset = source.getAttribute('srcset') || '';
                        if (srcset) {
                            imgSrc = srcset.split(',')[0].trim().split(' ')[0];
                        }
                    }
                }

                // Method 3: CSS background-image on any element
                if (!imgSrc || !imgSrc.includes('//')) {
                    const allEls = container.querySelectorAll('*');
                    for (const el of allEls) {
                        const style = window.getComputedStyle(el);
                        if (style.backgroundImage && style.backgroundImage !== 'none') {
                            const match = style.backgroundImage.match(/url\\(['"]?([^'"')]+)['"]?\\)/);
                            if (match && match[1].includes('//')) {
                                imgSrc = match[1];
                                break;
                            }
                        }
                    }
                }

                // Method 4: Check inline style for background-image
                if (!imgSrc || !imgSrc.includes('//')) {
                    const styleAttr = container.getAttribute('style') || '';
                    if (styleAttr.includes('url(')) {
                        const match = styleAttr.match(/url\\(['"]?([^'"')]+)['"]?\\)/);
                        if (match && match[1].includes('//')) {
                            imgSrc = match[1];
                        }
                    }
                }
            }

            // Clean the URL
            if (imgSrc) {
                if (imgSrc.startsWith('//')) imgSrc = 'https:' + imgSrc;
                if (!imgSrc.startsWith('http')) imgSrc = null;
                if (imgSrc) {
                    // Remove query params for cleaner URL
                    const clean = imgSrc.match(/(https?:\\/\\/[^?]+)/);
                    if (clean) imgSrc = clean[1];
                    imgSrc = imgSrc.substring(0, 250);
                }
            }

            if (imgSrc) results.push([name.substring(0, 80), imgSrc]);
        }
        return results;
    }""")
    return images


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
    'cerberus': {
        'name': 'Cerberus Strength',
        'mode': 'shopify_preload',
        'categories': {
            'Belts': 'https://cerberus-strength.us/collections/powerlifting-belts',
            'Sleeves': 'https://cerberus-strength.us/collections/powerlifting-sleeves',
            'Wraps': 'https://cerberus-strength.us/collections/powerlifting-wraps',
            'Singlets': 'https://cerberus-strength.us/collections/powerlifting-singlets',
            'Deadlift Suits': 'https://cerberus-strength.us/collections/deadlift-suits',
            'Plates': 'https://cerberus-strength.us/collections/weight-plates',
            'Barbells': 'https://cerberus-strength.us/collections/weightlifting-barbells',
            'Apparel': 'https://cerberus-strength.us/collections/apparel',
            'Accessories': 'https://cerberus-strength.us/collections/gym-accessories',
            'Strongman': 'https://cerberus-strength.us/collections/strongman',
        },
        'currency': 'USD',
    },
    'wh': {
        'name': 'Weightlifting House',
        'mode': 'shopify_preload',
        'categories': {
            'Equipment': 'https://store.weightliftinghouse.com/collections/equipment',
            'Belts': 'https://store.weightliftinghouse.com/collections/belts',
            'Knee Sleeves': 'https://store.weightliftinghouse.com/collections/knee-sleeves',
            'Wrist Wraps': 'https://store.weightliftinghouse.com/collections/wrist-wraps',
            'Straps': 'https://store.weightliftinghouse.com/collections/straps',
            'Tape': 'https://store.weightliftinghouse.com/collections/tape',
            'Singlets': 'https://store.weightliftinghouse.com/collections/singlet-all',
            'Bars': 'https://store.weightliftinghouse.com/collections/bars',
            'Bags': 'https://store.weightliftinghouse.com/collections/bags',
            'Accessories': 'https://store.weightliftinghouse.com/collections/accessories-mens',
            'Accessories Women': 'https://store.weightliftinghouse.com/collections/accessories-women',
            'Lightweight Wraps': 'https://store.weightliftinghouse.com/collections/lightweight-wraps',
            'Leather Straps': 'https://store.weightliftinghouse.com/collections/leather-straps',
        },
        'currency': 'USD',
    },
    'rep': {
        'name': 'REP Fitness',
        'mode': 'shopify_preload',
        'categories': {
            'Barbells': 'https://www.repfitness.com/collections/barbells',
            'Plates': 'https://www.repfitness.com/collections/bumper-plates',
            'Racks': 'https://www.repfitness.com/collections/power-racks',
        },
        'currency': 'USD',
    },
    'titan': {
        'name': 'Titan Fitness',
        'mode': 'shopify_preload',
        'categories': {
            'Barbells': 'https://www.titan.fitness/collections/barbells',
            'Racks': 'https://www.titan.fitness/collections/power-racks',
        },
        'currency': 'USD',
    },
    'rivalsteel': {
        'name': 'Rival Steel',
        'mode': 'shopify_preload',
        'categories': {
            'Barbells': 'https://rivalsteelfitness.com/collections/barbells',
            'Bumper Plates': 'https://rivalsteelfitness.com/collections/bumper-plates',
            'Plates': 'https://rivalsteelfitness.com/collections/plates',
            'Dumbbells': 'https://rivalsteelfitness.com/collections/dumbbells',
            'Racks': 'https://rivalsteelfitness.com/collections/racks',
            'Benches': 'https://rivalsteelfitness.com/collections/weight-lifitng-benches',
            'Rack Attachments': 'https://rivalsteelfitness.com/collections/rack-attachments',
            'Olympic Weight Sets': 'https://rivalsteelfitness.com/collections/olympic-weight-sets',
            'Home Gym Packages': 'https://rivalsteelfitness.com/collections/rival-steel-home-gym-packages',
        },
        'currency': 'USD',
    },
    'bos': {
        'name': 'Bells of Steel',
        'mode': 'shopify_preload',
        'categories': {
            'Barbells': 'https://bellsofsteel.com/collections/barbells',
            'Plates': 'https://bellsofsteel.com/collections/bumper-plates',
            'Racks': 'https://bellsofsteel.com/collections/racks',
        },
        'currency': 'CAD',
    },
    'elitefts': {
        'name': 'EliteFTS',
        'mode': 'shopify_preload',
        'categories': {
            'Barbells': 'https://elitefts.com/collections/barbells',
            'Knee Sleeves': 'https://elitefts.com/collections/knee-sleeves',
            'Plates': 'https://elitefts.com/collections/plates',
            'Racks': 'https://elitefts.com/collections/power-racks',
            'Apparel': 'https://elitefts.com/collections/apparel',
            'Footwear': 'https://elitefts.com/collections/footwear',
            'Accessories': 'https://elitefts.com/collections/accessories',
            'Support Gear': 'https://elitefts.com/collections/support-gear',
        },
        'currency': 'USD',
    },
    'onyx': {
        'name': 'Onyx Straps',
        'mode': 'shopify_preload',
        'categories': {
            'Lifting Straps': 'https://www.onyxstraps.com/collections/lifting-straps',
            'Wrist Wraps': 'https://www.onyxstraps.com/collections/wrist-wraps',
            'High-top Wraps': 'https://www.onyxstraps.com/collections/high-top-wrist-wraps',
            'Belts': 'https://www.onyxstraps.com/collections/the-belt',
            'Apparel': 'https://www.onyxstraps.com/collections/apparel',
            'Accessories': 'https://www.onyxstraps.com/collections/accessories',
            'Leather Care': 'https://www.onyxstraps.com/collections/leather-care',
            'Bags & Wallets': 'https://www.onyxstraps.com/collections/wallets-bags',
        },
        'currency': 'USD',
    },
    'twopood': {
        'name': '2POOD',
        'mode': 'shopify_preload',
        'categories': {
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
        'currency': 'USD',
    },
    'americanbarbell': {
        'name': 'American Barbell',
        'mode': 'shopify_preload',
        'categories': {
            'Barbells': 'https://www.americanbarbell.com/collections/bars',
            'Plates': 'https://www.americanbarbell.com/collections/bumper-and-olympic-plates',
            'Benches': 'https://www.americanbarbell.com/collections/benches-1',
            'Specialty Bars': 'https://www.americanbarbell.com/collections/all-specialty-bars',
            'Accessories': 'https://www.americanbarbell.com/collections/accessories',
        },
        'currency': 'USD',
    },
    'fringesport': {
        'name': 'Fringe Sport',
        'mode': 'shopify_preload',
        'categories': {
            'Barbells': 'https://www.fringesport.com/collections/barbells',
            'Plates': 'https://www.fringesport.com/collections/bumper-plates',
            'Racks': 'https://www.fringesport.com/collections/squat-racks',
            'Benches': 'https://www.fringesport.com/collections/weight-benches',
            'Accessories': 'https://www.fringesport.com/collections/accessories',
            'Apparel': 'https://www.fringesport.com/collections/apparel',
        },
        'currency': 'USD',
    },
    'pioneerfit': {
        'name': 'Pioneer Fitness',
        'mode': 'shopify_preload',
        'categories': {
            'Powerlifting Belts': 'https://pioneerfit.com/collections/pioneer-powerlifting-belts',
            'Lever Belts': 'https://pioneerfit.com/collections/pioneer-lever-lifting-belts',
            'Deadlift Belts': 'https://pioneerfit.com/collections/pioneer-deadlift-belts',
            'Straps': 'https://pioneerfit.com/collections/lifting-straps-by-pioneer',
            'Knee Wraps': 'https://pioneerfit.com/collections/knee-wraps-by-pioneer',
            'Accessories': 'https://pioneerfit.com/collections/leather-weight-lifting-accessories-by-pioneer',
            'Apparel': 'https://pioneerfit.com/collections/hats-apparel',
            'Singlets': 'https://pioneerfit.com/collections/powerlifting-weightlifting-singlets',
        },
        'currency': 'USD',
    },
    'nobullproject': {
        'name': 'NoBull',
        'mode': 'shopify_preload',
        'categories': {
            "Men's Shoes & Apparel": 'https://nobullproject.com/collections/all-mens',
            "Women's Shoes & Apparel": 'https://nobullproject.com/collections/all-womens',
            'Accessories': 'https://nobullproject.com/collections/accessories',
            'Bags': 'https://nobullproject.com/collections/bags',
        },
        'currency': 'USD',
    },
    'markbell': {
        'name': 'Mark Bell',
        'mode': 'shopify_preload',
        'categories': {
            'Hip Circles': 'https://markbell.com/collections/hip-circles',
            'Knee Wraps': 'https://markbell.com/collections/knee-wraps',
            'Wrist Wraps': 'https://markbell.com/collections/wrist-wraps',
            'Sleeves': 'https://markbell.com/collections/sleeves',
            'Lifting Straps': 'https://markbell.com/collections/lifting-straps',
            'Accessories': 'https://markbell.com/collections/accessories',
        },
        'currency': 'USD',
    },
    'slingshot': {
        'name': 'Slingshot',
        'mode': 'shopify_preload',
        'categories': {
            'Hip Circles': 'https://markbellslingshot.com/collections/hip-circle',
            'Knee Sleeves': 'https://markbellslingshot.com/collections/knee-sleeves',
            'Knee Wraps': 'https://markbellslingshot.com/collections/knee-wraps',
            'Wrist Wraps': 'https://markbellslingshot.com/collections/gangsta-wrist-wraps',
            'Elbow Sleeves': 'https://markbellslingshot.com/collections/elbow-sleeves',
            'Shake Straps': 'https://markbellslingshot.com/collections/shake-straps',
            'Apparel': 'https://markbellslingshot.com/collections/apparel',
            'Accessories': 'https://markbellslingshot.com/collections/accessories',
        },
        'currency': 'USD',
    },
    'forceusa': {
        'name': 'Force USA',
        'mode': 'shopify_preload',
        'categories': {
            'All-In-One Trainers': 'https://forceusa.com/collections/all-in-one',
            'Power Racks': 'https://forceusa.com/collections/power-racks',
            'Barbells': 'https://forceusa.com/collections/barbells',
            'Benches': 'https://forceusa.com/collections/benches',
            'Functional Trainers': 'https://forceusa.com/collections/functional-trainers',
            'Leg Machines': 'https://forceusa.com/collections/leg-machines',
            'Dual Station': 'https://forceusa.com/collections/dual-station-machines',
            'Accessories': 'https://forceusa.com/collections/accessories',
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