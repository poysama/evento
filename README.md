# Evento - One Piece Event Card binder tracker

A small local app for tracking a Japanese-language collection of every One Piece Card Game **Event** card
(ST-01 through OP-18), laid out as a 4x4 binder. Mark each slot as a JP card you own, note EN/KR placeholders
you're holding until you get the JP copy, and tick foils separately.

## Run it

Needs Python 3 (standard library only).

    python server.py

Then open http://127.0.0.1:8765/ (on Windows you can double-click `Start Binder.bat`).
Your collection is saved to `collection.json` next to `server.py`. Browsing is public and read-only. Set the `BINDER_EDIT_PASSCODE` environment variable before starting the local server to enable **Unlock editing**. An unset or empty passcode leaves editing disabled.

## Card images

Card art is **not included** in this repository (it belongs to Bandai and is not ours to redistribute).
Without images the grid shows blank tiles, with the card name and code still shown. To see art locally,
put your own images in `card_images/<CODE>.jpg` (e.g. `card_images/OP01-026.jpg`).

## Files

- `server.py` - tiny local server: serves the page and saves your collection (127.0.0.1 only)
- `index.html` - the whole UI
- `cards.json` - the 410 Event cards in binder order (404 from the sets plus 6 promo-only Events, placed by release date)
- `products.json` - every product (release date) and which products each card appears in
- `maintain.py` - data maintenance: sort the binder by release date, add sets, add cards (see below)
- `maintain_config.json` - config for the sort (release-date vs set-code order, and any manual placements)

## Maintaining the data (new sets and cards)

`maintain.py` keeps `cards.json` and `products.json` in release-date order as new products come out:

    python maintain.py sort                        # reorder the binder by release date (preview; add --apply to write)
    python maintain.py add-set OP-19 --name "God Valley" --family OP --kind "Booster pack" --date 2026-12-26
    python maintain.py add-card OP19-001 --name "..." --rar C --set OP-19 --col Red [--foil] [--also-in PRB-02]

`sort` orders the sets by the `date` in `products.json` (stable, so sets sharing a release date keep their
place) and renumbers every `slot`. `add-set` inserts a product in date order; `add-card` inserts a card,
records its set (and any reprints) in `products.json` `sources`, and renumbers the binder so slots stay
contiguous. Both `add-*` commands have `--dry-run`, and `sort` is a preview unless you pass `--apply`.

`add-card` (and `edit-card`) cover the whole card schema, not just the basics - the alt-art / Manga
metadata and the Japanese name / series id for the share page:

    python maintain.py add-card OP19-001 --name "..." --rar C --set OP-19 --col Red --foil \
        --jp "日本語名" --alt p1,p2 --altsrc "p1=Best Selection Vol.7,p2=PRB-02" \
        --alttype "p1=Original,p2=Comic" --altsame p2 --altstar p1 --altdup "p2=p1" --manga p1

    python maintain.py edit-card OP19-001 --jp "直した名前" --noen        # update an existing card

The alt-art flags take comma lists (`--alt`, `--altsame`, `--altstar`) or `id=value` maps (`--altsrc`,
`--alttype`, `--altdup`); `--manga` names the Manga alt id. `--jp` writes the Japanese name and the `sid`
is derived from the set code (ST-14 -> 550014, OP-17 -> 550117, EB-04 -> 550204, promos -> 550901) unless
you pass `--sid`. `edit-card` only rewrites the fields you give it.

The config in `maintain_config.json` controls the sort:

    {
      "sort_by": "date",        // "date" (release date, default) or "code" (set code)
      "place_after": {}         // optional: pin a set right after another, e.g. "P-2207": "ST-04"
    }

## Hosting (AWS) and auto-deploy

The same app runs on AWS at https://evento.peonbox.xyz - one Lambda (`aws/lambda_function.py`) behind an
API Gateway HTTP API, with your collection and card art in a private S3 bucket. Binder, Collection and Wishlist are publicly viewable. Editing requires **Unlock editing** with the existing passcode or an enrolled passkey. Notes, history, private drafts and account tools remain available only to editors.

**Every push to `main` deploys automatically** (`.github/workflows/deploy.yml`): it runs the tests in
Python and Node suites, packages the Python domain module, UI scripts and catalog fallbacks, updates the Lambda,
checks that the live code matches the build, and smoke-tests the site. AWS access uses short-lived OIDC
credentials for a role that only `main` of this repo can assume - no keys are stored in GitHub.

One-time setup scripts (run from a machine with AWS credentials): `aws/deploy.ps1` (bucket, role, function,
uploads local card art), `aws/setup-domain.ps1` (certificate, API, DNS), `aws/setup-ci.ps1` (GitHub deploy role).
Card art is uploaded only by `deploy.ps1` from your local `card_images/` and is never part of the repo or CI.

## Remembered editing access

Editing access is remembered in a Secure, HttpOnly cookie for one year. **Lock editing** clears this device’s cookie; changing the deployed passcode invalidates existing editing cookies. Passwords and session tokens are not stored in browser local storage or URLs. Unlocking reloads the full editor documents before changes can be saved.

