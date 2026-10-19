import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import recognition_settings as settings

TOKEN = "private-unit-test-token"


class SettingsTests(unittest.TestCase):
    def test_invalid_token_requests(self):
        for body in [
            None,
            {},
            {"token": None},
            {"token": ""},
            {"token": "a b"},
            {"token": "a\nb"},
            {"token": "x" * 513},
            {"token": TOKEN, "endpoint": "https://other.invalid"},
        ]:
            with self.assertRaises(settings.SettingsError):
                settings.token_from_request(body)
        self.assertEqual(
            settings.token_from_request({"token": " " + TOKEN + " "}), TOKEN
        )

    def test_local_private_atomic_persistence_and_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / settings.LOCAL_FILE)
            self.assertEqual(settings.load_local(path), "")
            settings.save_local(path, TOKEN)
            self.assertEqual(settings.load_local(path), TOKEN)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            status = settings.public_status(settings.load_local(path))
            self.assertEqual(
                status,
                {
                    "configured": True,
                    "provider": "deepseek",
                    "model": "deepseek-flash",
                    "maskedKey": "private-*****oken",
                },
            )
            self.assertNotIn(TOKEN, json.dumps(status))
            settings.save_local(path, "")
            self.assertEqual(settings.load_local(path), "")
            self.assertEqual(list(Path(directory).glob(".recognition-settings-*")), [])
            Path(path).write_text("broken")
            with self.assertRaises(settings.SettingsError):
                settings.load_local(path)

    def test_s3_encryption_and_tombstone(self):
        client = Mock()
        settings.save_s3(client, "bucket", TOKEN)
        sent = client.put_object.call_args.kwargs
        self.assertEqual(sent["ServerSideEncryption"], "AES256")
        self.assertEqual(sent["Key"], settings.S3_KEY)
        client.get_object.return_value = {"Body": io.BytesIO(sent["Body"])}
        self.assertEqual(settings.load_s3(client, "bucket"), TOKEN)
        settings.save_s3(client, "bucket", "")
        self.assertEqual(
            json.loads(client.put_object.call_args.kwargs["Body"])["token"], ""
        )
        client.delete_object.assert_not_called()

    def test_storage_errors_are_safe_and_missing_is_unconfigured(self):
        client = Mock()
        exception = RuntimeError(TOKEN)
        exception.response = {"Error": {"Code": "NoSuchKey"}}
        client.get_object.side_effect = exception
        self.assertEqual(settings.load_s3(client, "bucket"), "")
        exception.response["Error"]["Code"] = "AccessDenied"
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.load_s3(client, "bucket")
        self.assertNotIn(TOKEN, str(ctx.exception))
        client.put_object.side_effect = RuntimeError(TOKEN)
        with self.assertRaises(settings.SettingsError) as ctx:
            settings.save_s3(client, "bucket", TOKEN)
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_masked_preview_never_returns_full_key(self):
        for token in ["x", "short-token", "x" * 19]:
            self.assertEqual(settings.public_status(token)["maskedKey"], "*****")
        token = "sk-f00e2xxxxxxxx1ab1xx"
        masked = settings.public_status(token)["maskedKey"]
        self.assertEqual(masked, token[:8] + "*****" + token[-4:])
        self.assertNotEqual(masked, token)
        self.assertEqual(settings.public_status("")["maskedKey"], None)
