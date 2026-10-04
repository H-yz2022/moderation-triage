"""Human-review feedback loop.

Every reviewed queue item is a labelled example the system got wrong or
couldn't decide. This module turns those reviews into:

  agreement_report   how often the reviewer agreed with the system's lean, the
                     arbiter, and each specialist (and whether they cited the
                     same clauses)
  calibrate_weights  vote weights per agent from their accuracy against the
                     reviewer (log-odds, the optimal weighting for independent
                     voters), written to agent_weights.json and picked up by
                     the pipeline
  reviews_to_records a gold set in the eval JSONL format, so `eval --data`
                     can score any mode against human decisions

Caveat: reviewed items are the *escalated* ones, i.e. the hard cases. Accuracy
on them understates accuracy on the full stream, and weights calibrated on them
say who to trust when the panel disagrees - which is exactly when weights matter.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from .policy import Policy
from .voting import DEFAULT_WEIGHTS

VOTING_AGENTS = ("text", "context", "arbiter")


def _vote_action(v: dict) -> str | None:
    if not v.get("valid", True) or v.get("label") == "abstain":
        return None
    return "remove" if v.get("label") == "violation" else "allow"


def system_lean(d: dict) -> str | None:
    """What the system leaned towards before a human stepped in: the arbiter's
    vote when it gave one, otherwise the sign of the weighted panel score."""
    for v in d.get("votes", []):
        if v.get("agent") == "arbiter" and _vote_action(v):
            return _vote_action(v)
    score = d.get("score") or 0.0
    if score > 0:
        return "remove"
    if score < 0:
        return "allow"
    return None


def _rate(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def agreement_report(reviewed: list[dict]) -> dict[str, Any]:
    rows = [d for d in reviewed if d.get("reviewer_action") in ("remove", "allow")]
    actions: dict[str, int] = defaultdict(int)
    lean_n = lean_agree = 0
    agents: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    jaccard: list[float] = []
    by_route: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "agree": 0})

    for d in rows:
        human = d["reviewer_action"]
        actions[human] += 1
        lean = system_lean(d)
        if lean:
            lean_n += 1
            lean_agree += lean == human
            r = by_route[d.get("decided_by") or "unknown"]
            r["n"] += 1
            r["agree"] += lean == human
        for v in d.get("votes", []):
            a = v.get("agent", "?")
            if a == "metadata" and v.get("label") != "violation":
                continue  # metadata only asserts spam; its abstains aren't opinions
            pred = _vote_action(v)
            s = agents[a]
            if pred is None:
                s["abstain"] += 1
                continue
            s["n"] += 1
            s["agree"] += pred == human
            if human == "remove":
                s["tp" if pred == "remove" else "fn"] += 1
            else:
                s["fp" if pred == "remove" else "tn"] += 1
        if human == "remove" and d.get("clause_ids"):
            a, b = set(d["clause_ids"]), set(d.get("reviewer_clauses") or [])
            jaccard.append(len(a & b) / len(a | b) if a | b else 1.0)

    per_agent = {
        a: {
            "n": int(s["n"]),
            "abstain": int(s["abstain"]),
            "accuracy": _rate(s["agree"], s["n"]),
            "precision": _rate(s["tp"], s["tp"] + s["fp"]),
            "recall": _rate(s["tp"], s["tp"] + s["fn"]),
        }
        for a, s in sorted(agents.items())
    }
    return {
        "n_reviewed": len(rows),
        "reviewer_actions": dict(actions),
        "lean_agreement": {"n": lean_n, "rate": _rate(lean_agree, lean_n)},
        "by_route": {k: {**v, "rate": _rate(v["agree"], v["n"])} for k, v in sorted(by_route.items())},
        "agents": per_agent,
        "clause_agreement": {
            "n": len(jaccard),
            "mean_jaccard": round(sum(jaccard) / len(jaccard), 4) if jaccard else None,
        },
    }


def calibrate_weights(
    report: dict[str, Any], min_n: int = 10, base: dict[str, float] | None = None
) -> dict[str, dict[str, Any]]:
    """Log-odds weights from smoothed per-agent accuracy. Agents with fewer than
    `min_n` decisive votes keep their default. Only ratios matter to the vote
    (the score is normalized), so weights are rescaled to keep the calibrated
    agents' total equal to their default total, for readability."""
    base = dict(base or DEFAULT_WEIGHTS)
    raw: dict[str, float] = {}
    out: dict[str, dict[str, Any]] = {}
    for agent, w0 in base.items():
        s = report["agents"].get(agent, {})
        n = s.get("n", 0)
        if agent in VOTING_AGENTS and n >= min_n:
            acc = (s["accuracy"] * n + 1) / (n + 2)  # Laplace smoothing
            raw[agent] = max(math.log(acc / (1 - acc)), 0.05)  # never let an agent vote against itself
            out[agent] = {"weight": None, "default": w0, "n": n, "accuracy": s["accuracy"], "calibrated": True}
        else:
            out[agent] = {"weight": w0, "default": w0, "n": n, "accuracy": s.get("accuracy"), "calibrated": False}
    if raw:
        scale = sum(base[a] for a in raw) / sum(raw.values())
        for a, r in raw.items():
            out[a]["weight"] = round(min(max(r * scale, 0.1), 3.0), 3)
    return out


def weights_only(calibration: dict[str, dict[str, Any]]) -> dict[str, float]:
    return {a: c["weight"] for a, c in calibration.items()}


def save_weights(path: str | Path, calibration: dict[str, dict[str, Any]], report: dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"weights": weights_only(calibration), "calibration": calibration, "n_reviewed": report["n_reviewed"]}
    p.write_text(json.dumps(body, indent=1), encoding="utf-8")
    return p


def load_weights(path: str | Path) -> dict[str, float] | None:
    p = Path(path)
    if not p.exists():
        return None
    w = json.loads(p.read_text(encoding="utf-8")).get("weights") or {}
    return {k: float(v) for k, v in w.items()} or None


def reviews_to_records(reviewed: list[dict], policy: Policy) -> list[dict]:
    """Reviewed decisions -> eval records (same shape as `prepare` output)."""
    out = []
    for d in reviewed:
        human = d.get("reviewer_action")
        if human not in ("remove", "allow"):
            continue
        clauses = d.get("reviewer_clauses") or []
        cats = sorted({c for c in (policy.category_of(cid) for cid in clauses) if c})
        out.append(
            {
                "id": f"review-{d['id']}",
                "text": d["text"],
                "parent_text": d.get("parent_text"),
                "metadata": d.get("metadata") or {},
                "label": int(human == "remove"),
                "categories": cats if human == "remove" else [],
                "source": "human_review",
                "reviewed_at": d.get("reviewed_at"),
            }
        )
    return out