## Passkey sign-in

The hosted site signs you in with a **passkey** (Face ID, Windows Hello, Touch ID or a security key) instead of
typing the passcode. Open the site with the passcode once, use the **Passkeys** button (or the banner) to add
each device, and from then on sign in with one tap. The passcode keeps working as a backup and as the way to
add a passkey on a brand-new device. Passkeys are checked with the `webauthn` library (user verification
required, single-use challenges, sign-counter checks); the server only stores public keys.
Passkeys are bound to the domain `evento.peonbox.xyz` - if the domain ever changes, add them again.
`aws/test_handler.py` exercises the full registration and sign-in flows with a software authenticator.

## Share what you are looking for

The **Share** button creates an unlisted link to a read-only page showing the Japanese Event cards you are still
missing (optionally with the foil / alt-art versions), so friends can help you find them. The page needs no login,
shows only the missing cards, and can be stopped or replaced with a new link at any time.
Bandai does not allow its pictures to be embedded on other sites, so the pictures are served from the private bucket
through the secret link, only for cards on the list; "Show card pictures" can be switched off (names, codes and links
to Bandai's own card list remain). The page has a colour filter (toggle one or more of Red / Green / Blue / Purple / Black / Yellow, each showing how many
are still missing) and tags cards that are missing in JP only because you hold the EN or KR copy. The list can also be
copied as plain text (with the same EN/KR note).

## Backups and restoring

The bucket has S3 versioning on: every save of `data/` (your collection, the share link, passkeys) keeps the previous
copy for 14 days, then drops it (the current copy is never dropped). `aws/deploy.ps1` sets this up, together with the
one-day expiry of `used/`, because S3 replaces the whole lifecycle configuration on each update.

If a bad save ever wipes your data, list the versions and copy an older one back (use your default AWS profile):

```powershell
$B = 'evento-binder-861276084535'
aws s3api list-object-versions --bucket $B --prefix data/collection.json --region ap-southeast-1 --query "Versions[].[VersionId,LastModified,Size,IsLatest]" --output table
aws s3api copy-object --bucket $B --key data/collection.json --copy-source "$B/data/collection.json?versionId=THE_VERSION_ID" --region ap-southeast-1
```

Pick the newest version whose size looks right, then reload the site. (An old `null` version is the copy from before
versioning was switched on.)

## Bought by proxy (on order)

For cards you have bought through a proxy but not received yet: open the card and tap **Bought by proxy, on the way**, and
optionally write where it came from (shop, proxy, order id) in the note. A card on order is marked ON ORDER in the binder,
is taken off your friends' share page (it is no longer "looking for"; they only see "N more already bought and on the way",
never the note) and is not counted in What to buy. The **On order** button lists everything you are waiting for, with a
note field, a **Received** button and a copy-as-text list. Marking the card JP complete (or **Received**) ends the order
and clears the note. The Completed page and the binder progress still count only cards in hand.

## Alt art and Manga versions

The packaged legacy alt-art data records known print sources, illustration types (Comic / Original / Animation), and repeat-drawing foil finishes. It is a maintained snapshot, not a complete live official feed. A ★ next to the
rarity marks the special foil print. 17 cards have a Manga version (your list). The Alt art page hides the repeat-drawing prints
by default ("Different drawings only"). When you own an alt art or the Manga (mark **Have it**), it replaces the regular card
in the 4x4 grid (Japanese art mode), like the real binder; use **Show this in my binder** / **Show the regular card** in the
viewer to choose which one sits in the slot.

