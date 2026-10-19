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

    def test_private_binder_cards_are_hidden(self):
        source = self.fixture()
        source["binders"][0]["status"] = "draft"
        self.assertEqual(public.catalog(source)["cards"], [])
