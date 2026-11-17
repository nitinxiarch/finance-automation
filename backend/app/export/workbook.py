"""
Workbook writer: produces the output .xlsx with sheets
  Bank and Books, Summary, Matches, Exceptions, Sales_Actions, Audit_Log.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Optional

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Side, Border
from openpyxl.utils import get_column_letter

from app.models import (
    BankRow, BankStatement, BooksExport, BooksRow, ExceptionRecord,
    MatchRecord, ReconciliationResult, RunStatus,
)

CALIBRI = Font(name="Calibri", size=11)
CALIBRI_BOLD = Font(name="Calibri", size=11, bold=True)
YELLOW_FILL = PatternFill("solid", fgColor="FFFF00")
LIGHT_GREY = PatternFill("solid", fgColor="F2F2F2")
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
AMT_FORMAT = '#,##0.00'
TABULAR_NUMS = Alignment(horizontal="right")


def _fmt_paise(paise: int) -> float:
    """Convert paise to rupees as a float for Excel cells."""
    return paise / 100.0


def _set_header(ws, row_num: int, headers: list[str]) -> None:
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=row_num, column=col, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _auto_col_widths(ws, min_w: int = 10, max_w: int = 40) -> None:
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                val_len = len(str(cell.value)) if cell.value else 0
                max_len = max(max_len, val_len)
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, min_w), max_w)


def build_workbook(
    result: ReconciliationResult,
    bank_stmt: BankStatement,
    books_export: BooksExport,
    bank_file_hash: str,
    books_file_hash: str,
    engine_version: str = "1.0.0",
    model_name: str = "N/A",
    overrides: Optional[list[dict]] = None,
) -> Workbook:
    """Build the full output workbook."""
    wb = Workbook()
    wb.remove(wb.active)  # remove default sheet

    bank_map = {r.row_id: r for r in bank_stmt.rows}
    books_map = {r.row_id: r for r in books_export.rows}

    # Match lookups
    matched_books_ids: set[str] = set()
    matched_bank_ids: set[str] = set()
    group_match_books: set[str] = set()

    books_match_map: dict[str, MatchRecord] = {}
    bank_match_map: dict[str, MatchRecord] = {}

    for m in result.matches:
        for rid in m.bank_row_ids:
            matched_bank_ids.add(rid)
            bank_match_map[rid] = m
        for rid in m.books_row_ids:
            matched_books_ids.add(rid)
            books_match_map[rid] = m
            if m.is_group_match:
                group_match_books.add(rid)

    exc_books_map: dict[str, ExceptionRecord] = {}
    exc_bank_map: dict[str, ExceptionRecord] = {}
    for exc in result.exceptions:
        for rid in exc.books_row_ids:
            exc_books_map[rid] = exc
        for rid in exc.bank_row_ids:
            exc_bank_map[rid] = exc

    # --- Sheet 1: Bank and Books ---
    ws = wb.create_sheet("Bank and Books")
    headers = [
        "Source", "Date", "Transaction Details", "Transaction Type",
        "Reference Number", "Entity Number",
        "Debit (₹)", "Credit (₹)", "Net Amount (₹)",
        "Description", "Running Balance (₹)", "Remark"
    ]
    _set_header(ws, 1, headers)
    ws.freeze_panes = "A2"

    row_num = 2

    # Build the bank description map (bank remarks for matched books rows)
    def _bank_desc_for_books(brow: BooksRow) -> str:
        m = books_match_map.get(brow.row_id)
        if m and m.bank_row_ids:
            bank_remarks = [bank_map[bid].remarks for bid in m.bank_row_ids if bid in bank_map]
            return " | ".join(bank_remarks)
        return ""

    # --- Books rows first ---
    # Previous entry row (books opening / prior period adjustment)
    books_opening = result.books_opening_paise
    bank_opening = result.bank_opening_paise

    # Previous entry row
    ws.cell(row=row_num, column=1, value="Books")
    ws.cell(row=row_num, column=2, value=bank_stmt.period_from)
    ws.cell(row=row_num, column=3, value="Previous Entry")
    ws.cell(row=row_num, column=4, value="Opening Balance")
    ws.cell(row=row_num, column=11, value=_fmt_paise(bank_opening))
    ws.cell(row=row_num, column=11).number_format = AMT_FORMAT
    ws.cell(row=row_num, column=12, value="Opening balance from bank statement")
    for col in range(1, 13):
        ws.cell(row=row_num, column=col).font = CALIBRI
    row_num += 1

    running_bal_row = row_num  # first books data row
    running_balance = bank_opening

    for brow in books_export.rows:
        exc = exc_books_map.get(brow.row_id)
        remark = ""
        if exc:
            remark = exc.code.value
        elif brow.row_id not in matched_books_ids and brow.row_id not in exc_books_map:
            remark = "Extra entry"

        # Net amount formula: debit - credit (as formula in Excel)
        debit_rupees = _fmt_paise(brow.debit_paise)
        credit_rupees = _fmt_paise(brow.credit_paise)
        net_rupees = _fmt_paise(brow.net_paise)

        ws.cell(row=row_num, column=1, value="Books")
        ws.cell(row=row_num, column=2, value=brow.txn_date)
        ws.cell(row=row_num, column=3, value=brow.transaction_details)
        ws.cell(row=row_num, column=4, value=brow.transaction_type)
        ws.cell(row=row_num, column=5, value=brow.reference_number)
        ws.cell(row=row_num, column=6, value=brow.entity_number)

        # Debit/Credit cells
        dc = ws.cell(row=row_num, column=7, value=debit_rupees if debit_rupees else None)
        cc = ws.cell(row=row_num, column=8, value=credit_rupees if credit_rupees else None)
        dc.number_format = AMT_FORMAT
        cc.number_format = AMT_FORMAT

        # Net amount formula
        net_cell = ws.cell(row=row_num, column=9)
        net_cell.value = f"=N(G{row_num})-N(H{row_num})"
        net_cell.number_format = AMT_FORMAT

        # Description = matching bank remark
        ws.cell(row=row_num, column=10, value=_bank_desc_for_books(brow))

        # Running balance formula (chained from previous row)
        rb_cell = ws.cell(row=row_num, column=11)
        rb_cell.value = f"=K{row_num-1}+I{row_num}"
        rb_cell.number_format = AMT_FORMAT

        ws.cell(row=row_num, column=12, value=remark)

        # Yellow fill for group matches
        if brow.row_id in group_match_books:
            for col in [7, 8, 9]:
                ws.cell(row=row_num, column=col).fill = YELLOW_FILL

        for col in range(1, 13):
            cell = ws.cell(row=row_num, column=col)
            cell.font = CALIBRI

        row_num += 1

    # --- Bank rows ---
    for brow in bank_stmt.rows:
        exc = exc_bank_map.get(brow.row_id)
        remark = ""
        if exc:
            remark = exc.code.value

        ws.cell(row=row_num, column=1, value="Bank")
        ws.cell(row=row_num, column=2, value=brow.value_date)
        ws.cell(row=row_num, column=3, value=brow.remarks)
        ws.cell(row=row_num, column=4, value="")
        ws.cell(row=row_num, column=5, value=brow.tran_id)  # Tran. Id as ref number
        ws.cell(row=row_num, column=6, value="")

        dep = ws.cell(row=row_num, column=7, value=_fmt_paise(brow.deposit_paise) if brow.deposit_paise else None)
        wdl = ws.cell(row=row_num, column=8, value=_fmt_paise(brow.withdrawal_paise) if brow.withdrawal_paise else None)
        dep.number_format = AMT_FORMAT
        wdl.number_format = AMT_FORMAT

        net_cell = ws.cell(row=row_num, column=9, value=_fmt_paise(brow.signed_paise))
        net_cell.number_format = AMT_FORMAT

        ws.cell(row=row_num, column=10, value="")
        ws.cell(row=row_num, column=11, value=None)  # running_balance blank for bank rows
        ws.cell(row=row_num, column=12, value=remark)

        for col in range(1, 13):
            ws.cell(row=row_num, column=col).font = CALIBRI

        row_num += 1

    _auto_col_widths(ws)

    # --- Sheet 2: Summary ---
    ws2 = wb.create_sheet("Summary")
    _set_header(ws2, 1, ["Metric", "Value"])

    summary_data = [
        ("Bank Opening Balance", _fmt_paise(result.bank_opening_paise)),
        ("Bank Closing Balance", _fmt_paise(result.bank_closing_paise)),
        ("Bank Total Deposits", _fmt_paise(result.bank_total_deposits_paise)),
        ("Bank Total Withdrawals", _fmt_paise(result.bank_total_withdrawals_paise)),
        ("Books Opening Balance", _fmt_paise(result.books_opening_paise)),
        ("Books Closing Balance", _fmt_paise(result.books_closing_paise)),
        ("Books Total Debits", _fmt_paise(result.books_total_debits_paise)),
        ("Books Total Credits", _fmt_paise(result.books_total_credits_paise)),
        ("Match Count (L1 Exact)", sum(1 for m in result.matches if m.layer.value == "L1_EXACT")),
        ("Match Count (L2 Ref Group)", sum(1 for m in result.matches if m.layer.value == "L2_REFERENCE_GROUP")),
        ("Match Count (L3 Aggregate)", sum(1 for m in result.matches if m.layer.value == "L3_AGGREGATE")),
        ("Match Count (L4 Fuzzy)", sum(1 for m in result.matches if m.layer.value == "L4_FUZZY")),
        ("Exception Count", len(result.exceptions)),
        ("Status", result.status.value),
        ("All Gates Passed", str(result.all_gates_passed)),
        ("", ""),
        ("— Reconciliation Bridge —", ""),
        ("Books Closing Balance", _fmt_paise(result.books_closing_paise)),
    ]

    # Add adjustments from exceptions
    for exc in result.exceptions:
        summary_data.append((f"  Adjustment: {exc.code.value} ({exc.exception_id})", _fmt_paise(exc.amount_paise)))

    summary_data.append(("= Bank Closing Balance", _fmt_paise(result.bank_closing_paise)))

    for i, (label, value) in enumerate(summary_data, start=2):
        ws2.cell(row=i, column=1, value=label).font = CALIBRI
        val_cell = ws2.cell(row=i, column=2, value=value)
        val_cell.font = CALIBRI
        if isinstance(value, float):
            val_cell.number_format = AMT_FORMAT

    # Gate results
    ws2.cell(row=len(summary_data) + 3, column=1, value="Gate Results").font = CALIBRI_BOLD
    for i, gate in enumerate(result.gate_results, start=len(summary_data) + 4):
        ws2.cell(row=i, column=1, value=gate.gate).font = CALIBRI
        ws2.cell(row=i, column=2, value="PASS" if gate.passed else "FAIL").font = CALIBRI
        ws2.cell(row=i, column=3, value=gate.detail).font = CALIBRI

    _auto_col_widths(ws2)

    # --- Sheet 3: Matches ---
    ws3 = wb.create_sheet("Matches")
    _set_header(ws3, 1, [
        "Match ID", "Layer", "Bank Row IDs", "Books Row IDs",
        "Bank Total (₹)", "Books Total (₹)", "Confidence", "Rationale"
    ])
    for i, m in enumerate(result.matches, start=2):
        ws3.cell(row=i, column=1, value=m.match_id).font = CALIBRI
        ws3.cell(row=i, column=2, value=m.layer.value).font = CALIBRI
        ws3.cell(row=i, column=3, value=", ".join(m.bank_row_ids)).font = CALIBRI
        ws3.cell(row=i, column=4, value=", ".join(m.books_row_ids)).font = CALIBRI
        c5 = ws3.cell(row=i, column=5, value=_fmt_paise(m.bank_total_paise))
        c5.number_format = AMT_FORMAT
        c5.font = CALIBRI
        c6 = ws3.cell(row=i, column=6, value=_fmt_paise(m.books_total_paise))
        c6.number_format = AMT_FORMAT
        c6.font = CALIBRI
        ws3.cell(row=i, column=7, value=round(m.confidence, 4)).font = CALIBRI
        ws3.cell(row=i, column=8, value=m.rationale).font = CALIBRI
    _auto_col_widths(ws3)

    # --- Sheet 4: Exceptions ---
    ws4 = wb.create_sheet("Exceptions")
    _set_header(ws4, 1, [
        "Exception ID", "Code", "Bank Row IDs", "Books Row IDs",
        "Amount (₹)", "Evidence", "Owner", "Status", "Needs Human"
    ])
    for i, exc in enumerate(result.exceptions, start=2):
        ws4.cell(row=i, column=1, value=exc.exception_id).font = CALIBRI
        ws4.cell(row=i, column=2, value=exc.code.value).font = CALIBRI
        ws4.cell(row=i, column=3, value=", ".join(exc.bank_row_ids)).font = CALIBRI
        ws4.cell(row=i, column=4, value=", ".join(exc.books_row_ids)).font = CALIBRI
        c5 = ws4.cell(row=i, column=5, value=_fmt_paise(exc.amount_paise))
        c5.number_format = AMT_FORMAT
        c5.font = CALIBRI
        ws4.cell(row=i, column=6, value=exc.evidence).font = CALIBRI
        ws4.cell(row=i, column=7, value=exc.owner_team.value if exc.owner_team else "").font = CALIBRI
        ws4.cell(row=i, column=8, value=exc.status).font = CALIBRI
        ws4.cell(row=i, column=9, value=str(exc.needs_human)).font = CALIBRI
    _auto_col_widths(ws4)

    # --- Sheet 5: Sales_Actions ---
    ws5 = wb.create_sheet("Sales_Actions")
    _set_header(ws5, 1, [
        "Exception ID", "Code", "Owner", "Priority", "Summary",
        "Actions", "Customer Message", "Needs Human", "AI Verified"
    ])
    for i, exc in enumerate(result.exceptions, start=2):
        card = exc.ai_card
        ws5.cell(row=i, column=1, value=exc.exception_id).font = CALIBRI
        ws5.cell(row=i, column=2, value=exc.code.value).font = CALIBRI
        ws5.cell(row=i, column=3, value=exc.owner_team.value if exc.owner_team else "").font = CALIBRI
        if card:
            ws5.cell(row=i, column=4, value=card.priority.value).font = CALIBRI
            ws5.cell(row=i, column=5, value=card.summary).font = CALIBRI
            actions_text = "; ".join(
                f"{a.step} ({a.who}, {a.due_in_days}d)"
                for a in card.recommended_actions
            )
            ws5.cell(row=i, column=6, value=actions_text).font = CALIBRI
            ws5.cell(row=i, column=7, value=card.customer_message_draft or "").font = CALIBRI
            ws5.cell(row=i, column=8, value=str(card.needs_human)).font = CALIBRI
            ws5.cell(row=i, column=9, value=str(exc.ai_verified)).font = CALIBRI
        else:
            ws5.cell(row=i, column=4, value="").font = CALIBRI
            ws5.cell(row=i, column=5, value="AI unavailable — rule-based suggestion").font = CALIBRI
    _auto_col_widths(ws5)

    # --- Sheet 6: Audit_Log ---
    ws6 = wb.create_sheet("Audit_Log")
    _set_header(ws6, 1, ["Timestamp", "Event", "Detail"])
    audit_entries = [
        (datetime.now(timezone.utc).isoformat(), "RUN_CREATED", f"Run ID: {result.run_id}"),
        (datetime.now(timezone.utc).isoformat(), "BANK_FILE_HASH", bank_file_hash),
        (datetime.now(timezone.utc).isoformat(), "BOOKS_FILE_HASH", books_file_hash),
        (datetime.now(timezone.utc).isoformat(), "ENGINE_VERSION", engine_version),
        (datetime.now(timezone.utc).isoformat(), "MODEL", model_name),
        (datetime.now(timezone.utc).isoformat(), "STATUS", result.status.value),
        (datetime.now(timezone.utc).isoformat(), "RESULT_HASH", result.result_hash),
    ]
    if overrides:
        for ov in overrides:
            audit_entries.append((
                ov.get("timestamp", ""),
                "OVERRIDE",
                json.dumps(ov),
            ))

    for i, (ts, event, detail) in enumerate(audit_entries, start=2):
        ws6.cell(row=i, column=1, value=ts).font = CALIBRI
        ws6.cell(row=i, column=2, value=event).font = CALIBRI
        ws6.cell(row=i, column=3, value=detail).font = CALIBRI
    _auto_col_widths(ws6)

    return wb

