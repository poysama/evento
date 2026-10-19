"""Canonical catalog, print identities and collection policy shared by both servers."""

import copy
from datetime import date
import hashlib
import json
import re
import base64
import struct
import zlib

VERSION = re.compile(r"(?:p\d{1,2}|v_[a-zA-Z0-9_-]+)\Z")
STATUSES = {"draft", "announced", "released"}
STATES = {"want", "ordered", "have"}


def revision(value):
    return (
        '"'
        + hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        + '"'
    )


def safe_image(value):
    return isinstance(value, str) and bool(
        re.fullmatch(
            r"(?:card_images|card_images_jp)/[A-Za-z0-9_-]+\.(?:png|jpg)(?:\?v=[a-zA-Z0-9_-]+)?",
            value,
        )
    )


def artwork_id(card, version):
    if version in (None, "std"):
        return "std"
    rows = {v["id"]: v for v in card.get("variants", [])}
    original, seen = version, set()
    while version in rows and version not in seen:
        seen.add(version)
        v = rows[version]
        if v.get("sameDrawing") or version in card.get("altsame", []):
            return "std"
        target = v.get("sameAs") or card.get("altdup", {}).get(version)
        if not target:
            if v.get("kind") == "reprint" and v.get("artworkId") == "std":
                return "std"
            return version
        if target == "std":
            return "std"
        version = target
    return original


def alternate_versions(card):
    return [v for v in card.get("variants", []) if artwork_id(card, v["id"]) == v["id"]]


def print_sources(card, artwork="std"):
    return [v for v in card.get("variants", []) if artwork_id(card, v["id"]) == artwork]


def effective_state(card, row, artwork="std"):
    states = [row.get("alt", {}).get(v["id"]) for v in print_sources(card, artwork)]
    if artwork == "std":
        states.append("have" if row.get("jp") else "ordered" if row.get("ordered") else "skip" if row.get("baseWish") == "skip" else "want")
    if row.get("manga") and artwork == card.get("manga"):
        states.append("have")
    return next((s for s in ("have", "ordered", "want", "skip") if s in states), "untracked")


def normalize_catalog(value):
    c = copy.deepcopy(value)
    c.setdefault("schema", 2)
    for key, default in [
        ("cards", []),
        ("products", {"products": [], "sources": {}}),
        ("share", {}),
        ("binders", []),
        ("drafts", []),
    ]:
        c.setdefault(key, default)
    for card in c["cards"]:
        card.setdefault("slot", None)
        info = c["share"].setdefault(card["num"], {"jp": "", "sid": 0, "alt": []})
        if "variants" not in card:
            ids = list(
                dict.fromkeys(
                    card.get("alt", [])
                    + info.get("alt", [])
                    + ([card["manga"]] if card.get("manga") else [])
                )
            )
            card["variants"] = [
                {
                    "id": a,
                    "kind": "manga" if a == card.get("manga") else "parallel",
                    "label": "Manga" if a == card.get("manga") else "Alt art",
                    "source": card.get("altsrc", {}).get(a, ""),
                    "artType": card.get("alttype", {}).get(a, ""),
                    "sameAs": card.get("altdup", {}).get(a, ""),
                    "specialFoil": a in card.get("altstar", []),
                    "sameDrawing": a in card.get("altsame", []),
                }
                for a in ids
            ]
        for v in card["variants"]:
            if v["id"] in card.get("altsame", []):
                v["sameDrawing"] = True
            if not v.get("sameAs") and card.get("altdup", {}).get(v["id"]):
                v["sameAs"] = card["altdup"][v["id"]]
        identities = {v["id"]: artwork_id(card, v["id"]) for v in card["variants"]}
        for v in card["variants"]:
            v["artworkId"] = identities[v["id"]]
            if v["artworkId"] != v["id"]:
                v["kind"] = "reprint"
        card["alt"] = [v["id"] for v in alternate_versions(card)]
        info["alt"] = card["alt"][:]
        for key, field in [
            ("altsrc", "source"),
            ("alttype", "artType"),
            ("altdup", "sameAs"),
        ]:
            card[key] = {v["id"]: v[field] for v in card["variants"] if v.get(field)}
        card["altstar"] = [v["id"] for v in card["variants"] if v.get("specialFoil")]
        manga = next((v["id"] for v in card["variants"] if v["kind"] == "manga"), None)
        if manga:
            card["manga"] = manga
        else:
            card.pop("manga", None)
    return c


