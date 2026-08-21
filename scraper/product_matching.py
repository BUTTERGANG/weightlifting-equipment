#!/usr/bin/env python3
"""
Product matching via fuzzy name comparison — lightweight, portable, zero heavy deps.

Normalizes product names (strips brand prefixes, colors, sizes, SKU patterns),
then finds similar products in the same category using rapidfuzz.
Caches results in a product_matches table (PostgreSQL or SQLite compatible).

Usage:
    python product_matching.py                          # Match all products
    python product_matching.py --threshold 70            # Custom similarity threshold
    DATABASE_URL=postgres://... python product_matching.py  # Run against Neon
"""
import os
import re
import sys
import argparse
from collections import defaultdict
from datetime import datetime

from db import connect, init_schema

try:
    from rapidfuzz import fuzz
except ImportError:
    print("rapidfuzz not installed. Run: pip install rapidfuzz")
    sys.exit(1)

# Similarity floor for calling two cross-store products "the same thing".
# 65 was far too loose: on 6.8k products it produced 13k pairs (~2 per product),
# dominated by generic apparel whose normalised names collapse to one or two
# words. 82 plus the guards in _is_plausible_match keeps the pairs that a human
# would actually accept as the same item.
DEFAULT_THRESHOLD = 82

# Category labels that are store-shelf groupings, not product types. Matching
# within them compares unrelated items, so they're skipped.
GENERIC_CATEGORIES = {
    'all', 'all items', 'new', 'clearance', 'sale', 'gear', 'equipment',
    'accessories', 'apparel', 'uncategorized', 'featured', 'best sellers',
    'shop all', 'gift cards',
}

# ── Normalization ─────────────────────────────────────────────────────────

# Common brand prefixes to strip for better matching
BRAND_PATTERNS = [
    # Don't strip brand names — they're critical for distinguishing products
    # Only strip very generic prefixes that aren't brand-specific
]

# Patterns to strip: sizes, colors, materials, SKUs
STRIP_PATTERNS = [
    r'/\s*(xs|s|sm|m|md|l|lg|xl|2xl|xxl|3xl|xxxl|4xl|5xl|os|one\s*size|onesize)\s*',
    r'/\s*(black|white|red|blue|green|yellow|orange|purple|pink|gray|grey|tan|brown|camo|navy|olive|maroon|teal)\s*',
    r'-+\s*(black|white|red|blue|green|yellow|orange|purple|pink|gray|grey|tan|brown|camo|navy|olive|maroon|teal)\s*',
    r'\s*[#×x×]\s*\d+[".]?\s*(mm|cm|kg|lb|in|inches)?\s*i',
    r'\s*\d+\s*(mm|cm|kg|lb|oz)\s*',
    r'\s*\b(coach|custom|pro|elite|premium|standard|competition|training)\b\s*',
    r'\s*\([^)]*\)\s*',
    r'\s*\[[^\]]*\]\s*',
    r'--+.*$',  # Everything after double dash (variant separator)
    r'\b(women[\'’]?s|men[\'’]?s|unisex|kids|youth)\b[\s-]*',
    r"\b(small|medium|large|xlarge|2xlarge|3xlarge)\b",
    r"\b\d+[-]?(kg|lb|oz|mm|cm|inch|inches|piece|pair|set|pack)s?\b",  # Weights/units
    r"^\d+[\s-]+",  # Leading numbers
    r"\b(slim|thicc|regular)\b",  # Variant descriptors
    r"['’]s\b",  # Orphaned possessives
    r"\b\w*[a-z]{4,}\d+\w*\b",  # SKU/handle patterns with numbers embedded
    r"\b[b-df-hj-np-tv-z]{4,}\b",  # Consonant-only words (SKU codes like "wbpds")
    r"\b\d{2,}(?:\s*(lb|kg|oz|mm|cm|inch|inches))?\s*$",  # Trailing numbers with optional unit
    r"\b\d{2,}\b",  # Standalone 2+ digit numbers anywhere (weights, sizes)
    r"\b(black|white|red|blue|green|yellow|orange|purple|pink|gray|grey|tan|brown|camo|navy|olive|maroon)\s*$",  # Trailing colors
]


