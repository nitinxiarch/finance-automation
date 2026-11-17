"""
app/cards/agents/layout_agent.py

Layout Agent: asks an LLM to inspect a problematic statement's text layer and
produce a parser patch (regex + group names) that the pipeline can apply as a
T4 (AI-assisted) fallback when T1–T3 all fail gate checks.

Returns Optional[dict] with keys:
    line_pattern, date_group, description_group, amount_group,
    multiline_join, credit_markers

Returns None when the LLM is unavailable or all 3 rounds fail.
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from pathlib import Path
from typing import Optional

import httpx

from app.cards.models import CardStatement, GateResult
from app.cards.agents.tools import CardAgentTools

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPT_PATH = Path(__file__).parent.parent.parent.parent / "prompts" / "cards" / "layout_agent.md"
_MAX_ROUNDS = 3

# Required keys in the returned patch
_PATCH_KEYS = {
    "line_pattern",
    "date_group",
    "description_group",
    "amount_group",
    "multiline_join",
    "credit_markers",
}

# Regexes for redaction
_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 16-digit PAN / card numbers
    (re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "[CARD_NO]"),
    # Account numbers (10–18 digits)
    (re.compile(r"\b\d{10,18}\b"), "[ACCT_NO]"),
    # Indian mobile numbers
    (re.compile(r"\b[6-9]\d{9}\b"), "[MOBILE]"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact(text: str) -> str:
    """Remove card/account numbers and addresses from page text."""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _load_system_prompt() -> str:
    try:
        return _PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            "You are a layout agent. Study the statement text and return a "
            "JSON parser patch with keys: line_pattern, date_group, "
            "description_group, amount_group, multiline_join, credit_markers."
        )


def _validate_patch(obj: dict) -> bool:
    """Return True when the patch has all required keys with correct types."""
    if not isinstance(obj, dict):
        return False
    if not _PATCH_KEYS.issubset(obj.keys()):
        return False
    if not isinstance(obj.get("line_pattern"), str) or not obj["line_pattern"]:
        return False
    if not isinstance(obj.get("multiline_join"), bool):
        return False
    if not isinstance(obj.get("credit_markers"), list):
        return False
    # Validate that the regex compiles
    try:
        re.compile(obj["line_pattern"])
    except re.error:
        return False
    return True


def _extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response string."""
    # Try direct parse first
    text = text.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Try to find JSON block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_layout_agent(
    statement: CardStatement,
    page_texts: dict[int, str],
    gate_results: list[GateResult],
    tools: CardAgentTools,
    client: httpx.AsyncClient,
    model: str,
    audit_log: Optional[list] = None,
) -> Optional[dict]:
    """
    Run up to MAX_ROUNDS of LLM conversation to produce a parser patch.

    Parameters
    ----------
    statement     : The CardStatement that failed gate checks.
    page_texts    : dict of page_index → raw text (will be redacted before sending).
    gate_results  : Gate failures that triggered T4.
    tools         : CardAgentTools instance for tool calls.
    client        : httpx.AsyncClient connected to the LLM tunnel.
    model         : LLM model tag.
    audit_log     : Optional list to append every prompt/response/tool call.

    Returns
    -------
    Parser patch dict, or None on failure.
    """
    if audit_log is None:
        audit_log = []

    system_prompt = _load_system_prompt()

    # Build the initial user message
    gate_summary = "\n".join(
        f"  {gr.gate}: {'PASS' if gr.passed else 'FAIL'} — {gr.detail}"
        for gr in gate_results
    )

    # Redact page texts
    redacted_pages = {
        idx: _redact(text)
        for idx, text in list(page_texts.items())[:5]  # first 5 pages only
    }
    page_summary = "\n\n".join(
        f"=== PAGE {idx} ===\n{text[:2000]}"  # truncate very long pages
        for idx, text in sorted(redacted_pages.items())
    )

    user_msg = textwrap.dedent(f"""
        Statement ID: {statement.statement_id}
        Issuer: {statement.issuer}  Card: ...{statement.last4}  Cardholder: {statement.cardholder}
        Period: {statement.period_from} → {statement.period_to}

        Gate results (from previous parse attempt):
        {gate_summary}

        Redacted page text (first 5 pages):
        {page_summary}

        Please study this layout and produce a parser patch JSON.
    """).strip()

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    audit_log.append({"kind": "layout_agent_start", "statement_id": statement.statement_id})

    for round_num in range(1, _MAX_ROUNDS + 1):
        audit_log.append({"kind": "prompt", "round": round_num, "messages": messages})

        try:
            resp = await client.post(
                "/api/chat",
                json={"model": model, "messages": messages},
                timeout=60.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.warning("Layout agent LLM unavailable (round %d): %s", round_num, exc)
            audit_log.append({"kind": "llm_error", "round": round_num, "error": str(exc)})
            return None

        reply_content: str = ""
        # Support both Ollama-style and OpenAI-style responses
        if "message" in data:
            reply_content = data["message"].get("content", "")
        elif "choices" in data:
            reply_content = data["choices"][0]["message"]["content"]
        else:
            reply_content = str(data)

        audit_log.append({"kind": "response", "round": round_num, "content": reply_content})

        # Check for a tool call
        tool_call_match = re.search(r'\{"tool_call"\s*:', reply_content)
        if tool_call_match:
            try:
                tc_json = _extract_json(reply_content[tool_call_match.start():])
                if tc_json and "tool_call" in tc_json:
                    tc = tc_json["tool_call"]
                    tool_name = tc.get("name", "")
                    tool_args = tc.get("args", {})

                    tool_method = getattr(tools, tool_name, None)
                    if tool_method:
                        tool_result = tool_method(**tool_args)
                    else:
                        tool_result = {"error": f"Unknown tool: {tool_name}"}

                    audit_log.append({
                        "kind": "tool_call",
                        "round": round_num,
                        "name": tool_name,
                        "args": tool_args,
                        "result": tool_result,
                    })

                    # Append assistant reply and tool result to messages
                    messages.append({"role": "assistant", "content": reply_content})
                    messages.append({
                        "role": "user",
                        "content": f"Tool result for {tool_name}:\n{json.dumps(tool_result, default=str)}",
                    })
                    continue  # next round
            except Exception as exc:
                logger.debug("Could not parse tool call: %s", exc)

        # No tool call — try to extract patch JSON
        patch = _extract_json(reply_content)
        if patch and _validate_patch(patch):
            audit_log.append({"kind": "patch_accepted", "round": round_num, "patch": patch})
            return patch

        # Invalid patch — ask for a correction
        messages.append({"role": "assistant", "content": reply_content})
        messages.append({
            "role": "user",
            "content": (
                "The patch you returned is missing required keys or has an invalid regex. "
                f"Required keys: {sorted(_PATCH_KEYS)}. "
                "Please return only the corrected JSON patch."
            ),
        })

    logger.warning(
        "Layout agent exhausted %d rounds without a valid patch for statement %s",
        _MAX_ROUNDS,
        statement.statement_id,
    )
    audit_log.append({"kind": "patch_failed", "statement_id": statement.statement_id})
    return None
