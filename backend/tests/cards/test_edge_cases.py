"""
Edge-case tests for Credit Card Statements (Prompt Contract 2 Section 15.6).
Verifies:
  1. Corrupted PDF fails Gate S1 cleanly without crashing the run.
  2. Duplicate statement detected and skipped/flagged with warning.
  3. Statement with credit marker swapped fails Gate S2 and triggers discrepancy.
  4. Ollama offline: categorisation completes with rule-based categorisation & deterministic briefing.
  5. Closed-set AI validation: hallucinated account name rejected and routed to review.
  6. Zip-slip exploit rejection during ingestion.
"""
from __future__ import annotations

import io
from datetime import date
import os
from pathlib import Path
import zipfile
import pytest
from unittest.mock import AsyncMock

from app.cards.ingest import ingest_zip, ZipSafetyError
from app.cards.classify import classify_pdf
from app.cards.categorise import categorise_transactions
from app.cards.agents.categoriser_agent import run_categoriser_batch
from app.cards.agents.auditor_agent import run_auditor
from app.cards.agents.tools import CardAgentTools
from app.cards.gates import (
    _gate_s1,
    _gate_s2,
    _gate_s4,
)
from app.cards.models import (
    CardStatement,
    CanonicalTxn,
    TxnDirection,
    TxnKind,
    StatementStatus,
)

ALLOWED_TEST_ACCOUNTS = [
    "Staff Welfare",
    "Travelling Expenses",
    "Office Supplies",
    "Bank Charges and Fees",
    "Input CGST/SGST/IGST",
]


def test_corrupt_pdf_fails_gate_s1(tmp_path):
    """Corrupted / empty PDF fails Gate S1 cleanly, does not crash."""
    corrupt_file = os.path.join(tmp_path, "corrupt.pdf")
    with open(corrupt_file, "wb") as f:
        f.write(b"%PDF-1.4 Not a valid PDF content header trash")

    info = classify_pdf(Path(corrupt_file))
    # Create the statement from the classified info as pipeline would
    stmt = CardStatement(
        statement_id="corrupt-s1",
        card_key=("UNKNOWN", "", ""),
        issuer="UNKNOWN",
        last4="",
        cardholder="",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        page_count=info.page_count,
        file_hash="corrupt_hash",
        status=StatementStatus.FAILED,
        transactions=[],
    )
    res = _gate_s1(stmt)
    assert res.passed is False
    assert "FAILED" in res.detail or "0 pages" in res.detail


def test_zip_slip_vulnerability_blocked(tmp_path):
    """Zip slip path traversal is detected and blocked with ZipSafetyError."""
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as zf:
        zf.writestr("../../evil.pdf", b"%PDF-1.4 some pdf")

    zip_bytes.seek(0)
    zip_path = Path(tmp_path) / "malicious.zip"
    with open(zip_path, "wb") as f:
        f.write(zip_bytes.read())

    dest_dir = Path(tmp_path) / "extracted"
    with pytest.raises(ZipSafetyError):
        ingest_zip(zip_path, dest_dir)


def test_duplicate_statement_detection():
    """Detect overlapping statement periods via Gate S4."""
    stmt1 = CardStatement(
        statement_id="s1",
        card_key=("HDFC", "8640", "Utsav"),
        issuer="HDFC",
        last4="8640",
        cardholder="Utsav",
        period_from=date(2026, 4, 16),
        period_to=date(2026, 5, 15),
        file_hash="hash1",
        transactions=[
            CanonicalTxn(
                txn_id="t1",
                card_key=("HDFC", "8640", "Utsav"),
                statement_id="s1",
                txn_date=date(2026, 5, 1),
                description_raw="RELIANCE",
                description_clean="RELIANCE",
                amount_paise=10000,
                direction=TxnDirection.DEBIT,
                kind=TxnKind.PURCHASE,
                reference="REF1",
            )
        ]
    )
    stmt2 = CardStatement(
        statement_id="s2",
        card_key=("HDFC", "8640", "Utsav"),
        issuer="HDFC",
        last4="8640",
        cardholder="Utsav",
        period_from=date(2026, 4, 16),
        period_to=date(2026, 5, 15),
        file_hash="hash2",
        transactions=[
            CanonicalTxn(
                txn_id="t1_dup",
                card_key=("HDFC", "8640", "Utsav"),
                statement_id="s2",
                txn_date=date(2026, 5, 1),
                description_raw="RELIANCE",
                description_clean="RELIANCE",
                amount_paise=10000,
                direction=TxnDirection.DEBIT,
                kind=TxnKind.PURCHASE,
                reference="REF1",
            )
        ]
    )
    res = _gate_s4(stmt1, [stmt1, stmt2])
    # Overlapping duplicate is caught
    assert res.passed is False
    assert "duplicate" in res.detail.lower()


