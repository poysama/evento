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
