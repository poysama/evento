#!/usr/bin/env python3
"""Maintenance tool for the Evento binder data (cards.json, products.json, aws/share_data.json).

Three commands:

  python maintain.py sort                       # reorder the binder by release date (preview; add --apply to write)
  python maintain.py add-set OP-19 --name "God Valley" --family OP --kind "Booster pack" --date 2026-12-26
  python maintain.py add-card OP19-001 --name "..." --rar C --set OP-19 --col Red [--foil] [--jp "..." ] [--alt ...]
  python maintain.py edit-card OP19-001 [--jp "..." ] [--alt ...] [--sid ...]   # update metadata on an existing card

Sorting is driven by the release dates in products.json and can be tweaked in
maintain_config.json (next to this file):

  {
    "sort_by": "date",        # "date"  -> sort sets by their release date (default)
                              # "code"  -> sort sets by their set code instead
    "place_after": {          # optional: pin a set to sit immediately after another
      "P-2207": "ST-04"       #   (used for promo pseudo-sets that have no real release date)
    }
  }

add-card covers the whole card schema, not just the basics:

  * cards.json fields: name, num, rar, set, col, foil, noen, and the alt-art / Manga
    metadata (alt, altsrc, alttype, altsame, altstar, altdup, manga).
  * aws/share_data.json: the Japanese name (jp), Bandai series id (sid) and the alt list.

The sid is derived automatically from the set code (ST-14 -> 550014, OP-17 -> 550117,
EB-04 -> 550204, PRB-01 -> 550301, promos -> 550901); pass --sid to override.

alt-art flags take comma lists or key=value maps:
  --alt p1,p2                --altsrc "p1=Best Selection Vol.1,p2=PRB-01"
  --alttype "p1=Original,p2=Comic"   --altsame p1,p2   --altstar p2
  --altdup "p2=p1"           --manga p1

Only the standard library is used, like server.py.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CARDS = HERE / 'cards.json'
PRODUCTS = HERE / 'products.json'
SHARE = HERE / 'aws' / 'share_data.json'
CONFIG = HERE / 'maintain_config.json'

COLOURS = {'Red', 'Green', 'Blue', 'Purple', 'Black', 'Yellow'}
ALT_TYPES = {'Comic', 'Original', 'Animation', 'Other'}
SET_RE = re.compile(r'^(?:[A-Z]{2,3}-\d{2}|P-\d{3,4})$')
NUM_RE = re.compile(r'^(?:[A-Z]{2,3}\d{2}|P)-\d{3}$')
ALT_RE = re.compile(r'^p\d{1,2}$')

DEFAULT_CONFIG = {
    'sort_by': 'date',
    'place_after': {},
}


# ---------------------------------------------------------------- io
def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def save(path, obj):
    path.write_text(dumps(obj), encoding='utf-8')


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        cfg.update(load(path))
    return cfg


def natural_key(s):
    """'ST01-014' -> ['ST', 1, '-', 14] so numbers sort naturally."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', str(s)) if t]


# ---------------------------------------------------------------- sorting
def set_dates(products):
    return {p['code']: p.get('date') or '9999-12-31' for p in products}


def ordered_sets(cards, products, config):
    """The set order: by release date (or code), then any place_after overrides."""
    sets = []
    for c in cards:
        if c['set'] not in sets:
            sets.append(c['set'])
    if config.get('sort_by') == 'code':
        sets.sort(key=natural_key)
    else:
        dates = set_dates(products)
        sets.sort(key=lambda s: dates.get(s, '9999-12-31'))  # stable: same-day sets keep their original order
    for promo, anchor in (config.get('place_after') or {}).items():
        if promo in sets and anchor in sets:
            sets.remove(promo)
            sets.insert(sets.index(anchor) + 1, promo)
    return sets


def sort_cards(cards, products, config):
    order = {s: i for i, s in enumerate(ordered_sets(cards, products, config))}
    return sorted(cards, key=lambda c: (order.get(c['set'], 10**9), natural_key(c['num'])))


def sort_products(products):
    return sorted(products, key=lambda p: p.get('date') or '9999-12-31')


def renumber(cards):
    for i, c in enumerate(cards):
        c['slot'] = i + 1
    return cards


def set_order(cards):
    sets = []
    for c in cards:
        if c['set'] not in sets:
            sets.append(c['set'])
    return sets


def diff_order(old, new):
    old_idx = {n: i for i, n in enumerate(old)}
    return [n for i, n in enumerate(new) if old_idx.get(n) != i]


# ---------------------------------------------------------------- alt-art / metadata parsing
def parse_list(s):
    """'p1,p2,p3' -> ['p1', 'p2', 'p3']; '' -> []"""
    return [x.strip() for x in (s or '').split(',') if x.strip()]