def valid_catalog(c):
    try:
        return _valid_catalog(c)
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def valid_date(row):
    if "releaseDate" not in row or row["releaseDate"] == "":
        return True
    try:
        return (
            isinstance(row["releaseDate"], str)
            and date.fromisoformat(row["releaseDate"]).isoformat() == row["releaseDate"]
        )
    except ValueError:
        return False


def _valid_catalog(c):
    if (
        not isinstance(c, dict)
        or c.get("schema") != 2
        or not all(k in c for k in ("cards", "products", "share", "binders", "drafts"))
    ):
        return False
    if (
        not isinstance(c["cards"], list)
        or not isinstance(c["drafts"], list)
        or not isinstance(c["binders"], list)
        or not isinstance(c["share"], dict)
    ):
        return False
    p = c["products"]
    if (
        not isinstance(p, dict)
        or not isinstance(p.get("products"), list)
        or not isinstance(p.get("sources"), dict)
    ):
        return False
    if not all(
        isinstance(num, str)
        and isinstance(codes, list)
        and all(isinstance(code, str) for code in codes)
        for num, codes in p["sources"].items()
    ):
        return False
    if len(
        {
            p.get("code")
            for p in c["products"]["products"]
            if isinstance(p, dict) and isinstance(p.get("code"), str)
        }
    ) != len(c["products"]["products"]):
        return False
    for num, info in c["share"].items():
        if (
            not isinstance(num, str)
            or not isinstance(info, dict)
            or not isinstance(info.get("jp"), str)
            or type(info.get("sid")) != int
            or not isinstance(info.get("alt"), list)
            or not all(isinstance(v, str) and VERSION.fullmatch(v) for v in info["alt"])
        ):
            return False
    nums = set()
    slots = set()
    binders = set()
    for b in c["binders"]:
        if (
            not isinstance(b, dict)
            or not isinstance(b.get("id"), str)
            or not b["id"]
            or b["id"] in binders
            or not isinstance(b.get("name"), str)
            or b.get("status", "released") not in STATUSES
        ):
            return False
        binders.add(b["id"])
    for card in c["cards"]:
        if not valid_date(card):
            return False
        if (
            not isinstance(card, dict)
            or not isinstance(card.get("num"), str)
            or not re.fullmatch(r"(?:[A-Z]{2,3}\d{2}|P)-\d{3}", card["num"])
            or card["num"] in nums
        ):
            return False
        nums.add(card["num"])
        if (
            not isinstance(card.get("rar"), str)
            or not isinstance(card.get("set"), str)
            or not re.fullmatch(r"(?:[A-Z]{2,3}-\d{2}|P-\d{3,4})", card["set"])
            or type(card.get("foil")) != bool
            or card.get("col")
            not in {"Red", "Green", "Blue", "Purple", "Black", "Yellow"}
        ):
            return False
        if (
            not isinstance(card.get("name"), str)
            or not card["name"].strip()
            or card.get("status", "released") not in STATUSES
        ):
            return False
        slot = card.get("slot")
        if slot is not None:
            if (
                type(slot) != int
                or slot < 1
                or (card.get("binder", "main"), slot) in slots
            ):
                return False
            slots.add((card.get("binder", "main"), slot))
        for field in ("releaseDate", "sourceUrl"):
            if field in card and (
                not isinstance(card[field], str) or len(card[field]) > 2000
            ):
                return False
        if (
            "sourceUrl" in card
            and card["sourceUrl"]
            and not re.match(r"^https?://", card["sourceUrl"])
        ):
            return False
        ids = set()
        if not isinstance(card.get("variants", []), list):
            return False
        for v in card.get("variants", []):
            if not isinstance(v, dict) or not valid_date(v):
                return False
            if (
                not isinstance(v, dict)
                or not isinstance(v.get("id"), str)
                or not VERSION.fullmatch(v["id"])
                or v["id"] in ids
                or v.get("kind") not in {"parallel", "manga", "reprint"}
            ):
                return False
            ids.add(v["id"])
            if (
                not all(isinstance(v.get(k), str) for k in ("label", "source"))
                or v.get("status", "released") not in STATUSES
            ):
                return False
            if "image" in v and not safe_image(v["image"]):
                return False
            for field in ("officialId", "releaseDate", "artType", "sameAs"):
                if field in v and not isinstance(v[field], str):
                    return False
            if v.get("sameAs") and v["sameAs"] != "std" and not VERSION.fullmatch(v["sameAs"]):
                return False
            if "sameDrawing" in v and type(v["sameDrawing"]) != bool:
                return False
            if "artworkId" in v and (not isinstance(v["artworkId"], str) or (v["artworkId"] != "std" and not VERSION.fullmatch(v["artworkId"]))):
                return False
            if "specialFoil" in v and type(v["specialFoil"]) != bool:
                return False
        variants = {v["id"]: v for v in card.get("variants", [])}
        for v in variants.values():
            current, visited = v["id"], set()
            while current in variants:
                if current in visited:
                    return False
                visited.add(current)
                target = variants[current].get("sameAs")
                if not target or target == "std":
                    break
                if target not in variants:
                    return False
                current = target
            if v.get("kind") == "reprint" and artwork_id(card, v["id"]) == v["id"]:
                return False
        if any(
            field in card and not safe_image(card[field])
            for field in ("image", "enImage")
        ):
            return False
    draft_ids = set()
    for d in c["drafts"]:
        if (
            not isinstance(d, dict)
            or not isinstance(d.get("id"), str)
            or not d["id"]
            or d.get("status", "draft") != "draft"
            or d["id"] in draft_ids
            or any(field in d and not safe_image(d[field]) for field in ("image", "enImage"))
        ):
            return False
        draft_ids.add(d["id"])
    return all(
        isinstance(p, dict)
        and isinstance(p.get("code"), str)
        and p.get("status", "released") in STATUSES
        for p in c["products"]["products"]
    )


