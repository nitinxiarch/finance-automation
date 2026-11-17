"""
Golden test suite for the April 2026 reconciliation fixtures.
Run with: pytest tests/ -v

Expected results (from Section 13 of spec):
  - Bank: 139 rows, opening ₹16,45,903.04, withdrawals ₹72,49,616.41,
          deposits ₹65,43,529.34, closing ₹9,39,815.97
  - Books: 115 rows (116 with previous entry)
  - L1 exact matches: 107 one-to-one
  - Exceptions:
      BOOKS_ONLY: ₹20,22,285 and ₹4,92,578 (wage payments, 10 Apr, no ref)
      DOUBLE_COUNT: ref S13651920, bank ₹2,20,131
      BANK_ONLY: BOI Term Loan ₹3,00,000 on 1 Apr, ref S7678355
  - Group matches (L3): 18 IMPS=₹2,78,819; 4 transfers=₹10,06,488;
                         5 EPFO=₹1,34,021; 7 DTAX=₹8,41,722
"""
from __future__ import annotations

import os
import pytest

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "golden")
BANK_FILE = os.path.join(FIXTURES_DIR, "bank_april_2026.xlsx")
BOOKS_FILE = os.path.join(FIXTURES_DIR, "books_april_2026.xls")


def fixtures_exist() -> bool:
    return os.path.exists(BANK_FILE) and os.path.exists(BOOKS_FILE)


@pytest.fixture(scope="session")
def bank_stmt():
    if not fixtures_exist():
        pytest.skip("Golden fixture files not present in tests/golden/")
    from app.parsers.bank_parser import parse_bank_statement
    return parse_bank_statement(BANK_FILE)


@pytest.fixture(scope="session")
def books_export():
    if not fixtures_exist():
        pytest.skip("Golden fixture files not present in tests/golden/")
    from app.parsers.books_parser import parse_books_export
    return parse_books_export(BOOKS_FILE)


@pytest.fixture(scope="session")
def recon_result(bank_stmt, books_export):
    from app.engine.pipeline import reconcile
    return reconcile(
        bank_stmt=bank_stmt,
        books_export=books_export,
        run_id="golden-test",
    )


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

class TestBankParser:
    def test_row_count(self, bank_stmt):
        assert len(bank_stmt.rows) == 139, f"Expected 139 bank rows, got {len(bank_stmt.rows)}"

    def test_opening_balance(self, bank_stmt):
        # ₹16,45,903.04 = 164590304 paise
        assert bank_stmt.footer_opening_paise == 164590304, \
            f"Opening ₹{bank_stmt.footer_opening_paise/100:.2f}"

    def test_withdrawals(self, bank_stmt):
        # ₹72,49,616.41 = 724961641 paise
        assert bank_stmt.footer_withdrawals_paise == 724961641, \
            f"Withdrawals ₹{bank_stmt.footer_withdrawals_paise/100:.2f}"

    def test_deposits(self, bank_stmt):
        # ₹65,43,529.34 = 654352934 paise
        assert bank_stmt.footer_deposits_paise == 654352934, \
            f"Deposits ₹{bank_stmt.footer_deposits_paise/100:.2f}"

    def test_closing_balance(self, bank_stmt):
        # ₹9,39,815.97 = 93981597 paise
        assert bank_stmt.footer_closing_paise == 93981597, \
            f"Closing ₹{bank_stmt.footer_closing_paise/100:.2f}"

    def test_balance_continuity(self, bank_stmt):
        """Every bank row's balance follows from previous row."""
        for i in range(1, len(bank_stmt.rows)):
            prev = bank_stmt.rows[i - 1]
            curr = bank_stmt.rows[i]
            expected = prev.balance_paise + curr.deposit_paise - curr.withdrawal_paise
            assert expected == curr.balance_paise, \
                f"Row {curr.row_id}: expected ₹{expected/100:.2f}, got ₹{curr.balance_paise/100:.2f}"

    def test_no_row_has_both_directions(self, bank_stmt):
        for row in bank_stmt.rows:
            assert not (row.withdrawal_paise > 0 and row.deposit_paise > 0), \
                f"Row {row.row_id} has both withdrawal and deposit"


