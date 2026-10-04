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
CACHE_WRITE_MULT = 1.25  # 5-minute TTL
CACHE_TTL_S = 300
BATCH_DISCOUNT = 0.5  # Message Batches API: 50% off all token usage

# Minimum cacheable prefix (tokens). Shorter prefixes silently don't cache, even
# with a cache_control marker. Haiku 4.5's minimum is 4096, so our ~1.5k-token
# specialist prompts never cache - which is why we keep them lean instead.
CACHE_MIN_TOKENS: dict[str, int] = {
    "claude-haiku-4-5": 4096,
    "claude-haiku-4-5-20251001": 4096,
    "claude-sonnet-5": 1024,
}

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


def price_call(model: str, inp: int, out: int, cache_read: int = 0, cache_write: int = 0, batch: bool = False) -> float:
    pin, pout = PRICING.get(model, DEFAULT_PRICE)
    usd = inp * pin + out * pout + cache_read * pin * CACHE_READ_MULT + cache_write * pin * CACHE_WRITE_MULT
    return round(usd / 1_000_000 * (BATCH_DISCOUNT if batch else 1.0), 8)


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
        """Conservative ceiling used for budget reservations: no cache discount."""
        inp = estimate_tokens(self.system_policy + self.system_role + self.user)
        return price_call(self.model, inp, self.max_tokens)

    def prefix_tokens(self) -> int:
        """Estimated size of the cacheable prefix (tools + system prompt)."""
        return estimate_tokens(_TOOL_JSON + self.system_policy + self.system_role)


@dataclass
class LLMResponse:
    data: dict[str, Any]
    usage: CallUsage


_TOOL_JSON = json.dumps(VOTE_TOOL)


class LLMClient(Protocol):
    def call(self, req: LLMRequest) -> LLMResponse: ...


class LLMError(RuntimeError):
    pass


class DeferredCall(Exception):  # noqa: N818 - control flow, not an error
    """Raised by BatchingClient while collecting requests for a batch. Agents
    re-raise it instead of failing closed, so the item is simply retried in
    the next round once its responses are in the cache."""


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

    @staticmethod
    def _params(req: LLMRequest, messages: list[dict[str, Any]]) -> dict[str, Any]:
        # The breakpoint sits on the LAST system block, so the cached prefix is
        # tools + policy + role. Longer than policy alone, which is what gets the
        # arbiter over Sonnet's 1024-token minimum. The prefix is byte-stable per
        # agent (no timestamps/ids), and the per-item content lives in messages.
        return {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": [
                {"type": "text", "text": req.system_policy},
                {"type": "text", "text": req.system_role, "cache_control": {"type": "ephemeral"}},
            ],
            "tools": [VOTE_TOOL],
            "tool_choice": {"type": "auto"},
            "messages": messages,
        }

    @staticmethod
    def _vote(content) -> dict[str, Any] | None:
        for block in content:
            if block.type == "tool_use" and block.name == "cast_vote":
                return dict(block.input)
        return _extract_json("".join(b.text for b in content if b.type == "text"))

    @staticmethod
    def _add_usage(totals: dict[str, int], u) -> None:
        totals["in"] += u.input_tokens or 0
        totals["out"] += u.output_tokens or 0
        totals["cr"] += getattr(u, "cache_read_input_tokens", 0) or 0
        totals["cw"] += getattr(u, "cache_creation_input_tokens", 0) or 0

    def call(self, req: LLMRequest) -> LLMResponse:
        messages: list[dict[str, Any]] = [{"role": "user", "content": req.user}]
        totals = {"in": 0, "out": 0, "cr": 0, "cw": 0}
        t0 = time.perf_counter()
        data = None
        for attempt in range(2):
            resp = self._client.messages.create(**self._params(req, messages))
            self._add_usage(totals, resp.usage)
            data = self._vote(resp.content)
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

    def run_batch(self, reqs: dict[str, LLMRequest], poll_s: float = 30.0) -> dict[str, LLMResponse]:
        """Submit requests to the Message Batches API (50% off) and wait for them.
        Returns successes keyed like the input; errored/expired/no-vote requests
        are left out and fall back to a normal call on the final pass."""
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        ids = {f"r{i}": key for i, key in enumerate(reqs)}  # custom_id: <= 64 chars, [A-Za-z0-9_-]
        batch = self._client.messages.batches.create(
            requests=[
                Request(
                    custom_id=cid,
                    params=MessageCreateParamsNonStreaming(
                        **self._params(reqs[key], [{"role": "user", "content": reqs[key].user}])
                    ),
                )
                for cid, key in ids.items()
            ]
        )
        t0 = time.perf_counter()
        while batch.processing_status != "ended":
            time.sleep(poll_s)
            batch = self._client.messages.batches.retrieve(batch.id)
            c = batch.request_counts
            print(f"    batch {batch.id}: {c.succeeded + c.errored} done, {c.processing} processing", flush=True)
        out: dict[str, LLMResponse] = {}
        for res in self._client.messages.batches.results(batch.id):  # any order: key by custom_id
            key = ids.get(res.custom_id)
            if key is None or res.result.type != "succeeded":
                continue
            msg = res.result.message
            data = self._vote(msg.content)
            if data is None:
                continue
            req, totals = reqs[key], {"in": 0, "out": 0, "cr": 0, "cw": 0}
            self._add_usage(totals, msg.usage)
            out[key] = LLMResponse(
                data=data,
                usage=CallUsage(
                    agent=req.agent,
                    model=req.model,
                    input_tokens=totals["in"],
                    output_tokens=totals["out"],
                    cache_read_tokens=totals["cr"],
                    cache_write_tokens=totals["cw"],
                    cost_usd=price_call(req.model, totals["in"], totals["out"], totals["cr"], totals["cw"], batch=True),
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                    batch=True,
                ),
            )
        return out


