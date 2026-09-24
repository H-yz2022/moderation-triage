import importlib
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