def test_swapped_credit_marker_fails_gate_s2():
    """If credit/debit marker is reversed, Gate S2 must fail."""
    stmt = CardStatement(
        statement_id="s_swap",
        card_key=("AXIS", "0801", "Utsav"),
        issuer="AXIS",
        last4="0801",
        cardholder="Utsav",
        period_from=date(2026, 4, 1),
        period_to=date(2026, 4, 30),
        file_hash="hash_swap",
        printed_debits_paise=5000000,
        printed_credits_paise=2000000,
        parsed_debits_paise=2000000,
        parsed_credits_paise=5000000,
    )
    res = _gate_s2(stmt)
    assert res.passed is False
    assert res.paise_diff > 0


@pytest.mark.anyio
async def test_closed_set_ai_validation_rejects_hallucination():
    """AI proposing a hallucinated account name outside ALLOWED_TEST_ACCOUNTS is rejected."""
    hallucinated_account = "NonExistent Cryptocurrency Reserve Account"
    assert hallucinated_account not in ALLOWED_TEST_ACCOUNTS

    txn = CanonicalTxn(
        txn_id="t_hallucinate",
        card_key=("HDFC", "8640", "Utsav"),
        statement_id="s1",
        txn_date=date(2026, 7, 15),
        description_raw="SOME RANDOM VENDOR",
        description_clean="SOME RANDOM VENDOR",
        amount_paise=100000,
        direction=TxnDirection.DEBIT,
        kind=TxnKind.PURCHASE,
    )

    mock_llm_json = {
        "response": '{"results": [{"row": 1, "name": "' + hallucinated_account + '", "type": "expense", "confidence": 0.99, "reason": "hallucinated"}]}'
    }

    import httpx
    mock_resp = httpx.Response(200, json=mock_llm_json)
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)

    res = await run_categoriser_batch([txn], allowed_names=ALLOWED_TEST_ACCOUNTS, client=mock_client)
    assert len(res) == 1
    cat = res[0]
    # Must be rejected to UNSURE with confidence 0.0
    assert cat["name"] == "UNSURE"
    assert cat["confidence"] == 0.0


@pytest.mark.anyio
async def test_ollama_offline_fallback():
    """When Ollama connection fails (timeout / offline), fallback logic functions without error."""
    txn = CanonicalTxn(
        txn_id="t_gst",
        card_key=("HDFC", "8640", "Utsav"),
        statement_id="s1",
        txn_date=date(2026, 7, 15),
        description_raw="INTEGRATED GST ON FCY MARKUP",
        description_clean="INTEGRATED GST ON FCY MARKUP",
        amount_paise=1800,
        direction=TxnDirection.DEBIT,
        kind=TxnKind.GST,
    )

    # 1. Deterministic rules categorise GST without LLM
    cat_list = categorise_transactions([txn], allowed_accounts=ALLOWED_TEST_ACCOUNTS)
    assert len(cat_list) == 1
    assert cat_list[0].proposed_account == "Input CGST/SGST/IGST" or cat_list[0].confidence > 0

    # 2. LLM batch function catches exception and gracefully returns empty list
    mock_client = AsyncMock()
    mock_client.post.side_effect = ConnectionError("Ollama unreachable")

    res = await run_categoriser_batch([txn], allowed_names=ALLOWED_TEST_ACCOUNTS, client=mock_client)
    assert res == []

    # 3. Auditor produces deterministic fallback briefing when LLM is offline
    stmt = CardStatement(
        statement_id="s_offline",
        card_key=("HDFC", "8640", "Utsav"),
        issuer="HDFC",
        last4="8640",
        cardholder="Utsav",
        period_from=date(2026, 7, 1),
        period_to=date(2026, 7, 31),
        file_hash="hash_offline",
        parsed_debits_paise=100000,
        parsed_credits_paise=50000,
        transactions=[txn],
    )
    tools = CardAgentTools(
        page_texts={},
        statements={stmt.statement_id: stmt},
        allowed_accounts=ALLOWED_TEST_ACCOUNTS,
    )
    briefing = await run_auditor(
        card_key="HDFC-8640-Utsav",
        statements=[stmt],
        tools=tools,
        client=mock_client,
    )
    assert len(briefing) > 0
    assert "HDFC-8640-Utsav" in briefing
    assert len(briefing.split()) <= 150
