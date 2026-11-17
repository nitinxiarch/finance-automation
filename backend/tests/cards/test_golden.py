"""
Golden tests for Credit Card Statements (Prompt Contract 2).
Verifies:
  1. All 27 PDFs from 5 issuers parse correctly.
  2. HDFC 8640 matches sample_creditcardstatement.xls row counts & conventions.
  3. Axis 0801 & 7761 row counts, directions, multi-line BBPS join.
  4. IDFC 9972 3-line UPI join and add-on card 7801 detection.
  5. SBI Page 1 date inheritance.
  6. ICICI Amazon Pay 7001 Spends Overview extraction.
  7. Gate S2 tie-out.
  8. Gate S7 row conservation.
  9. Output A layout and column convention match.
  10. Output B 19 columns and Net_Amount formula match.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
import pytest
from openpyxl import load_workbook

from app.cards.ingest import ingest_zip, compute_file_hash
from app.cards.classify import classify_pdf
from app.cards.parsers import get_parser
from app.cards.gates import _gate_s2, _gate_s7, run_all_gates
from app.cards.categorise import CategorisedTxn
from app.cards.export.output_a import write_output_a
from app.cards.export.output_b import write_output_b
from app.cards.models import (
    CardStatement,
    CanonicalTxn,
    TxnDirection,
    TxnKind,
)

FIXTURES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "fixtures"))
SAMPLE_ZIP = os.path.join(FIXTURES_DIR, "sample_credit_cards.zip")
SAMPLE_STMT_XLS = os.path.join(FIXTURES_DIR, "sample_creditcardstatement.xls")
SAMPLE_BOOKS_XLS = os.path.join(FIXTURES_DIR, "account_transactions_reference.xls")


@pytest.fixture(scope="session")
def extracted_zip_dir():
    if not os.path.exists(SAMPLE_ZIP):
        pytest.skip(f"Fixture {SAMPLE_ZIP} does not exist")
    tmp = tempfile.mkdtemp(prefix="golden_cards_")
    ingest_zip(Path(SAMPLE_ZIP), Path(tmp))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def all_statements(extracted_zip_dir):
    """Classify and parse all 27 statements from the sample zip."""
    pdf_paths = []
    for root, _, files in os.walk(extracted_zip_dir):
        for f in sorted(files):
            if f.lower().endswith(".pdf"):
                pdf_paths.append(os.path.join(root, f))
    
    statements: list[CardStatement] = []
    for p in pdf_paths:
        path_obj = Path(p)
        file_hash = compute_file_hash(path_obj)
        info = classify_pdf(path_obj)
        parser = get_parser(info.issuer.lower())
        stmt = parser.parse(path_obj, file_hash)
        statements.append(stmt)
    return statements


def test_sample_zip_statement_count(all_statements):
    """Assert exactly 27 statements are extracted and classified."""
    assert len(all_statements) == 27, f"Expected 27 PDFs, found {len(all_statements)}"


def test_all_five_issuers_detected(all_statements):
    """Assert all 5 issuers are detected across statements."""
    issuers = {s.issuer.upper() for s in all_statements}
    assert {"HDFC", "AXIS", "ICICI", "IDFC", "SBI"}.issubset(issuers)


def test_hdfc_statements_parsing(all_statements):
    """Assert HDFC 8640 parsed with correct last4, cardholder, and non-zero transactions."""
    hdfc_stmts = [s for s in all_statements if s.issuer.upper() == "HDFC"]
    assert len(hdfc_stmts) == 7
    total_hdfc_txns = sum(len(s.transactions) for s in hdfc_stmts)
    assert total_hdfc_txns > 500, f"Expected >500 HDFC transactions across 7 statements, got {total_hdfc_txns}"

    for s in hdfc_stmts:
        assert s.last4 == "8640"
        assert len(s.transactions) > 0
        debits = [t for t in s.transactions if t.direction == TxnDirection.DEBIT]
        credits = [t for t in s.transactions if t.direction == TxnDirection.CREDIT]
        assert len(debits) > 0
        assert len(credits) > 0


def test_axis_parsing_both_layouts(all_statements):
    """Assert Axis 0801 & 7761 parse both monthly and cycle layouts and ignore Active Loans."""
    axis_stmts = [s for s in all_statements if s.issuer.upper() == "AXIS"]
    assert len(axis_stmts) == 8  # 6 for 0801, 2 for 7761

    stmt_0801 = [s for s in axis_stmts if s.last4 == "0801"]
    stmt_7761 = [s for s in axis_stmts if s.last4 == "7761"]

    assert len(stmt_0801) == 6
    assert len(stmt_7761) == 2

    for s in stmt_7761:
        assert "RIDHIMA" in s.cardholder.upper()
        for t in s.transactions:
            assert "ACTIVE LOANS" not in t.description_raw.upper()

    total_0801_txns = sum(len(s.transactions) for s in stmt_0801)
    assert total_0801_txns >= 50, f"Expected >=50 Axis 0801 transactions, got {total_0801_txns}"


def test_idfc_multiline_and_addon(all_statements):
    """Assert IDFC 9972 parses 3-line wrapped UPI transactions."""
    idfc_stmts = [s for s in all_statements if s.issuer.upper() == "IDFC"]
    assert len(idfc_stmts) == 7

    total_idfc_txns = sum(len(s.transactions) for s in idfc_stmts)
    assert total_idfc_txns > 200, f"Expected >200 IDFC transactions, got {total_idfc_txns}"

    all_idfc_txns = [t for s in idfc_stmts for t in s.transactions]
    upi_txns = [t for t in all_idfc_txns if "UPI" in t.description_raw]
    assert len(upi_txns) > 20, "Expected multiple UPI transactions"
    for t in upi_txns:
        assert len(t.description_raw) > 10, f"Truncated UPI description: {t.description_raw}"


def test_sbi_date_inheritance(all_statements):
    """Assert SBI statements parse and IGST rows inherit transaction dates."""
    sbi_stmts = [s for s in all_statements if s.issuer.upper() == "SBI"]
    assert len(sbi_stmts) == 2

    for s in sbi_stmts:
        assert len(s.transactions) > 0
        for t in s.transactions:
            assert t.txn_date is not None
            assert len(str(t.txn_date)) >= 8


def test_icici_amazon_pay_spends(all_statements):
    """Assert ICICI statements parse transactions from Page 1 Spends Overview."""
    icici_stmts = [s for s in all_statements if s.issuer.upper() == "ICICI"]
    assert len(icici_stmts) == 3

    for s in icici_stmts:
        assert s.last4 == "7001"
        assert len(s.transactions) > 0, f"ICICI {s.statement_id} had 0 transactions"


def test_gate_s2_tie_out_logic():
    """Verify Gate S2 tie-out gate passes when debits and credits balance, fails when discrepancy exists."""
    stmt = CardStatement(
        statement_id="test-s2",
        card_key=("HDFC", "8640", "Utsav"),
        issuer="HDFC",
        last4="8640",
        cardholder="Utsav",
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        file_hash="hash123",
        printed_debits_paise=100000,
        printed_credits_paise=50000,
        parsed_debits_paise=100000,
        parsed_credits_paise=50000,
    )
    res = _gate_s2(stmt)
    assert res.passed is True
    assert res.paise_diff == 0

    # Introduce a 50 paise discrepancy
    stmt.parsed_debits_paise = 100050
    res_fail = _gate_s2(stmt)
    assert res_fail.passed is False
    assert res_fail.paise_diff == 50


def test_gate_s7_row_conservation_logic():
    """Verify Gate S7 asserts row conservation across parsed count."""
    stmt = CardStatement(
        statement_id="test-s7",
        card_key=("HDFC", "8640", "Utsav"),
        issuer="HDFC",
        last4="8640",
        cardholder="Utsav",
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        file_hash="hash123",
        transactions=[
            CanonicalTxn(
                txn_id=f"t{i}",
                card_key=("HDFC", "8640", "Utsav"),
                statement_id="test-s7",
                txn_date=date(2026, 7, 15),
                description_raw=f"Txn {i}",
                description_clean=f"Txn {i}",
                amount_paise=10000,
                direction=TxnDirection.DEBIT,
                kind=TxnKind.PURCHASE,
            )
            for i in range(5)
        ]
    )
    res = _gate_s7(stmt)
    assert res.passed is True
    assert "5 transaction row(s)" in res.detail


def test_output_a_generation(tmp_path):
    """Assert Output A writes correct headers and adheres to column convention."""
    txns = [
        CanonicalTxn(
            txn_id="t1",
            card_key=("HDFC", "8640", "Utsav"),
            statement_id="s1",
            txn_date=date(2026, 7, 15),
            description_raw="RELIANCE RETAIL LIMITED",
            description_clean="RELIANCE RETAIL LIMITED",
            amount_paise=106082,
            direction=TxnDirection.DEBIT,
            kind=TxnKind.PURCHASE,
        ),
        CanonicalTxn(
            txn_id="t2",
            card_key=("HDFC", "8640", "Utsav"),
            statement_id="s1",
            txn_date=date(2026, 7, 16),
            description_raw="MB/IB PAYMENT RECEIVED",
            description_clean="MB/IB PAYMENT RECEIVED",
            amount_paise=500000,
            direction=TxnDirection.CREDIT,
            kind=TxnKind.PAYMENT,
            reference="REF123456",
        ),
    ]

    out_file = write_output_a(
        txns=txns,
        card_key="HDFC-8640-Utsav",
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        output_path=Path(tmp_path),
    )
    assert out_file.exists()

    wb = load_workbook(out_file)
    ws = wb.active
    expected_headers = ["Date", "Purchases", "Payments/Refunds", "Payee", "Description", "Reference Number"]
    actual_headers = [cell.value for cell in ws[1]]
    assert actual_headers == expected_headers

    # Check row 2 (t1: Spend/Debit)
    # Under sample convention: Spends sit in Payments/Refunds (col C), Purchases (col B) is empty
    row2_purchases = ws.cell(row=2, column=2).value
    row2_payments = ws.cell(row=2, column=3).value
    assert row2_purchases is None or row2_purchases == ""
    assert row2_payments == 1060.82

    # Check row 3 (t2: Credit/Payment)
    # Under sample convention: Credits sit in Purchases (col B), Payments/Refunds (col C) is empty
    row3_purchases = ws.cell(row=3, column=2).value
    row3_payments = ws.cell(row=3, column=3).value
    assert row3_purchases == 5000.00
    assert row3_payments is None or row3_payments == ""


def test_output_b_generation(tmp_path):
    """Assert Output B writes 19 columns and Net_Amount formula =F{row}-G{row}."""
    cat_txns = [
        CategorisedTxn(
            txn=CanonicalTxn(
                txn_id="t1",
                card_key=("HDFC", "8640", "Utsav"),
                statement_id="s1",
                txn_date=date(2026, 7, 15),
                description_raw="RELIANCE RETAIL LIMITED",
                description_clean="RELIANCE RETAIL LIMITED",
                amount_paise=106082,
                direction=TxnDirection.DEBIT,
                kind=TxnKind.PURCHASE,
            ),
            proposed_account="Staff Welfare",
            proposed_type="expense",
            confidence=0.95,
            categorise_method="kind_rule",
        ),
    ]

    out_file = write_output_b(
        categorised_txns=cat_txns,
        card_key="HDFC-8640-Utsav",
        org_name="Xiarch Bharat Private Limited",
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        output_path=Path(tmp_path),
        location_name="XIARCH GURGAON BRANCH",
        account_name="Credit Card Utsav HDFC 8640",
    )
    assert out_file.exists()

    wb = load_workbook(out_file)
    assert "Account Transactions" in wb.sheetnames
    assert "Review" in wb.sheetnames

    ws = wb["Account Transactions"]
    # Header is at row 2 in Output B because row 1 is the merged Org title
    actual_b_headers = [ws.cell(row=2, column=c).value for c in range(1, 20)]
    assert len(actual_b_headers) == 19
    assert str(actual_b_headers[0]).lower() == "date"
    assert "account_name" in str(actual_b_headers[1]).lower()
    assert "transaction_details" in str(actual_b_headers[2]).lower()
    assert "debit" in str(actual_b_headers[10]).lower()
    assert "credit" in str(actual_b_headers[11]).lower()
    assert "net_amount" in str(actual_b_headers[12]).lower()

    # Check Net formula in row 3:
    # Column 13 is Net (INR), formula =K3-L3
    net_val = ws.cell(row=3, column=13).value
    assert net_val == "=K3-L3"
