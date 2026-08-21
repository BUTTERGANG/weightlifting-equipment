"""Tests for the ingest path — the code where the data-correctness bugs lived."""
import pytest

from ingest import dedupe, ingest


def product(site='RepFit', name='Ohio Bar', url='https://x.com/p/ohio',
            price=100.0, **kw):
    base = {'site': site, 'name': name, 'url': url, 'price': price,
            'price_text': f'${price}', 'currency': 'USD',
            'category': 'Barbells', 'image_url': 'https://x.com/i.jpg',
            'source_url': 'https://x.com/c/bars'}
    base.update(kw)
    return base


# ── dedupe ────────────────────────────────────────────────────────────────

def test_variants_sharing_a_name_stay_separate():
    """The bug that poisoned deal detection: NoBull listed 28 distinct lace
    colourways all named "NOBULL Laces" at two different prices. Keyed on name
    they collapsed into one product whose price flip-flopped every scrape."""
    raw = [
        product(site='NoBull', name='NOBULL Laces', url='https://n.com/p/ginger', price=7.0),
        product(site='NoBull', name='NOBULL Laces', url='https://n.com/p/blue', price=9.0),
        product(site='NoBull', name='NOBULL Laces', url='https://n.com/p/camo', price=7.0),
    ]
    out, _ = dedupe(raw)
    assert len(out) == 3
    assert sorted(p['price'] for p in out) == [7.0, 7.0, 9.0]


def test_same_product_in_two_collections_is_merged():
    """One product legitimately appears under several categories; that must not
    produce two price rows for the same item in one run."""
    raw = [
        product(url='https://x.com/p/shoe', category='Lifters'),
        product(url='https://x.com/p/shoe', category='Barefoot'),
    ]
    out, _ = dedupe(raw)
    assert len(out) == 1
    assert out[0]['category'] == 'Lifters'  # first sighting wins


def test_merge_backfills_missing_fields():
    raw = [
        product(url='https://x.com/p/a', category=None, image_url=None),
        product(url='https://x.com/p/a', category='Barbells',
                image_url='https://x.com/i.jpg'),
    ]
    out, _ = dedupe(raw)
    assert out[0]['category'] == 'Barbells'
    assert out[0]['image_url'] == 'https://x.com/i.jpg'


@pytest.mark.parametrize('bad', [
    {'price': None}, {'price': 0}, {'price': -5}, {'price': 'not-a-number'},
])
def test_rows_without_a_usable_price_are_dropped(bad):
    out, dropped = dedupe([product(**bad)])
    assert out == []
    assert dropped['no_price'] == 1


def test_rows_without_a_url_are_dropped():
    out, dropped = dedupe([product(url='')])
    assert out == []
    assert dropped['no_url'] == 1


def test_rows_without_a_name_are_skipped():
    assert dedupe([product(name='')])[0] == []
    assert dedupe([product(name='Unknown')])[0] == []


# ── ingest ────────────────────────────────────────────────────────────────

def test_one_price_row_per_product_per_run(sqlite_db):
    raw = [
        product(url='https://n.com/p/ginger', name='NOBULL Laces', price=7.0),
        product(url='https://n.com/p/blue', name='NOBULL Laces', price=9.0),
        product(url='https://n.com/p/ginger', name='NOBULL Laces',
                price=7.0, category='New'),   # same product, second collection
    ]
    stats = ingest(raw, db=sqlite_db, quiet=True)
    assert stats['products'] == 2
    assert stats['price_records'] == 2

    dupes = sqlite_db.query("""
        SELECT product_id FROM price_history
        GROUP BY product_id HAVING COUNT(*) > 1
    """)
    assert dupes == []


