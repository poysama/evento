import unittest
import workflow as w


class WorkflowTests(unittest.TestCase):
    def test_base(self):
        c = w.normalize_catalog(
            {
                "cards": [
                    {"num": "OP01-001", "alt": ["p1"]},
                    {"num": "OP01-002", "status": "draft"},
                    {"num": "OP01-003", "status": "announced"},
                ]
            }
        )
        self.assertEqual(
            [x["id"] for x in w.wishlist(c, {"OP01-001": {"alt": {"p1": "have"}}})],
            ["OP01-001:std"],
        )
        self.assertEqual(len(w.wishlist(c, {}, include_upcoming=True)), 2)

    def test_notes(self):
        self.assertTrue(
            w.valid_collection(
                {
                    "OP01-001": {
                        "alt": {"v_custom": "ordered"},
                        "altNotes": {"v_custom": "private"},
                    }
                }
            )
        )
        self.assertFalse(w.valid_collection({"OP01-001": {"altNotes": {"bad": "x"}}}))

    def test_images(self):
        self.assertFalse(w.safe_image("https://bad.test/x.png"))
        self.assertTrue(w.safe_image("card_images_jp/OP01-001_v_custom.png?v=abc"))


class MalformedInputTests(unittest.TestCase):
    def test_bad_shapes_rejected(self):
        import copy

        c = {
            "schema": 2,
            "cards": [
                {
                    "num": "OP01-001",
                    "name": "X",
                    "rar": "C",
                    "set": "OP-01",
                    "col": "Red",
                    "foil": False,
                    "slot": 1,
                }
            ],
            "products": {"products": [], "sources": {}},
            "share": {},
            "binders": [],
            "drafts": [],
        }
        for field in ("col", "binder", "status"):
            bad = copy.deepcopy(c)
            bad["cards"][0][field] = []
            self.assertFalse(w.valid_catalog(bad))

    def test_false_images_rejected(self):
        import base64

        for raw, ext in [
            (b"\xff\xd8\xff" + b"x" * 20 + b"\xff\xd9", "jpg"),
            (
                base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                )
                + b"x",
                "png",
            ),
        ]:
            with self.assertRaises(ValueError):
                w.decode_image(
                    "card_images_jp/OP01-001." + ext, base64.b64encode(raw).decode()
                )


class CustomVersionTests(unittest.TestCase):
    def test_custom_rayleigh_is_untracked_until_explicitly_wanted(self):
        import json
        import subprocess
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        catalog = w.normalize_catalog(
            {
                "cards": json.loads((root / "cards.json").read_text()),
                "share": json.loads((root / "aws/share_data.json").read_text()),
            }
        )
        card = next(c for c in catalog["cards"] if c["num"] == "OP12-018")
        variant = next(v for v in card["variants"] if v["id"] == "v_rayleigh")
        self.assertEqual(variant["source"], "")
        self.assertNotIn("officialId", variant)
        self.assertNotIn("artType", variant)
        self.assertIn("v_rayleigh", catalog["share"]["OP12-018"]["alt"])
        owned = {"OP12-018": {"jp": True, "foil": True}}
        self.assertNotIn(
            "OP12-018:v_rayleigh", {x["id"] for x in w.wishlist(catalog, owned)}
        )
        owned["OP12-018"]["alt"] = {"v_rayleigh": "want"}
        self.assertIn(
            "OP12-018:v_rayleigh", {x["id"] for x in w.wishlist(catalog, owned)}
        )
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", variant["image"]],
            cwd=root,
            capture_output=True,
        )
        self.assertNotEqual(tracked.returncode, 0)


class SharedPolicyTests(unittest.TestCase):
    def test_shared_javascript_fixtures(self):
        import json
        from pathlib import Path

        cases = json.loads((Path(__file__).parent / "wishlist_cases.json").read_text())
        for case in cases:
            with self.subTest(case["name"]):
                actual = w.wishlist(
                    w.normalize_catalog(case["catalog"]),
                    case["collection"],
                    include_upcoming=case["upcoming"],
                )
                self.assertEqual(
                    sorted(x["id"] for x in actual), sorted(case["expected"])
                )

    def test_english_image_validated(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        catalog = w.normalize_catalog(
            {"cards": json.loads((root / "cards.json").read_text())}
        )
        catalog["cards"][0]["enImage"] = "card_images/ST01-014.jpg?v=abc"
        self.assertTrue(w.valid_catalog(catalog))
        catalog["cards"][0]["enImage"] = "https://external.test/private.jpg"
        self.assertFalse(w.valid_catalog(catalog))