def parse_map(s):
    """'p1=Best Selection,p2=PRB-01' -> {'p1': 'Best Selection', 'p2': 'PRB-01'}"""
    out = {}
    for part in parse_list(s):
        k, sep, v = part.partition('=')
        if k:
            out[k] = v if sep else ''
    return out


def derive_sid(set_code):
    """Bandai series id from a set code. ST-14 -> 550014, OP-17 -> 550117, EB-04 -> 550204,
    PRB-01 -> 550301, promos (P-...) -> 550901. None when it cannot be derived."""
    if set_code.startswith('P-'):
        return 550901
    m = re.match(r'^([A-Z]{2,3})-(\d{2})$', set_code)
    if m:
        base = {'ST': 550000, 'OP': 550100, 'EB': 550200, 'PRB': 550300}.get(m.group(1))
        if base is not None:
            return base + int(m.group(2))
    return None


def alt_ids_from_args(args):
    """The list of alt ids, from --alt or inferred from the other alt flags."""
    if args.alt is not None:
        return parse_list(args.alt)
    ids = set()
    ids.update(parse_map(args.altsrc).keys())
    ids.update(parse_map(args.alttype).keys())
    ids.update(parse_map(args.altdup).keys())
    ids.update(parse_list(args.altsame))
    ids.update(parse_list(args.altstar))
    if args.manga:
        ids.add(args.manga)
    return sorted(ids, key=natural_key)


def apply_alt(card, args):
    """Build (or clear) the alt-art / Manga metadata onto `card` from the alt flags.

    For add-card this is only called when the card has alt prints; for edit-card it is
    called only when --alt was explicitly given (so '--alt ""' clears everything)."""
    alt = alt_ids_from_args(args)
    if not alt:
        for k in ('alt', 'altsrc', 'alttype', 'altsame', 'altstar', 'altdup', 'manga'):
            card.pop(k, None)
        return
    card['alt'] = alt
    for key, value in (('altsrc', args.altsrc), ('alttype', args.alttype), ('altdup', args.altdup)):
        m = parse_map(value)
        if m:
            card[key] = m
        else:
            card.pop(key, None)
    for key, value in (('altsame', args.altsame), ('altstar', args.altstar)):
        lst = parse_list(value)
        if lst:
            card[key] = lst
        else:
            card.pop(key, None)
    if args.manga:
        card['manga'] = args.manga
    else:
        card.pop('manga', None)
    warn_alt(card)


def warn_alt(card):
    """Friendly consistency checks (non-fatal)."""
    alt = set(card.get('alt') or [])
    for key in ('altsrc', 'alttype', 'altdup'):
        if key in card:
            stray = set(card[key]) - alt
            if stray:
                print(f'warning: {key} keys not in alt: {", ".join(sorted(stray))}')
    for key in ('altsame', 'altstar'):
        if key in card:
            stray = set(card[key]) - alt
            if stray:
                print(f'warning: {key} ids not in alt: {", ".join(sorted(stray))}')
    if card.get('manga') and card['manga'] not in alt:
        print(f'warning: manga {card["manga"]} is not in alt')
    for t in (card.get('alttype') or {}).values():
        if t not in ALT_TYPES:
            print(f'warning: unknown alt illustration type {t!r} (one of {", ".join(sorted(ALT_TYPES))})')


def validate_alt_ids(args):
    for name, ids in (('--alt', args.alt and parse_list(args.alt)),
                      ('--altsame', parse_list(args.altsame)),
                      ('--altstar', parse_list(args.altstar))):
        if ids is None:
            continue
        for a in ids:
            if not ALT_RE.match(a):
                sys.exit(f'error: bad alt id {a!r} in {name} (expect p1, p2, ...)')


# ---------------------------------------------------------------- commands
def cmd_sort(args):
    cards = load(CARDS)
    products = load(PRODUCTS)
    config = load_config(args.config)

    new_cards = renumber(sort_cards(cards, products['products'], config))
    new_products = sort_products(products['products'])

    old_order = set_order(cards)
    new_order = set_order(new_cards)
    moved = diff_order(old_order, new_order)

    print(f'{len(cards)} cards, {len(old_order)} sets.')
    print(f'sort_by: {config["sort_by"]}')
    if config.get('place_after'):
        print('place_after:', json.dumps(config['place_after']))
    if not moved:
        print('already in order - nothing to change.')
        return
    print(f'{len(moved)} sets would move: {", ".join(moved)}')
    print('new set order:')
    print('  ' + ' -> '.join(new_order))

    if args.apply:
        save(CARDS, new_cards)
        products['products'] = new_products
        save(PRODUCTS, products)
        print('written: cards.json + products.json')
    else:
        print('(preview only - pass --apply to write)')


