import importlib
import json
import os

import pytest
from conftest import make_settings

fastapi = pytest.importorskip("fastapi")


@pytest.fixture()
def client(monkeypatch):
    s = make_settings()
    monkeypatch.setenv("MODTRIAGE_DB", str(s.db_path))
    monkeypatch.setenv("MODTRIAGE_GATE", str(s.gate_path))
    monkeypatch.setenv("MODTRIAGE_REPORTS", str(s.reports_dir))
    monkeypatch.setenv("MODTRIAGE_WEIGHTS", str(s.weights_path))
    monkeypatch.setenv("MODTRIAGE_PROVIDER", "mock")
    from fastapi.testclient import TestClient

    import modtriage.api.app as appmod

    appmod = importlib.reload(appmod)
    return TestClient(appmod.app)


def test_health_and_policy(client):
    h = client.get("/api/health").json()
    assert h["provider"] == "mock" and "cascade" in h["modes"]
    p = client.get("/api/policy").json()
    assert any(c["id"] == "HAR-1" for c in p["clauses"])


def test_moderate_and_review_flow(client):
    r = client.post(
        "/api/moderate",
        json={"text": 'He said "all immigrants are criminals" and that is unacceptable.', "mode": "full"},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["action"] in ("remove", "allow", "escalate") and d["id"] > 0
    if d["action"] == "escalate":
        bad = client.post(f"/api/queue/{d['id']}/review", json={"action": "remove", "clause_ids": []})
        assert bad.status_code == 400
        ok = client.post(f"/api/queue/{d['id']}/review", json={"action": "allow", "clause_ids": [], "note": "EX-1"})
        assert ok.json()["review_status"] == "reviewed"


def test_validation_errors(client):
    assert client.post("/api/moderate", json={"text": ""}).status_code == 422
    assert client.post("/api/moderate", json={"text": "hi", "mode": "yolo"}).status_code == 400
    assert client.get("/api/eval/latest").status_code == 404
    assert os.environ["MODTRIAGE_PROVIDER"] == "mock"


def test_batch_moderation_and_rate_limit(client):
    items = [{"text": "Thanks, very helpful."}, {"text": "You are an idiot.", "id": "mine"}]
    r = client.post("/api/moderate/batch", json={"items": items, "mode": "full"})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["n"] == 2 and sum(body["summary"]["actions"].values()) == 2
    assert body["results"][1]["item_id"] == "mine" and all(x["id"] > 0 for x in body["results"])
    assert client.post("/api/moderate/batch", json={"items": []}).status_code == 422
    assert client.post("/api/moderate/batch", json={"items": items, "mode": "yolo"}).status_code == 400
    # 2 used; a 25-item batch would exceed the 30/min window
    big = client.post("/api/moderate/batch", json={"items": [{"text": "hi"}] * 25})
    assert big.status_code == 200
    assert client.post("/api/moderate/batch", json={"items": [{"text": "hi"}] * 5}).status_code == 429


def test_search_exports_and_agreement(client):
    for t in ["You are an idiot.", "Lovely weather today."]:
        client.post("/api/moderate", json={"text": t, "mode": "full"})
    hits = client.get("/api/decisions", params={"q": "weather"}).json()
    assert [d["text"] for d in hits] == ["Lovely weather today."]
    d = hits[0]
    client.post(f"/api/queue/{d['id']}/review", json={"action": "allow", "clause_ids": []})

    csv_text = client.get("/api/export/decisions.csv").text
    assert csv_text.splitlines()[0].startswith("id,created_at,action") and "Lovely weather" in csv_text
    lines = client.get("/api/export/reviews.jsonl").text.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["label"] == 0

    a = client.get("/api/review/agreement").json()
    assert a["n_reviewed"] == 1 and set(a["calibration"]) >= {"text", "context", "arbiter"}
    assert "agent_weights" in client.get("/api/health").json()
    assert client.get("/api/policy").json()["examples"]
