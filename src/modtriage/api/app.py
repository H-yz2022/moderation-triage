"""FastAPI service: moderation endpoint, human review queue, usage and eval results.
Also serves the built React UI from frontend/dist when present."""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import ROOT, get_settings
from ..data import read_jsonl
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


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_PER_MIN:
        raise HTTPException(429, "rate limit: 30 moderation requests per minute")
    q.append(now)


class ModerateIn(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    parent_text: str | None = Field(default=None, max_length=10_000)
    metadata: Metadata = Field(default_factory=Metadata)
    mode: str | None = None


class ReviewIn(BaseModel):
    action: Literal["remove", "allow"]
    clause_ids: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=2000)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "provider": triage.provider,
        "mode": settings.mode,
        "gate_loaded": triage.gate is not None,
        "policy_version": triage.policy.version,
        "specialist_model": settings.specialist_model,
        "arbiter_model": settings.arbiter_model,
        "modes": list(MODES),
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
    }


@app.post("/api/moderate")
def moderate(body: ModerateIn, request: Request):
    _rate_limit(request)
    if body.mode and body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    llm_allowed = store.calls_today() < settings.max_daily_llm_calls
    item = Item(
        id=f"api-{int(time.time() * 1000)}", text=body.text, parent_text=body.parent_text, metadata=body.metadata
    )
    item.text = item.text[: settings.max_text_chars]
    d = triage.moderate(item, mode=body.mode, save=False, llm_allowed=llm_allowed)
    decision_id = store.save_decision(item, d)
    out = d.model_dump()
    out.update(
        id=decision_id,
        cost_usd=d.cost_usd,
        list_cost_usd=d.list_cost_usd,
        provider=triage.provider,
        llm_allowed=llm_allowed,
    )
    return out


@app.get("/api/decisions")
def decisions(limit: int = 50, status: str | None = None):
    return store.list_decisions(limit=min(limit, 500), status=status)


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
