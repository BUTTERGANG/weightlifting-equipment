"""Tests for cross-store product matching."""
import pytest

from product_matching import (normalize_name, _is_plausible_match, compute_matches,
                              DEFAULT_THRESHOLD, GENERIC_CATEGORIES)
from ingest import ingest


def product(site, name, url, category='Barbells', price=100.0):
    return {'site': site, 'name': name, 'url': url, 'price': price,
            'price_text': f'${price}', 'currency': 'USD', 'category': category,
            'image_url': None, 'source_url': ''}


# ── Normalisation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('raw,expected', [
    ('Rogue Ohio Bar', 'rogue ohio bar'),
    ('EliteFTS Stainless Steel Bar - 20KG', 'elitefts stainless steel bar'),
    ('Cerberus Strength Knee Sleeves - Black / Medium', 'cerberus strength knee sleeves'),
])
def test_normalize_strips_variant_noise(raw, expected):
    assert expected in normalize_name(raw)


def test_normalize_handles_empty():
    assert normalize_name('') == ''
    assert normalize_name(None) == ''


# ── Plausibility guards ───────────────────────────────────────────────────

def test_short_names_are_rejected():
    """"shorts" vs "short" scores 91 but means nothing."""
    assert not _is_plausible_match('shorts', 'short', 91)


def test_names_without_a_shared_token_are_rejected():
    assert not _is_plausible_match('stainless steel bar', 'rubber hex dumbbell', 85)


def test_names_sharing_only_a_tiny_token_are_rejected():
    assert not _is_plausible_match('ohio bar set', 'texas bar kit', 85)


def test_genuine_match_is_accepted():
    assert _is_plausible_match('rep fitness open trap bar',
                               'titan fitness open trap bar', 95)


# ── End to end ────────────────────────────────────────────────────────────

def test_matches_are_cross_store_and_bidirectional(sqlite_db, monkeypatch):
    """Matches used to be stored one-directional, so the dashboard — which looks
    them up with WHERE product_id = ? — could only ever see half of them."""
    import product_matching
    monkeypatch.setattr(product_matching, 'connect', lambda *a, **k: sqlite_db)
    monkeypatch.setattr(sqlite_db, 'close', lambda: None)

    ingest([
        product('RepFit', 'Open Trap Bar', 'https://rep.com/p/trap'),
        product('Titan', 'Open Trap Bar', 'https://titan.com/p/trap'),
    ], db=sqlite_db, quiet=True)

    compute_matches()

    ids = {r['site']: r['id'] for r in sqlite_db.query('SELECT id, site FROM products')}
    forward = sqlite_db.query('SELECT * FROM product_matches WHERE product_id = ?',
                              (ids['RepFit'],))
    backward = sqlite_db.query('SELECT * FROM product_matches WHERE product_id = ?',
                               (ids['Titan'],))
    assert len(forward) == 1 and len(backward) == 1
    assert forward[0]['matched_product_id'] == ids['Titan']
    assert backward[0]['matched_product_id'] == ids['RepFit']


def test_same_store_products_are_not_matched(sqlite_db, monkeypatch):
    import product_matching
    monkeypatch.setattr(product_matching, 'connect', lambda *a, **k: sqlite_db)
    monkeypatch.setattr(sqlite_db, 'close', lambda: None)

    ingest([
        product('RepFit', 'Open Trap Bar', 'https://rep.com/p/trap-1'),
        product('RepFit', 'Open Trap Bar', 'https://rep.com/p/trap-2'),
    ], db=sqlite_db, quiet=True)

    compute_matches()
    assert sqlite_db.query_one('SELECT COUNT(*) c FROM product_matches')['c'] == 0


def test_generic_categories_are_skipped(sqlite_db, monkeypatch):
    """"All" and "Apparel" are shelf groupings, not product types — matching
    inside them compared unrelated items and produced most of the old noise."""
    import product_matching
    monkeypatch.setattr(product_matching, 'connect', lambda *a, **k: sqlite_db)
    monkeypatch.setattr(sqlite_db, 'close', lambda: None)

    ingest([
        product('RepFit', 'Open Trap Bar', 'https://rep.com/p/trap', category='All'),
        product('Titan', 'Open Trap Bar', 'https://titan.com/p/trap', category='All'),
    ], db=sqlite_db, quiet=True)

    compute_matches()
    assert sqlite_db.query_one('SELECT COUNT(*) c FROM product_matches')['c'] == 0


def test_recompute_replaces_previous_matches(sqlite_db, monkeypatch):
    import product_matching
    monkeypatch.setattr(product_matching, 'connect', lambda *a, **k: sqlite_db)
    monkeypatch.setattr(sqlite_db, 'close', lambda: None)

    ingest([
        product('RepFit', 'Open Trap Bar', 'https://rep.com/p/trap'),
        product('Titan', 'Open Trap Bar', 'https://titan.com/p/trap'),
    ], db=sqlite_db, quiet=True)

    compute_matches()
    first = sqlite_db.query_one('SELECT COUNT(*) c FROM product_matches')['c']
    compute_matches()
    second = sqlite_db.query_one('SELECT COUNT(*) c FROM product_matches')['c']
    assert first == second == 2


def test_generic_category_list_is_lowercase():
    assert all(c == c.lower() for c in GENERIC_CATEGORIES)


def test_threshold_is_strict_enough():
    assert DEFAULT_THRESHOLD >= 80
