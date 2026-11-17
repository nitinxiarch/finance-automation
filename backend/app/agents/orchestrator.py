"""
AI agent orchestrator: Investigator, Verifier, Sales Advisor, Judge.
Falls back to rule-based suggestions when LLM is unavailable.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from app.agents.client import call_llm, check_tunnel
from app.agents.redact import redact_exception_for_llm
from app.agents.tools import AgentTools
from app.models import (
    ExceptionCode, ExceptionRecord, GateResult, JudgeResult, JudgeVerdict,
    MatchRecord, OwnerTeam, Priority, ReconciliationResult,
    RecommendedAction, SalesActionCard, FactRef, VerifierResult,
)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
BATCH_SIZE = 15


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def _extract_numbers(text: str) -> set[str]:
    """Extract all numeric tokens from text (for anti-hallucination check)."""
    return set(re.findall(r"\d[\d,]*(?:\.\d+)?", text))


def _numbers_in_tool_results(tool_log: list[dict]) -> set[str]:
    """Collect all numbers from tool call outputs."""
    nums: set[str] = set()
    for entry in tool_log:
        output_str = json.dumps(entry.get("output", ""))
        nums.update(_extract_numbers(output_str))
    return nums


# ---------------------------------------------------------------------------
# Rule-based fallback for when LLM is unavailable
# ---------------------------------------------------------------------------

def _rule_based_card(exc: ExceptionRecord) -> SalesActionCard:
    """Generate a template action card without LLM."""
    amount_str = f"₹{exc.amount_paise/100:,.2f}"

    if exc.code == ExceptionCode.BANK_ONLY:
        summary = f"Bank receipt {amount_str} not in books — verify with customer"
        actions = [
            RecommendedAction(step="Obtain remittance advice from customer", who="Sales", due_in_days=2),
            RecommendedAction(step="Apply receipt in books against correct invoice", who="Finance", due_in_days=3),
        ]
        msg = None
        priority = Priority.P1
    elif exc.code == ExceptionCode.BOOKS_ONLY:
        summary = f"Books entry {amount_str} not seen in bank — confirm receipt"
        actions = [
            RecommendedAction(step="Confirm with customer whether payment was sent", who="Sales", due_in_days=2),
            RecommendedAction(step="Reverse if not received; re-issue invoice", who="Finance", due_in_days=5),
        ]
        msg = None
        priority = Priority.P1
    elif exc.code == ExceptionCode.AMOUNT_MISMATCH:
        summary = f"Amount differs by {amount_str} — check TDS/bank charges"
        actions = [
            RecommendedAction(step="Request TDS certificate or balance payment", who="Sales", due_in_days=3),
        ]
        msg = None
        priority = Priority.P2
    elif exc.code == ExceptionCode.DOUBLE_COUNT:
        summary = f"Duplicate entry {amount_str} — remove extra posting"
        actions = [
            RecommendedAction(step="Delete duplicate books entry", who="Finance", due_in_days=1),
        ]
        msg = None
        priority = Priority.P2
    else:
        summary = f"Exception {exc.code.value} for {amount_str} — finance review required"
        actions = [
            RecommendedAction(step="Finance team to investigate and post correcting entry", who="Finance", due_in_days=5),
        ]
        msg = None
        priority = Priority.P3

    return SalesActionCard(
        exception_id=exc.exception_id,
        code=exc.code.value,
        owner_team=exc.owner_team or OwnerTeam.FINANCE,
        priority=priority,
        summary=summary,
        facts_used=[],
        recommended_actions=actions,
        customer_message_draft=msg,
        needs_human=False,
    )


# ---------------------------------------------------------------------------
# Sales Advisor
# ---------------------------------------------------------------------------

ADVISOR_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "exception_id": {"type": "string"},
                    "code": {"type": "string"},
                    "owner_team": {"type": "string"},
                    "priority": {"type": "string"},
                    "summary": {"type": "string"},
                    "facts_used": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "fact_id": {"type": "string"},
                                "source_tool_call_id": {"type": "string"},
                            }
                        }
                    },
                    "recommended_actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "who": {"type": "string"},
                                "due_in_days": {"type": "integer"},
                            }
                        }
                    },
                    "customer_message_draft": {"type": ["string", "null"]},
                    "needs_human": {"type": "boolean"},
                }
            }
        }
    }
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "exception_id": {"type": "string"},
                    "card_passed": {"type": "boolean"},
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim": {"type": "string"},
                                "verdict": {"type": "string"},
                                "fact_id": {"type": ["string", "null"]},
                            }
                        }
                    }
                }
            }
        }
    }
}

VERIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "identity_confirmed": {"type": "boolean"},
        "identity_detail": {"type": "string"},
        "exception_reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "exception_id": {"type": "string"},
                    "verdict": {"type": "string"},
                    "reason": {"type": "string"},
                }
            }
        },
        "disagreements": {"type": "array", "items": {"type": "string"}},
    }
}


async def run_ai_layer(
    result: ReconciliationResult,
    tools: AgentTools,
    mask_names: bool = False,
    progress_callback=None,
) -> tuple[ReconciliationResult, VerifierResult | None]:
    """
    Run AI agents: Verifier + Sales Advisor + Judge.
    Returns updated result with ai_card fields populated.
    Falls back to rule-based if LLM unavailable.
    """
    def _progress(step: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(step, detail)

    tunnel = await check_tunnel()
    llm_available = tunnel["tunnel_ok"] and tunnel["model_found"]

    if not llm_available:
        _progress("AI_FALLBACK", "LLM unavailable — using rule-based suggestions")
        for exc in result.exceptions:
            exc.ai_card = _rule_based_card(exc)
            exc.ai_card.summary += " [AI unavailable, rule-based]"
            exc.ai_verified = False
        return result, None

    # --- Verifier ---
    _progress("AI_VERIFIER", "Running independent verifier…")
    identity = tools.recompute_identity()
    verifier_input = {
        "identity": identity,
        "gate_results": [g.model_dump() for g in result.gate_results],
        "exceptions": [
            redact_exception_for_llm(e.model_dump(), mask_names)
            for e in result.exceptions[:30]
        ],
    }
    verifier_result_raw, v_call_id = await call_llm(
        system_prompt=_load_prompt("verifier"),
        user_message=json.dumps(verifier_input),
        response_schema=VERIFIER_SCHEMA,
    )

    verifier_obj: Optional[VerifierResult] = None
    if verifier_result_raw:
        try:
            verifier_obj = VerifierResult(**verifier_result_raw)
        except Exception:
            pass

    # --- Sales Advisor (batched) ---
    _progress("AI_ADVISOR", "Running Sales Advisor…")
    sales_exceptions = [e for e in result.exceptions if e.owner_team == OwnerTeam.SALES]
    all_exceptions = result.exceptions
    # Process all exceptions in batches of 15
    all_cards: dict[str, SalesActionCard] = {}

    for i in range(0, len(all_exceptions), BATCH_SIZE):
        batch = all_exceptions[i:i + BATCH_SIZE]
        batch_input = {
            "exceptions": [
                redact_exception_for_llm(e.model_dump(), mask_names)
                for e in batch
            ]
        }
        advisor_raw, a_call_id = await call_llm(
            system_prompt=_load_prompt("advisor"),
            user_message=json.dumps(batch_input),
            response_schema=ADVISOR_SCHEMA,
        )
        if advisor_raw and "cards" in advisor_raw:
            for card_data in advisor_raw["cards"]:
                exc_id = card_data.get("exception_id", "")
                exc = next((e for e in batch if e.exception_id == exc_id), None)
                if not exc:
                    continue
                try:
                    card = SalesActionCard(
                        exception_id=exc_id,
                        code=exc.code.value,
                        owner_team=exc.owner_team or OwnerTeam.FINANCE,
                        priority=Priority(card_data.get("priority", "P3")),
                        summary=card_data.get("summary", "")[:200],
                        facts_used=[
                            FactRef(**f) for f in card_data.get("facts_used", [])
                        ],
                        recommended_actions=[
                            RecommendedAction(**a)
                            for a in card_data.get("recommended_actions", [])
                        ],
                        customer_message_draft=card_data.get("customer_message_draft"),
                        needs_human=card_data.get("needs_human", False),
                    )
                    all_cards[exc_id] = card
                except Exception:
                    pass

    # Fallback for any missing cards
    for exc in all_exceptions:
        if exc.exception_id not in all_cards:
            all_cards[exc.exception_id] = _rule_based_card(exc)

    # --- Judge ---
    _progress("AI_JUDGE", "Running Judge to verify action cards…")
    judge_input = {
        "exceptions": [
            redact_exception_for_llm(e.model_dump(), mask_names)
            for e in all_exceptions[:30]
        ],
        "cards": [all_cards[e.exception_id].model_dump() for e in all_exceptions if e.exception_id in all_cards],
    }
    judge_raw, j_call_id = await call_llm(
        system_prompt=_load_prompt("judge"),
        user_message=json.dumps(judge_input),
        response_schema=JUDGE_SCHEMA,
    )

    judge_pass: set[str] = set()
    judge_fail: set[str] = set()
    if judge_raw and "verdicts" in judge_raw:
        for v in judge_raw["verdicts"]:
            exc_id = v.get("exception_id", "")
            if v.get("card_passed", True):
                judge_pass.add(exc_id)
            else:
                judge_fail.add(exc_id)
                # Fallback to rule-based for failed cards
                exc = next((e for e in all_exceptions if e.exception_id == exc_id), None)
                if exc:
                    all_cards[exc_id] = _rule_based_card(exc)

    # --- Anti-hallucination G9 guard ---
    tool_numbers = _numbers_in_tool_results(tools.call_log)
    verified_count = 0
    unverified_removed = 0

    for exc in result.exceptions:
        card = all_cards.get(exc.exception_id)
        if card:
            # Check all numbers in card text are grounded in tool results
            card_text = json.dumps(card.model_dump())
            card_numbers = _extract_numbers(card_text)
            # Allow amount from exception itself
            exc_amount_str = str(exc.amount_paise)
            tool_numbers_local = tool_numbers | {exc_amount_str}
            ungrounded = card_numbers - tool_numbers_local
            if ungrounded:
                # Strip the ungrounded card, use rule-based
                card = _rule_based_card(exc)
                unverified_removed += 1

            exc.ai_card = card
            exc.ai_verified = exc.exception_id in judge_pass
            verified_count += 1

    _progress("AI_COMPLETE", f"Verified: {verified_count}, Unverified/removed: {unverified_removed}")
    return result, verifier_obj

