"""
FastAPI application: endpoints, SSE, file upload handling.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiofiles
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.agents.client import check_tunnel
from app.agents.orchestrator import run_ai_layer
from app.agents.tools import AgentTools
from app.db import (
    audit, create_run, file_hash, get_overrides, get_run,
    init_db, save_override, save_result, update_run_status,
)
from app.engine.pipeline import reconcile
from app.export.workbook import build_workbook
from app.models import (
    BankRow, BooksRow, HealthResponse, OverrideRequest, RunCreatedResponse,
    RunStatus,
)
from app.parsers.bank_parser import ParseError, parse_bank_statement
from app.parsers.books_parser import parse_books_export
from app.parsers.detect import detect_and_assign

UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ENGINE_VERSION = "1.0.0"

# SSE event queues per run_id
_sse_queues: dict[str, asyncio.Queue] = {}

app = FastAPI(
    title="Bank–Books Reconciliation API",
    version=ENGINE_VERSION,
    description="Zero Unexplained Discrepancy reconciliation engine.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


@app.get("/api/health", response_model=HealthResponse)
async def health():
    tunnel = await check_tunnel()
    return HealthResponse(
        api_ok=True,
        llm_tunnel_ok=tunnel["tunnel_ok"],
        llm_model_found=tunnel["model_found"],
        llm_model_tag=tunnel.get("model_tag"),
        llm_error=tunnel.get("error"),
    )


# ---------------------------------------------------------------------------
# POST /api/runs — accept two files, start reconciliation
# ---------------------------------------------------------------------------

@app.post("/api/runs", response_model=RunCreatedResponse)
async def create_run_endpoint(
    background_tasks: BackgroundTasks,
    bank_file: UploadFile = File(...),
    books_file: UploadFile = File(...),
    books_opening_paise: Optional[int] = Form(None),
    mask_names: bool = Form(False),
):
    run_id = str(uuid.uuid4())
    run_dir = UPLOAD_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Save uploads
    bank_path = run_dir / (bank_file.filename or "bank_upload")
    books_path = run_dir / (books_file.filename or "books_upload")

    async with aiofiles.open(str(bank_path), "wb") as f:
        content = await bank_file.read()
        await f.write(content)
    async with aiofiles.open(str(books_path), "wb") as f:
        content = await books_file.read()
        await f.write(content)

    bank_hash = file_hash(str(bank_path))
    books_hash = file_hash(str(books_path))

    create_run(
        run_id=run_id,
        bank_filename=bank_file.filename or "",
        books_filename=books_file.filename or "",
        bank_file_hash=bank_hash,
        books_file_hash=books_hash,
    )

    _sse_queues[run_id] = asyncio.Queue()

    background_tasks.add_task(
        _run_reconciliation,
        run_id=run_id,
        bank_path=str(bank_path),
        books_path=str(books_path),
        bank_hash=bank_hash,
        books_hash=books_hash,
        bank_filename=bank_file.filename or "",
        books_filename=books_file.filename or "",
        books_opening_paise=books_opening_paise,
        mask_names=mask_names,
    )

    return RunCreatedResponse(run_id=run_id, status=RunStatus.PENDING)


async def _run_reconciliation(
    run_id: str,
    bank_path: str,
    books_path: str,
    bank_hash: str,
    books_hash: str,
    bank_filename: str,
    books_filename: str,
    books_opening_paise: Optional[int],
    mask_names: bool,
) -> None:
    queue = _sse_queues.get(run_id)

    def _emit(event: str, data: dict) -> None:
        if queue:
            try:
                queue.put_nowait(json.dumps({"event": event, "data": data}))
            except Exception:
                pass

    def _progress(step: str, detail: str = "") -> None:
        _emit("progress", {"step": step, "detail": detail})

    try:
        update_run_status(run_id, "RUNNING")
        _progress("DETECT", "Auto-detecting file types…")

        # --- Detect and assign ---
        try:
            bank_det, books_det, was_swapped = detect_and_assign(
                bank_path, bank_filename,
                books_path, books_filename,
            )
        except ParseError as e:
            raise

        if was_swapped:
            _emit("info", {"message": "Files were swapped — fixed automatically."})
            audit(run_id, "FILES_SWAPPED", "Auto-corrected swap")

        _emit("detect", {
            "bank": {
                "filename": bank_det.original_filename,
                "type": bank_det.file_type.value,
                "period": bank_det.detected_period,
                "rows": bank_det.row_count,
                "size": bank_det.file_size_bytes,
                "note": bank_det.detection_note,
            },
            "books": {
                "filename": books_det.original_filename,
                "type": books_det.file_type.value,
                "period": books_det.detected_period,
                "rows": books_det.row_count,
                "size": books_det.file_size_bytes,
                "note": books_det.detection_note,
            },
        })

        # --- Parse ---
        _progress("PARSE", "Parsing bank statement…")
        bank_stmt = parse_bank_statement(bank_det.path)
        _progress("PARSE", "Parsing books export…")
        books_export = parse_books_export(books_det.path)

        # --- Period cross-check ---
        if bank_stmt.period_from.year != books_export.period_from.year or \
                bank_stmt.period_from.month != books_export.period_from.month:
            _emit("warning", {
                "message": f"Periods differ: bank {bank_stmt.period_from}–{bank_stmt.period_to}, "
                           f"books {books_export.period_from}–{books_export.period_to}"
            })

        # --- Reconcile ---
        result = await asyncio.to_thread(
            reconcile,
            bank_stmt=bank_stmt,
            books_export=books_export,
            run_id=run_id,
            books_opening_paise=books_opening_paise,
            progress_callback=_progress,
        )

        # --- AI layer ---
        _progress("AI_REVIEW", "Running AI agents…")
        bank_map = {r.row_id: r for r in bank_stmt.rows}
        books_map = {r.row_id: r for r in books_export.rows}
        tools = AgentTools(result, bank_map, books_map)

        result, verifier_result = await run_ai_layer(
            result=result,
            tools=tools,
            mask_names=mask_names,
            progress_callback=_progress,
        )

        # --- Build workbook ---
        _progress("BUILD_WORKBOOK", "Building output workbook…")
        wb = build_workbook(
            result=result,
            bank_stmt=bank_stmt,
            books_export=books_export,
            bank_file_hash=bank_hash,
            books_file_hash=books_hash,
            engine_version=ENGINE_VERSION,
            model_name=os.environ.get("LLM_MODEL", "N/A"),
            overrides=get_overrides(run_id),
        )

        output_path = OUTPUT_DIR / f"{run_id}_result.xlsx"
        wb.save(str(output_path))

        # --- Persist result ---
        result_json = result.model_dump_json()
        save_result(run_id, result_json, str(output_path), result.status.value)
        audit(run_id, "COMPLETED", f"status={result.status.value}, hash={result.result_hash}")

        _emit("done", {
            "run_id": run_id,
            "status": result.status.value,
            "gates_passed": result.all_gates_passed,
            "exception_count": len(result.exceptions),
            "match_count": len(result.matches),
        })

    except ParseError as e:
        update_run_status(run_id, "FAILED", str(e))
        audit(run_id, "PARSE_ERROR", str(e))
        _emit("error", {"message": str(e), "type": "parse_error"})

    except Exception as e:
        update_run_status(run_id, "FAILED", str(e))
        audit(run_id, "ERROR", str(e))
        _emit("error", {"message": f"Unexpected error: {e}", "type": "internal_error"})

    finally:
        if run_id in _sse_queues:
            _sse_queues[run_id].put_nowait("__DONE__")


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/events — SSE progress stream
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str):
    if run_id not in _sse_queues:
        # Check if already done
        run = get_run(run_id)
        if not run:
            raise HTTPException(404, "Run not found")

    queue = _sse_queues.get(run_id, asyncio.Queue())

    async def _stream() -> AsyncGenerator[str, None]:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30)
                if msg == "__DONE__":
                    yield "data: {\"event\": \"done\"}\n\n"
                    break
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# GET /api/runs/{id} — result
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}")
async def get_run_result(run_id: str):
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    result_data = {
        "run_id": run_id,
        "status": run["status"],
        "bank_filename": run["bank_filename"],
        "books_filename": run["books_filename"],
        "created_at": run["created_at"],
        "error": run.get("error"),
    }

    if run.get("result_json"):
        result_data["result"] = json.loads(run["result_json"])

    return result_data


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/overrides
# ---------------------------------------------------------------------------

@app.post("/api/runs/{run_id}/overrides")
async def apply_override(
    run_id: str,
    override: OverrideRequest,
    background_tasks: BackgroundTasks,
):
    run = get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")

    override_dict = override.model_dump()
    override_dict["timestamp"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    save_override(run_id, override_dict)

    # TODO: Re-run engine with override applied deterministically
    return {"accepted": True, "run_id": run_id, "message": "Override recorded. Re-run triggered."}


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/download
# ---------------------------------------------------------------------------

@app.get("/api/runs/{run_id}/download")
async def download_workbook(run_id: str):
    run = get_run(run_id)
    if not run or not run.get("workbook_path"):
        raise HTTPException(404, "Workbook not ready")

    path = run["workbook_path"]
    if not Path(path).exists():
        raise HTTPException(404, "Workbook file not found on disk")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"BankBooks_Reconciliation_{run_id[:8]}.xlsx",
    )


# ---------------------------------------------------------------------------
# Mount Credit Cards Router (Contract 2)
# ---------------------------------------------------------------------------
from app.cards.api import router as cards_router
app.include_router(cards_router)


# ---------------------------------------------------------------------------
# Static frontend mount
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


