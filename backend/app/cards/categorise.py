"""
Categorisation pipeline for card transactions.

Three-stage pipeline:
  1. KindRules   – pattern-based classification on description + TxnKind
  2. HistoryLookup – fuzzy match against historical reference rows
  3. UserKeywordRules – editable keyword → account mapping

Anything with final confidence < 0.85 is marked for review.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import of rapidfuzz (optional dependency)
# ---------------------------------------------------------------------------

try:
    from rapidfuzz import fuzz, process as rfuzz_process  # type: ignore
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:  # pragma: no cover
    _RAPIDFUZZ_AVAILABLE = False
    logger.warning("rapidfuzz not installed; HistoryLookup will use exact matching only")


# ---------------------------------------------------------------------------
# Local imports — these live in models.py written by backend-core
# ---------------------------------------------------------------------------

from app.cards.models import (
    CanonicalTxn,
    TxnDirection,
    TxnKind,
)


# ---------------------------------------------------------------------------
# CategorisedTxn — CanonicalTxn + categorisation fields
# ---------------------------------------------------------------------------

@dataclass
class CategorisedTxn:
    """
    Wraps a CanonicalTxn and adds categorisation output.

    categorise_method is one of:
      "kind_rule" | "history" | "user_rule" | "ai" | "review"
    """

    txn: CanonicalTxn
    proposed_account: str = ""
    proposed_type: str = ""
    confidence: float = 0.0
    categorise_method: str = "review"

    # Pass-through convenience properties
    @property
    def txn_id(self) -> str:
        return self.txn.txn_id

    @property
    def description(self) -> str:
        return self.txn.description_clean or self.txn.description_raw

    @property
    def amount_paise(self) -> int:
        return self.txn.amount_paise

    @property
    def direction(self) -> TxnDirection:
        return self.txn.direction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lower-case, collapse whitespace, strip punctuation for matching."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Stage 1 – KindRules
# ---------------------------------------------------------------------------

# Each rule: (compiled_regex, matched_kinds_or_None, direction_or_None, account, txn_type, kind)
# direction_or_None means "only match if direction is this"

_KIND_RULE_PATTERNS: list[tuple] = [
    # GST / IGST
    (re.compile(r"\b(gst|igst)\b", re.I), None, None, "Bank Fees and Charges", "expense", TxnKind.GST),
    # Debit interest / finance charge
    (re.compile(r"\b(debit interest|finance charge)\b", re.I), None, None, "Bank Fees and Charges", "expense", TxnKind.INTEREST),
    # Annual fee
    (re.compile(r"\bannual fee\b", re.I), None, None, "Bank Fees and Charges", "expense", TxnKind.FEE),
    # FCY markup / consolidated FCY
    (re.compile(r"\b(fcy markup|consolidated fcy|foreign currency markup)\b", re.I), None, None, "Bank Fees and Charges", "expense", TxnKind.FX_MARKUP),
    # Petrol surcharge waiver (credit)
    (re.compile(r"\b(petro surcharge|petrol surcharge)\b", re.I), None, TxnDirection.CREDIT, "Bank Fees and Charges", "expense_refund", TxnKind.SURCHARGE_WAIVER),
    # Petrol surcharge fee (debit)
    (re.compile(r"\b(petro surcharge|petrol surcharge)\b", re.I), None, TxnDirection.DEBIT, "Bank Fees and Charges", "expense", TxnKind.FEE),
    # Payments: credit card payment / autopay / MB payment / BBPS
    (re.compile(r"\b(credit card payment|autopay|mb payment|mb/ib payment|bbps payment|bbps)\b", re.I), None, None, "", "card_payment", TxnKind.PAYMENT),
]


class KindRules:
    """
    Pattern-based classifier.

    Runs the fixed regex table against description_raw (and description_clean).
    Returns the first match, or (None, None, 0.0) if no match.
    """

    def classify(
        self, txn: CanonicalTxn
    ) -> tuple[Optional[str], Optional[str], float]:
        """
        Returns (proposed_account, proposed_type, confidence).
        confidence is 1.0 on a kind-rule hit, 0.0 on no match.
        """
        text = txn.description_raw + " " + txn.description_clean

        for pattern, _kinds, direction_filter, account, txn_type, kind in _KIND_RULE_PATTERNS:
            if direction_filter is not None and txn.direction != direction_filter:
                continue
            if pattern.search(text):
                # Override the txn kind in-place so downstream can use it
                txn.kind = kind
                return account, txn_type, 1.0

        return None, None, 0.0


# ---------------------------------------------------------------------------
# Stage 2 – HistoryLookup
# ---------------------------------------------------------------------------

class HistoryLookup:
    """
    Fuzzy-matches transaction descriptions against historical reference rows.

    reference_rows: list of dicts with at minimum:
      { "description": str, "account": str, "type": str }
    """

    _THRESHOLD = 85  # rapidfuzz score threshold (0-100)

    def __init__(self, reference_rows: list[dict]) -> None:
        self._rows = reference_rows
        # Pre-compute normalised descriptions for fast lookup
        self._choices: list[str] = [_normalise(r.get("description", "")) for r in reference_rows]

    def classify(
        self, txn: CanonicalTxn
    ) -> tuple[Optional[str], Optional[str], float]:
        """
        Returns (proposed_account, proposed_type, confidence) or (None, None, 0.0).
        """
        if not self._rows:
            return None, None, 0.0

        query = _normalise(txn.description_clean or txn.description_raw)
        if not query:
            return None, None, 0.0

        if _RAPIDFUZZ_AVAILABLE:
            result = rfuzz_process.extractOne(
                query,
                self._choices,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=self._THRESHOLD,
            )
            if result is None:
                return None, None, 0.0
            best_text, score, idx = result
        else:
            # Exact match fallback
            best_score = 0
            best_idx = -1
            for i, choice in enumerate(self._choices):
                if choice == query:
                    best_score = 100
                    best_idx = i
                    break
            if best_idx < 0:
                return None, None, 0.0
            score, idx = best_score, best_idx

        row = self._rows[idx]
        account = row.get("account", "")
        txn_type = row.get("type", "")
        confidence = round(score / 100.0, 4)
        return account, txn_type, confidence


# ---------------------------------------------------------------------------
# Stage 3 – UserKeywordRules
# ---------------------------------------------------------------------------

class UserKeywordRules:
    """
    User-editable keyword → account/type mapping.

    rules: list of dicts:
      { "keyword": str, "account": str, "type": str, "confidence": float }
    """

    def __init__(self, rules: list[dict]) -> None:
        self._rules: list[tuple[re.Pattern, str, str, float]] = []
        for rule in rules:
            kw = rule.get("keyword", "")
            if not kw:
                continue
            pattern = re.compile(re.escape(kw), re.I)
            account = rule.get("account", "")
            txn_type = rule.get("type", "")
            confidence = float(rule.get("confidence", 0.9))
            self._rules.append((pattern, account, txn_type, confidence))

    def classify(
        self, txn: CanonicalTxn
    ) -> tuple[Optional[str], Optional[str], float]:
        """
        Returns (proposed_account, proposed_type, confidence) or (None, None, 0.0).
        """
        text = txn.description_raw + " " + txn.description_clean
        for pattern, account, txn_type, confidence in self._rules:
            if pattern.search(text):
                return account, txn_type, confidence
        return None, None, 0.0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

REVIEW_THRESHOLD = 0.85


def categorise_transactions(
    txns: list[CanonicalTxn],
    reference_rows: Optional[list[dict]] = None,
    user_rules: Optional[list[dict]] = None,
    allowed_accounts: Optional[list[str]] = None,
) -> list[CategorisedTxn]:
    """
    Categorise a list of CanonicalTxn objects.

    Pipeline order:
      1. KindRules
      2. HistoryLookup
      3. UserKeywordRules
      4. review (confidence < REVIEW_THRESHOLD)

    Parameters
    ----------
    txns:
        Parsed transactions.
    reference_rows:
        Historical export rows for fuzzy matching.
    user_rules:
        User-defined keyword rules.
    allowed_accounts:
        Whitelist of valid account names; enforced at the end.

    Returns
    -------
    list[CategorisedTxn]
    """
    kind_rules = KindRules()
    history = HistoryLookup(reference_rows or [])
    user_kw = UserKeywordRules(user_rules or [])

    allowed_set = set(allowed_accounts or [])

    results: list[CategorisedTxn] = []

    for txn in txns:
        cat = CategorisedTxn(txn=txn)

        # Stage 1 – KindRules
        account, txn_type, conf = kind_rules.classify(txn)
        if conf > 0:
            cat.proposed_account = account
            cat.proposed_type = txn_type
            cat.confidence = conf
            cat.categorise_method = "kind_rule"

        # Stage 2 – HistoryLookup (only if not already categorised at high confidence)
        if cat.confidence < REVIEW_THRESHOLD:
            account2, txn_type2, conf2 = history.classify(txn)
            if conf2 > cat.confidence:
                cat.proposed_account = account2
                cat.proposed_type = txn_type2
                cat.confidence = conf2
                cat.categorise_method = "history"

        # Stage 3 – UserKeywordRules
        if cat.confidence < REVIEW_THRESHOLD:
            account3, txn_type3, conf3 = user_kw.classify(txn)
            if conf3 > cat.confidence:
                cat.proposed_account = account3
                cat.proposed_type = txn_type3
                cat.confidence = conf3
                cat.categorise_method = "user_rule"

        # Enforce allowed_accounts whitelist
        if cat.proposed_account and allowed_set and cat.proposed_account not in allowed_set:
            logger.debug(
                "Account '%s' not in allowed_accounts; marking for review",
                cat.proposed_account,
            )
            cat.confidence = min(cat.confidence, 0.5)

        # Final review gate
        if cat.confidence < REVIEW_THRESHOLD:
            cat.categorise_method = "review"

        results.append(cat)

    return results
