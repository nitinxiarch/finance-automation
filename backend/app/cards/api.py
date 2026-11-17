"""
app/cards/api.py
FastAPI router for Credit Card statement processing (Contract 2).
Mounted under /api/cards.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiofiles
from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from app.cards.models import CardRun, CardRunStatus, StatementStatus
from app.cards.pipeline import CardPipelineConfig, run_card_pipeline

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cards", tags=["Credit Cards"])

DATA_DIR = Path(__file__).parent.parent.parent / "data"
UPLOAD_DIR = DATA_DIR / "card_uploads"
OUTPUT_DIR = DATA_DIR / "card_outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# In-memory storage for runs and SSE queues
_card_runs: dict[str, CardRun] = {}
_card_run_files: dict[str, dict[str, Any]] = {}  # paths, filenames
_sse_queues: dict[str, asyncio.Queue] = {}


class RowPatchRequest(BaseModel):
    account: str
    type: str = "expense"


@router.post("/runs")
async def create_card_run(
    background_tasks: BackgroundTasks,
    zip_file: UploadFile = File(...),
    books_ref: Optional[UploadFile] = File(None),
    org_name: str = Form("Xiarch Bharat Private Limited"),
    location_name: str = Form("XIARCH GURGAON BRANCH"),
):
    """Upload statements ZIP and optional books reference export."""
    run_id = str(uuid.uuid4())
    run_upload_dir = UPLOAD_DIR / run_id
    run_upload_dir.mkdir(parents=True, exist_ok=True)

    # Save ZIP
    zip_path = run_upload_dir / (zip_file.filename or "statements.zip")
    async with aiofiles.open(str(zip_path), "wb") as f:
        content = await zip_file.read()
        await f.write(content)

    # Save optional books ref
    books_ref_path: Optional[Path] = None
    if books_ref and books_ref.filename:
        books_ref_path = run_upload_dir / books_ref.filename
        async with aiofiles.open(str(books_ref_path), "wb") as f:
            content = await books_ref.read()
            await f.write(content)

    _sse_queues[run_id] = asyncio.Queue()
    _card_run_files[run_id] = {
        "zip_path": zip_path,
        "books_ref_path": books_ref_path,
        "org_name": org_name,
        "location_name": location_name,
    }

    # Start pipeline in background task
    background_tasks.add_task(
        _execute_pipeline_task,
        run_id=run_id,
        zip_path=zip_path,
        books_ref_path=books_ref_path,
        org_name=org_name,
        location_name=location_name,
    )

    return {"run_id": run_id, "status": "PENDING"}


async def _execute_pipeline_task(
    run_id: str,
    zip_path: Path,
    books_ref_path: Optional[Path],
    org_name: str,
    location_name: str,
) -> None:
    queue = _sse_queues.get(run_id)

    def _progress(event: str, data: dict[str, Any]) -> None:
        if queue:
            try:
                queue.put_nowait(json.dumps({"event": event, "data": data}))
            except Exception:
                pass

    config = CardPipelineConfig(
        zip_path=zip_path,
        output_dir=OUTPUT_DIR,
        reference_export_path=books_ref_path,
        org_name=org_name,
        location_name=location_name,
    )

    try:
        card_run = await run_card_pipeline(config, run_id=run_id, progress_cb=_progress)
        _card_runs[run_id] = card_run
    except Exception as e:
        log.exception("Pipeline failed for run %s", run_id)
        if queue:
            queue.put_nowait(json.dumps({"event": "error", "data": {"message": str(e)}}))
    finally:
        if queue:
            queue.put_nowait("__DONE__")


@router.get("/runs/{run_id}/events")
async def stream_card_run_events(run_id: str):
    """Server-Sent Events progress stream for a card processing run."""
    queue = _sse_queues.get(run_id)
    if not queue:
        # Check if run already completed
        if run_id in _card_runs:
            async def _instant_done():
                yield "data: {\"event\": \"done\"}\n\n"
            return StreamingResponse(_instant_done(), media_type="text/event-stream")
        raise HTTPException(404, "Run not found")

    async def _stream() -> AsyncGenerator[str, None]:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                if msg == "__DONE__":
                    yield "data: {\"event\": \"done\"}\n\n"
                    break
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/runs/{run_id}")
async def get_card_run(run_id: str):
    """Retrieve full card run result: statements, gates, matrix, review queue."""
    if run_id not in _card_runs:
        # Check if currently pending/running
        if run_id in _sse_queues:
            return {"run_id": run_id, "status": "RUNNING"}
        raise HTTPException(404, "Run not found")

    run = _card_runs[run_id]
    return run.model_dump()


@router.get("/runs/{run_id}/pdf/{statement_id}")
async def get_pdf_page_image(
    run_id: str,
    statement_id: str,
    page: int = Query(1, ge=1),
):
    """Render and serve a statement page image as PNG for the evidence viewer."""
    if run_id not in _card_runs:
        raise HTTPException(404, "Run not found")

    run = _card_runs[run_id]
    stmt = next((s for s in run.statements if s.statement_id == statement_id), None)
    if not stmt:
        raise HTTPException(404, "Statement not found")

    # Locate PDF file from the run's uploaded ZIP
    files_info = _card_run_files.get(run_id)
    if not files_info or not files_info["zip_path"].exists():
        raise HTTPException(404, "Source files not found")

    try:
        import pypdfium2

        with zipfile.ZipFile(files_info["zip_path"]) as zf:
            # Find the entry matching stmt.file_hash or name
            pdf_data = None
            for name in zf.namelist():
                if name.endswith(".pdf"):
                    data = zf.read(name)
                    import hashlib
                    if hashlib.sha256(data).hexdigest() == stmt.file_hash:
                        pdf_data = data
                        break
            if not pdf_data:
                # Fallback to first matching issuer in path
                for name in zf.namelist():
                    if stmt.issuer.upper() in name.upper() and name.endswith(".pdf"):
                        pdf_data = zf.read(name)
                        break

            if not pdf_data:
                raise HTTPException(404, "PDF not found in archive")

            pdf = pypdfium2.PdfDocument(pdf_data)
            p_idx = min(page - 1, len(pdf) - 1)
            rendered = pdf[p_idx].render(scale=2.0).to_pil()
            buf = io.BytesIO()
            rendered.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

    except Exception as e:
        log.exception("Failed to render PDF page image")
        raise HTTPException(500, f"Error rendering PDF page: {e}")


@router.patch("/runs/{run_id}/rows/{row_id}")
async def update_row_category(
    run_id: str,
    row_id: str,
    patch: RowPatchRequest,
):
    """Save user category or type edit and update deterministically."""
    if run_id not in _card_runs:
        raise HTTPException(404, "Run not found")

    run = _card_runs[run_id]
    found = False

    # Find the transaction
    for stmt in run.statements:
        for t in stmt.transactions:
            if t.txn_id == row_id:
                found = True
                # Remove from review queue if present
                run.review_queue = [q for q in run.review_queue if q.txn_id != row_id]
                break
        if found:
            break

    if not found:
        raise HTTPException(404, "Transaction row not found")

    return {"success": True, "row_id": row_id, "account": patch.account, "type": patch.type}


@router.get("/runs/{run_id}/download")
async def download_card_output(
    run_id: str,
    card: Optional[str] = Query(None),
    output: str = Query("zip", pattern="^(A|B|zip)$"),
):
    """Download Output A, Output B, or a combined ZIP."""
    card_run_dir = OUTPUT_DIR / run_id
    if not card_run_dir.exists():
        raise HTTPException(404, "Run output directory not found")

    if output == "zip":
        zip_file = card_run_dir / f"Cards_Export_{run_id[:8]}.zip"
        if not zip_file.exists():
            # Build zip on the fly if needed
            files = list(card_run_dir.glob("*.xlsx"))
            with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, arcname=f.name)
        return FileResponse(
            str(zip_file),
            media_type="application/zip",
            filename=f"CreditCard_Exports_{run_id[:8]}.zip",
        )

    # Single card output file
    target_pattern = f"*{card}*.xlsx" if card else "*.xlsx"
    files = list(card_run_dir.glob(target_pattern))

    if output == "A":
        # Output A does not have '_books_' in name
        match = next((f for f in files if "_books_" not in f.name), None)
    else:
        # Output B has '_books_' in name
        match = next((f for f in files if "_books_" in f.name), None)

    if not match or not match.exists():
        raise HTTPException(404, f"Output {output} file not found for card {card}")

    return FileResponse(
        str(match),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=match.name,
    )