def normalize_name(name):
    """Normalize a product name for matching. Returns cleaned string."""
    if not name:
        return ''
    # Lowercase
    s = name.lower().strip()
    # Remove SKU-like patterns in parentheses
    s = re.sub(r'\s*\([a-z0-9-]{3,}\)\s*', ' ', s)
    # Strip brand prefixes
    for pat in BRAND_PATTERNS:
        s = re.sub(pat, '', s, count=1)
    # Remove variant separators
    s = re.sub(r'\s*[–—]+\s*.*$', '', s)  # em dash → end
    s = re.sub(r'\s*-\s+.*$', '', s)  # " - something" → stop
    # Strip colors/sizes/variant info
    for pat in STRIP_PATTERNS:
        s = re.sub(pat, ' ', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    # Remove trailing special chars
    s = re.sub(r'[\s,-]+$', '', s)
    return s


def test_normalize():
    tests = [
        ("Rogue Ohio Bar", "rogue ohio bar"),
        ("EliteFTS Stainless Steel Bar - 20KG", "elitefts stainless steel bar"),
        ("REP Fitness Stainless Steel Bar - 20KG", "rep fitness stainless steel bar"),
        ("Cerberus Strength Knee Sleeves - Black / Medium", "cerberus strength knee sleeves"),
        ("Slingshot Hip Circle - Black / One size", "slingshot hip circle"),
        ("NoBull Men's Knit Runner - Black / 10", "nobull knit runner"),
        ("2POOD Lava Lamp 4\" Belt - One size", "2pood lava lamp 4\" belt"),
        ("Get Rx'd 10 Premium Wall Ball Black Wbpds 10", "get rx'd wall ball"),
        ("Pioneer Fitness Powerlifting Belt - Black / Medium", "pioneer fitness powerlifting belt"),
    ]
    for raw, expected in tests:
        result = normalize_name(raw)
        mark = "✓" if expected in result else "✗"
        print(f"  {mark} {raw[:55]:55s} → {result[:45]:45s}")


# ── Database helpers ──────────────────────────────────────────────────────

def _norm_tokens(name):
    return {t for t in re.split(r'[^a-z0-9"\']+', name) if len(t) > 1}


def _is_plausible_match(n1, n2, sim):
    """Guard rails on top of the raw fuzzy score.

    A high token_sort_ratio between two very short normalised names is close to
    meaningless — "shorts" vs "short" scores 91 but says nothing. Require enough
    substance, and require the two names to actually share a distinctive token.
    """
    if len(n1) < 8 or len(n2) < 8:
        return False
    t1, t2 = _norm_tokens(n1), _norm_tokens(n2)
    if len(t1) < 2 or len(t2) < 2:
        return False
    shared = t1 & t2
    if not shared:
        return False
    # At least one shared token has to be more specific than "bar"/"set".
    if not any(len(t) >= 4 for t in shared):
        return False
    return sim >= DEFAULT_THRESHOLD or sim >= 90


# ── Matching engine ──────────────────────────────────────────────────────

def compute_matches(threshold=None, batch_size=1000):
    """Recompute cross-store product matches. Returns a stats dict."""
    threshold = DEFAULT_THRESHOLD if threshold is None else threshold
    print('Product matching v2 (rapidfuzz)')
    print(f'Threshold: {threshold}%')

    db = connect()
    try:
        init_schema(db)
        products = db.query("""
            SELECT id, site, name, COALESCE(category, 'Uncategorized') AS category
            FROM products
        """)
        print(f'Loaded {len(products)} products')

        by_category = defaultdict(list)
        skipped_categories = 0
        for p in products:
            cat = p['category']
            if cat.strip().lower() in GENERIC_CATEGORIES:
                skipped_categories += 1
                continue
            norm = normalize_name(p['name'])
            if not norm:
                continue
            by_category[cat].append((p['id'], p['site'], norm))

        print(f'Matching across {len(by_category)} product categories '
              f'({skipped_categories} rows in generic categories skipped)')

        db.execute('DELETE FROM product_matches')
        db.commit()

        pairs = []
        total = 0
        start_time = datetime.now()
        for cat, items in sorted(by_category.items()):
            n = len(items)
            if n < 2:
                continue
            for i in range(n):
                id1, site1, n1 = items[i]
                for j in range(i + 1, n):
                    id2, site2, n2 = items[j]
                    if site1 == site2:      # we only care about cross-store matches
                        continue
                    sim = fuzz.token_sort_ratio(n1, n2)
                    if sim < threshold:
                        continue
                    if not _is_plausible_match(n1, n2, sim):
                        continue
                    sim = round(float(sim), 1)
                    # Store both directions: the dashboard looks up matches with
                    # WHERE product_id = ?, so a one-directional row made half of
                    # every match invisible.
                    pairs.append((id1, id2, sim))
                    pairs.append((id2, id1, sim))
                    total += 1

            if len(pairs) >= batch_size:
                _insert_batch(db, pairs)
                pairs = []
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f'  {cat[:42]:42s} {n:5d} products → {total:6d} matches ({elapsed:.0f}s)',
                  flush=True)

        _insert_batch(db, pairs)
        elapsed = (datetime.now() - start_time).total_seconds()
        stats = db.query_one(f"""
            SELECT COUNT(DISTINCT product_id) AS matched_products,
                   {db.round('AVG(similarity)', 1)} AS avg_sim
            FROM product_matches
        """)
        print(f'\nDone in {elapsed:.1f}s')
        print(f'Total: {total} cross-store matches '
              f'({stats["matched_products"]} products, '
              f'avg similarity {stats["avg_sim"]}%)')
        return {'matches': total, **stats}
    finally:
        db.close()