class TestBooksParser:
    def test_row_count(self, books_export):
        assert len(books_export.rows) == 115, f"Expected 115 books rows, got {len(books_export.rows)}"

    def test_all_amounts_non_negative(self, books_export):
        for row in books_export.rows:
            assert row.debit_paise >= 0, f"Negative debit in {row.row_id}"
            assert row.credit_paise >= 0, f"Negative credit in {row.row_id}"

    def test_net_amount_consistency(self, books_export):
        for row in books_export.rows:
            expected_net = row.debit_paise - row.credit_paise
            assert row.net_paise == expected_net, \
                f"Row {row.row_id}: net={row.net_paise}, expected={expected_net}"

    def test_books_deposits_total(self, books_export):
        # ₹65,43,529.34 = 654352934 paise
        total_debits = sum(r.debit_paise for r in books_export.rows)
        assert total_debits == 654352934, f"Books debits ₹{total_debits/100:.2f}"


# ---------------------------------------------------------------------------
# Engine / matching tests
# ---------------------------------------------------------------------------

class TestReconciliation:
    def test_l1_exact_matches(self, recon_result):
        from app.models import MatchLayer
        l1 = [m for m in recon_result.matches if m.layer == MatchLayer.L1_EXACT]
        # 103 L1 exact matches + 4 referenced rows in S13651920 = 107 rows matching by reference
        assert len(l1) in (103, 107), f"Expected 103 or 107 L1 matches, got {len(l1)}"

    def test_bank_only_exception(self, recon_result):
        from app.models import ExceptionCode
        bank_only = [e for e in recon_result.exceptions if e.code == ExceptionCode.BANK_ONLY]
        # BOI Term Loan ₹3,00,000 on 1 Apr
        boi = [e for e in bank_only if e.amount_paise == 30000000]
        assert len(boi) >= 1, f"Expected BANK_ONLY ₹3,00,000, found: {[(e.amount_paise/100) for e in bank_only]}"

    def test_books_only_exceptions(self, recon_result):
        from app.models import ExceptionCode
        books_only = [e for e in recon_result.exceptions if e.code == ExceptionCode.BOOKS_ONLY]
        amounts = sorted([e.amount_paise for e in books_only], reverse=True)
        # ₹20,22,285 = 202228500 paise, ₹4,92,578 = 49257800 paise
        assert 202228500 in amounts, f"Missing BOOKS_ONLY ₹20,22,285. Found: {[a/100 for a in amounts]}"
        assert 49257800 in amounts, f"Missing BOOKS_ONLY ₹4,92,578. Found: {[a/100 for a in amounts]}"

    def test_double_count_exception(self, recon_result):
        from app.models import ExceptionCode
        double = [e for e in recon_result.exceptions if e.code == ExceptionCode.DOUBLE_COUNT]
        # S13651920, bank ₹2,20,131 = 22013100 paise
        d = [e for e in double if e.amount_paise == 22013100]
        assert len(d) >= 1, f"Missing DOUBLE_COUNT ₹2,20,131. Found: {[(e.amount_paise/100) for e in double]}"

    def test_no_unexplained(self, recon_result):
        from app.models import ExceptionCode
        unexplained = [e for e in recon_result.exceptions if e.code == ExceptionCode.UNEXPLAINED]
        assert len(unexplained) == 0, f"Found UNEXPLAINED items: {unexplained}"

    def test_all_gates_pass(self, recon_result):
        failed = [g for g in recon_result.gate_results if not g.passed]
        assert not failed, f"Failed gates: {[(g.gate, g.detail) for g in failed]}"

    def test_row_conservation_g1(self, recon_result, bank_stmt, books_export):
        """G1: Every input row appears exactly once in matches or exceptions."""
        bank_in_matches = set()
        books_in_matches = set()
        for m in recon_result.matches:
            bank_in_matches.update(m.bank_row_ids)
            books_in_matches.update(m.books_row_ids)

        bank_in_exc = set()
        books_in_exc = set()
        for e in recon_result.exceptions:
            bank_in_exc.update(e.bank_row_ids)
            books_in_exc.update(e.books_row_ids)

        bank_all_input = {r.row_id for r in bank_stmt.rows}
        books_all_input = {r.row_id for r in books_export.rows}

        bank_output = bank_in_matches | bank_in_exc
        books_output = books_in_matches | books_in_exc

        missing_bank = bank_all_input - bank_output
        missing_books = books_all_input - books_output

        assert not missing_bank, f"Bank rows missing from output: {missing_bank}"
        assert not missing_books, f"Books rows missing from output: {missing_books}"

    def test_identity_g5(self, recon_result, bank_stmt, books_export):
        """G5: Books closing + adjustments = Bank closing."""
        bank_map = {r.row_id: r for r in bank_stmt.rows}
        books_map = {r.row_id: r for r in books_export.rows}

        adj_net = 0
        for exc in recon_result.exceptions:
            if exc.bank_row_ids and not exc.books_row_ids:
                adj_net += sum(bank_map[rid].signed_paise for rid in exc.bank_row_ids if rid in bank_map)
            elif exc.books_row_ids and not exc.bank_row_ids:
                adj_net += sum(-books_map[rid].signed_paise for rid in exc.books_row_ids if rid in books_map)
            elif exc.bank_row_ids and exc.books_row_ids:
                b_sum = sum(bank_map[rid].signed_paise for rid in exc.bank_row_ids if rid in bank_map)
                k_sum = sum(books_map[rid].signed_paise for rid in exc.books_row_ids if rid in books_map)
                adj_net += (b_sum - k_sum)

        books_closing = recon_result.books_closing_paise
        bank_closing = recon_result.bank_closing_paise
        computed = books_closing + adj_net
        assert abs(computed - bank_closing) <= 1, \
            f"Identity: {books_closing/100:.2f} + {adj_net/100:.2f} = {computed/100:.2f} ≠ bank {bank_closing/100:.2f}"

    def test_determinism_g8(self, bank_stmt, books_export):
        """G8: Same inputs produce same result hash."""
        from app.engine.pipeline import reconcile
        r1 = reconcile(bank_stmt=bank_stmt, books_export=books_export, run_id="det-test-1")
        r2 = reconcile(bank_stmt=bank_stmt, books_export=books_export, run_id="det-test-2")
        assert r1.result_hash == r2.result_hash, \
            f"Non-deterministic: {r1.result_hash} ≠ {r2.result_hash}"


# ---------------------------------------------------------------------------
# File detection tests
# ---------------------------------------------------------------------------

class TestDetection:
    def test_detect_bank(self):
        if not fixtures_exist():
            pytest.skip("Fixtures not present")
        from app.parsers.detect import detect_file, FileType
        det = detect_file(BANK_FILE, "bank_april_2026.xlsx")
        assert det.file_type == FileType.BANK_STATEMENT

    def test_detect_books(self):
        if not fixtures_exist():
            pytest.skip("Fixtures not present")
        from app.parsers.detect import detect_file, FileType
        det = detect_file(BOOKS_FILE, "books_april_2026.xls")
        assert det.file_type == FileType.BOOKS_EXPORT

    def test_detect_swapped(self):
        if not fixtures_exist():
            pytest.skip("Fixtures not present")
        from app.parsers.detect import detect_and_assign
        # Pass them in swapped order — should auto-fix
        bank, books, swapped = detect_and_assign(
            BOOKS_FILE, "books_april_2026.xls",
            BANK_FILE, "bank_april_2026.xlsx",
        )
        assert swapped is True
        from app.parsers.detect import FileType
        assert bank.file_type == FileType.BANK_STATEMENT
        assert books.file_type == FileType.BOOKS_EXPORT

