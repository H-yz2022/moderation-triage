import json

from conftest import make_settings

from modtriage import cli
from modtriage.store import Store


def run(monkeypatch, s, argv):
    monkeypatch.setattr(cli, "get_settings", lambda: s)
    cli.main(argv)


def test_moderate_file_csv_then_review_export_and_calibrate(monkeypatch, tmp_path, capsys):
    s = make_settings()
    src = tmp_path / "in.csv"
    src.write_text(
        'id,comment,parent\na,"You are an idiot.",\nb,"Thanks, that helps.",\nc,"",\n'
        'd,"He said all immigrants are criminals and that is unacceptable.","I agree with the mayor"\n',
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"
    run(monkeypatch, s, ["moderate-file", str(src), "--text-col", "comment", "--out", str(out), "--mode", "full"])
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == ["a", "b", "d"]  # empty text skipped
    assert all(r["action"] in ("remove", "allow", "escalate") for r in rows)
    assert "3 items" in capsys.readouterr().out

    store = Store(s.db_path)
    decisions = store.list_decisions()
    assert len(decisions) == 3
    for d in decisions:
        store.review(d["id"], "remove" if "idiot" in d["text"] else "allow", ["HAR-1"] if "idiot" in d["text"] else [])

    gold = tmp_path / "gold.jsonl"
    run(monkeypatch, s, ["export-reviews", "--out", str(gold)])
    recs = [json.loads(line) for line in gold.read_text(encoding="utf-8").splitlines()]
    assert sorted(r["label"] for r in recs) == [0, 0, 1]

    run(monkeypatch, s, ["calibrate", "--min-n", "1", "--write"])
    assert "agent" in capsys.readouterr().out
    assert json.loads(s.weights_path.read_text())["n_reviewed"] == 3


def test_moderate_file_no_save_leaves_queue_empty(monkeypatch, tmp_path):
    s = make_settings()
    src = tmp_path / "in.jsonl"
    src.write_text(json.dumps({"id": "x", "text": "You are an idiot.", "label": 1}) + "\n", encoding="utf-8")
    run(monkeypatch, s, ["moderate-file", str(src), "--no-save"])
    out = [json.loads(x) for x in src.with_suffix(".decisions.jsonl").read_text().splitlines()]
    assert out[0]["label"] == 1
    assert Store(s.db_path).list_decisions() == []
