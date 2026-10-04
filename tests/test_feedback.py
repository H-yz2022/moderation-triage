import json

import pytest
from conftest import make_settings

from modtriage.feedback import (
    agreement_report,
    calibrate_weights,
    load_weights,
    reviews_to_records,
    save_weights,
    system_lean,
)
from modtriage.pipeline import Triage
from modtriage.policy import load_policy
from modtriage.schemas import Item
from modtriage.store import Store


def vote(agent, label, conf=0.8, valid=True):
    return {"agent": agent, "label": label, "confidence": conf, "valid": valid}


def reviewed(i, human, votes, score=0.0, clauses=None, reviewer_clauses=None, decided_by="arbiter"):
    return {
        "id": i,
        "text": f"comment {i}",
        "parent_text": None,
        "metadata": {},
        "score": score,
        "votes": votes,
        "clause_ids": clauses or [],
        "decided_by": decided_by,
        "reviewer_action": human,
        "reviewer_clauses": reviewer_clauses or [],
        "reviewed_at": "2026-09-28T00:00:00+00:00",
    }


def test_system_lean_prefers_arbiter_then_score():
    assert system_lean({"votes": [vote("arbiter", "violation")], "score": -0.9}) == "remove"
    assert system_lean({"votes": [vote("arbiter", "abstain")], "score": -0.2}) == "allow"
    assert system_lean({"votes": [], "score": 0.0}) is None


def test_agreement_report_counts_agents_and_clauses():
    rows = [
        reviewed(1, "remove", [vote("text", "violation"), vote("context", "no_violation")], 0.1, ["HAR-1"], ["HAR-1"]),
        reviewed(2, "allow", [vote("text", "violation"), vote("context", "no_violation")], -0.1),
        reviewed(3, "remove", [vote("text", "abstain"), vote("metadata", "abstain")], 0.0, ["HAR-1"], ["HATE-1"]),
    ]
    r = agreement_report(rows)
    assert r["n_reviewed"] == 3 and r["reviewer_actions"] == {"remove": 2, "allow": 1}
    assert r["lean_agreement"] == {"n": 2, "rate": 1.0}
    assert r["agents"]["text"]["accuracy"] == 0.5 and r["agents"]["text"]["abstain"] == 1
    assert r["agents"]["context"]["accuracy"] == 0.5
    assert "metadata" not in r["agents"]  # metadata abstains are not opinions
    assert r["clause_agreement"] == {"n": 2, "mean_jaccard": 0.5}


def test_calibration_rewards_the_more_accurate_agent():
    rows = []
    for i in range(20):
        human = "remove" if i % 2 else "allow"
        wrong = "no_violation" if human == "remove" else "violation"
        right = "violation" if human == "remove" else "no_violation"
        rows.append(reviewed(i, human, [vote("context", right), vote("text", right if i % 4 < 2 else wrong)]))
    cal = calibrate_weights(agreement_report(rows), min_n=10)
    assert cal["context"]["calibrated"] and cal["text"]["calibrated"]
    assert cal["context"]["weight"] > cal["text"]["weight"]
    assert not cal["arbiter"]["calibrated"] and cal["arbiter"]["weight"] == cal["arbiter"]["default"]


def test_calibration_keeps_defaults_below_min_n():
    cal = calibrate_weights(agreement_report([reviewed(1, "remove", [vote("text", "violation")])]), min_n=10)
    assert not any(c["calibrated"] for c in cal.values())


def test_weights_round_trip_and_pipeline_loads_them():
    s = make_settings()
    rows = [reviewed(i, "remove", [vote("text", "violation"), vote("context", "violation")]) for i in range(12)]
    report = agreement_report(rows)
    save_weights(s.weights_path, calibrate_weights(report, min_n=5), report)
    w = load_weights(s.weights_path)
    assert set(w) >= {"text", "context", "arbiter", "metadata"}
    assert Triage(s, gate=False).weights == w
    assert Triage(make_settings(), gate=False).weights is None  # no file -> voting defaults


def test_reviews_to_records_is_eval_ready():
    policy = load_policy(make_settings().policy_path)
    recs = reviews_to_records(
        [reviewed(1, "remove", [], reviewer_clauses=["HATE-1", "HAR-1"]), reviewed(2, "allow", [])], policy
    )
    assert [r["label"] for r in recs] == [1, 0]
    assert recs[0]["categories"] == ["harassment", "hate"] and recs[1]["categories"] == []
    assert all(r["source"] == "human_review" for r in recs)


def test_store_search_and_reviewed(tmp_path):
    s = make_settings(db_path=tmp_path / "s.db")
    store = Store(s.db_path)
    tri = Triage(s, store=store, gate=False)
    for t in ["you are an idiot", "100% agree with this", "nice_work team"]:
        tri.moderate(Item(text=t), mode="full")
    assert [d["text"] for d in store.list_decisions(q="idiot")] == ["you are an idiot"]
    assert [d["text"] for d in store.list_decisions(q="100%")] == ["100% agree with this"]  # % is literal
    assert [d["text"] for d in store.list_decisions(q="e_w")] == ["nice_work team"]  # _ is literal
    d = store.list_decisions(limit=1)[0]
    store.review(d["id"], "allow", [], "fine")
    assert [r["id"] for r in store.reviewed()] == [d["id"]]


@pytest.mark.parametrize("bad", [{"label": "violation"}, {"label": "no_violation", "clause_ids": ["NOPE-1"]}])
def test_invalid_policy_examples_are_rejected(tmp_path, bad):
    import yaml

    raw = yaml.safe_load(make_settings().policy_path.read_text(encoding="utf-8"))
    raw["examples"] = [{"text": "x", "why": "y", **bad}]
    p = tmp_path / "policy.yaml"
    p.write_text(json.dumps(raw), encoding="utf-8")  # JSON is valid YAML
    with pytest.raises(ValueError):
        load_policy(p)
