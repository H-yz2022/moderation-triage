"""Vote aggregation.

Weighted, confidence-scaled vote:  score = sum(w_a * c_a * s_a) / sum(w_a * c_a)
with s_a = +1 (violation) / -1 (no_violation); abstentions and invalid votes
don't count. Outcomes:

  remove    score >= remove_threshold and no dissenting valid vote
  allow     score <= -allow_threshold and no dissenting valid vote
  arbiter   anything else (split panel / weak consensus)

Guards applied after the vote (safety asymmetry):
  - high-severity guard: an "allow" is turned into "escalate" if any valid vote
    cites a high-severity clause (hate/violence) with confidence >= threshold.
  - metadata risk: a high metadata risk prior blocks auto-allow of a split case.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .policy import Policy
from .schemas import Vote

DEFAULT_WEIGHTS = {"text": 1.0, "context": 1.1, "metadata": 0.8, "arbiter": 1.0}


@dataclass
class Tally:
    outcome: str  # remove | allow | arbiter | escalate
    score: float
    confidence: float
    clause_ids: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    risk: float = 0.0


def counted(votes: list[Vote]) -> list[Vote]:
    out = []
    for v in votes:
        if not v.valid or v.label == "abstain":
            continue
        if v.agent == "metadata" and (v.label != "violation" or v.clause_ids != ["SPAM-1"]):
            continue  # metadata agent may only assert spam
        out.append(v)
    return out


def _clauses(votes: list[Vote], policy: Policy) -> tuple[list[str], list[str]]:
    c = Counter(cid for v in votes if v.label == "violation" for cid in v.clause_ids)
    ids = [cid for cid, _ in c.most_common()]
    cats = sorted({policy.category_of(cid) for cid in ids if policy.category_of(cid)})
    return ids, cats


def weighted_score(votes: list[Vote], weights: dict[str, float] | None = None) -> tuple[float, float]:
    w = weights or DEFAULT_WEIGHTS
    num = den = 0.0
    for v in counted(votes):
        wc = w.get(v.agent, 1.0) * max(v.confidence, 1e-3)
        num += wc * (1 if v.label == "violation" else -1)
        den += wc
    if den == 0:
        return 0.0, 0.0
    score = num / den
    conf = sum(v.confidence for v in counted(votes)) / len(counted(votes))
    return round(score, 4), round(conf, 4)


def aggregate(
    votes: list[Vote],
    policy: Policy,
    remove_threshold: float = 0.45,
    allow_threshold: float = 0.45,
    high_sev_conf: float = 0.5,
    weights: dict[str, float] | None = None,
) -> Tally:
    live = counted(votes)
    risk = max((v.risk or 0.0) for v in votes) if votes else 0.0
    if not live:
        return Tally("escalate", 0.0, 0.0, reasons=["no valid votes"], risk=risk)
    score, conf = weighted_score(votes, weights)
    n_viol = sum(v.label == "violation" for v in live)
    n_ok = len(live) - n_viol
    clause_ids, cats = _clauses(live, policy)
    t = Tally("arbiter", score, conf, clause_ids, cats, risk=risk)

    if score >= remove_threshold and n_ok == 0:
        t.outcome = "remove"
        t.reasons.append(f"panel agrees on violation ({n_viol} vote(s), score {score:+.2f})")
    elif score <= -allow_threshold and n_viol == 0:
        t.outcome = "allow"
        t.reasons.append(f"panel agrees no violation ({n_ok} vote(s), score {score:+.2f})")
    else:
        t.reasons.append(f"split/weak panel ({n_viol} violation vs {n_ok} no-violation, score {score:+.2f})")
    return apply_guards(t, votes, policy, high_sev_conf)


def apply_guards(t: Tally, votes: list[Vote], policy: Policy, high_sev_conf: float) -> Tally:
    if t.outcome != "allow":
        return t
    high = policy.high_severity_categories()
    for v in counted(votes):
        if v.label == "violation" and v.confidence >= high_sev_conf and set(v.categories) & high:
            t.outcome = "escalate"
            t.reasons.append(f"high-severity guard: {v.agent} flagged {sorted(set(v.categories) & high)}")
            break
    return t


def majority(votes: list[Vote], policy: Policy) -> Tally:
    """Plain majority vote (ablation baseline): ties escalate."""
    live = counted(votes)
    n_viol = sum(v.label == "violation" for v in live)
    n_ok = len(live) - n_viol
    ids, cats = _clauses(live, policy)
    if n_viol > n_ok:
        return Tally("remove", 1.0, 1.0, ids, cats, ["majority violation"])
    if n_ok > n_viol:
        return Tally("allow", -1.0, 1.0, [], [], ["majority no violation"])
    return Tally("escalate", 0.0, 0.0, ids, cats, ["tie"])
