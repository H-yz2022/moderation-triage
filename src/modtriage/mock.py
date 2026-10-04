"""Offline stand-in for the Claude agents.

Deterministic heuristics (lexicons + the local gate score) that return the same
`cast_vote` payload shape a real model would. Lets the full system - voting,
arbiter, escalation, budget, cache, API, UI and eval - run with no API key and
in CI. Costs are *simulated* from estimated token counts so cost reports still
show what a real run would roughly cost. Numbers from mock runs are a
pipeline smoke test, not a result to put on a resume.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .features import category_scores, style_features
from .llm import (
    _TOOL_JSON,
    CACHE_MIN_TOKENS,
    CACHE_TTL_S,
    LLMRequest,
    LLMResponse,
    estimate_tokens,
    price_call,
)
from .schemas import CallUsage

CLAUSE_FOR = {
    "harassment": "HAR-1",
    "hate": "HATE-1",
    "violence": "VIO-1",
    "sexual": "SEX-1",
    "profanity": "PROF-1",
    "spam": "SPAM-1",
}


def _vote(p: float, cats: dict[str, float], text: str, extra_clauses=(), exceptions=()) -> dict[str, Any]:
    label = "violation" if p >= 0.5 else "no_violation"
    conf = round(min(0.97, 0.5 + abs(p - 0.5)), 3)
    clauses = []
    if label == "violation":
        clauses = [CLAUSE_FOR[c] for c, s in sorted(cats.items(), key=lambda kv: -kv[1]) if s >= 0.3]
        clauses += list(extra_clauses)
        if not clauses:
            clauses = ["HAR-1"]
    return {
        "label": label,
        "confidence": conf,
        "clause_ids": list(dict.fromkeys(clauses)),
        "exception_ids": list(exceptions) if label == "no_violation" else [],
        "evidence": "",
        "rationale": f"[mock] p={p:.2f} signals={ {k: round(v, 2) for k, v in cats.items()} }",
    }


def _text_agent(meta: dict) -> dict:
    text = meta["text"]
    cats = category_scores(text)
    sty = style_features(text)
    # Mild non-directed profanity is allowed (EX-4).
    if "profanity" in cats and not sty["second_person"] and cats["profanity"] < 0.7:
        cats["profanity"] *= 0.5
    lex = max(cats.values(), default=0.0)
    gate = meta.get("gate_score")
    p = lex if gate is None else 0.45 * lex + 0.55 * gate
    return _vote(p, cats, text)


def _context_agent(meta: dict) -> dict:
    text, parent = meta["text"], meta.get("parent_text") or ""
    cats = category_scores(text)
    sty = style_features(text)
    lex = max(cats.values(), default=0.0)
    gate = meta.get("gate_score")
    p = lex if gate is None else 0.7 * lex + 0.3 * gate
    exceptions, extra = [], []
    if sty["quote_cue"]:
        p -= 0.3
        exceptions.append("EX-1")
    if sty["counter_cue"]:
        p -= 0.3
        exceptions.append("EX-2")
    if parent and sty["second_person"] and cats.get("harassment", 0) > 0.3:
        p += 0.15
        extra.append("HAR-3")
    parent_hostile = max(category_scores(parent).values(), default=0.0) if parent else 0.0
    if parent_hostile > 0.6 and sty["counter_cue"]:
        p -= 0.1
    return _vote(max(0.0, min(1.0, p)), cats, text, extra, exceptions)


def _arbiter(meta: dict) -> dict:
    votes = meta.get("votes", [])
    num = den = 0.0
    clauses: list[str] = []
    for v in votes:
        if v["label"] == "abstain":
            continue
        s = 1 if v["label"] == "violation" else -1
        num += s * v["confidence"]
        den += v["confidence"]
        clauses += v.get("clause_ids", [])
    score = num / den if den else 0.0
    gate = meta.get("gate_score")
    if gate is not None:
        score = 0.7 * score + 0.3 * (2 * gate - 1)
    if abs(score) < 0.15:
        return {"label": "abstain", "confidence": 0.4, "clause_ids": [], "rationale": "[mock] too close to call"}
    label = "violation" if score > 0 else "no_violation"
    return {
        "label": label,
        "confidence": round(min(0.95, 0.55 + abs(score) / 2), 3),
        "clause_ids": list(dict.fromkeys(clauses)) if label == "violation" else [],
        "rationale": f"[mock] weighted panel score {score:.2f}",
    }


def _metadata_agent(meta: dict) -> dict:  # only used if metadata LLM mode is on
    from .agents import metadata_rules

    return metadata_rules(meta["text"], meta.get("metadata") or {})


HANDLERS = {"text": _text_agent, "context": _context_agent, "arbiter": _arbiter, "metadata": _metadata_agent}


class MockClient:
    """Heuristic votes with simulated token costs. Prompt caching is simulated
    the way the API applies it: only when the prefix (tools + system) reaches
    the model's minimum cacheable length, written at 1.25x, read at 0.1x while
    the 5-minute TTL is warm. Below the minimum (every Haiku call here) the
    whole prompt is billed at full price, as in production."""

    def __init__(self):
        self._warm: dict[tuple[str, int], float] = {}  # (model, prefix hash) -> last use
        self._lock = threading.Lock()

    def call(self, req: LLMRequest, cache: bool = True, batch: bool = False) -> LLMResponse:
        data = HANDLERS[req.agent](req.meta)
        total = estimate_tokens(_TOOL_JSON + req.system_policy + req.system_role + req.user)
        out = estimate_tokens(str(data))
        prefix = req.prefix_tokens()
        cr = cw = 0
        if cache and prefix >= CACHE_MIN_TOKENS.get(req.model, 10**9):
            k, now = (req.model, hash((req.system_policy, req.system_role))), time.time()
            with self._lock:
                warm = now - self._warm.get(k, -1e18) < CACHE_TTL_S
                self._warm[k] = now
            cr, cw = (prefix, 0) if warm else (0, prefix)
        inp = total - cr - cw
        usage = CallUsage(
            agent=req.agent,
            model=req.model,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cr,
            cache_write_tokens=cw,
            cost_usd=price_call(req.model, inp, out, cr, cw, batch=batch),
            latency_ms=0.0,
            simulated=True,
            batch=batch,
        )
        return LLMResponse(data=data, usage=usage)

    def run_batch(self, reqs: dict[str, LLMRequest]) -> dict[str, LLMResponse]:
        # Cache hits inside a batch are best-effort, so none are simulated.
        return {k: self.call(r, cache=False, batch=True) for k, r in reqs.items()}
