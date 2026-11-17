"""
Matching engine: Layers L1 through L5.

L1 Exact         – same Tran.Id ↔ reference_number, same signed amount, same date (1:1)
L2 Ref Group     – one bank ref → multiple books rows summing to bank amount (1:N)
L3 Aggregate     – unreferenced books lump sums matched to groups of bank rows via subset-sum
L4 Fuzzy         – same amount ±0, date ±3 days, name similarity ≥ 0.85
L5 Residual      – everything unmatched → BANK_ONLY or BOOKS_ONLY

Deterministic tie-breaking throughout.
"""
from __future__ import annotations

import itertools
from collections import defaultdict
from datetime import timedelta
from typing import Optional

from rapidfuzz import fuzz

from app.engine.normalise import category_signal, clean_reference, normalise_text
from app.models import BankRow, BooksRow, ExceptionCode, ExceptionRecord, MatchLayer, MatchRecord


_match_counter = 0


def _new_match_id() -> str:
    global _match_counter
    _match_counter += 1
    return f"M-{_match_counter:04d}"


def reset_match_counter() -> None:
    global _match_counter
    _match_counter = 0


def find_subset_sum(
    target_paise: int,
    candidates: list[BooksRow],
    max_items: int = 12,
    ref_date=None,
) -> Optional[list[BooksRow]]:
    """
    Find the smallest subset of candidate BooksRows whose |signed_paise| sum = target_paise.
    Deterministic: sorts candidates by date proximity (if ref_date given) then row_id before searching.
    Returns None if no solution found.
    """
    if ref_date is not None:
        candidates = sorted(candidates, key=lambda r: (abs((r.txn_date - ref_date).days), r.row_id))
    else:
        candidates = sorted(candidates, key=lambda r: r.row_id)
    # Use absolute values of signed_paise for matching
    candidate_amounts = [abs(r.signed_paise) for r in candidates]

    for size in range(1, min(max_items, len(candidates)) + 1):
        for combo in itertools.combinations(range(len(candidates)), size):
            total = sum(candidate_amounts[i] for i in combo)
            if total == target_paise:
                return [candidates[i] for i in combo]
    return None


# ---------------------------------------------------------------------------
# Layer 1: Exact match (Tran.Id ↔ reference_number, same amount, same date)
# ---------------------------------------------------------------------------

def layer1_exact(
    bank_rows: list[BankRow],
    books_rows: list[BooksRow],
) -> tuple[list[MatchRecord], list[BankRow], list[BooksRow]]:
    """
    Returns (matches, unmatched_bank, unmatched_books).
    One-to-one exact matches only (exactly 1 bank row and 1 books row with this reference).
    """
    matches: list[MatchRecord] = []
    matched_bank: set[str] = set()
    matched_books: set[str] = set()

    # Build lookup: reference_number → list[BooksRow]
    books_by_ref: dict[str, list[BooksRow]] = defaultdict(list)
    for br in books_rows:
        ref = clean_reference(br.reference_number)
        if ref:
            books_by_ref[ref].append(br)

    # Build lookup: tran_id → list[BankRow]
    bank_by_ref: dict[str, list[BankRow]] = defaultdict(list)
    for b in bank_rows:
        tid = clean_reference(b.tran_id)
        if tid:
            bank_by_ref[tid].append(b)

    for bank in sorted(bank_rows, key=lambda r: r.row_id):
        tid = clean_reference(bank.tran_id)
        if not tid or tid in matched_bank:
            continue

        candidates = books_by_ref.get(tid, [])
        # Strictly one-to-one: exactly 1 bank row and 1 books row
        if len(candidates) != 1 or len(bank_by_ref.get(tid, [])) != 1:
            continue

        books = candidates[0]
        if books.row_id in matched_books:
            continue
        # Same signed amount
        if books.signed_paise != bank.signed_paise:
            continue
        # Same value date
        if books.txn_date != bank.value_date:
            continue

        match = MatchRecord(
            match_id=_new_match_id(),
            layer=MatchLayer.L1_EXACT,
            bank_row_ids=[bank.row_id],
            books_row_ids=[books.row_id],
            bank_total_paise=bank.signed_paise,
            books_total_paise=books.signed_paise,
            confidence=1.0,
            rationale="Exact match on Tran.Id, amount, and date.",
        )
        matches.append(match)
        matched_bank.add(bank.row_id)
        matched_books.add(books.row_id)

    unmatched_bank = [r for r in bank_rows if r.row_id not in matched_bank]
    unmatched_books = [r for r in books_rows if r.row_id not in matched_books]
    return matches, unmatched_bank, unmatched_books