def valid_collection(data):
    if not isinstance(data, dict):
        return False

    def text(x):
        return isinstance(x, str) and len(x) <= 1000

    def history(x):
        return (
            isinstance(x, list)
            and len(x) <= 100
            and all(
                isinstance(h, dict)
                and set(h) <= {"version", "status", "note", "group", "at"}
                and isinstance(h.get("version"), str)
                and (h["version"] == "std" or VERSION.fullmatch(h["version"]))
                and h.get("status") in STATES | {"untracked", "skip"}
                and text(h.get("at"))
                and all(text(h[k]) for k in ("note", "group") if k in h)
                for h in x
            )
        )

    for num, row in data.items():
        if not isinstance(num, str) or not isinstance(row, dict) or not row:
            return False
        for k, v in row.items():
            if k in {"jp", "ordered", "foil", "en", "kr", "manga"}:
                if v is not True:
                    return False
            elif k in {"note", "orderGroup"}:
                if not text(v) or not v or len(v) > 200:
                    return False
            elif k == "show":
                if not isinstance(v, str) or not (v == "std" or VERSION.fullmatch(v)):
                    return False
            elif k == "baseWish":
                if v != "skip":
                    return False
            elif k in {"alt", "altNotes", "altOrders"}:
                if (
                    not isinstance(v, dict)
                    or (k == "alt" and not v)
                    or len(v) > 200
                    or not all(
                        isinstance(a, str)
                        and VERSION.fullmatch(a)
                        and (
                            x in STATES
                            if k == "alt" and isinstance(x, str)
                            else text(x) if k != "alt" else False
                        )
                        for a, x in v.items()
                    )
                ):
                    return False
            elif k in {"history", "baseHistory"}:
                if not history(v):
                    return False
            else:
                return False
    return True


def normalize_collection(data, catalog):
    result = copy.deepcopy(data)
    for c in catalog["cards"]:
        row = result.get(c["num"], {})
        if row.get("manga") and c.get("manga"):
            row.setdefault("alt", {}).setdefault(c["manga"], "have")
    return result


def release_status(card, variant=None, catalog=None):
    if card.get("status") == "draft":
        return "draft"
    if variant and variant.get("status"):
        return variant["status"]
    if card.get("status"):
        return card["status"]
    products = (catalog or {}).get("products", {}).get("products", [])
    source = (variant or {}).get("source") or card.get("set")
    return next(
        (p.get("status", "released") for p in products if p.get("code") == source),
        "released",
    )


