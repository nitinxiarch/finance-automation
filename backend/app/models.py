"""
Core Pydantic models and typed row objects for the reconciliation engine.
All monetary amounts stored as integer paise (1 INR = 100 paise).
"""
from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class MatchLayer(str, Enum):
    L1_EXACT = "L1_EXACT"
    L2_REFERENCE_GROUP = "L2_REFERENCE_GROUP"
    L3_AGGREGATE = "L3_AGGREGATE"
    L4_FUZZY = "L4_FUZZY"
    L5_RESIDUAL = "L5_RESIDUAL"


class ExceptionCode(str, Enum):
    BANK_ONLY = "BANK_ONLY"
    BOOKS_ONLY = "BOOKS_ONLY"
    DOUBLE_COUNT = "DOUBLE_COUNT"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH"
    SIGN_FLIP = "SIGN_FLIP"
    DUPLICATE_REF = "DUPLICATE_REF"
    TIMING = "TIMING"
    ROUNDING = "ROUNDING"
    UNEXPLAINED = "UNEXPLAINED"


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    RECONCILED = "RECONCILED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    FAILED_ACCURACY_GATE = "FAILED_ACCURACY_GATE"
    FAILED = "FAILED"


class OwnerTeam(str, Enum):
    SALES = "Sales"
    FINANCE = "Finance"
    TREASURY = "Treasury"
    HR_PAYROLL = "HR-Payroll"


class Priority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


# ---------------------------------------------------------------------------
# Raw parsed rows
# ---------------------------------------------------------------------------

class BankRow(BaseModel):
    """One row from the ICICI bank statement."""
    row_id: str  # B-0001, B-0002, ...
    sn: int
    tran_id: str
    value_date: date
    transaction_date: date
    transaction_posted_date: date
    cheque_ref: str = ""
    remarks: str = ""
    withdrawal_paise: int = 0   # always >= 0
    deposit_paise: int = 0      # always >= 0
    balance_paise: int = 0

    # Derived: signed_paise = deposit - withdrawal (positive = inflow)
    @property
    def signed_paise(self) -> int:
        return self.deposit_paise - self.withdrawal_paise

    @property
    def is_withdrawal(self) -> bool:
        return self.withdrawal_paise > 0


class BooksRow(BaseModel):
    """One row from the Zoho books export."""
    row_id: str  # K-0001, K-0002, ...
    txn_date: date
    account_name: str = ""
    transaction_details: str = ""
    transaction_id: str = ""
    reference_transaction_id: str = ""
    offset_account_id: str = ""
    offset_account_type: str = ""
    transaction_type: str = ""
    reference_number: str = ""   # maps to bank Tran. Id
    entity_number: str = ""
    debit_paise: int = 0         # money INTO bank (matches bank Deposit)
    credit_paise: int = 0        # money OUT of bank (matches bank Withdrawal)
    net_paise: int = 0           # debit - credit
    contact_id: str = ""
    account_id: str = ""
    currency_code: str = "INR"
    account_group: str = ""
    account_type: str = ""
    location_name: str = ""

    @property
    def signed_paise(self) -> int:
        return self.net_paise  # positive = inflow (debit)


class BankStatement(BaseModel):
    """Parsed bank statement with metadata and footer totals."""
    account_name: str
    account_number: str
    currency: str
    period_from: date
    period_to: date
    ifsc: str
    rows: list[BankRow]
    footer_opening_paise: int
    footer_withdrawals_paise: int
    footer_deposits_paise: int
    footer_closing_paise: int


class BooksExport(BaseModel):
    """Parsed Zoho books export."""
    organisation: str
    period_from: date
    period_to: date
    rows: list[BooksRow]


# ---------------------------------------------------------------------------
# Engine output models
# ---------------------------------------------------------------------------

class MatchRecord(BaseModel):
    """One match between bank and books rows."""
    match_id: str
    layer: MatchLayer
    bank_row_ids: list[str]
    books_row_ids: list[str]
    bank_total_paise: int
    books_total_paise: int
    confidence: float = 1.0  # < 1.0 for L4 fuzzy
    rationale: str = ""
    is_group_match: bool = False  # yellow fill in workbook


class ExceptionRecord(BaseModel):
    """One flagged / unexplained item."""
    exception_id: str
    code: ExceptionCode
    bank_row_ids: list[str] = Field(default_factory=list)
    books_row_ids: list[str] = Field(default_factory=list)
    amount_paise: int
    evidence: str = ""
    owner_team: Optional[OwnerTeam] = None
    status: str = "open"
    # filled by AI layer
    ai_card: Optional[SalesActionCard] = None
    ai_verified: bool = False
    needs_human: bool = False


class GateResult(BaseModel):
    gate: str
    passed: bool
    detail: str = ""
    amount_paise: int = 0
    rows: list[str] = Field(default_factory=list)


class ReconciliationResult(BaseModel):
    run_id: str
    status: RunStatus
    bank_opening_paise: int
    bank_closing_paise: int
    books_opening_paise: int  # user-supplied or computed
    books_closing_paise: int
    bank_total_deposits_paise: int
    bank_total_withdrawals_paise: int
    books_total_debits_paise: int
    books_total_credits_paise: int
    matches: list[MatchRecord]
    exceptions: list[ExceptionRecord]
    gate_results: list[GateResult]
    all_gates_passed: bool
    identity_ok: bool
    result_hash: str = ""


# ---------------------------------------------------------------------------
# AI / agent models
# ---------------------------------------------------------------------------

class FactRef(BaseModel):
    fact_id: str
    source_tool_call_id: str


class RecommendedAction(BaseModel):
    step: str
    who: str
    due_in_days: int


class SalesActionCard(BaseModel):
    exception_id: str
    code: str
    owner_team: OwnerTeam
    priority: Priority
    summary: str = Field(..., max_length=200)
    facts_used: list[FactRef] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    customer_message_draft: Optional[str] = None
    needs_human: bool = False


class JudgeVerdict(BaseModel):
    claim: str
    verdict: Literal["supported", "unsupported", "contradicted"]
    fact_id: Optional[str] = None


class JudgeResult(BaseModel):
    exception_id: str
    verdicts: list[JudgeVerdict]
    card_passed: bool


class VerifierResult(BaseModel):
    identity_confirmed: bool
    identity_detail: str
    exception_reviews: list[dict[str, Any]]
    disagreements: list[str]


# ---------------------------------------------------------------------------
# API models
# ---------------------------------------------------------------------------

class RunCreatedResponse(BaseModel):
    run_id: str
    status: RunStatus


class OverrideRequest(BaseModel):
    action: Literal["accept", "reject"]
    match_id: Optional[str] = None
    bank_row_ids: Optional[list[str]] = None
    books_row_ids: Optional[list[str]] = None
    rationale: str = ""
    user: str = "user"


class HealthResponse(BaseModel):
    api_ok: bool
    llm_tunnel_ok: bool
    llm_model_found: bool
    llm_model_tag: Optional[str] = None
    llm_error: Optional[str] = None


# Fix forward reference
ExceptionRecord.model_rebuild()

