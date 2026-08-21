#!/usr/bin/env python3
"""
Canonical category taxonomy.

Retailers use 82 different category labels for the same handful of product
types, and nearly half of all scraped rows land in a bucket that says nothing
about the product — "All" (1,200 rows), "Accessories" (783), "Apparel" (474),
"All Items", "Clearance", "New", "Equipment", "Gear". Filtering by the store's
own label is therefore close to useless.

This module maps every product to one of CANONICAL_CATEGORIES using:

  1. the product name, when it clearly identifies the type (a "Lever Belt" is a
     belt no matter which shelf the store put it on), then
  2. the retailer's own category, when that label is specific, and finally
  3. a coarser name pass for the leftovers.

Name rules run first because a specific name beats a vague shelf, but they are
written to be conservative: a rule fires on a distinctive token ("knee sleeve",
"lever belt"), never on a generic one ("pro", "black").
"""
import re

# Display order matters: the dashboard renders category tabs in this sequence.
CANONICAL_CATEGORIES = [
    'Barbells',
    'Plates',
    'Dumbbells & Kettlebells',
    'Racks & Rigs',
    'Benches',
    'Machines & Cable',
    'Belts',
    'Sleeves',
    'Wraps',
    'Straps & Grips',
    'Footwear',
    'Singlets & Supportive Gear',
    'Apparel',
    'Bands & Conditioning',
    'Strongman',
    'Storage & Collars',
    'Recovery & Mobility',
    'Chalk & Accessories',
    'Other',
]

CATEGORY_ICONS = {
    'Barbells': '🏋',
    'Plates': '⚪',
    'Dumbbells & Kettlebells': '🔔',
    'Racks & Rigs': '🏗',
    'Benches': '🛋',
    'Machines & Cable': '⚙',
    'Belts': '🥋',
    'Sleeves': '🦵',
    'Wraps': '🩹',
    'Straps & Grips': '🪢',
    'Footwear': '👟',
    'Singlets & Supportive Gear': '🦺',
    'Apparel': '👕',
    'Bands & Conditioning': '🎽',
    'Strongman': '🪨',
    'Storage & Collars': '📦',
    'Recovery & Mobility': '💆',
    'Chalk & Accessories': '🧴',
    'Other': '❓',
}

# Store labels that describe a shelf, not a product type. Products in these are
# always classified from their name.
GENERIC_LABELS = {
    'all', 'all items', 'new', 'clearance', 'sale', 'gear', 'equipment',
    'accessories', 'apparel', 'featured', 'best sellers', 'shop all',
    'uncategorized', 'strength tools', 'mobility & gear', 'support gear',
    'ipf equipment', 'uspa gear', 'usaw gear', 'weightlifting', 'xebex fitness',
    'grip & straps',
}

# Labels used only when no name rule matched. NoBull files shoes and clothing
# under one "Shoes & Apparel" label and names its shoes by model ("Drive 2",
# "Journey 2"), which no vocabulary rule can catch — but the clothing in the
# same bucket ("Training Short", "Elements Sweatpant") IS caught by the apparel
# rule, so what falls through is reliably footwear.
_FALLBACK_LABEL_MAP = {
    "men's shoes & apparel": 'Footwear',
    "women's shoes & apparel": 'Footwear',
    'shoes & apparel': 'Footwear',
}

# ── Name rules ────────────────────────────────────────────────────────────
# Ordered: the first match wins, so narrower patterns come first.
#
# Patterns are built with words() / phrase() rather than written by hand,
# because hand-written `\b(alternatives)\b` silently fails on plurals: after
# matching "resistance band" in "Resistance Bands" the trailing \b sits between
# "d" and "s", which is not a word boundary, so the rule never fires. The
# helpers append an optional plural inside the boundary.

def words(*alternatives):
    r"""\b(?:a|b|c)(?:e?s)?\b — each alternative, singular or plural."""
    return r'\b(?:' + '|'.join(alternatives) + r')(?:e?s)?\b'


def phrase(*alternatives):
    r"""Same, but each alternative may contain spaces (normalised to \s*)."""
    alts = [a.replace(' ', r'\s*') for a in alternatives]
    return words(*alts)


