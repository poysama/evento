# Evento - One Piece Event Card binder tracker

A small local app for tracking a Japanese-language collection of every One Piece Card Game **Event** card
(ST-01 through OP-17), laid out as a 4x4 binder. Mark each slot as a JP card you own, note EN/KR placeholders
you're holding until you get the JP copy, and tick foils separately.

## Run it

Needs Python 3 (standard library only).

    python server.py

Then open http://127.0.0.1:8765/ (on Windows you can double-click `Start Binder.bat`).
Your collection is saved to `collection.json` next to `server.py`.

## Card images

Card art is **not included** in this repository (it belongs to Bandai and is not ours to redistribute).
Without images the grid shows blank tiles, with the card name and code still shown. To see art locally,
put your own images in `card_images/<CODE>.jpg` (e.g. `card_images/OP01-026.jpg`).

## Files

- `server.py` - tiny local server: serves the page and saves your collection (127.0.0.1 only)
- `index.html` - the whole UI
- `cards.json` - the 410 Event cards in binder order (404 from the sets plus 6 promo-only Events, placed by release date)

## Hosting (AWS) and auto-deploy

The same app runs on AWS at https://evento.peonbox.xyz - one Lambda (`aws/lambda_function.py`) behind an
API Gateway HTTP API, with your collection and card art in a private S3 bucket, all behind a passcode.

**Every push to `main` deploys automatically** (`.github/workflows/deploy.yml`): it runs the tests in
`aws/test_handler.py`, packages `aws/lambda_function.py` + `index.html` + `cards.json`, updates the Lambda,
checks that the live code matches the build, and smoke-tests the site. AWS access uses short-lived OIDC
credentials for a role that only `main` of this repo can assume - no keys are stored in GitHub.

One-time setup scripts (run from a machine with AWS credentials): `aws/deploy.ps1` (bucket, role, function,
uploads local card art), `aws/setup-domain.ps1` (certificate, API, DNS), `aws/setup-ci.ps1` (GitHub deploy role).
Card art is uploaded only by `deploy.ps1` from your local `card_images/` and is never part of the repo or CI.

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
to Bandai's own card list remain). The list can also be copied as plain text.

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

## Foil and Manga are optional extras

Foil and Manga are ticked *after* a card is marked JP complete (their buttons stay greyed out until then) and never
affect completion. Any card can be marked foil; the built-in list of which cards have a foil is not treated as complete.
Un-ticking the JP card hides its foil / Manga marks but keeps them, so they come back if you tick it again.

## Promo-only Events

Six Events have no set of their own: P-002, P-024 and P-057 to P-060. They sit in the binder where they came out
(pseudo-sets `P-2207`, `P-2209`, `P-2311`). The four Uta cards also come, in parallel art, in the ST-16 deck.

## Completed

The **Completed** button is the opposite of What to buy: the sets where every Event card is in your binder, with card
lists and foil counts, plus other products that have nothing left to find.

## What to buy

The **What to buy** button shows how many cards you are missing per product, which products contain them (including
reprints in the PRB boosters and starter decks), and a shortest shopping list that covers everything you are missing
with as few products as possible. The Boosters / Starter decks / Promos filter limits both the list and the products shown (decks have fixed contents, so decks only is a safe way to buy). "14 only in OP-03" means 14 of your missing cards appear in no other product. Data: `products.json` (which products each card appears in, from Bandai's card list).

## Manga and alt-art

Cards with a Japanese alt-art printing get Standard / Alt art buttons in the card viewer; the four Manga Events
(OP09-020, -057, -078, -096) are marked and have an optional "Manga" owned flag that never affects completion.
