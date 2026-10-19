"""Reserve a conservative recognition allowance before any model invocation."""

import copy
from datetime import datetime, timezone
import json
import os
import re
import tempfile

MONTHLY_LIMIT_MILLI_USD = 1000
RESERVATION_MILLI_USD = 25
MAX_ATTEMPTS = MONTHLY_LIMIT_MILLI_USD // RESERVATION_MILLI_USD
MAX_INPUT_TOKENS = 8192
MAX_PROMPT_BYTES = 2000
MAX_IMAGE_TOKENS = 1024
MAX_OUTPUT_TOKENS = 700
PLAN = "deepseek-flash-8192-input-700-output-v1"
LEGACY_PLAN = "apac-nova-lite-300k-input-700-output-v1"
S3_KEY = "data/recognition-budget.json"
LOCAL_FILE = ".recognition-budget.json"


class BudgetError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def initial_ledger():
    return {"schema": 1, "plan": PLAN, "months": {}}


def _advance(value, now=None):
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value.get("schema") != 1
        or value.get("plan") not in {PLAN, LEGACY_PLAN}
        or not isinstance(value.get("months"), dict)
        or len(value["months"]) > 1200
    ):
        raise ValueError("invalid budget ledger")
    for month, attempts in value["months"].items():
        if (
            not isinstance(month, str)
            or not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month)
            or type(attempts) is not int
            or not 0 <= attempts <= MAX_ATTEMPTS
        ):
            raise ValueError("invalid budget period")
    current = (
        (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m")
    )
    attempts = value["months"].get(current, 0)
    if attempts >= MAX_ATTEMPTS:
        raise BudgetError(
            429,
            "The $1 monthly card-recognition allowance is used up. Add the card manually or try next month (UTC).",
        )
    result = copy.deepcopy(value)
    result["plan"] = PLAN
    result["months"][current] = attempts + 1
    return result


def _decode(raw):
    if len(raw) > 32_000:
        raise ValueError("budget ledger too large")
    return json.loads(raw)


def _unavailable():
    return BudgetError(
        503,
        "Card recognition is unavailable because its spending guard is not ready. You can still add cards manually.",
    )


def reserve_s3(client, bucket, now=None):
    for _ in range(5):
        try:
            stored = client.get_object(Bucket=bucket, Key=S3_KEY)
            etag = stored.get("ETag")
            if not isinstance(etag, str) or not etag:
                raise ValueError("missing budget revision")
            next_value = _advance(_decode(stored["Body"].read()), now)
        except BudgetError:
            raise
        except Exception:
            raise _unavailable() from None
        try:
            client.put_object(
                Bucket=bucket,
                Key=S3_KEY,
                Body=json.dumps(next_value).encode(),
                ContentType="application/json",
                IfMatch=etag,
            )
            return
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") in {
                "PreconditionFailed",
                "ConditionalRequestConflict",
                "412",
                "409",
            }:
                continue
            raise _unavailable() from None
    raise _unavailable()


def reserve_local(path, now=None):
    try:
        import fcntl

        with open(path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with open(path, "rb") as current:
                next_value = _advance(_decode(current.read()), now)
            fd, pending = tempfile.mkstemp(
                prefix=".recognition-budget-",
                dir=os.path.dirname(os.path.abspath(path)),
            )
            try:
                with os.fdopen(fd, "w") as output:
                    json.dump(next_value, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(pending, path)
                directory = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(pending):
                    os.unlink(pending)
    except BudgetError:
        raise
    except Exception:
        raise _unavailable() from None


def initialize_local(path):
    with open(path, "x") as output:
        json.dump(initial_ledger(), output)
        output.flush()
        os.fsync(output.fileno())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Provision a new local recognition ledger, or print its initial S3 JSON. Never reset an existing ledger."
    )
    parser.add_argument("action", choices=["init-local", "initial-json"])
    parser.add_argument("path", nargs="?", default=LOCAL_FILE)
    args = parser.parse_args()
    if args.action == "init-local":
        initialize_local(args.path)
    else:
        print(json.dumps(initial_ledger()))
