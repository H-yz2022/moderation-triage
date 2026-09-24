"""Evaluation harness: precision / recall / escalation / cost per configuration.

Definitions (y = gold violation, weights re-balance a stratified sample back to
population prevalence when present):

  auto            items the system decided without a human (remove | allow)
  precision       P(y=1 | removed)                     - wrongful takedowns
  auto_recall     removed & y=1 / all y=1               - caught with NO human
  system_recall   (removed | escalated) & y=1 / all y=1 - caught if reviewers are right
  escalation_rate share of items sent to the human queue (reviewer workload)
  f1_auto         F1 over auto-decided items only
  cost            list cost per 1k items (pre-cache) and actual spend
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .data import record_to_item
from .policy import Policy
from .schemas import Decision, Vote
from .voting import aggregate


@dataclass
class Row:
    y: int
    w: float
    gold_cats: list[str]
    action: str
    pred_cats: list[str]
    decided_by: str
    list_cost: float
    spend: float
    calls: int
    cache_hits: int
    latency_ms: float
    votes: list[Vote]


def _div(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def core_metrics(y: np.ndarray, w: np.ndarray, action: np.ndarray) -> dict[str, float | None]:
    rem, alw, esc = action == "remove", action == "allow", action == "escalate"
    pos = y == 1
    tp = w[rem & pos].sum()
    fp = w[rem & ~pos].sum()
    fn_auto = w[alw & pos].sum()
    p = _div(tp, tp + fp)
    r_auto_only = _div(tp, tp + fn_auto)
    f1 = round(2 * p * r_auto_only / (p + r_auto_only), 4) if p and r_auto_only else None
    return {
        "precision": p,
        "auto_recall": _div(tp, w[pos].sum()),
        "system_recall": _div(w[(rem | esc) & pos].sum(), w[pos].sum()),
        "recall_on_auto": r_auto_only,
        "f1_auto": f1,
        "escalation_rate": _div(w[esc].sum(), w.sum()),
        "automation_rate": _div(w[rem | alw].sum(), w.sum()),
        "false_allow_rate": _div(fn_auto, w[pos].sum()),
        "wrongful_removal_rate": _div(fp, w[~pos].sum()),
    }


def bootstrap_ci(y, w, action, n_boot: int = 500, seed: int = 7) -> dict[str, list[float]]:
    rng = np.random.default_rng(seed)
    keys = ["precision", "auto_recall", "system_recall", "escalation_rate"]
    samples: dict[str, list[float]] = {k: [] for k in keys}
    n = len(y)
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        m = core_metrics(y[idx], w[idx], action[idx])
        for k in keys:
            if m[k] is not None:
                samples[k].append(m[k])
    return {
        k: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]
        for k, v in samples.items()
        if v
    }


def category_metrics(rows: list[Row], categories: list[str]) -> dict[str, dict]:
    out = {}
    for c in categories:
        gold = np.array([c in r.gold_cats for r in rows])
        pred = np.array([r.action == "remove" and c in r.pred_cats for r in rows])
        w = np.array([r.w for r in rows])
        tp = w[gold & pred].sum()
        out[c] = {"support": int(gold.sum()), "precision": _div(tp, w[pred].sum()), "recall": _div(tp, w[gold].sum())}
    return out


def agent_metrics(rows: list[Row]) -> dict[str, dict]:
    """Stand-alone quality of each specialist on the items it voted on, plus
    how often text/context agents agree (error correlation proxy)."""
    stats: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    agree = n_both = 0
    invalid = total = 0
    for r in rows:
        labels = {}
        for v in r.votes:
            total += 1
            if not v.valid and "clause" in v.invalid_reason:
                invalid += 1
            if not v.valid or v.label == "abstain":
                stats[v.agent]["abstain"] += 1
                continue
            pred = v.label == "violation"
            labels[v.agent] = pred
            s = stats[v.agent]
            s["n"] += 1
            s["tp"] += pred and r.y == 1
            s["fp"] += pred and r.y == 0
            s["fn"] += (not pred) and r.y == 1
        if "text" in labels and "context" in labels:
            n_both += 1
            agree += labels["text"] == labels["context"]
    out = {}
    for a, s in stats.items():
        out[a] = {
            "votes": int(s["n"]),
            "abstain": int(s["abstain"]),
            "precision": _div(s["tp"], s["tp"] + s["fp"]),
            "recall": _div(s["tp"], s["tp"] + s["fn"]),
        }
    out["_panel"] = {"text_context_agreement": _div(agree, n_both), "invalid_citation_rate": _div(invalid, total)}
    return out


def citation_accuracy(rows: list[Row]) -> float | None:
    hits = n = 0
    for r in rows:
        if r.action == "remove" and r.y == 1 and r.gold_cats:
            n += 1
            hits += bool(set(r.pred_cats) & set(r.gold_cats))
    return _div(hits, n)


def summarize(rows: list[Row], policy: Policy, mode: str, n_boot: int) -> dict[str, Any]:
    y = np.array([r.y for r in rows])
    w = np.array([r.w for r in rows], dtype=float)
    a = np.array([r.action for r in rows])
    lat = np.array([r.latency_ms for r in rows])
    n = len(rows)
    by = defaultdict(int)
    for r in rows:
        by[r.decided_by] += 1
    return {
        "mode": mode,
        "n": n,
        "positives": int(y.sum()),
        "metrics": core_metrics(y, w, a),
        "ci95": bootstrap_ci(y, w, a, n_boot) if n_boot else {},
        "per_category": category_metrics(rows, list(policy.categories)),
        "citation_accuracy": citation_accuracy(rows),
        "agents": agent_metrics(rows),
        "decided_by": dict(by),
        "cost": {
            "list_cost_per_1k_usd": round(1000 * sum(r.list_cost for r in rows) / max(n, 1), 4),
            "actual_spend_usd": round(sum(r.spend for r in rows), 4),
            "llm_calls_per_item": round(sum(r.calls for r in rows) / max(n, 1), 3),
            "cache_hit_rate": _div(sum(r.cache_hits for r in rows), sum(r.calls for r in rows)),
            "arbiter_rate": _div(sum(any(v.agent == "arbiter" for v in r.votes) for r in rows), n),
        },
        "latency_ms": {"p50": round(float(np.percentile(lat, 50)), 1), "p95": round(float(np.percentile(lat, 95)), 1)},
    }


def threshold_sweep(rows: list[Row], policy: Policy, taus=None) -> list[dict]:
    """Re-aggregate stored panel votes at different thresholds (no new LLM calls).
    Arbiter is not re-run: split cases count as escalations."""
    taus = taus or [round(t, 2) for t in np.arange(0.1, 0.95, 0.1)]
    y = np.array([r.y for r in rows])
    w = np.array([r.w for r in rows], dtype=float)
    out = []
    for t in taus:
        acts = []
        for r in rows:
            panel = [v for v in r.votes if v.agent != "arbiter"]
            if r.decided_by == "gate":
                acts.append(r.action)
                continue
            tally = aggregate(panel, policy, t, t)
            acts.append("escalate" if tally.outcome == "arbiter" else tally.outcome)
        m = core_metrics(y, w, np.array(acts))
        out.append(
            {"threshold": t, **{k: m[k] for k in ("precision", "auto_recall", "system_recall", "escalation_rate")}}
        )
    return out


def run_mode(triage, records: list[dict], mode: str, progress: bool = True) -> list[Row]:
    rows = []
    for i, rec in enumerate(records):
        t0 = time.perf_counter()
        d: Decision = triage.moderate(record_to_item(rec), mode=mode, save=False)
        wall = (time.perf_counter() - t0) * 1000
        rows.append(
            Row(
                y=int(rec["label"]),
                w=float(rec.get("weight", 1.0)),
                gold_cats=list(rec.get("categories", [])),
                action=d.action,
                pred_cats=d.categories,
                decided_by=d.decided_by,
                list_cost=d.list_cost_usd,
                spend=d.cost_usd,
                calls=len(d.usage),
                cache_hits=sum(u.cached for u in d.usage),
                latency_ms=wall,
                votes=d.votes,
            )
        )
        if progress and (i + 1) % 50 == 0:
            label = "simulated cost, $0 charged" if triage.provider == "mock" else "spend"
            print(f"  [{mode}] {i + 1}/{len(records)}  {label} ${triage.ledger.spent:.4f}", flush=True)
    return rows


def to_markdown(report: dict) -> str:
    L = [
        f"# Eval report - {report['dataset']}",
        "",
        f"- generated: {report['generated_at']}",
        f"- provider: **{report['provider']}**"
        + (" (heuristic mock - pipeline smoke test, NOT model quality)" if report["provider"] == "mock" else ""),
        f"- items: {report['n_items']} (weighted: {report['weighted']})",
        f"- policy: {report['policy_version']}",
        "",
        "| mode | precision | auto recall | system recall | escalation | F1 (auto) | $/1k items | calls/item | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    def f(x):
        return "-" if x is None else f"{x:.3f}"

    for r in report["results"]:
        m, c = r["metrics"], r["cost"]
        L.append(
            f"| {r['mode']} | {f(m['precision'])} | {f(m['auto_recall'])} | {f(m['system_recall'])} | "
            f"{f(m['escalation_rate'])} | {f(m['f1_auto'])} | {c['list_cost_per_1k_usd']:.3f} | "
            f"{c['llm_calls_per_item']:.2f} | {r['latency_ms']['p95']:.0f} |"
        )
    for r in report["results"]:
        if r.get("ci95"):
            L += [
                "",
                f"**{r['mode']}** 95% bootstrap CI: "
                + ", ".join(f"{k} {v[0]:.3f}-{v[1]:.3f}" for k, v in r["ci95"].items()),
            ]
    best = report["results"][-1]
    L += [
        "",
        f"## Per-category ({best['mode']})",
        "",
        "| category | support | precision | recall |",
        "|---|---|---|---|",
    ]
    for c, m in best["per_category"].items():
        L.append(f"| {c} | {m['support']} | {f(m['precision'])} | {f(m['recall'])} |")
    L += [
        "",
        f"Citation accuracy (cited category matches a gold category, true removals): {f(best['citation_accuracy'])}",
    ]
    if report.get("sweep"):
        L += [
            "",
            f"## Threshold sweep ({report['sweep_mode']}, panel only)",
            "",
            "| threshold | precision | auto recall | system recall | escalation |",
            "|---|---|---|---|---|",
        ]
        for s in report["sweep"]:
            L.append(
                f"| {s['threshold']} | {f(s['precision'])} | {f(s['auto_recall'])} | {f(s['system_recall'])} | {f(s['escalation_rate'])} |"
            )
    return "\n".join(L) + "\n"


def run_eval(
    make_triage,
    records: list[dict],
    modes: list[str],
    dataset: str,
    policy: Policy,
    reports_dir: Path,
    n_boot: int = 500,
    provider: str = "mock",
) -> dict:
    results, rows_by_mode = [], {}
    for mode in modes:
        tri = make_triage()  # fresh budget ledger per configuration
        print(f"running mode={mode} on {len(records)} items")
        rows = run_mode(tri, records, mode)
        rows_by_mode[mode] = rows
        results.append(summarize(rows, policy, mode, n_boot))
    sweep_mode = next((m for m in ("full", "cascade") if m in rows_by_mode), None)
    report = {
        "dataset": dataset,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": provider,
        "policy_version": policy.version,
        "n_items": len(records),
        "weighted": any("weight" in r for r in records),
        "results": results,
        "sweep_mode": sweep_mode,
        "sweep": threshold_sweep(rows_by_mode[sweep_mode], policy) if sweep_mode else [],
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    for name in (f"eval_{stamp}", "eval_latest"):
        (reports_dir / f"{name}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        (reports_dir / f"{name}.md").write_text(to_markdown(report), encoding="utf-8")
    return report
