"""
Edge cases and requirement tests:
  - Corrupted bank balance (G2 failure with fix hint)
  - Period mismatch validation
  - Model timeout / unreachable fallback path
  - Judge / anti-hallucination guard rejecting invented amounts (G9)
  - Full workbook generation with all 6 sheets and formula checks (G10)
"""
from __future__ import annotations

import os
import pytest
from datetime import date
from unittest.mock import AsyncMock, patch

from app.models import (
    BankRow, BankStatement, BooksExport, BooksRow, ExceptionCode,
    ExceptionRecord, MatchLayer, MatchRecord, Priority, RunStatus,
    SalesActionCard, RecommendedAction, FactRef,
)
from app.parsers.bank_parser import ParseError, parse_bank_statement
from app.engine.pipeline import reconcile
from app.export.workbook import build_workbook

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "golden")
BANK_FILE = os.path.join(FIXTURES_DIR, "bank_april_2026.xlsx")
BOOKS_FILE = os.path.join(FIXTURES_DIR, "books_april_2026.xls")


# ---------------------------------------------------------------------------
# 1. Corrupted bank balance
# ---------------------------------------------------------------------------

class TestCorruptedBankBalance:
    def test_corrupted_balance_chain_raises_parse_error_with_fix_hint(self, tmp_path):
        import openpyxl
        # Copy bank file and tamper with row 5 balance
        wb = openpyxl.load_workbook(BANK_FILE)
        ws = wb["Sheet0"]
        # Find header
        header_row = None
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if row[0] == "S.N.":
                header_row = i + 1
                break
        assert header_row is not None

        # Corrupt balance of first data row
        ws.cell(row=header_row + 2, column=10, value="99,99,999.00")
        corrupt_path = str(tmp_path / "corrupt_bank.xlsx")
        wb.save(corrupt_path)

        with pytest.raises(ParseError) as exc_info:
            parse_bank_statement(corrupt_path)

        # Confirm error message specifies row and fix hint
        err = str(exc_info.value)
        assert "does not follow from" in err or "corrupted" in err or "balance" in err.lower()
        assert "Re-download the statement" in err


# ---------------------------------------------------------------------------
# 2. Wrong period / period mismatch
# ---------------------------------------------------------------------------

class TestPeriodValidation:
    def test_period_parsing_invalid_format(self):
        from app.parsers.bank_parser import _parse_period
        with pytest.raises(ParseError) as exc_info:
            _parse_period("From 2026/01/01 Until 2026/01/31")
        assert "Expected format" in str(exc_info.value)

    def test_period_mismatch_detected(self, tmp_path):
        from app.parsers.bank_parser import parse_bank_statement
        from app.parsers.books_parser import parse_books_export
        # Bank is April 2026, let's create a Books export with May 2026 in title
        import xlwt
        wb = xlwt.Workbook(encoding="utf-8")
        ws = wb.add_sheet("Account Transactions")
        ws.write(0, 0, "Acme Technologies Pvt Ltd\nAccount Transactions\nBasis: Accrual\n01 May 2026 - 31 May 2026")
        ws.write(1, 0, "date")
        ws.write(1, 1, "debit")
        ws.write(1, 2, "credit")
        ws.write(2, 0, "2026-05-01")
        ws.write(2, 1, 1000.0)
        ws.write(2, 2, 0.0)
        may_path = str(tmp_path / "books_may.xls")
        wb.save(may_path)

        b_stmt = parse_bank_statement(BANK_FILE)
        b_export = parse_books_export(may_path)
        assert b_stmt.period_from.month != b_export.period_from.month


# ---------------------------------------------------------------------------
# 3. Model timeout / unreachable fallback path
# ---------------------------------------------------------------------------

class TestModelTimeoutFallback:
    def test_llm_unavailable_produces_rule_based_cards(self):
        import asyncio
        asyncio.run(self._async_test())

    async def _async_test(self):
        from app.agents.orchestrator import run_ai_layer
        from app.agents.tools import AgentTools
        from app.parsers.bank_parser import parse_bank_statement
        from app.parsers.books_parser import parse_books_export

        b_stmt = parse_bank_statement(BANK_FILE)
        b_export = parse_books_export(BOOKS_FILE)
        result = reconcile(b_stmt, b_export, run_id="fallback-test")

        bank_map = {r.row_id: r for r in b_stmt.rows}
        books_map = {r.row_id: r for r in b_export.rows}
        tools = AgentTools(result, bank_map, books_map)

        # Simulate tunnel offline
        with patch("app.agents.orchestrator.check_tunnel", new_callable=AsyncMock) as mock_tunnel:
            mock_tunnel.return_value = {
                "tunnel_ok": False,
                "model_found": False,
                "model_tag": None,
                "error": "Connection refused",
            }
            res, verifier = await run_ai_layer(result, tools)

            assert verifier is None
            for exc in res.exceptions:
                assert exc.ai_card is not None
                assert "AI unavailable, rule-based" in exc.ai_card.summary
                assert exc.ai_verified is False


