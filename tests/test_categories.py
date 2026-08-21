"""Tests for the canonical category taxonomy.

The taxonomy is what the dashboard navigates by, so these tests pin the
behaviours that were wrong at some point during its development — mostly cases
where a broad equipment rule claimed a branded accessory.
"""
import pytest

from categories import (CANONICAL_CATEGORIES, CATEGORY_ICONS, classify,
                        words, phrase)


@pytest.mark.parametrize('name,expected', [
    # -- core equipment
    ('The Ohio Bar - Cerakote', 'Barbells'),
    ('20kg Training Barbell', 'Barbells'),
    ('Safety Squat Bar', 'Barbells'),
    ('Competition Bumper Plates (LB)', 'Plates'),
    ('Black Bumper Plate Set', 'Plates'),
    ('Rubber Hex Dumbbells', 'Dumbbells & Kettlebells'),
    ('Pro Power Cage', 'Racks & Rigs'),
    ('Adjustable Bench', 'Benches'),
    ('Lat Pulldown Attachment', 'Machines & Cable'),

    # -- supportive gear
    ('Classic Lever Belt (10mm)', 'Belts'),
    ('3" 10MM Double Suede Lever Weight Belt', 'Belts'),
    ('Heavy Duty 7mm Knee Sleeves', 'Sleeves'),
    ('Titan El Diablo Wrist Wraps', 'Wraps'),
    ('Figure 8-Deadlift Cotton Lifting Straps', 'Straps & Grips'),
    ('Competition Powerlifting Singlet', 'Singlets & Supportive Gear'),
    ('Bench Shirt', 'Singlets & Supportive Gear'),

    # -- soft goods
    ("Men's L-2 Lifter - Black Gum", 'Footwear'),
    ('LUXIAOJUN PowerPro Weightlifting Shoes', 'Footwear'),
    ('Performance Ankle Sock (Black)', 'Apparel'),
    ('Resistance Bands', 'Bands & Conditioning'),
    ('Rogue Echo Bike V3.0', 'Bands & Conditioning'),
])
def test_classification(name, expected):
    assert classify(name, 'Accessories') == expected


@pytest.mark.parametrize('name,expected', [
    # A branded shirt is a shirt, not the equipment it names.
    ('Load The Bar T-Shirt', 'Apparel'),
    ('2POOD Barbell Patch', 'Chalk & Accessories'),
    ('Rogue Barbell Sticker', 'Chalk & Accessories'),
    ('Barbell Rescue 360 Barbell Cleaning Brush', 'Chalk & Accessories'),
    ('Barbell Selection Guide', 'Chalk & Accessories'),
    ('Bumper Plate Magnet', 'Chalk & Accessories'),
    ('Squat Racerback Tank (White Plate Graphic)', 'Apparel'),
    ('Barbell Squat Pad', 'Chalk & Accessories'),
    # Equipment whose name mentions another type.
    ('Kids Builder Pull-Up Bar', 'Racks & Rigs'),
    ('Multi-Grip Swiss Barbell', 'Barbells'),
    ('42" Plate and Ball Shelf', 'Storage & Collars'),
])
def test_decisive_nouns_beat_equipment_words(name, expected):
    assert classify(name, 'All') == expected


def test_plurals_are_matched():
    """`(alternatives)\\b` silently failed on plurals — the trailing boundary
    sits between "d" and "s". words()/phrase() must handle both."""
    assert classify('Resistance Band', 'All') == 'Bands & Conditioning'
    assert classify('Resistance Bands', 'All') == 'Bands & Conditioning'
    assert classify('Knee Sleeve', 'All') == 'Sleeves'
    assert classify('Knee Sleeves', 'All') == 'Sleeves'


def test_helpers_build_plural_safe_patterns():
    import re
    pat = re.compile(words('band'), re.I)
    assert pat.search('Band') and pat.search('Bands')
    assert not pat.search('bandana')

    pat = re.compile(phrase('knee sleeve'), re.I)
    assert pat.search('Knee Sleeve') and pat.search('Knee Sleeves')


def test_generic_store_label_falls_back_to_name():
    """A shelf called "All" says nothing; the name has to decide."""
    assert classify('Lever Belt', 'All') == 'Belts'
    assert classify('Lever Belt', None) == 'Belts'


def test_specific_store_label_used_when_name_is_opaque():
    assert classify('Model XR-7', 'Barbells') == 'Barbells'
    assert classify('Model XR-7', 'Knee Sleeves') == 'Sleeves'


def test_nobull_shoe_models_fall_back_to_footwear():
    """NoBull files shoes and clothing under one label and names its shoes by
    model, which no vocabulary rule can catch. Clothing in that bucket is caught
    by the apparel rule, so the remainder is footwear."""
    assert classify("Men's Drive 2", "Men's Shoes & Apparel") == 'Footwear'
    assert classify("Women's Journey 2", "Women's Shoes & Apparel") == 'Footwear'
    # ...but a garment there is still a garment.
    assert classify("Men's Dealmaker 5-Pocket Pant", "Men's Shoes & Apparel") == 'Apparel'


def test_unclassifiable_is_other():
    assert classify('Zzzz 9000', 'All') == 'Other'
    assert classify('', None) == 'Other'
    assert classify(None, None) == 'Other'


def test_every_returned_category_is_canonical():
    canonical = set(CANONICAL_CATEGORIES)
    samples = ['Ohio Bar', 'Lever Belt', 'Knee Sleeves', 'T-Shirt', 'Zzzz 9000',
               'Bumper Plates', 'Resistance Bands', "Men's Drive 2"]
    for name in samples:
        assert classify(name, 'All') in canonical


def test_every_canonical_category_has_an_icon():
    for category in CANONICAL_CATEGORIES:
        assert CATEGORY_ICONS.get(category)


def test_real_catalogue_is_mostly_classified():
    """Guard against a regression that dumps everything into Other."""
    import json
    from pathlib import Path
    sample = Path(__file__).resolve().parent.parent / 'data' / 'scrape_latest.json'
    if not sample.exists():
        pytest.skip('no scrape sample available')
    products = json.loads(sample.read_text())
    other = sum(1 for p in products
                if classify(p.get('name'), p.get('category')) == 'Other')
    assert other / len(products) < 0.10
