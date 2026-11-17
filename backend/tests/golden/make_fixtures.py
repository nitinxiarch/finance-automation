"""
Fixture generator for April 2026 golden test data.
Generates:
  - tests/golden/bank_april_2026.xlsx (ICICI statement, 139 rows)
  - tests/golden/books_april_2026.xls (Zoho export, 115 rows)

All amounts, totals, counts, and exceptions match Section 13 exactly:
  Bank: 139 rows, Opening ₹16,45,903.04, Withdrawals ₹72,49,616.41,
        Deposits ₹65,43,529.34, Closing ₹9,39,815.97
  Books: 115 rows, Deposits ₹65,43,529.34, Withdrawals ₹96,84,610.41
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
import openpyxl
from openpyxl.styles import Font
import xlwt

FIXTURES_DIR = os.path.dirname(__file__)
BANK_PATH = os.path.join(FIXTURES_DIR, "bank_april_2026.xlsx")
BOOKS_PATH = os.path.join(FIXTURES_DIR, "books_april_2026.xls")


def generate():
    # -----------------------------------------------------------------------
    # 1. Plan rows and amounts (in integer paise)
    # -----------------------------------------------------------------------
    # Bank totals
    BANK_OPENING = 164590304     # ₹16,45,903.04
    BANK_CLOSING = 93981597      # ₹9,39,815.97
    BANK_DEPOSITS = 654352934    # ₹65,43,529.34
    BANK_WITHDRAWALS = 724961641 # ₹72,49,616.41

    # Special items (paise)
    BOI_LOAN = 30000000          # ₹3,00,000 (BANK_ONLY)
    S13651920_AMT = 22013100     # ₹2,20,131 (bank & books matched, 3 split extras)
    IMPS_TOTAL = 27881900        # ₹2,78,819 (18 bank rows, 1 books row Salary Payable)
    TRANSFERS_TOTAL = 100648800  # ₹10,06,488 (4 bank rows, 2 books rows: 513910 + 492578)
    EPFO_TOTAL = 13402100        # ₹1,34,021 (5 bank rows, 1 books row)
    DTAX_TOTAL = 84172200        # ₹8,41,722 (7 bank rows, 2 books rows: 281998 + 559724)

    # L1 exact matches: 103 pairs (30 deposits, 73 withdrawals)
    # Deposits: 30 rows summing to BANK_DEPOSITS (654,352,934 paise)
    # Withdrawals: 73 rows summing to:
    # 724961641 - (30000000 + 22013100 + 27881900 + 100648800 + 13402100 + 84172200) = 446843541 paise
    L1_WITHDRAWAL_TOTAL = BANK_WITHDRAWALS - (
        BOI_LOAN + S13651920_AMT + IMPS_TOTAL + TRANSFERS_TOTAL + EPFO_TOTAL + DTAX_TOTAL
    )
    assert L1_WITHDRAWAL_TOTAL == 446843541

    # Partition BANK_DEPOSITS into 30 amounts
    n_dep = 30
    base_dep = BANK_DEPOSITS // n_dep
    dep_amts = [base_dep] * n_dep
    dep_amts[-1] += BANK_DEPOSITS - sum(dep_amts)
    # Add minor variation
    for i in range(n_dep - 1):
        dep_amts[i] += (i % 7 - 3) * 10000
    dep_amts[-1] = BANK_DEPOSITS - sum(dep_amts[:-1])
    assert sum(dep_amts) == BANK_DEPOSITS

    # Partition L1_WITHDRAWAL_TOTAL into 73 amounts
    n_wdl = 73
    base_wdl = L1_WITHDRAWAL_TOTAL // n_wdl
    wdl_amts = [base_wdl] * n_wdl
    wdl_amts[-1] += L1_WITHDRAWAL_TOTAL - sum(wdl_amts)
    for i in range(n_wdl - 1):
        wdl_amts[i] += (i % 11 - 5) * 5000
    wdl_amts[-1] = L1_WITHDRAWAL_TOTAL - sum(wdl_amts[:-1])
    assert sum(wdl_amts) == L1_WITHDRAWAL_TOTAL

    # 18 IMPS amounts summing to IMPS_TOTAL (27881900 paise)
    n_imps = 18
    base_imps = IMPS_TOTAL // n_imps
    imps_amts = [base_imps] * n_imps
    imps_amts[-1] = IMPS_TOTAL - sum(imps_amts[:-1])
    assert sum(imps_amts) == IMPS_TOTAL

    # 4 transfers summing to TRANSFERS_TOTAL (100648800 paise)
    n_trans = 4
    base_trans = TRANSFERS_TOTAL // n_trans
    trans_amts = [base_trans] * n_trans
    trans_amts[-1] = TRANSFERS_TOTAL - sum(trans_amts[:-1])
    assert sum(trans_amts) == TRANSFERS_TOTAL

    # 5 EPFO amounts summing to EPFO_TOTAL (13402100 paise)
    n_epfo = 5
    base_epfo = EPFO_TOTAL // n_epfo
    epfo_amts = [base_epfo] * n_epfo
    epfo_amts[-1] = EPFO_TOTAL - sum(epfo_amts[:-1])
    assert sum(epfo_amts) == EPFO_TOTAL

    # 7 DTAX amounts summing to DTAX_TOTAL (84172200 paise)
    n_dtax = 7
    base_dtax = DTAX_TOTAL // n_dtax
    dtax_amts = [base_dtax] * n_dtax
    dtax_amts[-1] = DTAX_TOTAL - sum(dtax_amts[:-1])
    assert sum(dtax_amts) == DTAX_TOTAL

    # Build Bank Rows list: total 139 rows
    # Interleave deposits and withdrawals so balance stays healthy and positive
    bank_data = []

    # Deposit queue
    dep_queue = []
    for i, amt in enumerate(dep_amts, 1):
        d_day = min(1 + (i * 28) // 30, 28)
        dep_queue.append({
            "tran_id": f"S_DEP_{i:04d}",
            "date": date(2026, 4, d_day),
            "remarks": f"Customer Receipt NEFT Client {i}",
            "withdrawal": 0,
            "deposit": amt,
        })

    # Withdrawal queue
    wdl_queue = []
    # 1. BOI Term loan on 1 Apr (S.N. 1)
    wdl_queue.append({
        "tran_id": "S7678355",
        "date": date(2026, 4, 1),
        "remarks": "BOI Term Loan - Installment Debit",
        "withdrawal": BOI_LOAN,
        "deposit": 0,
    })
    # 2. S13651920 on 5 Apr
    wdl_queue.append({
        "tran_id": "S13651920",
        "date": date(2026, 4, 5),
        "remarks": "Vendor Payment S13651920",
        "withdrawal": S13651920_AMT,
        "deposit": 0,
    })
    # 3. 18 IMPS payments on 10 Apr
    for i, amt in enumerate(imps_amts, 1):
        wdl_queue.append({
            "tran_id": f"S_IMPS_{i:03d}",
            "date": date(2026, 4, 10),
            "remarks": f"IMPS Salary Transfer batch BULD {i:02d}",
            "withdrawal": amt,
            "deposit": 0,
        })
    # 4. 4 transfers on 15 Apr
    for i, amt in enumerate(trans_amts, 1):
        wdl_queue.append({
            "tran_id": f"S_TR_{i:03d}",
            "date": date(2026, 4, 15),
            "remarks": f"Fund Transfer to subsidiary {i}",
            "withdrawal": amt,
            "deposit": 0,
        })
    # 5. 5 EPFO payments on 20 Apr
    for i, amt in enumerate(epfo_amts, 1):
        wdl_queue.append({
            "tran_id": f"S_EPF_{i:03d}",
            "date": date(2026, 4, 20),
            "remarks": f"EPFO electronic challan return {i}",
            "withdrawal": amt,
            "deposit": 0,
        })
    # 6. 7 DTAX payments on 22 Apr
    for i, amt in enumerate(dtax_amts, 1):
        wdl_queue.append({
            "tran_id": f"S_DTX_{i:03d}",
            "date": date(2026, 4, 22),
            "remarks": f"DTAX payment GIB challan 281 item {i}",
            "withdrawal": amt,
            "deposit": 0,
        })
    # 7. 73 Withdrawals (L1 matched)
    for i, amt in enumerate(wdl_amts, 1):
        d_day = min(1 + (i * 28) // 73, 28)
        wdl_queue.append({
            "tran_id": f"S_WDL_{i:04d}",
            "date": date(2026, 4, d_day),
            "remarks": f"Vendor Clearing RTGS Vendor {i}",
            "withdrawal": amt,
            "deposit": 0,
        })

    # Interleave: sort by date (deposits first on same date)
    all_uncombined = dep_queue + wdl_queue
    bank_data = sorted(all_uncombined, key=lambda r: (r["date"], 0 if r["deposit"] > 0 else 1, r["tran_id"]))

    assert len(bank_data) == 139, f"Bank rows count {len(bank_data)} != 139"

    # Compute running balances
    curr_bal = BANK_OPENING
    for row in bank_data:
        curr_bal = curr_bal + row["deposit"] - row["withdrawal"]
        row["balance"] = curr_bal

    assert curr_bal == BANK_CLOSING, f"Bank closing {curr_bal} != {BANK_CLOSING}"
    assert sum(r["deposit"] for r in bank_data) == BANK_DEPOSITS
    assert sum(r["withdrawal"] for r in bank_data) == BANK_WITHDRAWALS

    # -----------------------------------------------------------------------
    # 2. Write bank_april_2026.xlsx
    # -----------------------------------------------------------------------
    wb_bank = openpyxl.Workbook()
    ws_bank = wb_bank.active
    ws_bank.title = "Sheet0"

    # Metadata block
    ws_bank.append(["Name", "Acme Technologies Pvt Ltd"])
    ws_bank.append(["A/C No", "000405001234"])
    ws_bank.append(["Currency", "INR"])
    ws_bank.append(["Transaction Period", "From 01/04/2026 To 30/04/2026"])
    ws_bank.append(["IFSC", "ICIC0000004"])
    ws_bank.append([])  # blank

    # Header
    ws_bank.append([
        "S.N.", "Tran. Id", "Value Date", "Transaction Date",
        "Transaction Posted Date", "Cheque. No./Ref. No.",
        "Transaction Remarks", "Withdrawal Amt (INR)",
        "Deposit Amt (INR)", "Balance (INR)"
    ])

    def fmt_inr(paise: int) -> str:
        r = paise / 100.0
        # Indian comma formatting e.g. 15,47,687.04
        s = f"{r:.2f}"
        parts = s.split(".")
        integer_part = parts[0]
        dec_part = parts[1]
        if len(integer_part) > 3:
            last3 = integer_part[-3:]
            rest = integer_part[:-3]
            # group in 2s
            groups = []
            while len(rest) > 2:
                groups.insert(0, rest[-2:])
                rest = rest[:-2]
            if rest:
                groups.insert(0, rest)
            integer_part = ",".join(groups) + "," + last3
        return f"{integer_part}.{dec_part}"

    def fmt_date(d: date) -> str:
        months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return f"{d.day:02d}/{months[d.month]}/{d.year}"

    for sn, r in enumerate(bank_data, 1):
        ws_bank.append([
            sn,
            r["tran_id"],
            fmt_date(r["date"]),
            fmt_date(r["date"]),
            fmt_date(r["date"]),
            "",
            r["remarks"],
            fmt_inr(r["withdrawal"]) if r["withdrawal"] else "",
            fmt_inr(r["deposit"]) if r["deposit"] else "",
            fmt_inr(r["balance"]),
        ])

    # Footer block
    ws_bank.append([])  # blank row ends table
    ws_bank.append(["Opening Bal :", fmt_inr(BANK_OPENING)])
    ws_bank.append(["Total Withdrawls :", fmt_inr(BANK_WITHDRAWALS)])
    ws_bank.append(["Total Deposits :", fmt_inr(BANK_DEPOSITS)])
    ws_bank.append(["Closing Bal :", fmt_inr(BANK_CLOSING)])
    ws_bank.append(["Legend: ..."])

    os.makedirs(FIXTURES_DIR, exist_ok=True)
    wb_bank.save(BANK_PATH)
    print(f"Saved {BANK_PATH}")

    # -----------------------------------------------------------------------
    # 3. Build Books Rows: total 115 rows
    # -----------------------------------------------------------------------
    books_rows = []

    # 1. 30 Deposits (L1 matched to bank deposits)
    for i, amt in enumerate(dep_amts, 1):
        d_day = min(1 + (i * 28) // 30, 28)
        books_rows.append({
            "date": f"2026-04-{d_day:02d}",
            "account_name": "Accounts Receivable",
            "transaction_details": f"Invoice Payment Client {i}",
            "transaction_id": f"TXN_DEP_{i:04d}",
            "reference_number": f"S_DEP_{i:04d}",
            "debit": amt / 100.0,
            "credit": 0.0,
            "net_amount": amt / 100.0,
        })

    # 2. 73 Withdrawals (L1 matched to bank withdrawals)
    for i, amt in enumerate(wdl_amts, 1):
        d_day = min(1 + (i * 28) // 73, 28)
        books_rows.append({
            "date": f"2026-04-{d_day:02d}",
            "account_name": "Accounts Payable",
            "transaction_details": f"Bill Payment Vendor {i}",
            "transaction_id": f"TXN_WDL_{i:04d}",
            "reference_number": f"S_WDL_{i:04d}",
            "debit": 0.0,
            "credit": amt / 100.0,
            "net_amount": -amt / 100.0,
        })

    # 3. S13651920: 1 single row of ₹2,20,131 (Undeposited Funds)
    books_rows.append({
        "date": "2026-04-05",
        "account_name": "Undeposited Funds",
        "transaction_details": "Vendor Payment S13651920 clearing",
        "transaction_id": "TXN_UF_001",
        "reference_number": "S13651920",
        "debit": 0.0,
        "credit": S13651920_AMT / 100.0,
        "net_amount": -S13651920_AMT / 100.0,
    })

    # 4. S13651920: 3 split rows summing to ₹2,20,131 (₹59,400 + ₹1,12,941 + ₹47,790)
    split_amts = [5940000, 11294100, 4779000]
    assert sum(split_amts) == S13651920_AMT
    for i, amt in enumerate(split_amts, 1):
        books_rows.append({
            "date": "2026-04-05",
            "account_name": "Accounts Payable",
            "transaction_details": f"Vendor Bill Part {i} S13651920",
            "transaction_id": f"TXN_SPLIT_{i}",
            "reference_number": "S13651920",
            "debit": 0.0,
            "credit": amt / 100.0,
            "net_amount": -amt / 100.0,
        })

    # 5. BOOKS_ONLY: two wage payments dated 10 Apr (₹20,22,285 and ₹4,92,578, no reference)
    books_rows.append({
        "date": "2026-04-10",
        "account_name": "Wages Payable",
        "transaction_details": "Direct factory wage disbursement Apr-10",
        "transaction_id": "TXN_WAGE_01",
        "reference_number": "",
        "debit": 0.0,
        "credit": 2022285.00,
        "net_amount": -2022285.00,
    })
    books_rows.append({
        "date": "2026-04-10",
        "account_name": "Wages Payable",
        "transaction_details": "Staff wage supplement disbursement Apr-10",
        "transaction_id": "TXN_WAGE_02",
        "reference_number": "",
        "debit": 0.0,
        "credit": 492578.00,
        "net_amount": -492578.00,
    })

    # 6. Group match: 18 IMPS -> 1 books row "Salary Payable" ₹2,78,819 on 10 Apr
    books_rows.append({
        "date": "2026-04-10",
        "account_name": "Salary Payable",
        "transaction_details": "Consolidated IMPS salary batch Apr-10",
        "transaction_id": "TXN_SALARY_LUMP",
        "reference_number": "",
        "debit": 0.0,
        "credit": IMPS_TOTAL / 100.0,
        "net_amount": -IMPS_TOTAL / 100.0,
    })

    # 7. Group match: 4 transfers on 15 Apr -> 2 books rows: ₹5,13,910 + ₹4,92,578
    books_rows.append({
        "date": "2026-04-15",
        "account_name": "Inter-company Transfer",
        "transaction_details": "Subsidiary fund transfer tranche 1",
        "transaction_id": "TXN_TR_01",
        "reference_number": "",
        "debit": 0.0,
        "credit": 513910.00,
        "net_amount": -513910.00,
    })
    books_rows.append({
        "date": "2026-04-15",
        "account_name": "Salary Payable",
        "transaction_details": "Executive salary transfer Apr-15",
        "transaction_id": "TXN_TR_02",
        "reference_number": "",
        "debit": 0.0,
        "credit": 492578.00,
        "net_amount": -492578.00,
    })

    # 8. Group match: 5 EPFO payments -> 1 books row ₹1,34,021 on 20 Apr
    books_rows.append({
        "date": "2026-04-20",
        "account_name": "EPFO Payable",
        "transaction_details": "PF contribution for March 2026",
        "transaction_id": "TXN_EPFO_LUMP",
        "reference_number": "",
        "debit": 0.0,
        "credit": EPFO_TOTAL / 100.0,
        "net_amount": -EPFO_TOTAL / 100.0,
    })

    # 9. Group match: 7 DTAX payments -> 2 books rows: ₹2,81,998 + ₹5,59,724 on 22 Apr
    books_rows.append({
        "date": "2026-04-22",
        "account_name": "TDS Payable",
        "transaction_details": "TDS deduction payment Section 194C/194J",
        "transaction_id": "TXN_DTAX_01",
        "reference_number": "",
        "debit": 0.0,
        "credit": 281998.00,
        "net_amount": -281998.00,
    })
    books_rows.append({
        "date": "2026-04-22",
        "account_name": "Advance Tax Payable",
        "transaction_details": "Direct tax installment payment",
        "transaction_id": "TXN_DTAX_02",
        "reference_number": "",
        "debit": 0.0,
        "credit": 559724.00,
        "net_amount": -559724.00,
    })

    assert len(books_rows) == 115, f"Books rows count {len(books_rows)} != 115"

    total_books_debit = sum(r["debit"] for r in books_rows)
    total_books_credit = sum(r["credit"] for r in books_rows)

    assert int(round(total_books_debit * 100)) == BANK_DEPOSITS, f"Books debit {total_books_debit} != deposits"
    assert int(round(total_books_credit * 100)) == 968461041, f"Books credit {total_books_credit} != 9684610.41"

    # -----------------------------------------------------------------------
    # 4. Write books_april_2026.xls (xlwt, OLE2 format)
    # -----------------------------------------------------------------------
    wb_books = xlwt.Workbook(encoding="utf-8")
    ws_books = wb_books.add_sheet("Account Transactions")

    # Cell A1: title
    title_text = "Acme Technologies Pvt Ltd\nAccount Transactions\nBasis: Accrual\n01 Apr 2026 - 30 Apr 2026"
    ws_books.write(0, 0, title_text)

    # Row 1: Header
    headers = [
        "date", "account_name", "transaction_details", "transaction_id",
        "reference_transaction_id", "offset_account_id", "offset_account_type",
        "transaction_type", "reference_number", "entity_number",
        "debit", "credit", "net_amount", "contact_id", "account_id",
        "currency_code", "account_group", "account_type", "location_name",
    ]
    for col_idx, h in enumerate(headers):
        ws_books.write(1, col_idx, h)

    # Data rows
    for row_idx, r in enumerate(books_rows, start=2):
        ws_books.write(row_idx, 0, r["date"])
        ws_books.write(row_idx, 1, r["account_name"])
        ws_books.write(row_idx, 2, r["transaction_details"])
        ws_books.write(row_idx, 3, r["transaction_id"])
        ws_books.write(row_idx, 4, "")
        ws_books.write(row_idx, 5, "")
        ws_books.write(row_idx, 6, "")
        ws_books.write(row_idx, 7, "Payment" if r["credit"] > 0 else "Receipt")
        ws_books.write(row_idx, 8, r["reference_number"])
        ws_books.write(row_idx, 9, "")
        ws_books.write(row_idx, 10, r["debit"] if r["debit"] else "")
        ws_books.write(row_idx, 11, r["credit"] if r["credit"] else "")
        ws_books.write(row_idx, 12, r["net_amount"])
        ws_books.write(row_idx, 13, "")
        ws_books.write(row_idx, 14, "")
        ws_books.write(row_idx, 15, "INR")
        ws_books.write(row_idx, 16, "Current Assets")
        ws_books.write(row_idx, 17, "Bank")
        ws_books.write(row_idx, 18, "Head Office")

    # Final blank row (dropped by parser)
    ws_books.write(len(books_rows) + 2, 0, "")

    wb_books.save(BOOKS_PATH)
    print(f"Saved {BOOKS_PATH}")


if __name__ == "__main__":
    generate()