def _insert_batch(db, pairs, commit=True):
    """Insert a batch of (product_id, matched_product_id, similarity) rows."""
    if not pairs:
        return 0
    if db.is_postgres:
        from psycopg2.extras import execute_values
        execute_values(db.conn.cursor(), """
            INSERT INTO product_matches (product_id, matched_product_id, similarity)
            VALUES %s
            ON CONFLICT (product_id, matched_product_id) DO UPDATE SET
                similarity = EXCLUDED.similarity,
                updated_at = NOW()
        """, pairs)
    else:
        db.executemany("""
            INSERT INTO product_matches (product_id, matched_product_id, similarity, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT (product_id, matched_product_id) DO UPDATE SET
                similarity = excluded.similarity,
                updated_at = excluded.updated_at
        """, pairs)
    if commit:
        db.commit()
    return len(pairs)


def get_matches_for_product(pid, limit=5):
    """Top cross-store matches for one product."""
    with connect() as db:
        return db.query("""
            SELECT p.id, p.site, p.name, p.category, p.url,
                   ph.price, ph.price_text, pm.similarity
            FROM product_matches pm
            JOIN products p ON p.id = pm.matched_product_id
            LEFT JOIN price_history ph ON ph.product_id = p.id
                AND ph.scraped_at = (
                    SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)
            WHERE pm.product_id = ?
            ORDER BY pm.similarity DESC
            LIMIT ?
        """, (pid, limit))


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Product matching for Plate Magnet')
    parser.add_argument('--threshold', type=int, default=DEFAULT_THRESHOLD,
                        help=f'Similarity threshold %% (default: {DEFAULT_THRESHOLD})')
    parser.add_argument('--test', action='store_true', help='Run normalization tests')
    args = parser.parse_args()

    if args.test:
        test_normalize()
        return

    compute_matches(threshold=args.threshold)


if __name__ == '__main__':
    main()