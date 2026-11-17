"""
Unit tests for parsers and engine components (no fixture files needed).
"""
from __future__ import annotations

import pytest
from decimal import Decimal


class TestAmountParsing:
    def test_strip_indian_format(self):
        from app.parsers.bank_parser import _strip_amount
        assert _strip_amount("15,47,687.04") == 154768704
        assert _strip_amount("1,00,000.00") == 10000000
        assert _strip_amount("") is None
        assert _strip_amount("-") is None

    def test_zero_amount(self):
        from app.parsers.bank_parser import _strip_amount
        assert _strip_amount("0.00") == 0

    def test_negative_raises(self):
        from app.parsers.bank_parser import _strip_amount, ParseError
        with pytest.raises(ParseError):
            _strip_amount("-1000")


class TestDateParsing:
    def test_dd_mon_yyyy(self):
        from app.parsers.bank_parser import _parse_date
        from datetime import date
        assert _parse_date("01/Apr/2026") == date(2026, 4, 1)
        assert _parse_date("30/Apr/2026") == date(2026, 4, 30)

    def test_iso_date(self):
        from app.parsers.bank_parser import _parse_date
        from datetime import date
        assert _parse_date("2026-04-15") == date(2026, 4, 15)

    def test_timestamp_am_pm(self):
        from app.parsers.bank_parser import _parse_date
        from datetime import date
        assert _parse_date("01-04-2026 11:14:26 AM") == date(2026, 4, 1)
        assert _parse_date("02/04/2026 03:20:14 PM") == date(2026, 4, 2)
        assert _parse_date("2026-04-05 18:30:00") == date(2026, 4, 5)

    def test_dd_mm_yyyy_hyphen(self):
        from app.parsers.bank_parser import _parse_date
        from datetime import date
        assert _parse_date("15-04-2026") == date(2026, 4, 15)

    def test_invalid_raises(self):
        from app.parsers.bank_parser import _parse_date, ParseError
        with pytest.raises(ParseError):
            _parse_date("not-a-date")


class TestSubsetSum:
    def test_exact_match(self):
        from app.engine.matchers import find_subset_sum
        from app.models import BooksRow
        from datetime import date

        rows = [
            BooksRow(row_id="K-0001", txn_date=date(2026,4,1), debit_paise=10000, credit_paise=0, net_paise=10000),
            BooksRow(row_id="K-0002", txn_date=date(2026,4,1), debit_paise=20000, credit_paise=0, net_paise=20000),
            BooksRow(row_id="K-0003", txn_date=date(2026,4,1), debit_paise=50000, credit_paise=0, net_paise=50000),
        ]
        result = find_subset_sum(30000, rows)
        assert result is not None
        ids = {r.row_id for r in result}
        assert ids == {"K-0001", "K-0002"}

    def test_no_match(self):
        from app.engine.matchers import find_subset_sum
        from app.models import BooksRow
        from datetime import date

        rows = [
            BooksRow(row_id="K-0001", txn_date=date(2026,4,1), debit_paise=10000, credit_paise=0, net_paise=10000),
        ]
        result = find_subset_sum(99999, rows)
        assert result is None

    def test_prefers_smallest_set(self):
        from app.engine.matchers import find_subset_sum
        from app.models import BooksRow
        from datetime import date

        rows = [
            BooksRow(row_id="K-0001", txn_date=date(2026,4,1), debit_paise=100000, credit_paise=0, net_paise=100000),
            BooksRow(row_id="K-0002", txn_date=date(2026,4,1), debit_paise=50000, credit_paise=0, net_paise=50000),
            BooksRow(row_id="K-0003", txn_date=date(2026,4,1), debit_paise=50000, credit_paise=0, net_paise=50000),
        ]
        result = find_subset_sum(100000, rows)
        assert result is not None
        # Should prefer single row (smallest set)
        assert len(result) == 1
        assert result[0].row_id == "K-0001"


class TestNormalise:
    def test_normalise_text(self):
        from app.engine.normalise import normalise_text
        # normalise collapses whitespace to single spaces and strips
        assert normalise_text("  IMPS  Salary Payment  ") == "imps salary payment"

    def test_category_signal(self):
        from app.engine.normalise import category_signal
        assert category_signal("EPFO contribution May") == "EPFO"
        assert category_signal("DTAX payment GIB") == "DTAX"
        assert category_signal("random remark") == ""


class TestRedaction:
    def test_account_number_redacted(self):
        from app.agents.redact import redact_text
        result = redact_text("Account 1234567890 paid amount")
        assert "1234567890" not in result

    def test_ifsc_redacted(self):
        from app.agents.redact import redact_text
        result = redact_text("IFSC code ICIC0001234")
        assert "ICIC0001234" not in result