# Multi-word phrases that would otherwise be captured by a broader rule earlier
# in the list ("Pull-Up Bar" reads as a bar; "Plate Shelf" reads as a plate).
_EARLY_RULES = [
    ('Racks & Rigs', phrase('pull-?up bar', 'chin-?up bar', 'pull-?up station',
                            'squat stand', 'power cage', 'cable tower',
                            'utility arm', 'salmon ladder')),
    ('Storage & Collars', phrase('plate shelf', 'ball shelf', 'plate tree',
                                 'plate storage', 'plate rack', 'bar storage',
                                 'accessory holder')),
    # Garment and merchandise nouns are decisive whatever precedes them: a
    # "Load The Bar T-Shirt" is a shirt, and a "Barbell Patch" is a patch. These
    # run first because the equipment rules match on bare "bar"/"barbell"/"plate"
    # and would otherwise claim every branded item that mentions one.
    # NOTE: bare "shirt" is deliberately absent — a "Bench Shirt" is supportive
    # gear, not apparel.
    ('Apparel', phrase('t-?shirt', 'tee', 'tank top', 'tank', 'hoodie', 'hoody',
                       'sweatshirt', 'crewneck', 'pullover', 'sweatpant',
                       'jogger', 'legging', 'racerback', 'polo', 'beanie',
                       'snapback', 'dad hat', 'trucker hat', 'sock')),
    ('Chalk & Accessories', phrase('patch', 'sticker', 'decal', 'enamel pin',
                                   'lapel pin', 'keychain', 'magnet', 'mug',
                                   'poster', 'banner', 'koozie', 'gift card',
                                   'water bottle', 'shaker bottle', 'flag',
                                   'brush', 'guide', 'book', 'dvd', 'towel',
                                   'cleaning kit', 'lanyard')),
]

