"""
src/core/usage.py - Token / API-call accounting for benchmark validation.

Records every LLM call (input/output tokens, latency, purpose) and every tool
call (by name, success/error), and estimates USD cost from per-model pricing.
"""

import time
from collections import Counter
from typing import Any, Dict, List, Optional

# USD per million tokens (input, output) - Anthropic first-party list prices.
MODEL_PRICING_PER_MTOK = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    pricing = MODEL_PRICING_PER_MTOK.get(model)
    if pricing is None:
        return None
    input_price, output_price = pricing
    return round(input_tokens / 1_000_000 * input_price + output_tokens / 1_000_000 * output_price, 6)


def usage_from_message(message: Any) -> Dict[str, int]:
    """Pull token counts from a LangChain AIMessage (usage_metadata) or an Anthropic SDK Message (usage)."""
    usage = getattr(message, "usage_metadata", None)
    if usage:
        return {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
        }
    usage = getattr(message, "usage", None)
    if usage is not None:
        return {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        }
    return {"input_tokens": 0, "output_tokens": 0}


class UsageTracker:

    def __init__(self, model: str):
        self.model = model
        self.llm_calls: List[Dict[str, Any]] = []
        self.tool_calls: Counter = Counter()
        self.tool_errors: Counter = Counter()

    def record_llm_call(
        self,
        input_tokens: int,
        output_tokens: int,
        latency_s: float,
        purpose: str = "",
        turn: Optional[int] = None,
    ) -> None:
        self.llm_calls.append({
            "turn": turn,
            "purpose": purpose,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "latency_s": round(latency_s, 3),
            "timestamp": time.time(),
        })

    def record_tool_call(self, tool_name: str, ok: bool) -> None:
        self.tool_calls[tool_name] += 1
        if not ok:
            self.tool_errors[tool_name] += 1

    @property
    def input_tokens(self) -> int:
        return sum(call["input_tokens"] for call in self.llm_calls)

    @property
    def output_tokens(self) -> int:
        return sum(call["output_tokens"] for call in self.llm_calls)

    def summary(self) -> Dict[str, Any]:
        input_tokens, output_tokens = self.input_tokens, self.output_tokens
        return {
            "model": self.model,
            "api_calls": len(self.llm_calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": estimate_cost_usd(self.model, input_tokens, output_tokens),
            "llm_latency_s": round(sum(call["latency_s"] for call in self.llm_calls), 2),
            "tool_calls": sum(self.tool_calls.values()),
            "tool_errors": sum(self.tool_errors.values()),
            "tool_calls_by_name": dict(self.tool_calls),
            "tool_errors_by_name": dict(self.tool_errors),
            "llm_calls": list(self.llm_calls),
        }
