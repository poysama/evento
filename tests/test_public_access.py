import copy
import unittest

import public_access as public
import workflow as wf


class PublicProjectionTests(unittest.TestCase):
    def fixture(self):
        card = {
            "num": "OP01-001",
            "name": "Public",
            "rar": "C",
            "set": "OP-01",
            "col": "Red",
            "foil": False,
            "sourceUrl": "https://private.invalid",
            "private": "hidden",
            "variants": [
                {
                    "id": "p1",
                    "kind": "parallel",
                    "label": "AA",
                    "source": "OP-01",
                    "image": "card_images_jp/public.png",
                },
                {
                    "id": "v_draft",
                    "kind": "parallel",
                    "label": "Secret artwork",
                    "source": "OP-01",
                    "status": "draft",
                    "image": "card_images_jp/secret.png",
                },
            ],
        }
        draft = copy.deepcopy(card)
        draft.update(num="OP01-002", status="draft", name="Secret card")
        return wf.normalize_catalog(
            {
                "cards": [card, draft],
                "share": {
                    "OP01-001": {
                        "jp": "Public JP",
                        "sid": 1,
                        "alt": [],
                        "token": "private-token",
                    }
                },
                "products": {
                    "products": [
                        {"code": "OP-01", "name": "Public set", "sourceUrl": "private"},
                        {"code": "OP-02", "name": "Secret set", "status": "draft"},
                    ],
                    "sources": {"OP01-001": ["OP-01", "OP-02"]},
                },
                "binders": [{"id": "main", "name": "Main", "private": "hidden"}],
                "drafts": [{"name": "Secret draft"}],
            }
        )

    def test_allowlist_and_draft_filtering(self):
        source = self.fixture()
        before = copy.deepcopy(source)
        visible = public.catalog(source)
        self.assertEqual(source, before)
        self.assertEqual(len(visible["cards"]), 1)
        self.assertEqual([v["id"] for v in visible["cards"][0]["variants"]], ["p1"])
        self.assertEqual(visible["drafts"], [])
        self.assertEqual(visible["products"]["sources"], {"OP01-001": ["OP-01"]})
        self.assertNotIn("sourceUrl", visible["cards"][0])
        self.assertNotIn("token", visible["share"]["OP01-001"])
        self.assertNotIn("private", visible["binders"][0])
        self.assertNotIn("sourceUrl", visible["products"]["products"][0])

    def test_collection_public_status_only(self):
        catalog = public.catalog(self.fixture())
        source = {
            "OP01-001": {
                "jp": True,
                "ordered": True,
                "foil": True,
                "en": True,
                "kr": True,
                "manga": True,
                "show": "v_draft",
                "baseWish": "skip",
                "alt": {"p1": "ordered", "v_draft": "have"},
                "note": "private",
                "orderGroup": "private",
                "altNotes": {"p1": "private"},
                "altOrders": {"p1": "private"},
                "history": [{"at": "private"}],
                "baseHistory": [],
            },
            "OP01-002": {"jp": True},
        }
        projected = public.collection(source, catalog)
        self.assertEqual(
            projected,
            {
                "OP01-001": {
                    "jp": True,
                    "ordered": True,
                    "foil": True,
                    "en": True,
                    "kr": True,
                    "baseWish": "skip",
                    "alt": {"p1": "ordered"},
                }
            },
        )
        self.assertNotEqual(public.revision(projected), wf.revision(source))

    def test_only_public_image_references(self):
        paths = public.image_paths(public.catalog(self.fixture()))
        self.assertIn("card_images_jp/public.png", paths)
        self.assertIn("card_images_jp/OP01-001_p1.png", paths)
        self.assertNotIn("card_images_jp/secret.png", paths)
        self.assertNotIn("card_images_jp/OP01-001_v_draft.png", paths)
        self.assertNotIn("card_images_jp/OP01-002.png", paths)

    def test_custom_language_images_roundtrip_and_replacement(self):
        import json
        source = self.fixture()
        source["cards"][0].update(image="card_images_jp/v_custom_jp.png?v=abc123", enImage="card_images/v_custom_en.jpg?v=def456")
        visible = public.catalog(json.loads(json.dumps(source)))
        self.assertEqual(visible["cards"][0]["enImage"], source["cards"][0]["enImage"])
        self.assertEqual(visible["cards"][0]["image"], source["cards"][0]["image"])
        paths = public.image_paths(visible)
        self.assertIn("card_images/v_custom_en.jpg", paths)
        self.assertIn("card_images_jp/v_custom_jp.png", paths)
        source["cards"][0]["enImage"] = "card_images/v_replacement.png?v=789"
        paths = public.image_paths(public.catalog(source))
        self.assertIn("card_images/v_replacement.png", paths)
        self.assertNotIn("card_images/v_custom_en.jpg", paths)

    def test_private_draft_images_stay_private(self):
        source = self.fixture()
        source["cards"][1].update(image="card_images_jp/v_private.png", enImage="card_images/v_private.jpg")
        source["drafts"] = [{"id": "v_unreleased", "status": "draft", "image": "card_images_jp/v_unreleased.png", "enImage": "card_images/v_unreleased.jpg"}]
        visible = public.catalog(source)
        paths = public.image_paths(visible)
        self.assertFalse(any("v_private" in p or "v_unreleased" in p for p in paths))
        self.assertEqual(visible["drafts"], [])
        source["drafts"][0]["enImage"] = "card_images/OP01-001.jpg"
        self.assertIn("card_images/OP01-001.jpg", public.image_paths(public.catalog(source)))
        self.assertEqual(public.image_paths(public.catalog({})), set())

    def test_private_binder_cards_are_hidden(self):
        source = self.fixture()
        source["binders"][0]["status"] = "draft"
        self.assertEqual(public.catalog(source)["cards"], [])

    def test_variant_language_images_public_replacement_and_draft_denial(self):
        import json
        source = self.fixture()
        source["cards"][0]["variants"][0]["enImage"] = "card_images/v_public_en.jpg?v=first"
        source["cards"][0]["variants"][1]["enImage"] = "card_images/v_secret_en.png"
        visible = public.catalog(json.loads(json.dumps(source)))
        variant = visible["cards"][0]["variants"][0]
        self.assertEqual(variant["enImage"], "card_images/v_public_en.jpg?v=first")
        self.assertEqual(variant["image"], "card_images_jp/public.png")
        paths = public.image_paths(visible)
        self.assertIn("card_images/v_public_en.jpg", paths)
        self.assertNotIn("card_images/v_secret_en.png", paths)
        source["cards"][0]["variants"][0]["enImage"] = "card_images/v_replacement_en.png?v=second"
        paths = public.image_paths(public.catalog(source))
        self.assertIn("card_images/v_replacement_en.png", paths)
        self.assertNotIn("card_images/v_public_en.jpg", paths)
        source["cards"][0]["variants"] = []
        self.assertNotIn("card_images/v_replacement_en.png", public.image_paths(public.catalog(source)))
        source["cards"][0]["variants"] = [{"id": "v_hidden", "kind": "parallel", "label": "Hidden", "source": "", "enImage": "card_images/v_hidden.png"}]
        source["cards"][0]["status"] = "draft"
        self.assertNotIn("card_images/v_hidden.png", public.image_paths(public.catalog(source)))

    def test_announced_card_with_unknown_release_is_public(self):
        source = self.fixture()
        card = source["cards"][0]
        card.update(status="announced", set="", image="card_images_jp/v_spoiler.png", enImage="card_images/v_spoiler.png")
        visible = public.catalog(source)
        self.assertEqual(visible["cards"][0]["status"], "announced")
        self.assertEqual(visible["cards"][0]["set"], "")
        self.assertIn("card_images/v_spoiler.png", public.image_paths(visible))
        self.assertIn("card_images_jp/v_spoiler.png", public.image_paths(visible))