# ---------------------------------------------------------------------------
# Layer 2: Reference group (1 bank ref → N books rows, amounts sum)
# ---------------------------------------------------------------------------

def layer2_reference_group(
    bank_rows: list[BankRow],
    books_rows: list[BooksRow],
) -> tuple[list[MatchRecord], list[ExceptionRecord], list[BankRow], list[BooksRow]]:
    """
    One bank row matched to multiple books rows with the same reference_number.
    If a single books row already equals the bank amount AND more rows also sum to it
    → flag the extras as DOUBLE_COUNT, keep the single-row match.
    """
    matches: list[MatchRecord] = []
    exceptions: list[ExceptionRecord] = []
    matched_bank: set[str] = set()
    matched_books: set[str] = set()

    books_by_ref: dict[str, list[BooksRow]] = defaultdict(list)
    for br in books_rows:
        ref = clean_reference(br.reference_number)
        if ref:
            books_by_ref[ref].append(br)

    exc_counter = [0]

    def new_exc_id() -> str:
        exc_counter[0] += 1
        return f"EX-{exc_counter[0]:04d}"

    for bank in sorted(bank_rows, key=lambda r: r.row_id):
        tid = clean_reference(bank.tran_id)
        if not tid or bank.row_id in matched_bank:
            continue

        ref_books = [b for b in books_by_ref.get(tid, []) if b.row_id not in matched_books]
        if not ref_books:
            continue

        bank_signed = bank.signed_paise

        # Check if any single row matches exactly
        single_matches = [b for b in ref_books if b.signed_paise == bank_signed]
        all_sum = sum(abs(b.signed_paise) for b in ref_books)
        group_sum = sum(b.signed_paise for b in ref_books)

        if single_matches and abs(all_sum) != abs(bank_signed):
            # Single match exists AND other rows also exist → DOUBLE_COUNT on extras
            best = sorted(single_matches, key=lambda r: r.row_id)[0]
            extras = [b for b in ref_books if b.row_id != best.row_id]

            match = MatchRecord(
                match_id=_new_match_id(),
                layer=MatchLayer.L2_REFERENCE_GROUP,
                bank_row_ids=[bank.row_id],
                books_row_ids=[best.row_id],
                bank_total_paise=bank_signed,
                books_total_paise=best.signed_paise,
                confidence=1.0,
                rationale=f"Single books row matches bank amount; {len(extras)} extra(s) flagged DOUBLE_COUNT.",
            )
            matches.append(match)
            matched_bank.add(bank.row_id)
            matched_books.add(best.row_id)

            extra_sum = sum(abs(extra.signed_paise) for extra in extras)
            exceptions.append(ExceptionRecord(
                exception_id=new_exc_id(),
                code=ExceptionCode.DOUBLE_COUNT,
                bank_row_ids=[],
                books_row_ids=[extra.row_id for extra in extras],
                amount_paise=extra_sum,
                evidence=f"Reference {tid}: {len(extras)} split books rows ({', '.join(e.row_id for e in extras)}) sum to ₹{extra_sum/100:,.2f} but single entry {best.row_id} already cleared the bank amount.",
            ))
            for extra in extras:
                matched_books.add(extra.row_id)

        elif len(ref_books) > 1 and group_sum == bank_signed:
            # Valid split: all ref_books rows sum to bank amount
            match = MatchRecord(
                match_id=_new_match_id(),
                layer=MatchLayer.L2_REFERENCE_GROUP,
                bank_row_ids=[bank.row_id],
                books_row_ids=[b.row_id for b in ref_books],
                bank_total_paise=bank_signed,
                books_total_paise=group_sum,
                confidence=1.0,
                rationale=f"Split payment: {len(ref_books)} books rows sum to bank amount.",
                is_group_match=True,
            )
            matches.append(match)
            matched_bank.add(bank.row_id)
            for b in ref_books:
                matched_books.add(b.row_id)

        elif len(ref_books) == 1 and ref_books[0].signed_paise != bank_signed:
            # Same reference but different amounts → AMOUNT_MISMATCH (handled in L5)
            pass

    unmatched_bank = [r for r in bank_rows if r.row_id not in matched_bank]
    unmatched_books = [r for r in books_rows if r.row_id not in matched_books]
    return matches, exceptions, unmatched_bank, unmatched_books


