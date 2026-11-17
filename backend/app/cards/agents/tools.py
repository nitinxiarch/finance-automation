"""
app/cards/agents/tools.py

Read-only agent tools for the card pipeline agentic layer.
Every method returns a dict that includes a tool_call_id (uuid) so the
Auditor agent can cite specific tool calls in its briefing.
"""
from __future__ import annotations

import re
import uuid
from typing import Optional


class CardAgentTools:
    """
    Stateless (read-only) helper tools injected into every card agent.

    Parameters
    ----------
    page_texts : dict[str, dict[int, str]]
        Mapping of statement_id → {page_index → text}.
    statements : dict[str, object]
        Mapping of statement_id → CardStatement (typed as object to avoid
        circular imports at the module level).
    allowed_accounts : list[str]
        Vendor/account names from the reference export, used by the
        Categoriser and Auditor.
    """

    def __init__(
        self,
        page_texts: dict[str, dict[int, str]],
        statements: dict,
        allowed_accounts: list[str],
    ) -> None:
        self._page_texts: dict[str, dict[int, str]] = page_texts
        self._statements: dict = statements
        self._allowed_accounts: list[str] = allowed_accounts

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    @staticmethod
    def _make_id() -> str:
        return str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Public tools
    # ------------------------------------------------------------------

    def show_page_text(self, statement_id: str, page: int) -> dict:
        """
        Return the raw (unredacted) text of a single page.

        Returns
        -------
        dict with keys: tool_call_id, statement_id, page, text (str | None)
        """
        tool_call_id = self._make_id()
        pages = self._page_texts.get(statement_id, {})
        text = pages.get(page)
        return {
            "tool_call_id": tool_call_id,
            "statement_id": statement_id,
            "page": page,
            "text": text,
        }

    def find_lines(self, statement_id: str, regex: str) -> dict:
        """
        Search every cached page for lines matching *regex*.

        Returns
        -------
        dict with keys: tool_call_id, statement_id, regex, matches
            matches is a list of {page, line_number, text}
        """
        tool_call_id = self._make_id()
        pages = self._page_texts.get(statement_id, {})
        try:
            pattern = re.compile(regex)
        except re.error as exc:
            return {
                "tool_call_id": tool_call_id,
                "statement_id": statement_id,
                "regex": regex,
                "error": f"Invalid regex: {exc}",
                "matches": [],
            }

        matches: list[dict] = []
        for page_idx in sorted(pages):
            text = pages[page_idx] or ""
            for line_number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    matches.append({
                        "page": page_idx,
                        "line_number": line_number,
                        "text": line,
                    })

        return {
            "tool_call_id": tool_call_id,
            "statement_id": statement_id,
            "regex": regex,
            "matches": matches,
        }

    def sum_rows(self, row_ids: list[str]) -> dict:
        """
        Sum amount_paise for the given transaction row IDs across all
        cached statements.

        Returns
        -------
        dict with keys: tool_call_id, row_ids, total_paise, found, missing
        """
        tool_call_id = self._make_id()
        # Build a flat lookup of txn_id → amount_paise
        txn_map: dict[str, int] = {}
        for stmt in self._statements.values():
            for txn in getattr(stmt, "transactions", []):
                txn_map[txn.txn_id] = txn.amount_paise

        total = 0
        found: list[str] = []
        missing: list[str] = []
        for rid in row_ids:
            if rid in txn_map:
                total += txn_map[rid]
                found.append(rid)
            else:
                missing.append(rid)

        return {
            "tool_call_id": tool_call_id,
            "row_ids": row_ids,
            "total_paise": total,
            "found": found,
            "missing": missing,
        }

    def get_gate_report(self, statement_id: str) -> dict:
        """
        Return the gate results for a single statement.

        Returns
        -------
        dict with keys: tool_call_id, statement_id, gates (list of gate dicts)
        """
        tool_call_id = self._make_id()
        stmt = self._statements.get(statement_id)
        if stmt is None:
            return {
                "tool_call_id": tool_call_id,
                "statement_id": statement_id,
                "error": "Statement not found",
                "gates": [],
            }

        gates = []
        for gr in getattr(stmt, "gate_results", []):
            gates.append({
                "gate": gr.gate,
                "passed": gr.passed,
                "detail": gr.detail,
                "paise_diff": gr.paise_diff,
            })

        return {
            "tool_call_id": tool_call_id,
            "statement_id": statement_id,
            "gates": gates,
        }

    def list_allowed_accounts(self) -> dict:
        """
        Return the list of allowed account / vendor names from the reference
        export.  The Categoriser validates every AI suggestion against this list.

        Returns
        -------
        dict with keys: tool_call_id, accounts (list[str])
        """
        tool_call_id = self._make_id()
        return {
            "tool_call_id": tool_call_id,
            "accounts": list(self._allowed_accounts),
        }
