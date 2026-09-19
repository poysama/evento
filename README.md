# Evento - One Piece Event Card binder tracker

A small local app for tracking a Japanese-language collection of every One Piece Card Game **Event** card
(ST-01 through OP-17), laid out as a 4x4 binder. Mark each slot as a JP card you own, note EN/KR placeholders
you're holding until you get the JP copy, and track JP foils separately.

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
- `cards.json` - the 404 Event cards in binder order

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