def cmd_add_set(args):
    products = load(PRODUCTS)
    if any(p['code'] == args.code for p in products['products']):
        sys.exit(f'error: set {args.code} already exists')
    if not SET_RE.match(args.code):
        sys.exit(f'error: bad set code {args.code!r} (expect ST-01, OP-19, P-2207, ...)')

    entry = {'code': args.code, 'name': args.name, 'family': args.family,
             'kind': args.kind, 'date': args.date}
    products['products'] = sort_products(products['products'] + [entry])

    pos = next(i for i, p in enumerate(products['products']) if p['code'] == args.code)
    print(f'add set {args.code} ({args.name}) at position {pos + 1} of {len(products["products"])}')
    if not args.dry_run:
        save(PRODUCTS, products)
        print('written: products.json')
    else:
        print('(dry run - nothing written)')


def card_from_args(args):
    """The basic card dict (no slot, no alt) from add-card / edit-card flags."""
    return {'name': args.name, 'num': args.num, 'rar': args.rar,
            'set': args.set, 'col': args.col, 'foil': bool(args.foil)}


def check_card_fields(num, set_code, col):
    if not NUM_RE.match(num):
        sys.exit(f'error: bad card number {num!r} (expect OP19-001, ST01-014, P-101, ...)')
    if not SET_RE.match(set_code):
        sys.exit(f'error: bad set code {set_code!r}')
    if col not in COLOURS:
        sys.exit(f'error: unknown colour {col!r} (one of {", ".join(sorted(COLOURS))})')


def share_entry(num, set_code, args):
    sid = args.sid if args.sid is not None else derive_sid(set_code)
    return {'jp': args.jp or '', 'sid': sid, 'alt': alt_ids_from_args(args)}


def cmd_add_card(args):
    cards = load(CARDS)
    products = load(PRODUCTS)
    share = load(SHARE)
    config = load_config(args.config)

    if any(c['num'] == args.num for c in cards):
        sys.exit(f'error: card {args.num} already exists (use edit-card to change it)')
    check_card_fields(args.num, args.set, args.col)
    validate_alt_ids(args)
    known_sets = {p['code'] for p in products['products']}
    if args.set not in known_sets:
        print(f'warning: set {args.set} is not in products.json - add it first with add-set')

    card = card_from_args(args)
    if args.noen:
        card['noen'] = True
    apply_alt(card, args)
    cards = renumber(sort_cards(cards + [card], products['products'], config))

    sources = products.setdefault('sources', {})
    srcs = []
    for s in [args.set] + list(args.also_in or []):
        if s not in srcs:
            srcs.append(s)
    sources[args.num] = srcs

    share[args.num] = share_entry(args.num, args.set, args)

    idx = next(i for i, c in enumerate(cards) if c['num'] == args.num)
    print(f'add card {args.num} ({args.name}) at binder slot {idx + 1} of {len(cards)}')
    print(f'sources: {", ".join(srcs)}')
    se = share[args.num]
    print(f'share_data: jp={se["jp"]!r} sid={se["sid"]} alt={se["alt"]}')
    if not args.dry_run:
        save(CARDS, cards)
        save(PRODUCTS, products)
        save(SHARE, share)
        print('written: cards.json + products.json + aws/share_data.json')
    else:
        print('(dry run - nothing written)')


def cmd_edit_card(args):
    cards = load(CARDS)
    products = load(PRODUCTS)
    share = load(SHARE)
    config = load_config(args.config)

    idx = next((i for i, c in enumerate(cards) if c['num'] == args.num), None)
    if idx is None:
        sys.exit(f'error: card {args.num} not found')
    card = cards[idx]
    validate_alt_ids(args)

    if args.name is not None:
        card['name'] = args.name
    if args.rar is not None:
        card['rar'] = args.rar
    if args.col is not None:
        check_card_fields(card['num'], card['set'], args.col)
        card['col'] = args.col
    if args.foil is not None:
        card['foil'] = args.foil
    if args.noen is True:
        card['noen'] = True
    elif args.en is True:
        card.pop('noen', None)

    if args.set is not None:
        if not SET_RE.match(args.set):
            sys.exit(f'error: bad set code {args.set!r}')
        card['set'] = args.set

    # alt metadata: only rebuilt when --alt was explicitly given ('--alt ""' clears it);
    # the other alt flags are keyed by alt ids, so they need --alt to know the full list.
    if args.alt is not None:
        apply_alt(card, args)
    elif any(x is not None for x in (args.altsrc, args.alttype, args.altsame, args.altstar, args.altdup, args.manga)):
        print('note: --altsrc/--alttype/--altsame/--altstar/--altdup/--manga need --alt too (ignored)')

    if args.set is not None:
        cards = renumber(sort_cards(cards, products['products'], config))

    # share_data: update jp / sid / alt when the matching flag was given
    se = share.setdefault(args.num, {'jp': '', 'sid': None, 'alt': []})
    if args.jp is not None:
        se['jp'] = args.jp
    if args.sid is not None:
        se['sid'] = args.sid
    elif args.set is not None:
        se['sid'] = derive_sid(args.set)
    if args.alt is not None:
        se['alt'] = alt_ids_from_args(args)

    print(f'edit card {args.num} ({card["name"]})')
    print(f'  card: {json.dumps(card, ensure_ascii=False)}')
    print(f'  share_data: {json.dumps(se, ensure_ascii=False)}')
    if not args.dry_run:
        save(CARDS, cards)
        save(SHARE, share)
        print('written: cards.json + aws/share_data.json')
    else:
        print('(dry run - nothing written)')