def wishlist(catalog, owned, include_upcoming=False, include_parallels=True):
    catalog = normalize_catalog(catalog)
    owned = normalize_collection(owned, catalog)
    out = []
    for c in catalog["cards"]:
        row = owned.get(c["num"], {})
        for v in [None] + (alternate_versions(c) if include_parallels else []):
            status = release_status(c, v, catalog)
            if status == "draft" or status == "announced" and not include_upcoming:
                continue
            version = v["id"] if v else "std"
            wanted = effective_state(c, row, version) == "want"
            if wanted:
                out.append(
                    {
                        "id": c["num"] + ":" + version,
                        "num": c["num"],
                        "version": version,
                        "status": status,
                        "card": c,
                        "variant": v,
                    }
                )
    return out


def decode_image(rel, data):
    if not safe_image(rel) or "?" in rel or not isinstance(data, str):
        raise ValueError("bad image path")
    raw = base64.b64decode(data, validate=True)
    if len(raw) > 7_000_000:
        raise ValueError("image too large")
    try:
        if rel.endswith(".png"):
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("bad PNG")
            pos = 8
            chunks = []
            compressed = b""
            while pos < len(raw):
                if len(raw) - pos < 12:
                    raise ValueError("truncated PNG")
                n = struct.unpack(">I", raw[pos : pos + 4])[0]
                kind = raw[pos + 4 : pos + 8]
                chunk = raw[pos + 8 : pos + 8 + n]
                if (
                    len(chunk) != n
                    or pos + 12 + n > len(raw)
                    or zlib.crc32(kind + chunk) & 0xFFFFFFFF
                    != struct.unpack(">I", raw[pos + 8 + n : pos + 12 + n])[0]
                ):
                    raise ValueError("bad PNG checksum")
                chunks.append(kind)
                if kind == b"IHDR":
                    if len(chunk) != 13:
                        raise ValueError("bad PNG header")
                    width, height = struct.unpack(">II", chunk[:8])
                    if (
                        not 0 < width <= 10000
                        or not 0 < height <= 10000
                        or width * height > 25_000_000
                    ):
                        raise ValueError("bad dimensions")
                if kind == b"IDAT":
                    compressed += chunk
                pos += 12 + n
            if (
                not chunks
                or chunks[0] != b"IHDR"
                or chunks[-1] != b"IEND"
                or b"IDAT" not in chunks
                or chunks.count(b"IHDR") != 1
                or chunks.count(b"IEND") != 1
            ):
                raise ValueError("bad PNG structure")
            inflater = zlib.decompressobj()
            pixels = inflater.decompress(compressed, 100_000_001)
            if len(pixels) > 100_000_000 or not inflater.eof or inflater.unused_data:
                raise ValueError("bad PNG pixels")
        else:
            if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
                raise ValueError("bad JPEG")
            pos = 2
            frame = False
            scan = False
            while pos < len(raw) - 2:
                if raw[pos] != 255:
                    raise ValueError("bad JPEG marker")
                while raw[pos] == 255:
                    pos += 1
                marker = raw[pos]
                pos += 1
                length = struct.unpack(">H", raw[pos : pos + 2])[0]
                if length < 2 or pos + length > len(raw):
                    raise ValueError("bad JPEG segment")
                if marker in (0xC0, 0xC1, 0xC2):
                    if length < 8:
                        raise ValueError("bad JPEG frame")
                    height, width = struct.unpack(">HH", raw[pos + 3 : pos + 7])
                    if (
                        not 0 < width <= 10000
                        or not 0 < height <= 10000
                        or width * height > 25_000_000
                    ):
                        raise ValueError("bad dimensions")
                    frame = True
                pos += length
                if marker == 0xDA:
                    if not frame or pos >= len(raw) - 2:
                        raise ValueError("bad JPEG scan")
                    scan = True
                    break
            if not frame or not scan:
                raise ValueError("bad JPEG structure")
    except (struct.error, IndexError, zlib.error) as exc:
        raise ValueError("bad image") from exc
    return raw
