"""
app/cards/agents/categoriser_agent.py
Closed-set transaction categorisation using Qwen2.5-32B via Ollama.
Batches of max 25 rows. Strictly selects from allowed account/vendor names.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

from app.cards.models import CanonicalTxn

log = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:32b")

PROMPT_TEMPLATE = """You classify credit card transactions into an accounting account or vendor.
You are given a numbered list of rows (description only, plus kind) and the allowed names.
For each row, return the best allowed name, the transaction type, a confidence from 0 to 1, and a reason of at most 12 words.
If no allowed name fits, return UNSURE. Never invent a name, and never change or restate an amount or date. Respond only with JSON matching the schema.

Allowed Names:
{allowed_names_list}

Transactions to classify:
{txns_list}

Respond ONLY with valid JSON matching:
{{
  "results": [
    {{"row": 1, "name": "Travel Expense", "type": "expense", "confidence": 0.93, "reason": "Airline ticket purchase"}}
  ]
}}
"""


async def run_categoriser_batch(
    txns: list[CanonicalTxn],
    allowed_names: list[str],
    client: Optional[httpx.AsyncClient] = None,
    model: str = OLLAMA_MODEL,
) -> list[dict[str, Any]]:
    """
    Classify a batch of up to 25 transactions using the Qwen model.
    Enforces closed-set validation: any name not in allowed_names is discarded.
    """
    if not txns or not allowed_names:
        return []

    allowed_set = set(allowed_names)

    # Format batch
    txns_text = "\n".join(
        f"{idx+1}. Description: {t.description_clean or t.description_raw} | Kind: {t.kind.value}"
        for idx, t in enumerate(txns)
    )
    names_text = "\n".join(f"- {name}" for name in allowed_names[:100])

    prompt = PROMPT_TEMPLATE.format(allowed_names_list=names_text, txns_list=txns_text)

    should_close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=30.0)
        should_close_client = True

    try:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "seed": 42,
                    "num_ctx": 16384,
                },
            },
        )
        if response.status_code != 200:
            log.warning("Ollama categoriser returned status %d", response.status_code)
            return []

        res_json = response.json()
        raw_text = res_json.get("response", "{}")
        parsed = json.loads(raw_text)
        results = parsed.get("results", [])

        validated_results = []
        for r in results:
            row_idx = r.get("row")
            if not isinstance(row_idx, int) or row_idx < 1 or row_idx > len(txns):
                continue
            name = r.get("name", "").strip()
            # Enforce closed-set check
            if name != "UNSURE" and name not in allowed_set:
                log.info("Categoriser proposed name '%s' outside allowed set — rejected", name)
                name = "UNSURE"
                r["confidence"] = 0.0

            validated_results.append({
                "txn_id": txns[row_idx - 1].txn_id,
                "name": name,
                "type": r.get("type", "expense"),
                "confidence": float(r.get("confidence", 0.5)),
                "reason": str(r.get("reason", ""))[:100],
            })

        return validated_results

    except Exception as e:
        log.warning("Categoriser agent call failed: %s (falling back to classic rules)", e)
        return []
    finally:
        if should_close_client:
            await client.aclose()


async def run_categoriser_agent(
    txns: list[CanonicalTxn],
    allowed_names: list[str],
    batch_size: int = 25,
) -> dict[str, dict[str, Any]]:
    """
    Run categoriser across all transactions in chunks of batch_size.
    Returns mapping from txn_id to classification dict.
    """
    result_map: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=45.0) as client:
        for i in range(0, len(txns), batch_size):
            chunk = txns[i : i + batch_size]
            classifications = await run_categoriser_batch(chunk, allowed_names, client=client)
            for c in classifications:
                result_map[c["txn_id"]] = c
    return result_map

