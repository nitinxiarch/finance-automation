"""
Card Statements — canonical data models.

Column convention note:
  statement_column_convention = "HDFC_SAMPLE"

  HDFC quirk: in the printed statement, *spend* transactions appear in the
  "Payments/Refunds" column and *credits* (cashbacks, surcharge waivers, refunds)
  appear in the "Purchases" column.  This is the OPPOSITE of what you would expect.
  All parsers must honour this convention when populating OutputARow.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import NamedTuple, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Module-level column convention flag
# ---------------------------------------------------------------------------

# HDFC quirk: spends go in Payments/Refunds column, credits go in Purchases column.
# Keep this string here so exporters can branch on it without importing the whole
# parser stack.
statement_column_convention: str = "HDFC_SAMPLE"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TxnKind(str, Enum):
    PURCHASE = "PURCHASE"
    PAYMENT = "PAYMENT"
    REFUND = "REFUND"
    CASHBACK = "CASHBACK"
    FEE = "FEE"
    INTEREST = "INTEREST"
    GST = "GST"
    EMI_PRINCIPAL = "EMI_PRINCIPAL"
    EMI_INTEREST = "EMI_INTEREST"
    FX_MARKUP = "FX_MARKUP"
    SURCHARGE_WAIVER = "SURCHARGE_WAIVER"
    REVERSAL = "REVERSAL"


class TxnDirection(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class ParseTier(str, Enum):
    T1 = "T1"  # native text layer, high confidence
    T2 = "T2"  # native text layer, structural heuristics
    T3 = "T3"  # OCR-assisted
    T4 = "T4"  # AI-assisted / low confidence


class StatementStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PENDING = "PENDING"


class CardRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# CardKey identity tuple
# ---------------------------------------------------------------------------

class CardKey(NamedTuple):
    issuer: str
    last4: str
    cardholder: str

    def __str__(self) -> str:  # noqa: D105
        return f"{self.issuer}-{self.last4}-{self.cardholder}"


# ---------------------------------------------------------------------------
# GateResult  (declared before CardStatement so it can be referenced)
# ---------------------------------------------------------------------------

class GateResult(BaseModel):
    gate: str                   # e.g. "S1", "S2"
    passed: bool
    detail: str = ""
    paise_diff: int = 0

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# CanonicalTxn
# ---------------------------------------------------------------------------

class CanonicalTxn(BaseModel):
    """A single parsed and normalised credit-card transaction."""

    # Identity / provenance
    txn_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    card_key: tuple[str, str, str]        # (issuer, last4, cardholder)
    statement_id: str

    # Date & description
    txn_date: date
    description_raw: str
    description_clean: str

    # Monetary
    amount_paise: int = Field(..., gt=0, description="Always positive; direction stored separately")
    direction: TxnDirection

    # Classification
    kind: TxnKind

    # Optional FX fields
    foreign_amount: Optional[Decimal] = None
    foreign_currency: Optional[str] = None      # ISO 4217 e.g. "USD"

    # Reference
    reference: str = ""

    # Source provenance: file_hash, page number, line number, bounding-box
    source: dict = Field(
        default_factory=dict,
        description="Keys: file_hash (str), page (int), line (int), bbox (list[float]|None)",
    )

    # Parse quality
    tier: ParseTier = ParseTier.T1
    parse_confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# CardStatement
# ---------------------------------------------------------------------------

class CardStatement(BaseModel):
    """All parsed data for one credit-card statement PDF."""

    statement_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    card_key: tuple[str, str, str]        # (issuer, last4, cardholder)

    # Denormalised for easy access
    issuer: str
    last4: str
    cardholder: str

    # Period
    period_from: date
    period_to: date
    page_count: int = 0

    # Provenance
    file_hash: str
    tier: ParseTier = ParseTier.T1
    status: StatementStatus = StatementStatus.PENDING

    # Transactions
    transactions: list[CanonicalTxn] = Field(default_factory=list)

    # Printed totals (from summary page)
    printed_debits_paise: int = 0
    printed_credits_paise: int = 0

    # Computed totals (sum of parsed transactions)
    parsed_debits_paise: int = 0
    parsed_credits_paise: int = 0

    # Gate results (populated by run_all_gates)
    gate_results: list[GateResult] = Field(default_factory=list)

    # Balances (may not be present on all issuers)
    opening_balance_paise: Optional[int] = None
    closing_balance_paise: Optional[int] = None

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# CardRegistryEntry & CardRegistry
# ---------------------------------------------------------------------------

class CardRegistryEntry(BaseModel):
    """Mapping of a physical card to its books account."""

    display_name: str
    books_account: str              # Zoho Books account name/code
    issuer: str
    last4: str
    cardholder: str

    model_config = {"frozen": False}


class CardRegistry(BaseModel):
    """
    Registry of all known cards, keyed by "{issuer}-{last4}-{cardholder}".
    """

    entries: dict[str, CardRegistryEntry] = Field(default_factory=dict)

    model_config = {"frozen": False}

    def get(self, card_key: CardKey) -> Optional[CardRegistryEntry]:
        return self.entries.get(str(card_key))

    def register(self, entry: CardRegistryEntry) -> None:
        key = f"{entry.issuer}-{entry.last4}-{entry.cardholder}"
        self.entries[key] = entry


# ---------------------------------------------------------------------------
# ReviewItem
# ---------------------------------------------------------------------------

class ReviewItem(BaseModel):
    """A transaction flagged for human review."""

    txn_id: str
    reason: str                     # machine-readable reason code
    proposed_account: str = ""
    proposed_type: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason_text: str = ""           # human-readable explanation

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# CardRun
# ---------------------------------------------------------------------------

class CardRun(BaseModel):
    """
    Top-level container for one end-to-end card reconciliation run.
    """

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: CardRunStatus = CardRunStatus.PENDING
    zip_hash: str = ""

    statements: list[CardStatement] = Field(default_factory=list)
    registry: CardRegistry = Field(default_factory=CardRegistry)

    # {card_key_str: {month_str: StatementStatus_str}}
    coverage_matrix: dict = Field(default_factory=dict)

    review_queue: list[ReviewItem] = Field(default_factory=list)

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Output row models
# ---------------------------------------------------------------------------

class OutputARow(BaseModel):
    """
    One row in Output A (card-statement export, simplified ledger style).

    Column convention (HDFC_SAMPLE):
      - Spends  → payments_refunds column
      - Credits → purchases column
    """

    date: date
    purchases: Optional[Decimal] = None          # credits in HDFC convention
    payments_refunds: Optional[Decimal] = None   # debits/spends in HDFC convention
    payee: str = ""
    description: str = ""
    reference_number: str = ""

    model_config = {"frozen": False}


class OutputBRow(BaseModel):
    """
    One row in Output B — matches the Account_Transactions_2(1).xls export from
    Zoho Books exactly (20 columns, row-1 header names used as field names).

    Column order:
      date, account_name, transaction_details, transaction_id,
      reference_transaction_id, offset_account_id, offset_account_type,
      transaction_type, reference_number, entity_number,
      debit, credit, net_amount, contact_id, account_id,
      running_balance, currency_code, account_group, account_type, location_name
    """

    date: Optional[date] = None
    account_name: str = ""
    transaction_details: str = ""
    transaction_id: str = ""
    reference_transaction_id: str = ""
    offset_account_id: str = ""
    offset_account_type: str = ""
    transaction_type: str = ""
    reference_number: str = ""
    entity_number: str = ""
    debit: Optional[Decimal] = None
    credit: Optional[Decimal] = None
    net_amount: Optional[Decimal] = None
    contact_id: str = ""
    account_id: str = ""
    running_balance: Optional[Decimal] = None
    currency_code: str = "INR"
    account_group: str = ""
    account_type: str = ""
    location_name: str = ""

    model_config = {"frozen": False}
