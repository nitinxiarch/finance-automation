"""
Read-only tools exposed to AI agents.
All amounts returned as integer paise from the engine — agents must NOT invent numbers.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from app.engine.matchers import find_subset_sum
from app.models import (
    BankRow, BooksRow, ExceptionRecord, MatchRecord,
    ReconciliationResult,
)


class AgentTools:
    """
    Tool set bound to a specific reconciliation result.
    All methods are read-only and return deterministic values.
    """

    def __init__(
        self,
        result: ReconciliationResult,
        bank_map: dict[str, BankRow],
        books_map: dict[str, BooksRow],
    ):
        self._result = result
        self._bank_map = bank_map
        self._books_map = books_map
        self._call_log: list[dict[str, Any]] = []

    def _log(self, tool: str, args: dict, output: Any) -> str:
        call_id = f"tool-{tool}-{int(time.time() * 1000)}"
        self._call_log.append({
            "call_id": call_id,
            "tool": tool,
            "args": args,
            "output": output,
        })
        return call_id

    @property
    def call_log(self) -> list[dict]:
        return self._call_log

    def get_exception(self, exception_id: str) -> dict:
        """Return the exception record as a dict."""
        exc = next(
            (e for e in self._result.exceptions if e.exception_id == exception_id),
            None
        )
        output = exc.model_dump() if exc else {"error": f"Exception {exception_id} not found"}
        self._log("get_exception", {"exception_id": exception_id}, output)
        return output

    def search_rows(
        self,
        source: str,  # "bank" or "books"
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        min_amount: Optional[int] = None,
        max_amount: Optional[int] = None,
        text: Optional[str] = None,
    ) -> list[dict]:
        """Search rows by source, date range, amount range, or text."""
        from datetime import date as _date
        import re

        results: list[dict] = []

        def _in_range(d: _date) -> bool:
            if date_from:
                df = _date.fromisoformat(date_from)
                if d < df:
                    return False
            if date_to:
                dt = _date.fromisoformat(date_to)
                if d > dt:
                    return False
            return True

        def _matches_text(s: str) -> bool:
            if not text:
                return True
            return text.lower() in s.lower()

        def _in_amount(paise: int) -> bool:
            if min_amount is not None and paise < min_amount:
                return False
            if max_amount is not None and paise > max_amount:
                return False
            return True

        if source.lower() == "bank":
            for row in self._bank_map.values():
                if not _in_range(row.value_date):
                    continue
                if not _in_amount(abs(row.signed_paise)):
                    continue
                if not _matches_text(row.remarks):
                    continue
                results.append({
                    "row_id": row.row_id,
                    "date": row.value_date.isoformat(),
                    "amount_paise": row.signed_paise,
                    "remarks": row.remarks,
                    "tran_id": row.tran_id,
                })
        elif source.lower() == "books":
            for row in self._books_map.values():
                if not _in_range(row.txn_date):
                    continue
                if not _in_amount(abs(row.signed_paise)):
                    continue
                combined = f"{row.transaction_details} {row.account_name}"
                if not _matches_text(combined):
                    continue
                results.append({
                    "row_id": row.row_id,
                    "date": row.txn_date.isoformat(),
                    "amount_paise": row.signed_paise,
                    "details": row.transaction_details,
                    "reference": row.reference_number,
                })

        self._log("search_rows", {
            "source": source, "date_from": date_from, "date_to": date_to,
            "min_amount": min_amount, "max_amount": max_amount, "text": text
        }, results)
        return results

    def sum_rows(self, row_ids: list[str]) -> dict:
        """Return the exact total paise for a list of row IDs."""
        total = 0
        found: list[str] = []
        not_found: list[str] = []
        for rid in row_ids:
            if rid in self._bank_map:
                total += self._bank_map[rid].signed_paise
                found.append(rid)
            elif rid in self._books_map:
                total += self._books_map[rid].signed_paise
                found.append(rid)
            else:
                not_found.append(rid)

        output = {"total_paise": total, "found": found, "not_found": not_found}
        self._log("sum_rows", {"row_ids": row_ids}, output)
        return output

    def find_subset_sum_tool(
        self,
        target_paise: int,
        candidate_row_ids: list[str],
        max_items: int = 12,
    ) -> dict:
        """Run deterministic subset-sum on candidate books rows."""
        candidates = [
            self._books_map[rid] for rid in candidate_row_ids
            if rid in self._books_map
        ]
        result = find_subset_sum(target_paise, candidates, max_items)
        output = {
            "found": result is not None,
            "matching_row_ids": [r.row_id for r in result] if result else [],
            "total_paise": sum(abs(r.signed_paise) for r in result) if result else 0,
        }
        self._log("find_subset_sum", {
            "target_paise": target_paise,
            "candidates": candidate_row_ids,
            "max_items": max_items,
        }, output)
        return output

    def recompute_identity(self) -> dict:
        """Recompute the reconciliation identity bridge."""
        from app.models import ExceptionCode
        adj_bank_only = sum(
            e.amount_paise for e in self._result.exceptions
            if e.code == ExceptionCode.BANK_ONLY
        )
        adj_books_only = sum(
            e.amount_paise for e in self._result.exceptions
            if e.code == ExceptionCode.BOOKS_ONLY
        )
        adj_double_count = sum(
            e.amount_paise for e in self._result.exceptions
            if e.code == ExceptionCode.DOUBLE_COUNT
        )
        books_closing = self._result.books_closing_paise
        bank_closing = self._result.bank_closing_paise

        adj_net = 0
        for exc in self._result.exceptions:
            if exc.bank_row_ids and not exc.books_row_ids:
                adj_net += sum(self._bank_map[rid].signed_paise for rid in exc.bank_row_ids if rid in self._bank_map)
            elif exc.books_row_ids and not exc.bank_row_ids:
                adj_net += sum(-self._books_map[rid].signed_paise for rid in exc.books_row_ids if rid in self._books_map)
            elif exc.bank_row_ids and exc.books_row_ids:
                b_sum = sum(self._bank_map[rid].signed_paise for rid in exc.bank_row_ids if rid in self._bank_map)
                k_sum = sum(self._books_map[rid].signed_paise for rid in exc.books_row_ids if rid in self._books_map)
                adj_net += (b_sum - k_sum)

        computed_bank = books_closing + adj_net
        output = {
            "books_closing_paise": books_closing,
            "bank_only_paise": adj_bank_only,
            "books_only_paise": adj_books_only,
            "double_count_paise": adj_double_count,
            "net_adjustment_paise": adj_net,
            "computed_bank_closing_paise": computed_bank,
            "actual_bank_closing_paise": bank_closing,
            "identity_holds": abs(computed_bank - bank_closing) <= 1,
        }
        self._log("recompute_identity", {}, output)
        return output

    def propose_match(
        self,
        bank_ids: list[str],
        books_ids: list[str],
        rationale: str,
    ) -> dict:
        """
        Propose a new match (from Investigator). Python verifies it.
        Returns accepted/rejected with reason.
        """
        # Verify: bank sum == books sum
        bank_total = sum(
            self._bank_map[rid].signed_paise for rid in bank_ids
            if rid in self._bank_map
        )
        books_total = sum(
            self._books_map[rid].signed_paise for rid in books_ids
            if rid in self._books_map
        )
        missing_bank = [rid for rid in bank_ids if rid not in self._bank_map]
        missing_books = [rid for rid in books_ids if rid not in self._books_map]

        if missing_bank or missing_books:
            output = {
                "accepted": False,
                "reason": f"Row IDs not found: bank={missing_bank}, books={missing_books}",
            }
        elif abs(bank_total - books_total) > 1:
            output = {
                "accepted": False,
                "reason": f"Amounts do not balance: bank={bank_total} paise, books={books_total} paise, diff={abs(bank_total-books_total)} paise",
            }
        else:
            output = {
                "accepted": True,
                "reason": "Amounts balance and all row IDs exist.",
                "bank_total_paise": bank_total,
                "books_total_paise": books_total,
            }

        self._log("propose_match", {
            "bank_ids": bank_ids, "books_ids": books_ids, "rationale": rationale
        }, output)
        return output

