# Bank–Books Reconciliation App

**Zero Unexplained Discrepancy (ZUD) reconciliation** between an ICICI bank statement and a Zoho Books export, with an optional Qwen2.5-32B AI layer for exception investigation and sales team action cards.

---

## Quick Start (Local Dev)

### 1. Prerequisites
- Python 3.12+ 
- Node 20+
- An ICICI Detailed Statement (.xlsx) and Zoho Account Transactions (.xls)

### 2. Backend

```bash
cd backend
pip install -r requirements.txt

# Configure (copy and edit)
cp .env.example .env

uvicorn app.main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

---

## LLM Tunnel (Qwen2.5-32B on vast.ai)

The AI layer connects to a Qwen2.5-32B model running via Ollama on a rented vast.ai GPU instance.

### Start the tunnel (run on your local machine):
```bash
ssh -N -p 10139 -L 11434:localhost:11434 root@ssh1.vast.ai
```

> Replace the port and host with your actual vast.ai instance details.

The app automatically detects if the tunnel is live. If not, it falls back to rule-based suggestions (labelled "AI unavailable, rule-based").

### Verify model availability:
```bash
curl http://localhost:11434/api/tags
```

The model tag must contain `qwen2.5` (e.g. `qwen2.5:32b-instruct`).

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:11434` | Ollama API base URL |
| `LLM_MODEL` | `qwen2.5:32b-instruct` | Exact model tag from `/api/tags` |
| `LLM_TIMEOUT_S` | `120` | Request timeout in seconds |

Create a `.env` file in `backend/`:

```env
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=qwen2.5:32b-instruct
LLM_TIMEOUT_S=120
```

**Never commit secrets or credentials to the repository.**

---

## Docker

```bash
docker compose up --build
```

Backend: http://localhost:8000  
Frontend: http://localhost:5173  
API docs: http://localhost:8000/docs

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/runs` | Upload files, start reconciliation |
| `GET` | `/api/runs/{id}/events` | SSE progress stream |
| `GET` | `/api/runs/{id}` | Get result + exceptions + AI cards |
| `POST` | `/api/runs/{id}/overrides` | Accept or reject a match |
| `GET` | `/api/runs/{id}/download` | Download output workbook |
| `GET` | `/api/health` | API + LLM tunnel status |

---

## Running Tests

```bash
cd backend
pip install pytest
pytest tests/ -v
```

**Golden tests** (require April 2026 fixture files in `tests/golden/`):
- Place `bank_april_2026.xlsx` and `books_april_2026.xls` in `tests/golden/`
- Run `pytest tests/test_golden.py -v`

---

## Architecture

```
recon-app/
├── backend/
│   ├── app/
│   │   ├── parsers/      # bank_parser.py, books_parser.py, detect.py
│   │   ├── engine/       # normalise, matchers (L1-L5), classify, gates, pipeline
│   │   ├── agents/       # LLM client, tools, orchestrator (Investigator/Verifier/Advisor/Judge)
│   │   ├── export/       # workbook writer (6 sheets)
│   │   ├── api/          # FastAPI routes
│   │   ├── models.py     # Pydantic v2 models
│   │   ├── db.py         # SQLite run store
│   │   └── main.py       # FastAPI app
│   ├── prompts/          # LLM system prompts (versioned .md files)
│   └── tests/
│       ├── golden/       # April 2026 fixture files
│       ├── test_golden.py
│       └── test_unit.py
└── frontend/
    └── src/
        ├── screens/      # Upload, Processing, Results
        └── lib/          # API client, types, formatters
```

---

## Key Design Decisions

### Zero Unexplained Discrepancy (ZUD)
The app only shows "Reconciled" (green) when every gate passes and the UNEXPLAINED bucket is exactly ₹0.00.

### LLM proposes, Python disposes
The LLM cannot produce or modify amounts. All numbers in AI output must have a `source_tool_call_id` tracing to a tool call result. Unverified numbers are stripped.

### Matching layers
1. **L1 Exact** — Tran.Id ↔ reference_number, same amount, same date
2. **L2 Reference group** — Split payments summing to bank amount; DOUBLE_COUNT for Undeposited Funds patterns
3. **L3 Aggregate** — Unreferenced lump sums (EPFO, DTAX, IMPS payroll) via bounded subset-sum
4. **L4 Fuzzy** — Same amount ±0, date ±3 days, name similarity ≥ 0.85
5. **L5 Residual** — Classify as BANK_ONLY or BOOKS_ONLY

### Opening balance
- Default: bank opening balance (₹16,45,903.04 for April 2026)
- Alternative: user-supplied books opening (₹16,57,076.02 noted in spec — ₹11,172.98 gap)
- Both values are shown in the Summary sheet

---

## Known Assumptions (Section 15)

1. **Opening balance gap**: ₹11,172.98 between bank and books opening. Set `books_opening_paise` in the UI or API to override.
2. **Ambiguous ₹4,92,578**: appears on 10 Apr (wage payment) and 15 Apr (Salary Payable). Labelled "needs human confirmation".
3. **Model tag**: confirm with `/api/tags` before running. Set `LLM_MODEL` accordingly.
4. **v1 supports only** ICICI Detailed Statement (.xlsx) and Zoho Account Transactions (.xls). Parsers sit behind a common interface for future formats.