class BatchingClient:
    """Two modes. While `collecting`, every request is recorded and DeferredCall
    is raised (nothing is sent). Otherwise requests go straight to `inner`.
    Sits *inside* CachedClient, so requests already answered never reach it."""

    def __init__(self, inner):
        self.inner = inner
        self.collecting = False
        self.pending: dict[str, LLMRequest] = {}
        self._lock = threading.Lock()

    def call(self, req: LLMRequest) -> LLMResponse:
        if not self.collecting:
            return self.inner.call(req)
        with self._lock:
            self.pending[req.cache_key()] = req
        raise DeferredCall(req.agent)

    def run_batch(self, reqs: dict[str, LLMRequest]) -> dict[str, LLMResponse]:
        return self.inner.run_batch(reqs)


class CachedClient:
    """Response cache keyed by the full prompt hash. Makes eval re-runs free and
    reproducible, and dedupes identical content (copypasta/spam waves)."""

    def __init__(self, inner: LLMClient, store):
        self.inner, self.store = inner, store
        # Batch results are paid for when the batch runs but written to the cache
        # "unbilled"; the first read that has this on reports the spend (so eval
        # cost accounting matches a synchronous run). Off during batch collection.
        self.bill_unbilled = True

    def call(self, req: LLMRequest) -> LLMResponse:
        key = req.cache_key()
        hit = self.store.cache_get(key)
        if hit is not None and "data" in hit:
            usage = CallUsage(**hit.get("usage", {"agent": req.agent, "model": req.model}))
            if hit.get("unbilled") and self.bill_unbilled:
                self.store.cache_put(key, {"data": hit["data"], "usage": hit["usage"]})
                return LLMResponse(data=hit["data"], usage=usage)
            usage.cached, usage.latency_ms = True, 0.0  # list cost kept; actual spend is $0
            return LLMResponse(data=hit["data"], usage=usage)
        resp = self.inner.call(req)
        self.store.cache_put(key, {"data": resp.data, "usage": resp.usage.model_dump()})
        return resp

    def put_unbilled(self, key: str, resp: LLMResponse) -> None:
        self.store.cache_put(key, {"data": resp.data, "usage": resp.usage.model_dump(), "unbilled": True})


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
