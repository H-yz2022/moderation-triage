"""FastAPI service: moderation endpoint, human review queue, usage and eval results.
Also serves the built React UI from frontend/dist when present."""

from __future__ import annotations

import csv
import io
import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import ROOT, get_settings
from ..data import read_jsonl
from ..feedback import agreement_report, calibrate_weights, reviews_to_records
from ..pipeline import MODES, Triage
from ..schemas import Item, Metadata
from ..store import Store

settings = get_settings()
store = Store(settings.db_path)
triage = Triage(settings, store=store)

app = FastAPI(title="Content Moderation Triage", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])

RATE_PER_MIN = 30
_hits: dict[str, deque] = defaultdict(deque)


def _rate_limit(request: Request, cost: int = 1) -> None:
    """Sliding one-minute window per client IP; a batch costs one hit per item."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) + cost > RATE_PER_MIN:
        raise HTTPException(429, f"rate limit: {RATE_PER_MIN} moderated items per minute")
    q.extend([now] * cost)


class ModerateIn(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    parent_text: str | None = Field(default=None, max_length=10_000)
    metadata: Metadata = Field(default_factory=Metadata)
    mode: str | None = None


MAX_BATCH = 25


class BatchItemIn(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    parent_text: str | None = Field(default=None, max_length=10_000)
    metadata: Metadata = Field(default_factory=Metadata)
    id: str | None = Field(default=None, max_length=200)


class BatchIn(BaseModel):
    items: list[BatchItemIn] = Field(min_length=1, max_length=MAX_BATCH)
    mode: str | None = None


class ReviewIn(BaseModel):
    action: Literal["remove", "allow"]
    clause_ids: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=2000)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "ai_enabled": settings.ai_enabled,
        "provider": triage.provider,
        "mode": settings.mode,
        "gate_loaded": triage.gate is not None,
        "policy_version": triage.policy.version,
        "specialist_model": settings.specialist_model,
        "arbiter_model": settings.arbiter_model,
        "modes": list(MODES),
        "agent_weights": triage.weights,
        "policy_examples": settings.policy_examples,
    }


@app.get("/api/policy")
def policy():
    p = triage.policy
    return {
        "version": p.version,
        "name": p.name,
        "categories": p.categories,
        "clauses": [c.__dict__ for c in p.clauses.values()],
        "exceptions": [{"id": k, **v} for k, v in p.exceptions.items()],
        "examples": list(p.examples),
    }


def _check_mode(mode: str | None) -> None:
    if mode and mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")


def _moderate_one(item: Item, mode: str | None) -> dict:
    # re-checked per item so a batch can't run past the daily cap
    llm_allowed = store.calls_today() < settings.max_daily_llm_calls
    item.text = item.text[: settings.max_text_chars]
    d = triage.moderate(item, mode=mode, save=False, llm_allowed=llm_allowed)
    decision_id = store.save_decision(item, d)
    out = d.model_dump()
    out.update(
        id=decision_id,
        cost_usd=d.cost_usd,
        list_cost_usd=d.list_cost_usd,
        llm_calls=d.llm_calls,
        provider=triage.provider,
        llm_allowed=llm_allowed,
    )
    return out


@app.post("/api/moderate")
def moderate(body: ModerateIn, request: Request):
    _check_mode(body.mode)
    _rate_limit(request)
    item = Item(
        id=f"api-{int(time.time() * 1000)}", text=body.text, parent_text=body.parent_text, metadata=body.metadata
    )
    return _moderate_one(item, body.mode)


@app.post("/api/moderate/batch")
def moderate_batch(body: BatchIn, request: Request):
    _check_mode(body.mode)
    _rate_limit(request, cost=len(body.items))
    stamp = int(time.time() * 1000)
    results = [
        _moderate_one(
            Item(id=it.id or f"batch-{stamp}-{i}", text=it.text, parent_text=it.parent_text, metadata=it.metadata),
            body.mode,
        )
        for i, it in enumerate(body.items)
    ]
    counts = {a: sum(r["action"] == a for r in results) for a in ("remove", "allow", "escalate")}
    return {
        "results": results,
        "summary": {
            "n": len(results),
            "actions": counts,
            "cost_usd": round(sum(r["cost_usd"] for r in results), 6),
            "llm_calls": sum(r["llm_calls"] for r in results),
        },
    }


@app.get("/api/decisions")
def decisions(limit: int = 50, status: str | None = None, q: str | None = None, action: str | None = None):
    return store.list_decisions(limit=min(limit, 500), status=status, q=(q or "").strip()[:200] or None, action=action)


CSV_COLS = [
    "id", "created_at", "action", "decided_by", "categories", "clause_ids", "score", "confidence",
    "gate_score", "cost_usd", "llm_calls", "review_status", "reviewer_action", "reviewer_clauses",
    "reviewer_note", "policy_version", "text", "parent_text",
]  # fmt: skip


@app.get("/api/export/decisions.csv")
def export_decisions(status: str | None = None, limit: int = 10_000):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLS, extrasaction="ignore")
    w.writeheader()
    for d in store.list_decisions(limit=min(limit, 100_000), status=status):
        w.writerow({k: ";".join(map(str, d[k])) if isinstance(d.get(k), list) else d.get(k) for k in CSV_COLS})
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="decisions.csv"'},
    )


@app.get("/api/export/reviews.jsonl")
def export_reviews():
    """Human-reviewed cases as an eval gold set (`modtriage eval --data reviews.jsonl`)."""
    recs = reviews_to_records(store.reviewed(), triage.policy)
    return Response(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="human_reviews.jsonl"'},
    )


@app.get("/api/review/agreement")
def review_agreement(min_n: int = 10):
    report = agreement_report(store.reviewed())
    report["calibration"] = calibrate_weights(report, min_n=max(1, min_n))
    report["active_weights"] = triage.weights
    return report


@app.get("/api/decisions/{decision_id}")
def decision(decision_id: int):
    d = store.get_decision(decision_id)
    if not d:
        raise HTTPException(404, "not found")
    return d


@app.post("/api/queue/{decision_id}/review")
def review(decision_id: int, body: ReviewIn):
    d = store.get_decision(decision_id)
    if not d:
        raise HTTPException(404, "not found")
    bad = [c for c in body.clause_ids if c not in triage.policy.clauses]
    if bad:
        raise HTTPException(400, f"unknown clause ids {bad}")
    if body.action == "remove" and not body.clause_ids:
        raise HTTPException(400, "a removal must cite at least one clause")
    return store.review(decision_id, body.action, body.clause_ids, body.note)


@app.get("/api/queue/stats")
def queue_stats():
    return store.queue_stats()


@app.get("/api/usage")
def usage():
    u = store.usage_summary()
    u.update(
        daily_cap=settings.max_daily_llm_calls,
        provider=triage.provider,
        run_budget_usd=settings.run_budget_usd,
        ledger_spent_usd=round(triage.ledger.spent, 6),
    )
    return u


@app.get("/api/eval/latest")
def eval_latest():
    p = Path(settings.reports_dir) / "eval_latest.json"
    if not p.exists():
        raise HTTPException(404, "no eval report yet - run `python -m modtriage.cli eval`")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/samples")
def samples():
    p = ROOT / "data" / "sample" / "dev_sample.jsonl"
    return read_jsonl(p) if p.exists() else []


# ---- static UI --------------------------------------------------------------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
