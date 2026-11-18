import base64
import json
from pathlib import Path
import struct
import unittest

import workflow as wf
import recognition
import public_access as public

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "webp.json").read_text())


class WebPTests(unittest.TestCase):
    def test_preserves_supported_still_formats(self):
        for name, data in FIXTURES.items():
            with self.subTest(name=name):
                raw = base64.b64decode(data)
                self.assertEqual(
                    wf.decode_image("card_images_jp/v_custom.webp", data), raw
                )
                self.assertEqual(wf.webp_dimensions(raw), (2, 3))
                self.assertTrue(wf.safe_image("card_images/v_custom.webp?v=abc"))

    def test_rejects_malformed_truncated_spoofed_or_oversized(self):
        raw = base64.b64decode(FIXTURES["lossy"])
        invalid = [
            b"RIFF" + b"\0" * 4 + b"WEBP",
            raw[:-1],
            raw + b"x",
            b"RIFF" + raw[4:8] + b"WAVE" + raw[12:],
            raw[:16] + struct.pack("<I", 99999) + raw[20:],
            b"<svg>not a webp</svg>",
        ]
        oversized = bytearray(raw)
        oversized[26:30] = struct.pack("<HH", 10001, 3)
        invalid.append(bytes(oversized))
        for value in invalid:
            with self.subTest(value=value[:20]):
                with self.assertRaises(ValueError):
                    wf.decode_image(
                        "card_images/v_bad.webp", base64.b64encode(value).decode()
                    )
        with self.assertRaises(ValueError):
            wf.decode_image("card_images/v_bad.png", FIXTURES["lossy"])

    def test_recognition_preserves_webp_and_limit(self):
        body = {"mime": "image/webp", "data": FIXTURES["lossy"], "languageHint": "en"}
        self.assertEqual(
            recognition.validate_image(body),
            (base64.b64decode(body["data"]), "webp", "en"),
        )
        oversized = bytearray(base64.b64decode(body["data"]))
        oversized[26:30] = struct.pack("<HH", 8001, 3)
        with self.assertRaises(recognition.RecognitionError) as error:
            recognition.validate_image(
                {**body, "data": base64.b64encode(oversized).decode()}
            )
        self.assertEqual(error.exception.status, 400)

    def test_public_projection_allows_only_referenced_non_draft_webp(self):
        catalog = wf.normalize_catalog(
            {
                "cards": [
                    {
                        "num": "OP01-001",
                        "status": "released",
                        "image": "card_images_jp/v_base.webp",
                        "enImage": "card_images/v_base.webp",
                        "variants": [
                            {
                                "id": "v_aa",
                                "kind": "parallel",
                                "label": "AA",
                                "source": "",
                                "image": "card_images_jp/v_aa.webp",
                                "enImage": "card_images/v_aa.webp",
                            },
                            {
                                "id": "v_draft",
                                "kind": "parallel",
                                "label": "Hidden",
                                "source": "",
                                "status": "draft",
                                "image": "card_images_jp/v_hidden.webp",
                            },
                        ],
                    }
                ]
            }
        )
        paths = public.image_paths(public.catalog(catalog))
        for path in (
            "card_images_jp/v_base.webp",
            "card_images/v_base.webp",
            "card_images_jp/v_aa.webp",
            "card_images/v_aa.webp",
        ):
            self.assertIn(path, paths)
        self.assertNotIn("card_images_jp/v_hidden.webp", paths)
        self.assertNotIn("card_images/v_unreferenced.webp", paths)

    def test_extended_headers_padding_and_animation_rejected(self):
        raw = base64.b64decode(FIXTURES["extended"])
        cases = []
        bad = bytearray(raw)
        bad[20] |= 2
        cases.append(bytes(bad))
        bad = bytearray(raw)
        bad[21] = 1
        cases.append(bytes(bad))
        bad = bytearray(raw)
        bad[20] |= 16
        cases.append(bytes(bad))
        bad = bytearray(raw)
        bad[24] = 2
        cases.append(bytes(bad))
        bad = bytearray(raw)
        bad[-1] = 1
        cases.append(bytes(bad))
        bad = bytearray(raw)
        bad[4:8] = (len(raw) - 10).to_bytes(4, "little")
        cases.append(bytes(bad))
        cases.append(b"RIFF" + (4).to_bytes(4, "little") + b"WEBP")
        for image in cases:
            with self.assertRaises(ValueError):
                wf.decode_image(
                    "card_images_jp/v_bad.webp", base64.b64encode(image).decode()
                )
        animation = b"ANIM" + (6).to_bytes(4, "little") + b"\0" * 6
        image = raw[:12] + animation + raw[12:]
        image = image[:4] + (len(image) - 8).to_bytes(4, "little") + image[8:]
        with self.assertRaisesRegex(ValueError, "Animated WebP"):
            wf.decode_image(
                "card_images/v_animated.webp", base64.b64encode(image).decode()
            )
        with self.assertRaisesRegex(recognition.RecognitionError, "Animated WebP"):
            recognition.validate_image(
                {"mime": "image/webp", "data": base64.b64encode(image).decode()}
            )

    def test_provider_receives_exact_webp_data_url_and_unchanged_budget(self):
        from unittest.mock import Mock, patch

        provider = Mock(
            return_value={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"num":"P-134","language":"en"}'},
                    }
                ]
            }
        )
        reserve = Mock()
        body = {"mime": "image/webp", "data": FIXTURES["lossless"]}
        with patch.object(recognition, "_complete", provider):
            recognition.recognize(body, {}, api_key="unit-test-token", reserve=reserve)
        reserve.assert_called_once_with()
        payload = provider.call_args.args[0]
        self.assertEqual(
            payload["messages"][1]["content"][1]["image_url"]["url"],
            "data:image/webp;base64," + FIXTURES["lossless"],
        )
        self.assertEqual(payload["max_tokens"], 700)
        oversized = {**body, "data": "A" * 4_700_000}
        with self.assertRaises(recognition.RecognitionError) as error:
            recognition.validate_image(oversized)
        self.assertEqual(error.exception.status, 413)

    def test_color_profile_precedes_alpha_and_image(self):
        def chunk(name, data):
            return (
                name
                + len(data).to_bytes(4, "little")
                + data
                + (b"\0" if len(data) & 1 else b"")
            )

        def container(chunks):
            body = b"WEBP" + b"".join(chunks)
            return b"RIFF" + len(body).to_bytes(4, "little") + body

        image = base64.b64decode(FIXTURES["lossy"])[12:]
        header = chunk(b"VP8X", b"\x20\0\0\0\x01\0\0\x02\0\0")
        profile = chunk(b"ICCP", b"profile")
        self.assertEqual(
            wf.webp_dimensions(container([header, profile, image])), (2, 3)
        )
        with self.assertRaises(ValueError):
            wf.webp_dimensions(container([header, image, profile]))
        alpha_header = chunk(b"VP8X", b"\x30\0\0\0\x01\0\0\x02\0\0")
        alpha = chunk(b"ALPH", b"\0" + b"\x80" * 6)
        self.assertEqual(
            wf.webp_dimensions(container([alpha_header, profile, alpha, image])), (2, 3)
        )
        with self.assertRaises(ValueError):
            wf.webp_dimensions(container([alpha_header, alpha, profile, image]))
        # EXIF and XMP are metadata, so their position does not affect decoding.
        metadata_header = chunk(b"VP8X", b"\x0c\0\0\0\x01\0\0\x02\0\0")
        exif, xmp = chunk(b"EXIF", b"exif"), chunk(b"XMP ", b"xmp")
        for parts in (
            [metadata_header, exif, xmp, image],
            [metadata_header, image, xmp, exif],
        ):
            self.assertEqual(wf.webp_dimensions(container(parts)), (2, 3))

    def test_compressed_alpha_requires_bitstream(self):
        raw = base64.b64decode(FIXTURES["alpha"])
        image = (
            raw[:30]
            + b"ALPH\x01\0\0\0\x01\0"
            + base64.b64decode(FIXTURES["lossy"])[12:]
        )
        image = image[:4] + (len(image) - 8).to_bytes(4, "little") + image[8:]
        with self.assertRaises(ValueError):
            wf.decode_image("card_images/v_bad.webp", base64.b64encode(image).decode())