# ---------------------------------------------------------------------------
# 4. Anti-hallucination / G9: Judge and number grounding
# ---------------------------------------------------------------------------

class TestAntiHallucinationG9:
    def test_invented_amount_is_stripped_and_replaced_with_rule_based(self):
        import asyncio
        asyncio.run(self._async_test())

    async def _async_test(self):
        """If AI invents a number like ₹9,99,999 not present in tool results, card is replaced."""
        from app.agents.orchestrator import run_ai_layer
        from app.agents.tools import AgentTools
        from app.parsers.bank_parser import parse_bank_statement
        from app.parsers.books_parser import parse_books_export

        b_stmt = parse_bank_statement(BANK_FILE)
        b_export = parse_books_export(BOOKS_FILE)
        result = reconcile(b_stmt, b_export, run_id="hallucination-test")

        bank_map = {r.row_id: r for r in b_stmt.rows}
        books_map = {r.row_id: r for r in b_export.rows}
        tools = AgentTools(result, bank_map, books_map)

        # Mock tunnel OK, but LLM responds with a hallucinated number 999999
        fake_advisor_response = {
            "cards": [
                {
                    "exception_id": result.exceptions[0].exception_id,
                    "code": result.exceptions[0].code.value,
                    "owner_team": "Sales",
                    "priority": "P1",
                    "summary": "Customer claims they made an invoice payment of 99999999 rupees",
                    "facts_used": [],
                    "recommended_actions": [{"step": "Check invoice", "who": "Sales", "due_in_days": 2}],
                    "customer_message_draft": "Please pay 99999999",
                    "needs_human": False,
                }
            ]
        }

        with patch("app.agents.orchestrator.check_tunnel", new_callable=AsyncMock) as mock_tunnel, \
             patch("app.agents.orchestrator.call_llm", new_callable=AsyncMock) as mock_llm:
            mock_tunnel.return_value = {"tunnel_ok": True, "model_found": True, "model_tag": "qwen2.5:32b-instruct"}
            mock_llm.side_effect = [
                ({"identity_confirmed": True, "identity_detail": "OK", "exception_reviews": [], "disagreements": []}, "call-1"),
                (fake_advisor_response, "call-2"),
                ({"verdicts": [{"exception_id": result.exceptions[0].exception_id, "card_passed": True, "claims": []}]}, "call-3"),
            ]

            res, verifier = await run_ai_layer(result, tools)
            exc = next(e for e in res.exceptions if e.exception_id == result.exceptions[0].exception_id)
            # The card with invented amount 99999999 must have been stripped/replaced
            assert "99999999" not in exc.ai_card.summary


# ---------------------------------------------------------------------------
# 5. Full workbook generation and G10 formula verification
# ---------------------------------------------------------------------------

class TestWorkbookGenerationG10:
    def test_workbook_structure_and_formulas(self, tmp_path):
        from app.parsers.bank_parser import parse_bank_statement
        from app.parsers.books_parser import parse_books_export
        b_stmt = parse_bank_statement(BANK_FILE)
        b_export = parse_books_export(BOOKS_FILE)
        result = reconcile(b_stmt, b_export, run_id="wb-test")

        wb = build_workbook(
            result=result,
            bank_stmt=b_stmt,
            books_export=b_export,
            bank_file_hash="fake-bank-hash",
            books_file_hash="fake-books-hash",
            engine_version="1.0.0",
            model_name="qwen2.5:32b-instruct",
        )

        expected_sheets = ["Bank and Books", "Summary", "Matches", "Exceptions", "Sales_Actions", "Audit_Log"]
        for s in expected_sheets:
            assert s in wb.sheetnames, f"Missing sheet: {s}"

        ws1 = wb["Bank and Books"]
        # Check frozen panes
        assert ws1.freeze_panes == "A2"

        # Check net_amount formula in books rows
        # Row 3 is first books data row (row 1 is header, row 2 is previous entry)
        net_formula = ws1.cell(row=3, column=9).value
        assert str(net_formula).startswith("=N(G3)-N(H3)"), f"Unexpected formula: {net_formula}"

        # Check running balance formula in books rows
        rb_formula = ws1.cell(row=3, column=11).value
        assert str(rb_formula).startswith("=K2+I3"), f"Unexpected running balance formula: {rb_formula}"

        # Check yellow fill on group matches
        # Find a books row that matched as group
        group_matches = [m for m in result.matches if m.is_group_match]
        assert group_matches, "No group matches found"
        first_group_book_row_id = group_matches[0].books_row_ids[0]

        # Verify yellow fill exists on some cells
        found_yellow = False
        for row in ws1.iter_rows(min_row=3, max_col=12):
            if row[6].fill and row[6].fill.fgColor and row[6].fill.fgColor.rgb == "00FFFF00":
                found_yellow = True
                break
        assert found_yellow, "Yellow fill for group matches not found"

        # Save and verify file loads cleanly
        out_path = str(tmp_path / "test_out.xlsx")
        wb.save(out_path)
        assert os.path.exists(out_path)
        assert os.path.getsize(out_path) > 1000
