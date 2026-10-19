"""Public projections of the private binder documents."""

import copy
import re

import workflow as wf

CARD_FIELDS = {
    "num",
    "name",
    "set",
    "rar",
    "col",
    "foil",
    "slot",
    "binder",
    "status",
    "releaseDate",
    "image",
    "noen",
}
VERSION_FIELDS = {
    "id",
    "kind",
    "label",
    "source",
    "status",
    "releaseDate",
    "image",
    "artType",
    "sameAs",
    "sameDrawing",
    "specialFoil",
    "artworkId",
    "finish",
    "officialId",
}
PRODUCT_FIELDS = {"code", "name", "family", "kind", "date", "status", "releaseDate"}


def pick(row, fields):
    return {
        k: copy.deepcopy(row[k])
        for k in fields
        if k in row and isinstance(row[k], (str, int, float, bool, type(None)))
    }


def catalog(document):
    value = wf.normalize_catalog(document)
    cards, share = [], {}
    private_binders = {
        b["id"] for b in value.get("binders", []) if b.get("status") == "draft"
    }
    for source in value["cards"]:
        if (
            wf.release_status(source, catalog=value) == "draft"
            or source.get("binder", "main") in private_binders
        ):
            continue
        card = pick(source, CARD_FIELDS)
        card["variants"] = [
            pick(v, VERSION_FIELDS)
            for v in source.get("variants", [])
            if wf.release_status(source, v, value) != "draft"
        ]
        ids = {v["id"] for v in card["variants"]}
        # References to hidden drafts must not disclose their identifiers.
        for variant in card["variants"]:
            for field in ("sameAs", "artworkId"):
                if (
                    variant.get(field)
                    and variant[field] != "std"
                    and variant[field] not in ids
                ):
                    variant.pop(field, None)
        card["altsame"] = [v["id"] for v in card["variants"] if v.get("sameDrawing")]
        cards.append(card)
        info = value.get("share", {}).get(source["num"], {})
        share[source["num"]] = pick(info, {"jp", "sid"})
    products = value.get("products", {})
    rows = [
        pick(p, PRODUCT_FIELDS)
        for p in products.get("products", [])
        if p.get("status") != "draft"
    ]
    codes = {p.get("code") for p in rows}
    nums = {c["num"] for c in cards}
    sources = {
        num: [code for code in codes_ if code in codes]
        for num, codes_ in products.get("sources", {}).items()
        if num in nums and isinstance(codes_, list)
    }
    return wf.normalize_catalog(
        {
            "schema": 2,
            "cards": cards,
            "share": share,
            "products": {"products": rows, "sources": sources},
            "binders": [
                pick(b, {"id", "name", "status"})
                for b in value.get("binders", [])
                if b.get("status") != "draft"
            ],
            "drafts": [],
        }
    )


def collection(document, public_catalog):
    result = {}
    for card in public_catalog["cards"]:
        num = card["num"]
        source = document.get(num, {})
        row = {
            k: True
            for k in ("jp", "ordered", "foil", "en", "kr", "manga")
            if source.get(k) is True
        }
        ids = {v["id"] for v in card.get("variants", [])}
        alts = {
            k: v
            for k, v in source.get("alt", {}).items()
            if k in ids and v in {"want", "ordered", "have"}
        }
        if not card.get("manga"):
            row.pop("manga", None)
        if alts:
            row["alt"] = alts
        if source.get("show") in ids | {"std"}:
            row["show"] = source["show"]
        if source.get("baseWish") == "skip":
            row["baseWish"] = "skip"
        if row:
            result[num] = row
    return result


def image_paths(public_catalog):
    paths = set()
    for card in public_catalog["cards"]:
        num = card["num"]
        paths.update(
            {
                "card_images/" + num + ".jpg",
                "card_images/" + num + ".png",
                "card_images_jp/" + num + ".png",
            }
        )
        if card.get("image"):
            paths.add(card["image"].split("?", 1)[0])
        for version in card.get("variants", []):
            paths.add("card_images_jp/" + num + "_" + version["id"] + ".png")
            if version.get("image"):
                paths.add(version["image"].split("?", 1)[0])
    return {
        p
        for p in paths
        if re.fullmatch(r"card_images(?:_jp)?/[A-Za-z0-9_-]+\.(?:jpg|png)", p)
    }


def revision(document):
    return '"public-' + wf.revision(document).strip('"') + '"'