The **Price** button (private, needs login - never shown on the public share page) shows an estimated cost for the
alt-art / Manga versions marked Want, from Yuyu-tei's sell prices. Yuyu-tei returns 403 Forbidden to requests from
AWS's IP ranges, so the Lambda cannot fetch this itself; the total is instead checked from elsewhere from time to
time and written to `data/price_cache.json` in the bucket, and `GET /api/price` only ever reads that cache (no live
"refresh" - one would be misleading, since it couldn't actually refresh). It flags itself as out of date if the
wishlist has changed since the price was last checked.

**My list** (`GET /mine`, needs login) is a private mirror of the share page - the same layout, with pictures, but
always shows everything you still need (missing cards and alt-art / Manga wants) regardless of the share link's own
settings, plus the price estimate. Pictures go through the ordinary authenticated image routes instead of a share
token, so nothing about it is reachable without logging in.

The **Alt art** page has two sections, **Alternate arts** (by set) and **Manga**, so you can see which cards have them.
The EN | JP switch inside the card viewer only changes the viewer; the switch at the top changes the binder. Searching for
a card grays out the rest of the page and makes the found card shine for a few seconds. Pictures load a few at a time and
are retried if the site is busy, so a page never gets stuck on the other language.

Every alt-art (parallel) and Manga picture has its own mark: **Want it**, **On order** or **Have it**. The **Alt art**
button opens a page with all of them: tap a picture to cycle Want, On order, Have, clear, filter by mark, or show only
Manga. In the card viewer, pick a version under the picture and mark it there. Marking a version **Have it** counts
the whole card as collected, even without the standard JP print - it already takes the card's slot in the binder,
so it counts the same way. Want and On order marks do not. The share page's optional "alt-art / Manga versions I want"
section lists only the versions marked Want (with pictures). The old on/off Manga flag is converted to a Have mark on
the Manga version.

## Foil is an optional extra

Foil is ticked *after* a card is marked JP complete (its button stays greyed out until then) and never
affects completion. Any card can be marked foil; the built-in list of which cards have a foil is not treated as complete.
Un-ticking the JP card hides its foil / Manga marks but keeps them, so they come back if you tick it again.

## Promo-only Events

Six Events have no set of their own: P-002, P-024 and P-057 to P-060. They sit in the binder where they came out
(pseudo-sets `P-2207`, `P-2209`, `P-2311`). The four Uta cards also come, in parallel art, in the ST-16 deck.

## Completed

The **Completed** button is the opposite of What to buy: the sets where every Event card is in your binder, with card
lists and foil counts, a **Close to completion** list (sets that are 80% or 90% collected, with the percentage, x/y and the
missing cards), plus other products that have nothing left to find.

## What to buy

The **What to buy** button shows how many cards you are missing per product, which products contain them (including
reprints in the PRB boosters and starter decks), and a shortest shopping list that covers everything you are missing
with as few products as possible. The Boosters / Starter decks / Promos filter limits both the list and the products shown (decks have fixed contents, so decks only is a safe way to buy). "14 only in OP-03" means 14 of your missing cards appear in no other product. Data: `products.json` (which products each card appears in, from Bandai's card list).

## Manga and alt-art

Cards with a Japanese alt-art printing get Standard / Alt art buttons in the card viewer; the four Manga Events
(OP09-020, -057, -078, -096) are marked and have an optional "Manga" owned flag - a Have mark on it counts the
card as complete, same as any other alt-art version.


## Collection workflow and safe editing

The Collection, Wishlist and Orders views track individual print versions. Missing Japanese base cards stay on the wishlist even when an alternate print fills their binder slot. Ordered bases and prints are excluded from actionable wants. Announced releases are opt-in; drafts are excluded. Existing cards default to released.

Catalog edits use one canonical catalog document and conditional ETag saves. Legacy packaged catalogs remain fallback input; canonical saved data takes precedence. Existing numbers, print IDs, collection marks and physical slots are preserved. New cards remain unplaced until explicitly assigned. Per-print notes and history are private. Select an image in the editor, then Save to commit its catalog reference.

For local verification:

```sh
python -m unittest discover -s tests
python aws/test_handler.py
node --test tests/*.test.js
```

## Review an official catalog import

`import_catalog.py` accepts an explicit Japanese official cardlist URL, optionally paired with saved HTML from that same page. It extracts Event records from official `modalCol` records, not a claimed complete feed. The official form uses `search=true`, `series` and `freewords`; confirm the resulting product list before accepting a proposal. No images are downloaded or redistributed.

Start the local server, then preview:

```sh
python import_catalog.py preview --source-url 'https://www.onepiece-cardgame.com/cardlist/?search=true&series=550112' --html /path/to/official.html --out /tmp/catalog-review.json
```

Inspect `changes`, `review` and the resulting `catalog` in the proposal file. Existing manual names, variants, slots and collection data are retained. Provenance aliases are recorded under `officialPrints`, with a hash of the supplied HTML in the proposal. Unknown parallel prints require explicit identity and kind review; a print suffix never establishes Manga. New bases are unplaced. The importer never removes cards or reorders slots. Empty/unrecognized source pages fail closed.

After review, apply the proposal explicitly:

```sh
python import_catalog.py apply /tmp/catalog-review.json
```

The proposal records the catalog endpoint and ETag. Apply sends one conditional catalog PUT; a concurrent edit rejects the proposal instead of overwriting newer data. Generate a fresh preview after a conflict. Hosted endpoints can be selected with `--endpoint`; authenticated requests accept `--cookie` (avoid storing cookies in shared shell history). Ambiguous review records are excluded from automatic changes and need manual version editing. Saved HTML provenance relies on the supplied source URL; this tool cannot authenticate an offline file's origin.

The official OP12-018 search checked during importer development returned only the base print, named 覇王色の覇気, from 師弟の絆【OP-12】. It did not establish an alternate-print ID for a separately supplied picture. Add such artwork as a manual version with source unspecified until an authoritative match is available; never manufacture a pN identity from the picture.

## What counts as alternate art

An AA/parallel is a distinct artwork, not simply another product printing or foil finish. Different drawings can keep the standard text frame, use a bordered full-art layout, or be Manga full art. A rarity star by itself does not establish alternate artwork.

Same-art reprints and foil finishes live under **Print sources / Foil**. Repeated prints of one AA share one artwork entry. Their original print IDs, ownership and order marks are retained, so changing classification does not erase collection history. **Add/Edit version** lets you distinguish different artwork, Manga, and a same-art reprint.