_NAME_RULES = [
    # -- Sleeves / wraps: before "knee"/"elbow" can fall through to apparel.
    ('Sleeves', phrase('knee sleeve', 'elbow sleeve', 'forearm sleeve',
                       'shin sleeve', 'calf sleeve', 'power sleeve',
                       'neoprene sleeve')),
    ('Wraps', phrase('knee wrap', 'wrist wrap', 'elbow wrap', 'hand wrap',
                     'thumb wrap', 'wrist support', 'high-?top wrap',
                     'wrist guard')),

    # -- Belts. "belt" alone is safe once bag/loop senses are excluded.
    ('Belts', phrase('lever belt', 'prong belt', 'dip belt', 'deadlift belt',
                     'powerlifting belt', 'weightlifting belt', 'nylon belt',
                     'velcro belt', 'lifting belt', 'lever buckle',
                     'belt attachment')
              + r'|\bbelts?\b(?!.*\b(?:bag|loop|drive|conveyor)\b)'
              + r'|\b\d\s*(?:"|inch|in)\s*belts?\b'),

    # -- Straps, grips, hooks, cuffs.
    ('Straps & Grips', phrase('lifting strap', 'wrist strap', 'figure 8 strap',
                              'figure eight strap', 'deadlift strap', 'ab strap',
                              'shake strap', 'lifting hook', 'gymnastic grip',
                              'hand grip', 'power cuff', 'ankle cuff', 'mega cuff')
                       + r'|\b(?:straps?|grips?)\b(?!.*\b(?:bar|barbell|rack|tank|tee|shirt)\b)'),

    # -- Barbells and bars.
    ('Barbells', r'\b(?:barbells?|bars?)\b(?!.*\b(?:bell\s*bag|pad|collar|clamp|'
                 r'holder|rack|storage|stand|jack|bag)\b)'
                 + '|' + phrase('ohio bar', 'bella bar', 'texas bar', 'swiss bar',
                                'safety squat bar', 'trap bar', 'hex bar', 'axle bar',
                                'log bar', 'cambered bar', 'buffalo bar', 'ez curl',
                                'specialty bar', 'deadlift bar', 'squat bar',
                                'power bar', 'training bar', 'technique bar')),

    # -- Plates.
    ('Plates', phrase('bumper plate', 'change plate', 'competition plate',
                      'technique plate', 'calibrated plate', 'fractional plate',
                      'steel plate', 'iron plate', 'urethane plate', 'weight plate')
              + r'|\bplates?\b(?!.*\b(?:tank|tee|shirt|graphic|hoodie|sweatshirt|shelf)\b)'),

    ('Dumbbells & Kettlebells', phrase('dumbbell', 'kettlebell', 'kettle bell',
                                       'db set')),

    # -- Big steel and attachments.
    ('Racks & Rigs', phrase('power rack', 'squat rack', 'half rack', 'wall mount',
                            'squat stand', 'rig', 'j-?hook', 'j-?cup', 'spotter arm',
                            'safety strap', 'rack attachment', 'landmine',
                            'pull-?up bar', 'pull-?up rig', 'pull-?up station',
                            'dip station', 'plate tree', 'land ?mine')
                    + r'|\bracks?\b(?!.*\b(?:pad|strap|shirt)\b)'),
    ('Benches', phrase('flat bench', 'adjustable bench', 'incline bench',
                       'decline bench', 'utility bench', 'competition bench', 'ghd')
                + r'|\bbench(?:es)?\b(?!.*\b(?:shirt|press\s*shirt|blokz|board)\b)'),
    ('Machines & Cable', phrase('lat pulldown', 'lat pull down', 'cable machine',
                                'cable attachment', 'cable handle', 'cable crossover',
                                'functional trainer', 'leg press', 'leg curl',
                                'leg extension', 'smith machine', 'pulley',
                                'pressdown', 'tricep rope', 'lat bar', 'row handle',
                                'd-?handle', 'chest press', 'rotating handle',
                                'double handle', 'seat')),

    # -- Competition supportive gear (before generic apparel).
    ('Singlets & Supportive Gear', phrase('singlet', 'squat suit', 'bench shirt',
                                          'deadlift suit', 'dl suit', 'brief',
                                          'erector shirt', 'bench blokz',
                                          'slingshot', 'bench board', 'squat briefs')),

    # -- Footwear.
    ('Footwear', phrase('shoe', 'lifter', 'trainer', 'sneaker', 'boot', 'slipper',
                        'cleat', 'romaleo', 'adipower', 'dropzero', 'barefoot',
                        'lace', 'slide', 'sandal', 'adistar', 'slip on', 'slip-?on')
                 + r'(?!.*\bbags?\b)'),

    # -- Conditioning and cardio.
    ('Bands & Conditioning', phrase('resistance band', 'hip circle', 'mini band',
                                    'monster band', 'band set', 'band handle',
                                    'jump rope', 'speed rope', 'plyo box',
                                    'jump box', 'agility ladder', 'speed ladder',
                                    'medicine ball', 'wall ball', 'slam ball',
                                    'battle rope', 'ab mat', 'ab wheel',
                                    'power wheel', 'sled', 'rower', 'air bike',
                                    'echo bike', 'assault bike', 'ski erg',
                                    'gymnastic ring', 'treadmill', 'climber',
                                    'elliptical', 'erg', 'band', 'sledtrac')),
    ('Strongman', phrase('atlas stone', 'yoke', 'farmer walk', 'farmers walk',
                         'farmers handle', 'log press', 'sandbag', 'keg',
                         'tire flip', 'husafell', 'circus dumbbell', 'strongman',
                         'strongwoman')),

    # -- Storage and collars.
    ('Storage & Collars', phrase('collar', 'clamp', 'lock jaw', 'storage', 'holder',
                                 'hanger', 'rack mount', 'barbell holder',
                                 'bumper storage', 'shelf', 'hitch pin', 'bar jack')),

    # -- Recovery.
    ('Recovery & Mobility', phrase('foam roller', 'massage', 'lacrosse ball',
                                   'mobility', 'stretch', 'recovery', 'liniment',
                                   'lotion', 'balm', 'ice pack', 'voodoo',
                                   'compression boot', 'percussion')),

    # -- Chalk, tape, consumables, small kit.
    ('Chalk & Accessories', phrase('chalk', 'tape', 'ammonia', 'smelling salt',
                                   'salt', 'sticker', 'decal', 'patch', 'flag',
                                   'banner', 'koozie', 'water bottle', 'shaker',
                                   'towel', 'book', 'dvd', 'gift card',
                                   'leather care', 'conditioner', 'brush',
                                   'nose tork', 'squat pad', 'arm blaster',
                                   'neck harness', 'head harness', 'bag',
                                   'backpack', 'wallet', 'keychain', 'mug',
                                   'poster', 'refill', 'pin', 'magnet', 'lanyard',
                                   'pivot pad', 'hip thrust pad', 'sunglasses',
                                   'timeline', 'stick', 'press handle')),

    # -- Apparel last: broad, and overlaps everything above.
    ('Apparel', phrase('t-?shirt', 'tee', 'tank', 'hoodie', 'sweatshirt',
                       'crewneck', 'crew', 'short', 'jogger', 'sweatpant', 'pant',
                       'chino', 'legging', 'tight', 'sports bra', 'bra',
                       'crop top', 'polo', 'jacket', 'pullover', 'vest', 'beanie',
                       'hat', 'cap', 'visor', 'sock', 'jersey', 'long sleeve',
                       'shirt', 'top', 'compression', 'underwear', 'boxer',
                       'glove', 'headband', 'hoody', 'comfort colors')),
]

