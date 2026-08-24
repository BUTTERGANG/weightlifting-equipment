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
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

try:
    from rapidfuzz import fuzz
except ImportError:
    print("rapidfuzz not installed. Run: pip install rapidfuzz")
    sys.exit(1)

DB_PATH = Path.home() / 'equipment_data' / 'equipment.db'
DATABASE_URL = os.environ.get('DATABASE_URL', '')

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

def get_db():
    if DATABASE_URL:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn, 'postgres'
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'


def ensure_matches_table(db, db_type):
    cur = db.cursor()
    if db_type == 'postgres':
        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_matches (
                product_id         INTEGER NOT NULL REFERENCES products(id),
                matched_product_id INTEGER NOT NULL REFERENCES products(id),
                similarity         REAL NOT NULL,
                updated_at         TIMESTAMP NOT NULL DEFAULT NOW(),
                PRIMARY KEY (product_id, matched_product_id)
            );
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS product_matches (
                product_id         INTEGER NOT NULL,
                matched_product_id INTEGER NOT NULL,
                similarity         REAL NOT NULL,
                updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (product_id, matched_product_id),
                FOREIGN KEY (product_id) REFERENCES products(id),
                FOREIGN KEY (matched_product_id) REFERENCES products(id)
            );
        """)
    db.commit()


def clear_matches(db, db_type):
    cur = db.cursor()
    cur.execute("DELETE FROM product_matches")
    db.commit()


# ── Matching engine ──────────────────────────────────────────────────────

def compute_matches(threshold=65, batch_size=1000):
    """Compute and store product matches within same categories."""
    print(f"Product matching v1.0 (rapidfuzz)")
    print(f"Threshold: {threshold}%")
    print(f"Database: {'Neon PostgreSQL' if DATABASE_URL else f'SQLite ({DB_PATH})'}")
    print()

    db, db_type = get_db()
    ensure_matches_table(db, db_type)
    clear_matches(db, db_type)
    cur = db.cursor()

    # Fetch all products grouped by category
    if db_type == 'postgres':
        cur.execute("""
            SELECT id, site, name, COALESCE(category, 'Uncategorized') as category
            FROM products ORDER BY category, site, name
        """)
    else:
        cur.execute("""
            SELECT id, site, name, COALESCE(category, 'Uncategorized') as category
            FROM products ORDER BY category, site, name
        """)

    products = cur.fetchall()
    total = len(products)
    print(f"Loaded {total} products")

    # Group by category
    from collections import defaultdict
    by_category = defaultdict(list)
    for p in products:
        cat = p['category'] if db_type == 'sqlite' else p[3]
        by_category[cat].append(p)

    print(f"Across {len(by_category)} categories\n")

    # Pre-compute normalized names
    normalized = {}
    for p in products:
        name = p['name'] if db_type == 'sqlite' else p[2]
        normalized[p['id'] if db_type == 'sqlite' else p[0]] = normalize_name(name)

    # Match within each category
    total_pairs = 0
    total_inserted = 0
    start = datetime.now()

    for cat, cat_products in sorted(by_category.items()):
        ids = [p['id'] if db_type == 'sqlite' else p[0] for p in cat_products]
        n = len(ids)
        if n < 2:
            continue

        pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                pid1, pid2 = ids[i], ids[j]
                names = (
                    normalized.get(pid1, ''),
                    normalized.get(pid2, '')
                )
                # Skip if either normalized name is empty
                if not names[0] or not names[1]:
                    continue
                # Skip same-store comparisons (we want cross-store matching)
                site1 = cat_products[i]['site'] if db_type == 'sqlite' else cat_products[i][1]
                site2 = cat_products[j]['site'] if db_type == 'sqlite' else cat_products[j][1]
                if site1 == site2:
                    continue

                # Combine token sort + partial ratio for better results
                sim = fuzz.token_sort_ratio(names[0], names[1])
                if sim >= threshold:
                    pairs.append((pid1, pid2, round(sim, 1)))
                    total_pairs += 1

                if len(pairs) >= batch_size:
                    total_inserted += insert_batch(db, db_type, pairs)
                    pairs = []

        if pairs:
            total_inserted += insert_batch(db, db_type, pairs)

        elapsed = (datetime.now() - start).total_seconds()
        print(f"  {cat[:45]:45s} {n:4d} products → {total_pairs:6d} pairs found ({elapsed:.0f}s)", end='\r')

    # Final insert
    if pairs:
        total_inserted += insert_batch(db, db_type, pairs)

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n\nDone in {elapsed:.1f}s")
    print(f"Total: {total_inserted} cross-store matches inserted")

    # Stats
    if db_type == 'postgres':
        cur.execute("""
            SELECT COUNT(DISTINCT product_id) as matched_products,
                   ROUND(AVG(similarity), 1) as avg_sim
            FROM product_matches
        """)
    else:
        cur.execute("""
            SELECT COUNT(DISTINCT product_id) as matched_products,
                   ROUND(AVG(similarity), 1) as avg_sim
            FROM product_matches
        """)
    stats = cur.fetchone()
    if db_type == 'postgres':
        print(f"Products with matches: {stats[0]}, Average similarity: {stats[1]}%")
    else:
        print(f"Products with matches: {stats['matched_products']}, Average similarity: {stats['avg_sim']}%")

    cur.close()
    db.close()


def insert_batch(db, db_type, pairs):
    """Insert a batch of match pairs."""
    cur = db.cursor()
    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ') if db_type == 'sqlite' else None

    if db_type == 'postgres':
        from psycopg2.extras import execute_values
        execute_values(cur, """
            INSERT INTO product_matches (product_id, matched_product_id, similarity)
            VALUES %s
            ON CONFLICT (product_id, matched_product_id) DO UPDATE SET
                similarity = EXCLUDED.similarity,
                updated_at = NOW()
        """, [(p1, p2, s) for p1, p2, s in pairs])
    else:
        cur.executemany("""
            INSERT OR REPLACE INTO product_matches (product_id, matched_product_id, similarity, updated_at)
            VALUES (?, ?, ?, ?)
        """, [(p1, p2, s, now) for p1, p2, s in pairs])

    db.commit()
    return len(pairs)


def get_matches_for_product(pid, limit=5):
    """Get top matches for a specific product."""
    db, db_type = get_db()
    cur = db.cursor()

    if db_type == 'postgres':
        cur.execute("""
            SELECT p.id, p.site, p.name, p.category, p.url,
                   ph.price, ph.price_text,
                   pm.similarity
            FROM product_matches pm
            JOIN products p ON p.id = pm.matched_product_id
            LEFT JOIN price_history ph ON ph.product_id = p.id
                AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)
            WHERE pm.product_id = %s
            ORDER BY pm.similarity DESC
            LIMIT %s
        """, (pid, limit))
    else:
        cur.execute("""
            SELECT p.id, p.site, p.name, p.category, p.url,
                   ph.price, ph.price_text,
                   pm.similarity
            FROM product_matches pm
            JOIN products p ON p.id = pm.matched_product_id
            LEFT JOIN price_history ph ON ph.product_id = p.id
                AND ph.scraped_at = (SELECT MAX(scraped_at) FROM price_history WHERE product_id = p.id)
            WHERE pm.product_id = ?
            ORDER BY pm.similarity DESC
            LIMIT ?
        """, (pid, limit))

    rows = cur.fetchall()
    if db_type == 'postgres':
        desc = cur.description
        results = [{desc[i][0]: r[i] for i in range(len(desc))} for r in rows]
    else:
        results = [dict(r) for r in rows]
    cur.close()
    db.close()
    return results


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Product matching for Plate Magnet')
    parser.add_argument('--threshold', type=int, default=65, help='Similarity threshold % (default: 65)')
    parser.add_argument('--test', action='store_true', help='Run normalization tests')
    args = parser.parse_args()

    if args.test:
        test_normalize()
        return

    compute_matches(threshold=args.threshold)


if __name__ == '__main__':
    main()