"""Private server-side credentials for image recognition."""

import json
import os
import tempfile

S3_KEY = "data/recognition-settings.json"
LOCAL_FILE = ".recognition-settings.json"
MAX_BODY_BYTES = 4096
MODEL = "deepseek-flash"


class SettingsError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def validate_token(token):
    if not isinstance(token, str):
        raise SettingsError(400, "Enter a DeepSeek API token")
    token = token.strip()
    if (
        not token
        or len(token) > 512
        or any(not 33 <= ord(char) <= 126 for char in token)
    ):
        raise SettingsError(
            400, "Enter a valid DeepSeek API token without spaces or control characters"
        )
    return token


def token_from_request(body):
    if not isinstance(body, dict) or set(body) != {"token"}:
        raise SettingsError(400, "Provide only the DeepSeek API token")
    return validate_token(body["token"])


def _document(token):
    return {"schema": 1, "provider": "deepseek", "token": token}


def _decode(raw):
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("settings too large")
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value["schema"] != 1
        or value.get("provider") != "deepseek"
        or not isinstance(value.get("token"), str)
    ):
        raise ValueError("invalid private recognition settings")
    return validate_token(value["token"]) if value["token"] else ""


def _unavailable():
    return SettingsError(503, "Recognition settings are unavailable. Try again later.")


def public_status(token):
    masked = (
        None
        if not token
        else (token[:8] + "*****" + token[-4:] if len(token) >= 20 else "*****")
    )
    return {
        "configured": bool(token),
        "provider": "deepseek",
        "model": MODEL,
        "maskedKey": masked,
    }


def load_local(path):
    try:
        with open(path, "rb") as source:
            return _decode(source.read(MAX_BODY_BYTES + 1))
    except FileNotFoundError:
        return ""
    except Exception:
        raise _unavailable() from None


def save_local(path, token):
    token = validate_token(token) if token else ""
    try:
        import fcntl

        lock_fd = os.open(path + ".lock", os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(lock_fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            directory = os.path.dirname(os.path.abspath(path))
            fd, pending = tempfile.mkstemp(
                prefix=".recognition-settings-", dir=directory
            )
            try:
                with os.fdopen(fd, "w") as output:
                    os.fchmod(output.fileno(), 0o600)
                    json.dump(_document(token), output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(pending, path)
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)
    except Exception:
        raise _unavailable() from None


def load_s3(client, bucket):
    try:
        result = client.get_object(Bucket=bucket, Key=S3_KEY)
        return _decode(result["Body"].read(MAX_BODY_BYTES + 1))
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in {
            "NoSuchKey",
            "404",
            "NotFound",
        }:
            return ""
        raise _unavailable() from None


def save_s3(client, bucket, token):
    token = validate_token(token) if token else ""
    try:
        client.put_object(
            Bucket=bucket,
            Key=S3_KEY,
            Body=json.dumps(_document(token)).encode(),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
    except Exception:
        raise _unavailable() from None
