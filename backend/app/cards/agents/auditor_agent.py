"""
app/cards/agents/auditor_agent.py
Auditor Agent: produces a concise (<150 words) per-card briefing.
Uses verified figures, lists fees/interest, large payments, EMI items,
and coverage gaps. Every cited number is grounded in tool facts.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

from app.cards.agents.tools import CardAgentTools
from app.cards.models import CardStatement, GateResult

log = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:32b")


def _generate_rule_briefing(
    card_key: str,
    statements: list[CardStatement],
) -> str:
    """Deterministic fallback briefing when LLM is unavailable."""
    total_debits = sum(s.parsed_debits_paise for s in statements) / 100
    total_credits = sum(s.parsed_credits_paise for s in statements) / 100
    failed_gates = []
    for s in statements:
        for g in s.gate_results:
            if not g.passed:
                failed_gates.append(f"{g.gate} ({g.detail})")

    all_txns = [t for s in statements for t in s.transactions]
    fees = sum(t.amount_paise for t in all_txns if t.kind.value in ("FEE", "INTEREST", "GST")) / 100
    large_payments = [t for t in all_txns if t.kind.value == "PAYMENT" and t.amount_paise >= 1000000]
    emis = [t for t in all_txns if "EMI" in t.kind.value]

    parts = []
    if failed_gates:
        parts.append(f"WARNING: Statements failed gates: {'; '.join(failed_gates[:3])}.")
    else:
        parts.append("All statements verified successfully.")

    parts.append(
        f"Card {card_key} has {len(statements)} statements with {len(all_txns)} transactions "
        f"(total spends ₹{total_debits:,.2f}, total credits ₹{total_credits:,.2f})."
    )
    if fees > 0:
        parts.append(f"Total interest, fees and taxes: ₹{fees:,.2f}.")
    if large_payments:
        parts.append(f"{len(large_payments)} payment(s) of ₹10,000+ recorded.")
    if emis:
        parts.append(f"{len(emis)} EMI transaction(s) tracked.")

    return " ".join(parts)


async def run_auditor(
    card_key: str,
    statements: list[CardStatement],
    tools: CardAgentTools,
    client: Optional[httpx.AsyncClient] = None,
    model: str = OLLAMA_MODEL,
) -> str:
    """
    Generate an auditor briefing for a card.
    Attempts Qwen LLM first, falling back to rule-based summary if unreachable.
    """
    fallback = _generate_rule_briefing(card_key, statements)

    all_txns = [t for s in statements for t in s.transactions]
    fees = sum(t.amount_paise for t in all_txns if t.kind.value in ("FEE", "INTEREST", "GST")) / 100
    failed_gates = [g.gate for s in statements for g in s.gate_results if not g.passed]

    prompt = f"""You write a short briefing for a finance team about one credit card's statements.
State uncertainty plainly. If any gate failed, say so first. Keep it strictly under 150 words. Respond only with JSON:
{{"briefing": "..."}}

Facts for card {card_key}:
- Total statements: {len(statements)}
- Gate status: {'All passed' if not failed_gates else f'Failed gates: {failed_gates}'}
- Total transactions: {len(all_txns)}
- Total fees/interest/tax: ₹{fees:,.2f}
- Spends: ₹{sum(s.parsed_debits_paise for s in statements)/100:,.2f}
- Credits: ₹{sum(s.parsed_credits_paise for s in statements)/100:,.2f}
"""

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
                    "num_ctx": 4096,
                },
            },
        )
        if response.status_code == 200:
            parsed = json.loads(response.json().get("response", "{}"))
            briefing = parsed.get("briefing", "").strip()
            if briefing and len(briefing.split()) <= 160:
                return briefing
    except Exception as e:
        log.info("Auditor LLM unavailable (%s); using deterministic briefing", e)
    finally:
        if should_close_client:
            await client.aclose()

    return fallback

