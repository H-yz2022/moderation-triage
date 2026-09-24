"""LLM access layer: Anthropic client, offline mock client, response cache,
pricing and a thread-safe budget ledger."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .schemas import CallUsage

# USD per million tokens (input, output). Source: platform.claude.com pricing,
# checked 2026-09. Cache reads bill at 10% of input, cache writes at 125%.
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}
DEFAULT_PRICE = (2.0, 10.0)
CACHE_READ_MULT = 0.10
CACHE_WRITE_MULT = 1.25

VOTE_TOOL = {
    "name": "cast_vote",
    "description": "Record your moderation vote for the content under review.",
    "input_schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": ["violation", "no_violation", "abstain"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "clause_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Policy clause ids violated, e.g. HAR-1. Required for a violation.",
            },
            "exception_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exception ids that apply, e.g. EX-2.",
            },
            "evidence": {
                "type": "string",
                "description": "Shortest verbatim quote from the content that supports the vote.",
            },
            "rationale": {"type": "string", "description": "One or two sentences."},
        },
        "required": ["label", "confidence", "clause_ids", "rationale"],
    },
}


def price_call(model: str, inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> float:
    pin, pout = PRICING.get(model, DEFAULT_PRICE)
    usd = inp * pin + out * pout + cache_read * pin * CACHE_READ_MULT + cache_write * pin * CACHE_WRITE_MULT
    return round(usd / 1_000_000, 8)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class LLMRequest:
    agent: str
    model: str
    system_policy: str  # shared, cacheable prefix
    system_role: str  # role-specific instructions
    user: str
    max_tokens: int = 400
    meta: dict[str, Any] = field(default_factory=dict)  # used by the mock only

    def cache_key(self) -> str:
        h = hashlib.sha256()
        for part in (self.model, self.system_policy, self.system_role, self.user):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def estimated_cost(self) -> float:
        inp = estimate_tokens(self.system_policy + self.system_role + self.user)
        return price_call(self.model, inp, self.max_tokens)


@dataclass
class LLMResponse:
    data: dict[str, Any]
    usage: CallUsage


class LLMClient(Protocol):
    def call(self, req: LLMRequest) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class AnthropicClient:
    """Calls the Messages API with the policy as a prompt-cached system prefix
    and a single `cast_vote` tool for structured output. tool_choice is left on
    `auto` (forced tool use is incompatible with extended/adaptive thinking on
    some models); we fall back to parsing JSON from text and retry once."""

    def __init__(self, api_key: str, timeout_s: float = 30.0):
        try:
            import anthropic  # lazy: tests/mock mode don't need the SDK
        except ImportError as e:  # pragma: no cover
            raise LLMError("pip install anthropic to use the real provider") from e
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=2)

    def call(self, req: LLMRequest) -> LLMResponse:
        system = [
            {"type": "text", "text": req.system_policy, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": req.system_role},
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": req.user}]
        totals = {"in": 0, "out": 0, "cr": 0, "cw": 0}
        t0 = time.perf_counter()
        data = None
        for attempt in range(2):
            resp = self._client.messages.create(
                model=req.model,
                max_tokens=req.max_tokens,
                system=system,
                tools=[VOTE_TOOL],
                tool_choice={"type": "auto"},
                messages=messages,
            )
            u = resp.usage
            totals["in"] += u.input_tokens or 0
            totals["out"] += u.output_tokens or 0
            totals["cr"] += getattr(u, "cache_read_input_tokens", 0) or 0
            totals["cw"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            for block in resp.content:
                if block.type == "tool_use" and block.name == "cast_vote":
                    data = dict(block.input)
                    break
            if data is None:
                text = "".join(b.text for b in resp.content if b.type == "text")
                data = _extract_json(text)
            if data is not None:
                break
            if attempt == 0:
                messages += [
                    {"role": "assistant", "content": resp.content},
                    {"role": "user", "content": "Call the cast_vote tool now with your vote."},
                ]
        if data is None:
            raise LLMError(f"{req.agent}: model did not return a vote")
        usage = CallUsage(
            agent=req.agent,
            model=req.model,
            input_tokens=totals["in"],
            output_tokens=totals["out"],
            cache_read_tokens=totals["cr"],
            cache_write_tokens=totals["cw"],
            cost_usd=price_call(req.model, totals["in"], totals["out"], totals["cr"], totals["cw"]),
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        )
        return LLMResponse(data=data, usage=usage)


class CachedClient:
    """Response cache keyed by the full prompt hash. Makes eval re-runs free and
    reproducible, and dedupes identical content (copypasta/spam waves)."""

    def __init__(self, inner: LLMClient, store):
        self.inner, self.store = inner, store

    def call(self, req: LLMRequest) -> LLMResponse:
        key = req.cache_key()
        hit = self.store.cache_get(key)
        if hit is not None and "data" in hit:
            usage = CallUsage(**hit.get("usage", {"agent": req.agent, "model": req.model}))
            usage.cached, usage.latency_ms = True, 0.0  # list cost kept; actual spend is $0
            return LLMResponse(data=hit["data"], usage=usage)
        resp = self.inner.call(req)
        self.store.cache_put(key, {"data": resp.data, "usage": resp.usage.model_dump()})
        return resp


class BudgetLedger:
    """Hard USD cap for a run (eval batch or server process)."""

    def __init__(self, limit_usd: float):
        self.limit = limit_usd
        self.spent = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def try_reserve(self, amount: float) -> bool:
        with self._lock:
            if self.spent + amount > self.limit:
                return False
            self.spent += amount
            return True

    def settle(self, reserved: float, actual: float) -> None:
        with self._lock:
            self.spent += actual - reserved
            self.calls += 1

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent)