_COMPILED_NAME_RULES = [(cat, re.compile(pat, re.I))
                        for cat, pat in _EARLY_RULES + _NAME_RULES]

# ── Store-label mapping ───────────────────────────────────────────────────
# Applied when the retailer's label is specific enough to trust.

_LABEL_MAP = {
    'barbells': 'Barbells', 'specialty bars': 'Barbells', 'barbell components': 'Barbells',
    'mens 20kg barbells': 'Barbells', 'womens 15kg barbells': 'Barbells',
    'technique barbells': 'Barbells', 'junior barbells': 'Barbells',

    'plates': 'Plates', 'steel plates': 'Plates', 'competition plates': 'Plates',
    'bumpers': 'Plates', 'bumper plates': 'Plates', 'change plates': 'Plates',
    'competition bumpers': 'Plates',

    'dumbbells': 'Dumbbells & Kettlebells', 'kettlebells': 'Dumbbells & Kettlebells',

    'racks': 'Racks & Rigs', 'power racks': 'Racks & Rigs', 'pull-up rigs': 'Racks & Rigs',
    'racks & ghds': 'Racks & Rigs', 'rigs': 'Racks & Rigs',
    'benches': 'Benches', 'bench boards': 'Benches',
    'dual station': 'Machines & Cable', 'leg machines': 'Machines & Cable',
    'functional trainers': 'Machines & Cable', 'all-in-one trainers': 'Machines & Cable',

    'belts': 'Belts', '3" belts': 'Belts', '4" belts': 'Belts', 'lever belts': 'Belts',
    'powerlifting belts': 'Belts', 'deadlift belts': 'Belts',

    'knee sleeves': 'Sleeves', 'elbow sleeves': 'Sleeves', 'sleeves': 'Sleeves',
    'knee wraps': 'Wraps', 'wrist wraps': 'Wraps', 'wraps': 'Wraps',
    'high-top wraps': 'Wraps',

    'straps': 'Straps & Grips', 'lifting straps': 'Straps & Grips',
    'shake straps': 'Straps & Grips',

    'shoes': 'Footwear', 'lifters': 'Footwear', 'trainers': 'Footwear',
    'weightlifting shoes': 'Footwear', 'footwear': 'Footwear', 'barefoot': 'Footwear',
    'l-1 lifters': 'Footwear', 'l-2 lifters': 'Footwear', "women's lifters": 'Footwear',

    'singlets': 'Singlets & Supportive Gear', 'squat suits': 'Singlets & Supportive Gear',
    'bench shirts': 'Singlets & Supportive Gear', 'deadlift suits': 'Singlets & Supportive Gear',
    'dl suits': 'Singlets & Supportive Gear',

    "men's tops": 'Apparel', "women's tops": 'Apparel', "men's shorts": 'Apparel',
    "women's shorts": 'Apparel', 'jackets': 'Apparel', "men's compression": 'Apparel',
    "women's compression": 'Apparel',

    'bands': 'Bands & Conditioning', 'hip circles': 'Bands & Conditioning',
    'conditioning & agility': 'Bands & Conditioning',
    'gymnastics & climbing': 'Bands & Conditioning',
    'strongman': 'Strongman',

    'collars': 'Storage & Collars', 'storage': 'Storage & Collars',
    'plate storage': 'Storage & Collars', 'wall storage': 'Storage & Collars',

    'recovery': 'Recovery & Mobility',
    'tape': 'Chalk & Accessories', 'books': 'Chalk & Accessories',
    'flags': 'Chalk & Accessories', 'leather care': 'Chalk & Accessories',
    'bags': 'Chalk & Accessories', 'bags & wallets': 'Chalk & Accessories',
}


def classify(name, raw_category=None):
    """Return the canonical category for a product.

    Name rules run first — a store's shelf is often generic while the product
    name is not — but a specific store label is trusted over the coarse fallback.
    """
    label = (raw_category or '').strip().lower()
    text = (name or '').strip()

    mapped = _LABEL_MAP.get(label)

    for category, pattern in _COMPILED_NAME_RULES:
        if pattern.search(text):
            return category

    if mapped:
        return mapped
    return _FALLBACK_LABEL_MAP.get(label, 'Other')


def classify_product(product):
    return classify(product.get('name'), product.get('category'))