# ---------------------------------------------------------------------------
# Layer 3: Aggregate (unreferenced books lump sums vs grouped bank rows)
# ---------------------------------------------------------------------------

def layer3_aggregate(
    bank_rows: list[BankRow],
    books_rows: list[BooksRow],
) -> tuple[list[MatchRecord], list[BankRow], list[BooksRow]]:
    """
    Books rows without a reference are matched to groups of bank rows.
    Group bank rows by date + category signal, then run subset-sum.
    """
    matches: list[MatchRecord] = []
    matched_bank: set[str] = set()
    matched_books: set[str] = set()

    # Only books rows with no reference
    books_no_ref = [b for b in books_rows if not clean_reference(b.reference_number)]

    if not books_no_ref or not bank_rows:
        return matches, bank_rows, books_rows

    # Group bank rows by (date, category)
    bank_groups: dict[tuple, list[BankRow]] = defaultdict(list)
    for bank in bank_rows:
        cat = category_signal(bank.remarks)
        # Group by category first; date is secondary key
        key = (cat or bank.value_date.isoformat(), cat)
        bank_groups[key].append(bank)

    # Also try grouping by date alone
    bank_by_date: dict[str, list[BankRow]] = defaultdict(list)
    for bank in bank_rows:
        bank_by_date[bank.value_date.isoformat()].append(bank)

    def try_match_group(
        bank_group: list[BankRow],
        books_pool: list[BooksRow],
        rationale_prefix: str,
    ) -> Optional[MatchRecord]:
        group_total = sum(abs(b.signed_paise) for b in bank_group)
        ref_date = bank_group[0].value_date if bank_group else None
        subset = find_subset_sum(group_total, books_pool, max_items=12, ref_date=ref_date)
        if subset is None:
            return None
        return MatchRecord(
            match_id=_new_match_id(),
            layer=MatchLayer.L3_AGGREGATE,
            bank_row_ids=[b.row_id for b in bank_group],
            books_row_ids=[b.row_id for b in subset],
            bank_total_paise=sum(b.signed_paise for b in bank_group),
            books_total_paise=sum(b.signed_paise for b in subset),
            confidence=1.0,
            rationale=rationale_prefix + f" {len(bank_group)} bank rows = {len(subset)} books rows",
            is_group_match=True,
        )

    # Try category-grouped bank rows first
    for key in sorted(bank_groups.keys()):
        cat_name = key[1]
        if not cat_name:
            continue
        bank_group = [b for b in bank_groups[key] if b.row_id not in matched_bank]
        books_pool = [b for b in books_no_ref if b.row_id not in matched_books]
        if not bank_group or not books_pool:
            continue

        m = try_match_group(bank_group, books_pool, f"[L3-{cat_name}]")
        if m:
            matches.append(m)
            for rid in m.bank_row_ids:
                matched_bank.add(rid)
            for rid in m.books_row_ids:
                matched_books.add(rid)

    # Try remaining bank rows grouped by date
    for date_str in sorted(bank_by_date.keys()):
        bank_group = [b for b in bank_by_date[date_str] if b.row_id not in matched_bank]
        books_pool = [b for b in books_no_ref if b.row_id not in matched_books]
        if not bank_group or not books_pool:
            continue
        # Only attempt if more than 1 bank row in this date group
        if len(bank_group) < 2:
            continue

        m = try_match_group(bank_group, books_pool, f"[L3-DATE-{date_str}]")
        if m:
            matches.append(m)
            for rid in m.bank_row_ids:
                matched_bank.add(rid)
            for rid in m.books_row_ids:
                matched_books.add(rid)

    unmatched_bank = [r for r in bank_rows if r.row_id not in matched_bank]
    unmatched_books = [r for r in books_rows if r.row_id not in matched_books]
    return matches, unmatched_bank, unmatched_books


