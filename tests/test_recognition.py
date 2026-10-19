import base64
import json
import unittest
from unittest.mock import Mock, patch

import recognition as r

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(
            "os.environ",
            {
                "RECOGNITION_MODEL_ID": "apac.amazon.nova-lite-v1:0",
                "RECOGNITION_REGION": "ap-southeast-1",
            },
        )
        self.config.start()
        self.addCleanup(self.config.stop)

    def body(self):
        return {"mime": "image/png", "data": PNG, "languageHint": "jp"}

    def test_japanese_match_and_no_translation(self):
        result = r.normalize_result(
            {
                "num": "op04-016",
                "language": "jp",
                "name": "Invented translation",
                "jpName": "反行儀",
                "col": "Red",
                "rar": "R",
                "cardType": "Event",
            },
            {"cards": [{"num": "OP04-016"}]},
        )
        self.assertEqual(result["match"], {"num": "OP04-016"})
        self.assertIsNone(result["fields"]["name"])
        self.assertEqual(result["fields"]["jpName"], "反行儀")
        self.assertTrue(result["reviewRequired"])

    def test_unknowns_and_draft_match(self):
        result = r.normalize_result(
            {
                "num": "P-134",
                "language": "en",
                "name": "Card",
                "jpName": "ignore",
                "col": "Rainbow",
                "rar": "*",
                "cardType": "Leader",
            },
            {"drafts": [{"id": "draft-a", "num": "P-134"}]},
        )
        self.assertEqual(result["match"]["draftId"], "draft-a")
        self.assertIsNone(result["fields"]["jpName"])
        self.assertIsNone(result["fields"]["col"])
        self.assertIsNone(result["fields"]["rar"])
        self.assertEqual(len(result["warnings"]), 2)

    def test_invalid_input_before_provider(self):
        for body, status in [
            (None, 400),
            ({"mime": "text/plain"}, 400),
            ({"mime": "image/png", "data": "junk"}, 400),
            ({"mime": "image/png", "data": "A" * 4_700_000}, 413),
        ]:
            with self.subTest(status=status), patch.object(r, "_client") as client:
                with self.assertRaises(r.RecognitionError) as ctx:
                    r.recognize(body, {})
                self.assertEqual(ctx.exception.status, status)
                client.assert_not_called()

    def test_provider_request_and_fenced_output(self):
        client = Mock()
        client.converse.return_value = {
            "stopReason": "end_turn",
            "output": {
                "message": {
                    "content": [
                        {
                            "text": '```json\n{"num":"P-134","language":"en","name":"Hello","cardType":"Event"}\n```'
                        }
                    ]
                }
            },
        }
        with patch.object(r, "_client", return_value=client):
            result = r.recognize(self.body(), {}, reserve=lambda: None)
        self.assertEqual(result["fields"]["num"], "P-134")
        sent = client.converse.call_args.kwargs
        self.assertIsInstance(
            sent["messages"][0]["content"][0]["image"]["source"]["bytes"], bytes
        )
        self.assertEqual(sent["inferenceConfig"]["temperature"], 0)

    def test_timeout_and_bad_output(self):
        for response in [
            TimeoutError(),
            {"stopReason": "max_tokens"},
            {
                "stopReason": "end_turn",
                "output": {"message": {"content": [{"text": "bad json"}]}},
            },
        ]:
            client = Mock()
            if isinstance(response, Exception):
                client.converse.side_effect = response
            else:
                client.converse.return_value = response
            with patch.object(r, "_client", return_value=client), self.assertRaises(
                r.RecognitionError
            ) as ctx:
                r.recognize(self.body(), {}, reserve=lambda: None)
            self.assertEqual(ctx.exception.status, 502)

    def test_disabled(self):
        with patch.dict(
            "os.environ", {"RECOGNITION_MODEL_ID": "disabled"}
        ), self.assertRaises(r.RecognitionError) as ctx:
            r.recognize(self.body(), {}, reserve=lambda: None)
        self.assertEqual(ctx.exception.status, 503)

    def test_invalid_enum_shapes(self):
        result = r.normalize_result(
            {"language": [], "col": {}, "rar": [], "cardType": {}}, {}
        )
        self.assertTrue(all(value is None for value in result["fields"].values()))

    def test_quota_and_configuration_errors(self):
        for code in [
            "ThrottlingException",
            "ServiceQuotaExceededException",
            "AccessDeniedException",
        ]:
            error = RuntimeError("private provider error")
            error.response = {"Error": {"Code": code}}
            client = Mock()
            client.converse.side_effect = error
            with patch.object(r, "_client", return_value=client), self.assertRaises(
                r.RecognitionError
            ) as ctx:
                r.recognize(self.body(), {}, reserve=lambda: None)
            self.assertEqual(ctx.exception.status, 503)
            self.assertNotIn("private provider error", str(ctx.exception))

    def test_duplicate_drafts_are_ambiguous(self):
        catalog = {"drafts": [{"id": "a", "num": "P-134"}, {"id": "b", "num": "P-134"}]}
        result = r.normalize_result(
            {"num": "P-134", "language": "en", "cardType": "Event"}, catalog
        )
        self.assertIsNone(result["match"])
        self.assertTrue(
            any("Multiple drafts" in warning for warning in result["warnings"])
        )
        catalog["cards"] = [{"num": "P-134"}]
        result = r.normalize_result(
            {"num": "P-134", "language": "en", "cardType": "Event"}, catalog
        )
        self.assertEqual(result["match"], {"num": "P-134"})
        self.assertFalse(
            any("Multiple drafts" in warning for warning in result["warnings"])
        )

    def test_budget_reserved_before_provider_and_not_refunded(self):
        events = []
        client = Mock()

        def fail(**kwargs):
            events.append("provider")
            raise TimeoutError()

        client.converse.side_effect = fail
        with patch.object(r, "_client", return_value=client), self.assertRaises(
            r.RecognitionError
        ):
            r.recognize(self.body(), {}, reserve=lambda: events.append("reserve"))
        self.assertEqual(events, ["reserve", "provider"])

    def test_missing_exhausted_and_broken_budget_block_provider(self):
        for reserve, status in [
            (None, 503),
            (Mock(side_effect=r.budget.BudgetError(429, "limit")), 429),
            (Mock(side_effect=OSError()), 503),
        ]:
            client = Mock()
            with patch.object(r, "_client", return_value=client), self.assertRaises(
                r.RecognitionError
            ) as ctx:
                r.recognize(self.body(), {}, reserve=reserve)
            self.assertEqual(ctx.exception.status, status)
            client.converse.assert_not_called()

    def test_unpriced_model_or_region_rejected_before_reservation(self):
        for config in [
            {"RECOGNITION_MODEL_ID": "apac.amazon.nova-pro-v1:0"},
            {"RECOGNITION_REGION": "us-east-1"},
        ]:
            reserve = Mock()
            with patch.dict("os.environ", config), patch.object(
                r, "_client"
            ) as client, self.assertRaises(r.RecognitionError) as ctx:
                r.recognize(self.body(), {}, reserve=reserve)
            self.assertEqual(ctx.exception.status, 503)
            reserve.assert_not_called()
            client.assert_not_called()

    def test_explicit_configuration_required(self):
        self.assertTrue(r.configured())
        with patch.dict("os.environ", {"RECOGNITION_MODEL_ID": ""}):
            self.assertFalse(r.configured())
            with patch.object(r, "_client") as client, self.assertRaises(
                r.RecognitionError
            ) as ctx:
                r.recognize(self.body(), {}, reserve=lambda: None)
            self.assertEqual(ctx.exception.status, 503)
            client.assert_not_called()
        with patch.dict("os.environ", {"RECOGNITION_REGION": "us-east-1"}):
            self.assertFalse(r.configured())