def test_ingest_is_idempotent_for_products_and_appends_history(sqlite_db):
    raw = [product(), product(url='https://x.com/p/other', name='Bella Bar')]

    first = ingest(raw, db=sqlite_db, quiet=True)
    assert first['new'] == 2

    second = ingest(raw, db=sqlite_db, quiet=True)
    assert second['new'] == 0
    assert second['updated'] == 2

    assert sqlite_db.query_one('SELECT COUNT(*) c FROM products')['c'] == 2
    assert sqlite_db.query_one('SELECT COUNT(*) c FROM price_history')['c'] == 4


def test_price_changes_are_tracked_per_product(sqlite_db):
    ingest([product(price=100.0)], db=sqlite_db, quiet=True)
    ingest([product(price=80.0)], db=sqlite_db, quiet=True)

    prices = [r['price'] for r in sqlite_db.query(
        'SELECT price FROM price_history ORDER BY id')]
    assert prices == [100.0, 80.0]


def test_rescrape_does_not_clobber_known_metadata(sqlite_db):
    ingest([product(category='Barbells', image_url='https://x.com/i.jpg')],
           db=sqlite_db, quiet=True)
    ingest([product(category=None, image_url=None)], db=sqlite_db, quiet=True)

    row = sqlite_db.query_one('SELECT category, image_url FROM products')
    assert row['category'] == 'Barbells'
    assert row['image_url'] == 'https://x.com/i.jpg'


def test_renamed_product_updates_in_place(sqlite_db):
    """A retailer renaming an item must update the row, not create a second one
    — that split its price history across two products."""
    ingest([product(name='Ohio Bar')], db=sqlite_db, quiet=True)
    ingest([product(name='Ohio Bar 20KG')], db=sqlite_db, quiet=True)

    rows = sqlite_db.query('SELECT name FROM products')
    assert len(rows) == 1
    assert rows[0]['name'] == 'Ohio Bar 20KG'
    assert sqlite_db.query_one('SELECT COUNT(*) c FROM price_history')['c'] == 2


def test_same_url_on_different_sites_are_distinct(sqlite_db):
    ingest([product(site='A', url='https://shared.example/p/1'),
            product(site='B', url='https://shared.example/p/1')],
           db=sqlite_db, quiet=True)
    assert sqlite_db.query_one('SELECT COUNT(*) c FROM products')['c'] == 2


# ── Category grouping ─────────────────────────────────────────────────────

def test_ingest_assigns_canonical_group(sqlite_db):
    ingest([product(name='Classic Lever Belt', url='https://x.com/p/belt',
                    category='All')], db=sqlite_db, quiet=True)
    assert sqlite_db.query_one('SELECT group_name FROM products')['group_name'] == 'Belts'


def test_regroup_prefers_a_specific_shelf_over_other(sqlite_db):
    """When the first sighting can't be classified, a later sighting under a
    specific shelf should rescue it."""
    out, _ = dedupe([
        product(name='Model XR-7', url='https://x.com/p/x', category='All'),
        product(name='Model XR-7', url='https://x.com/p/x', category='Knee Sleeves'),
    ])
    assert out[0]['group_name'] == 'Sleeves'


def test_backfill_groups_reclassifies_existing_rows(sqlite_db):
    from ingest import backfill_groups
    ingest([product(name='Classic Lever Belt', url='https://x.com/p/belt')],
           db=sqlite_db, quiet=True)
    sqlite_db.execute("UPDATE products SET group_name = 'Other'")
    sqlite_db.commit()

    changed = backfill_groups(db=sqlite_db, quiet=True)

    assert changed == 1
    assert sqlite_db.query_one('SELECT group_name FROM products')['group_name'] == 'Belts'


def test_rescrape_updates_group_when_taxonomy_changes(sqlite_db):
    ingest([product(name='Mystery Item', url='https://x.com/p/m', category='All')],
           db=sqlite_db, quiet=True)
    ingest([product(name='Mystery Item Lever Belt', url='https://x.com/p/m',
                    category='All')], db=sqlite_db, quiet=True)
    assert sqlite_db.query_one('SELECT group_name FROM products')['group_name'] == 'Belts'
