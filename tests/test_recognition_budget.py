import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import recognition_budget as budget

OCTOBER = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
NOVEMBER = datetime(2026, 11, 1, tzinfo=timezone.utc)


class Conflict(Exception):
    response = {"Error": {"Code": "PreconditionFailed"}}


class FakeS3:
    def __init__(self):
        self.value = budget.initial_ledger()
        self.version = 0
        self.lock = threading.Lock()
        self.puts = 0

    def get_object(self, **kwargs):
        with self.lock:
            return {
                "Body": io.BytesIO(json.dumps(self.value).encode()),
                "ETag": str(self.version),
            }

    def put_object(self, **kwargs):
        with self.lock:
            if kwargs.get("IfMatch") != str(self.version):
                raise Conflict()
            self.value = json.loads(kwargs["Body"])
            self.version += 1
            self.puts += 1


class BudgetTests(unittest.TestCase):
    def test_conservative_model_cost_bound(self):
        upper_bound = (
            Decimal(budget.MAX_INPUT_TOKENS) * Decimal("0.081")
            + Decimal(budget.MAX_OUTPUT_TOKENS) * Decimal("0.324")
        ) / 1_000_000
        self.assertEqual(upper_bound, Decimal("0.0245268"))
        reservation = Decimal(budget.RESERVATION_MILLI_USD) / 1000
        self.assertLessEqual(upper_bound, reservation)
        self.assertLessEqual(reservation * budget.MAX_ATTEMPTS, Decimal("1"))

    def test_limit_math_and_month_rollover(self):
        ledger = budget.initial_ledger()
        self.assertEqual(budget.MAX_ATTEMPTS, 40)
        for _ in range(40):
            ledger = budget._advance(ledger, OCTOBER)
        with self.assertRaises(budget.BudgetError) as ctx:
            budget._advance(ledger, OCTOBER)
        self.assertEqual(ctx.exception.status, 429)
        next_month = budget._advance(ledger, NOVEMBER)
        self.assertEqual(next_month["months"], {"2026-10": 40, "2026-11": 1})
        self.assertEqual(ledger["months"], {"2026-10": 40})

    def test_corrupt_ledger_fails_closed(self):
        for value in [
            None,
            {},
            {"schema": 1, "plan": "different-price", "months": {}},
            {"schema": 1, "plan": budget.PLAN, "months": {"2026-10": -1}},
            {"schema": 1, "plan": budget.PLAN, "months": {"2026-10": True}},
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                budget._advance(value, OCTOBER)

    def test_local_restart_and_missing_or_broken_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "ledger.json")
            with self.assertRaises(budget.BudgetError):
                budget.reserve_local(path, OCTOBER)
            self.assertFalse(Path(path).exists())
            budget.initialize_local(path)
            with self.assertRaises(FileExistsError):
                budget.initialize_local(path)
            for _ in range(40):
                budget.reserve_local(path, OCTOBER)
            with self.assertRaises(budget.BudgetError) as ctx:
                budget.reserve_local(path, OCTOBER)
            self.assertEqual(ctx.exception.status, 429)
            self.assertEqual(
                json.loads(Path(path).read_text())["months"]["2026-10"], 40
            )
            Path(path).write_text("corrupt")
            with self.assertRaises(budget.BudgetError) as ctx:
                budget.reserve_local(path, OCTOBER)
            self.assertEqual(ctx.exception.status, 503)
            self.assertEqual(Path(path).read_text(), "corrupt")

    def test_local_concurrent_reservations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "ledger.json")
            budget.initialize_local(path)

            def attempt(_):
                try:
                    budget.reserve_local(path, OCTOBER)
                    return True
                except budget.BudgetError:
                    return False

            with ThreadPoolExecutor(max_workers=12) as pool:
                results = list(pool.map(attempt, range(70)))
            self.assertEqual(sum(results), 40)
            self.assertEqual(
                json.loads(Path(path).read_text())["months"]["2026-10"], 40
            )

    def test_s3_concurrency_and_no_reset(self):
        s3 = FakeS3()

        def attempt(_):
            try:
                budget.reserve_s3(s3, "bucket", OCTOBER)
                return True
            except budget.BudgetError:
                return False

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(attempt, range(90)))
        self.assertLessEqual(sum(results), 40)
        self.assertEqual(sum(results), s3.value["months"]["2026-10"])
        self.assertEqual(s3.puts, sum(results))
        while s3.value["months"]["2026-10"] < 40:
            budget.reserve_s3(s3, "bucket", OCTOBER)
        with self.assertRaises(budget.BudgetError) as ctx:
            budget.reserve_s3(s3, "bucket", OCTOBER)
        self.assertEqual(ctx.exception.status, 429)
        budget.reserve_s3(s3, "bucket", NOVEMBER)
        self.assertEqual(s3.value["months"], {"2026-10": 40, "2026-11": 1})

    def test_s3_read_write_and_contention_fail_closed(self):
        s3 = FakeS3()
        with patch.object(s3, "get_object", side_effect=OSError()), self.assertRaises(
            budget.BudgetError
        ):
            budget.reserve_s3(s3, "bucket")
        self.assertEqual(s3.puts, 0)
        with patch.object(
            s3, "put_object", side_effect=Conflict()
        ) as put, self.assertRaises(budget.BudgetError):
            budget.reserve_s3(s3, "bucket")
        self.assertEqual(put.call_count, 5)
        self.assertEqual(s3.puts, 0)
        s3.value = None
        with self.assertRaises(budget.BudgetError):
            budget.reserve_s3(s3, "bucket")
        self.assertEqual(s3.puts, 0)
