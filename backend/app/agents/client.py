"""
LLM client for Qwen2.5-32B via Ollama (SSH tunnel to vast.ai).
Configuration is from environment variables only.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import httpx

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:32b-instruct")
LLM_TIMEOUT_S = int(os.environ.get("LLM_TIMEOUT_S", "120"))
LLM_CTX = 16384
LLM_MAX_PREDICT = 4096
LLM_SEED = 42


async def check_tunnel() -> dict[str, Any]:
    """Check if the LLM tunnel is up and the model is available."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{LLM_BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            model_found = any(LLM_MODEL in m for m in models)
            actual_tag = next((m for m in models if LLM_MODEL in m), None)
            return {
                "tunnel_ok": True,
                "model_found": model_found,
                "model_tag": actual_tag,
                "available_models": models,
                "error": None,
            }
    except Exception as e:
        return {
            "tunnel_ok": False,
            "model_found": False,
            "model_tag": None,
            "available_models": [],
            "error": str(e),
        }


async def call_llm(
    system_prompt: str,
    user_message: str,
    response_schema: dict,
    max_retries: int = 2,
    tool_call_id: str = "",
) -> tuple[dict, str]:
    """
    Call the Ollama API with structured JSON output.

    Returns (parsed_response_dict, call_id).
    On failure after retries, returns ({}, "") and logs the error.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": 0,
            "seed": LLM_SEED,
            "num_ctx": LLM_CTX,
            "num_predict": LLM_MAX_PREDICT,
        },
        "format": response_schema,
    }

    last_error: Optional[str] = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=LLM_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{LLM_BASE_URL}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["message"]["content"]
                try:
                    parsed = json.loads(content)
                    return parsed, tool_call_id or f"llm-{int(time.time())}"
                except json.JSONDecodeError:
                    # One repair attempt: strip markdown fences
                    content_clean = content.strip().strip("```json").strip("```").strip()
                    parsed = json.loads(content_clean)
                    return parsed, tool_call_id or f"llm-{int(time.time())}"
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                await _backoff(attempt)

    return {}, ""


async def _backoff(attempt: int) -> None:
    import asyncio
    await asyncio.sleep(2 ** attempt)