# ---------------------------------------------------------------- cli
def add_card_flags(p, required=False):
    """Add the card field flags to a subparser. `required` is for add-card."""
    p.add_argument('--name', required=required)
    p.add_argument('--rar', required=required, help='C, UC, R, SR or P')
    p.add_argument('--set', required=required, help='the set it first appears in, e.g. OP-19')
    p.add_argument('--col', required=required, help='Red, Green, Blue, Purple, Black or Yellow')
    p.add_argument('--foil', action='store_true', help='has a foil/parallel printing')
    p.add_argument('--noen', action='store_true', help='has no English printing')
    p.add_argument('--jp', help='Japanese name (for aws/share_data.json)')
    p.add_argument('--sid', type=int, help='Bandai series id (default: derived from the set)')
    p.add_argument('--alt', help='alt-art / Manga ids, comma-separated: p1,p2,p3')
    p.add_argument('--altsrc', help='where each print came from: "p1=Best Selection Vol.1,p2=PRB-01"')
    p.add_argument('--alttype', help='illustration type per print: "p1=Original,p2=Comic"')
    p.add_argument('--altsame', help='prints that repeat the regular card drawing: p1,p2')
    p.add_argument('--altstar', help='prints with the special foil finish: p2')
    p.add_argument('--altdup', help='prints that repeat another print: "p2=p1"')
    p.add_argument('--manga', help='the alt id that is the Manga version')


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')  # keep Windows consoles (cp1252) from choking on Japanese
        except Exception:
            pass

    p = argparse.ArgumentParser(prog='maintain.py', description='Maintain the Evento binder data.')
    p.add_argument('--config', type=Path, default=CONFIG, help='path to the sort config (default maintain_config.json)')
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('sort', help='reorder the binder by release date')
    s.add_argument('--apply', action='store_true', help='write the changes (default is a preview)')
    s.set_defaults(fn=cmd_sort)

    a = sub.add_parser('add-set', help='add a product to products.json')
    a.add_argument('code')
    a.add_argument('--name', required=True)
    a.add_argument('--family', required=True, help='ST, OP, EB, PRB or P')
    a.add_argument('--kind', required=True, help='e.g. "Booster pack", "Starter deck", "Extra booster", "Promo pack"')
    a.add_argument('--date', required=True, help='release date YYYY-MM-DD')
    a.add_argument('--dry-run', action='store_true')
    a.set_defaults(fn=cmd_add_set)

    c = sub.add_parser('add-card', help='add a card to cards.json (+ share_data) and renumber slots')
    c.add_argument('num')
    add_card_flags(c, required=True)
    c.add_argument('--also-in', nargs='*', default=[], help='other products that reprint this card')
    c.add_argument('--dry-run', action='store_true')
    c.set_defaults(fn=cmd_add_card)

    e = sub.add_parser('edit-card', help='update metadata on an existing card')
    e.add_argument('num')
    # same flags but optional; --foil/--noen become tri-state via extra --no-foil/--en
    add_card_flags(e, required=False)
    e.add_argument('--no-foil', dest='foil', action='store_false', help='clear the foil flag')
    e.add_argument('--en', action='store_true', help='clear the no-English flag')
    e.add_argument('--dry-run', action='store_true')
    e.set_defaults(foil=None, name=None, rar=None, set=None, col=None, noen=None, en=None,
                   jp=None, sid=None, alt=None, altsrc=None, alttype=None, altsame=None,
                   altstar=None, altdup=None, manga=None)
    e.set_defaults(fn=cmd_edit_card)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == '__main__':
    main(sys.argv[1:])