# ---------------------------------------------------------------------------
# Layer 4: Fuzzy (same amount, date ±3, name similarity ≥ 0.85)
# ---------------------------------------------------------------------------

def layer4_fuzzy(
    bank_rows: list[BankRow],
    books_rows: list[BooksRow],
) -> tuple[list[MatchRecord], list[BankRow], list[BooksRow]]:
    matches: list[MatchRecord] = []
    matched_bank: set[str] = set()
    matched_books: set[str] = set()

    for bank in sorted(bank_rows, key=lambda r: r.row_id):
        if bank.row_id in matched_bank:
            continue
        bank_text = normalise_text(bank.remarks)
        best_score = 0.0
        best_books: Optional[BooksRow] = None

        for books in sorted(books_rows, key=lambda r: r.row_id):
            if books.row_id in matched_books:
                continue
            # Same signed amount
            if books.signed_paise != bank.signed_paise:
                continue
            # Date within ±3 days
            delta = abs((books.txn_date - bank.value_date).days)
            if delta > 3:
                continue
            # Name similarity
            books_text = normalise_text(
                books.transaction_details or books.account_name
            )
            score = fuzz.token_sort_ratio(bank_text, books_text) / 100.0
            if score >= 0.85 and score > best_score:
                best_score = score
                best_books = books

        if best_books:
            match = MatchRecord(
                match_id=_new_match_id(),
                layer=MatchLayer.L4_FUZZY,
                bank_row_ids=[bank.row_id],
                books_row_ids=[best_books.row_id],
                bank_total_paise=bank.signed_paise,
                books_total_paise=best_books.signed_paise,
                confidence=best_score,
                rationale=f"Fuzzy match: similarity={best_score:.2f}, date_delta={abs((best_books.txn_date - bank.value_date).days)}d",
            )
            matches.append(match)
            matched_bank.add(bank.row_id)
            matched_books.add(best_books.row_id)

    unmatched_bank = [r for r in bank_rows if r.row_id not in matched_bank]
    unmatched_books = [r for r in books_rows if r.row_id not in matched_books]
    return matches, unmatched_bank, unmatched_books


# ---------------------------------------------------------------------------
# Layer 5: Residual classification
# ---------------------------------------------------------------------------

def layer5_residual(
    bank_rows: list[BankRow],
    books_rows: list[BooksRow],
    exc_start: int = 1,
) -> list[ExceptionRecord]:
    exceptions: list[ExceptionRecord] = []
    counter = exc_start

    def new_id() -> str:
        nonlocal counter
        exc_id = f"EX-{counter:04d}"
        counter += 1
        return exc_id

    for bank in bank_rows:
        exceptions.append(ExceptionRecord(
            exception_id=new_id(),
            code=ExceptionCode.BANK_ONLY,
            bank_row_ids=[bank.row_id],
            amount_paise=abs(bank.signed_paise),
            evidence=f"Bank row {bank.row_id} ({bank.tran_id}) has no matching books entry.",
        ))

    for books in books_rows:
        code = ExceptionCode.BOOKS_ONLY
        # Check for ROUNDING
        if abs(books.signed_paise) < 100:  # less than ₹1
            code = ExceptionCode.ROUNDING
        exceptions.append(ExceptionRecord(
            exception_id=new_id(),
            code=code,
            books_row_ids=[books.row_id],
            amount_paise=abs(books.signed_paise),
            evidence=f"Books row {books.row_id} has no matching bank entry.",
        ))

    return exceptions

