"""SQLite persistence: decisions + human review queue, LLM response cache,
and a usage ledger (for daily caps and cost reporting)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import Decision, Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT, created_at TEXT, text TEXT, parent_text TEXT, metadata_json TEXT,
    action TEXT, categories TEXT, clause_ids TEXT, score REAL, confidence REAL,
    decided_by TEXT, reasons_json TEXT, votes_json TEXT, usage_json TEXT,
    cost_usd REAL, llm_calls INTEGER, gate_score REAL, policy_version TEXT,
    review_status TEXT,                -- auto | pending | reviewed
    reviewer_action TEXT, reviewer_clauses TEXT, reviewer_note TEXT, reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_review ON decisions(review_status);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, data_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, day TEXT, agent TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
    cache_write_tokens INTEGER, cost_usd REAL, simulated INTEGER
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON llm_usage(day);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # -- cache --------------------------------------------------------------
    def cache_get(self, key: str) -> dict | None:
        rows = self._all("SELECT data_json FROM llm_cache WHERE key=?", (key,))
        return json.loads(rows[0]["data_json"]) if rows else None

    def cache_put(self, key: str, data: dict) -> None:
        self._exec("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?)", (key, json.dumps(data), _now().isoformat()))

    # -- usage --------------------------------------------------------------
    def record_usage(self, decision: Decision) -> None:
        now = _now()
        for u in decision.usage:
            if u.cached:
                continue
            self._exec(
                "INSERT INTO llm_usage (ts, day, agent, model, input_tokens, output_tokens, cache_read_tokens,"
                " cache_write_tokens, cost_usd, simulated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    now.isoformat(),
                    now.date().isoformat(),
                    u.agent,
                    u.model,
                    u.input_tokens,
                    u.output_tokens,
                    u.cache_read_tokens,
                    u.cache_write_tokens,
                    u.cost_usd,
                    int(u.simulated),
                ),
            )

    def calls_today(self) -> int:
        day = _now().date().isoformat()
        return self._all("SELECT COUNT(*) AS n FROM llm_usage WHERE day=?", (day,))[0]["n"]

    def usage_summary(self) -> dict[str, Any]:
        day = _now().date().isoformat()
        by_model = self._all(
            "SELECT model, agent, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens,"
            " SUM(output_tokens) AS output_tokens, SUM(cache_read_tokens) AS cache_read_tokens,"
            " ROUND(SUM(cost_usd), 6) AS cost_usd, MAX(simulated) AS simulated"
            " FROM llm_usage GROUP BY model, agent ORDER BY cost_usd DESC"
        )
        today = self._all(
            "SELECT COUNT(*) AS calls, ROUND(COALESCE(SUM(cost_usd),0), 6) AS cost_usd FROM llm_usage WHERE day=?",
            (day,),
        )[0]
        dec = self._all("SELECT decided_by, COUNT(*) AS n FROM decisions GROUP BY decided_by")
        cache_n = self._all("SELECT COUNT(*) AS n FROM llm_cache")[0]["n"]
        return {"today": today, "by_model": by_model, "decided_by": dec, "cache_entries": cache_n}

    # -- decisions / queue ----------------------------------------------------
    def save_decision(self, item: Item, d: Decision) -> int:
        cur = self._exec(
            "INSERT INTO decisions (item_id, created_at, text, parent_text, metadata_json, action, categories,"
            " clause_ids, score, confidence, decided_by, reasons_json, votes_json, usage_json, cost_usd, llm_calls,"
            " gate_score, policy_version, review_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.item_id,
                _now().isoformat(),
                item.text,
                item.parent_text,
                item.metadata.model_dump_json(exclude_none=True),
                d.action,
                json.dumps(d.categories),
                json.dumps(d.clause_ids),
                d.score,
                d.confidence,
                d.decided_by,
                json.dumps(d.reasons),
                json.dumps([v.model_dump() for v in d.votes]),
                json.dumps([u.model_dump() for u in d.usage]),
                d.cost_usd,
                d.llm_calls,
                d.gate_score,
                d.policy_version,
                "pending" if d.action == "escalate" else "auto",
            ),
        )
        self.record_usage(d)
        return int(cur.lastrowid)

    _JSON_COLS = {
        "categories": "categories",
        "clause_ids": "clause_ids",
        "reasons_json": "reasons",
        "votes_json": "votes",
        "usage_json": "usage",
        "metadata_json": "metadata",
        "reviewer_clauses": "reviewer_clauses",
    }

    @classmethod
    def _row(cls, r: dict) -> dict:
        out: dict[str, Any] = {}
        for k, v in r.items():
            if k in cls._JSON_COLS:
                out[cls._JSON_COLS[k]] = json.loads(v) if v else ({} if k == "metadata_json" else [])
            else:
                out[k] = v
        return out

    def list_decisions(self, limit: int = 50, status: str | None = None) -> list[dict]:
        if status:
            rows = self._all("SELECT * FROM decisions WHERE review_status=? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            rows = self._all("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
        return [self._row(r) for r in rows]

    def get_decision(self, decision_id: int) -> dict | None:
        rows = self._all("SELECT * FROM decisions WHERE id=?", (decision_id,))
        return self._row(rows[0]) if rows else None

    def review(self, decision_id: int, action: str, clause_ids: list[str], note: str = "") -> dict | None:
        self._exec(
            "UPDATE decisions SET review_status='reviewed', reviewer_action=?, reviewer_clauses=?,"
            " reviewer_note=?, reviewed_at=? WHERE id=?",
            (action, json.dumps(clause_ids), note, _now().isoformat(), decision_id),
        )
        return self.get_decision(decision_id)

    def queue_stats(self) -> dict[str, int]:
        rows = self._all("SELECT review_status, COUNT(*) AS n FROM decisions GROUP BY review_status")
        return {r["review_status"]: r["n"] for r in rows}
