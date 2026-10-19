import json
import unittest
from unittest.mock import Mock, patch
from urllib import error

import recognition as r

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
TOKEN = "unit-test-token"


class RecognitionTests(unittest.TestCase):
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

    def test_invalid_enum_shapes(self):
        result = r.normalize_result(
            {"language": [], "col": {}, "rar": [], "cardType": {}}, {}
        )
        self.assertTrue(all(value is None for value in result["fields"].values()))

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

    def reply(
        self,
        content='{"num":"P-134","language":"en","name":"Hello","cardType":"Event"}',
    ):
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}

    def test_invalid_input_before_provider(self):
        for body, status in [
            (None, 400),
            ({"mime": "text/plain"}, 400),
            ({"mime": "image/png", "data": "junk"}, 400),
            ({"mime": "image/png", "data": "A" * 4_700_000}, 413),
        ]:
            with patch.object(r, "_complete") as complete, self.assertRaises(
                r.RecognitionError
            ) as ctx:
                r.recognize(body, {}, api_key=TOKEN)
            self.assertEqual(ctx.exception.status, status)
            complete.assert_not_called()

    def test_provider_payload_and_fenced_output(self):
        with patch.object(
            r,
            "_complete",
            return_value=self.reply(
                '```json\n{"num":"P-134","language":"en","cardType":"Event"}\n```'
            ),
        ) as complete:
            result = r.recognize(self.body(), {}, api_key=TOKEN, reserve=lambda: None)
        self.assertEqual(result["fields"]["num"], "P-134")
        payload, token = complete.call_args.args
        self.assertEqual(token, TOKEN)
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 700)
        self.assertFalse(payload["stream"])
        image = payload["messages"][1]["content"][1]["image_url"]["url"]
        self.assertTrue(image.startswith("data:image/png;base64,"))

    def test_missing_token_and_budget_block_provider(self):
        for token, reserve, status in [
            (None, lambda: None, 503),
            (TOKEN, None, 503),
            (TOKEN, Mock(side_effect=r.budget.BudgetError(429, "limit")), 429),
            (TOKEN, Mock(side_effect=OSError()), 503),
        ]:
            with patch.object(r, "_complete") as complete, self.assertRaises(
                r.RecognitionError
            ) as ctx:
                r.recognize(self.body(), {}, api_key=token, reserve=reserve)
            self.assertEqual(ctx.exception.status, status)
            complete.assert_not_called()

    def test_budget_reserved_before_provider(self):
        events = []

        def complete(*args):
            events.append("provider")
            raise r.RecognitionError(502, "unavailable")

        with patch.object(r, "_complete", side_effect=complete), self.assertRaises(
            r.RecognitionError
        ):
            r.recognize(
                self.body(), {}, api_key=TOKEN, reserve=lambda: events.append("reserve")
            )
        self.assertEqual(events, ["reserve", "provider"])

    def test_long_prompt_blocks_before_budget(self):
        reserve = Mock()
        with patch.object(r, "PROMPT", "a" * 2001), patch.object(
            r, "_complete"
        ) as complete, self.assertRaises(r.RecognitionError):
            r.recognize(self.body(), {}, api_key=TOKEN, reserve=reserve)
        reserve.assert_not_called()
        complete.assert_not_called()

    def test_malformed_or_incomplete_model_output(self):
        for result in [
            None,
            {},
            {"choices": [{"finish_reason": "length"}]},
            self.reply("bad json"),
            self.reply("[]"),
        ]:
            with patch.object(r, "_complete", return_value=result), self.assertRaises(
                r.RecognitionError
            ) as ctx:
                r.recognize(self.body(), {}, api_key=TOKEN, reserve=lambda: None)
            self.assertEqual(ctx.exception.status, 502)

    def test_http_request_is_fixed_bounded_and_has_no_redirects(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(self.reply()).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(r.request, "build_opener", return_value=opener) as build:
            r._complete({"model": "deepseek-flash"}, TOKEN)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 20)
        response.read.assert_called_once_with(32769)
        self.assertIsInstance(build.call_args.args[0], r._NoRedirect)
        self.assertIsNone(
            r._NoRedirect().redirect_request(
                None, None, 302, "", {}, "https://other.invalid"
            )
        )

    def test_http_errors_and_timeout_are_safe(self):
        for exception, status in [
            (
                error.HTTPError(r.ENDPOINT, code, TOKEN, {}, None),
                503 if code in {401, 402, 429} else 502,
            )
            for code in [301, 401, 402, 429, 500]
        ] + [(TimeoutError(TOKEN), 502)]:
            opener = Mock()
            opener.open.side_effect = exception
            with patch.object(
                r.request, "build_opener", return_value=opener
            ), self.assertRaises(r.RecognitionError) as ctx:
                r._complete({}, TOKEN)
            self.assertEqual(ctx.exception.status, status)
            self.assertNotIn(TOKEN, str(ctx.exception))
