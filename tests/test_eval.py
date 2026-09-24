import json

import numpy as np
from conftest import DEV, make_settings

from modtriage.data import read_jsonl
from modtriage.evaluation import core_metrics, run_eval
from modtriage.pipeline import Triage
from modtriage.policy import load_policy
from modtriage.store import Store


def test_core_metrics_hand_computed():
    y = np.array([1, 1, 1, 0, 0, 0])
    a = np.array(["remove", "escalate", "allow", "remove", "allow", "allow"])
    m = core_metrics(y, np.ones(6), a)
    assert m["precision"] == 0.5  # 1 TP / (1 TP + 1 FP)
    assert m["auto_recall"] == round(1 / 3, 4)
    assert m["system_recall"] == round(2 / 3, 4)  # escalated positive counts
    assert m["escalation_rate"] == round(1 / 6, 4)
    assert m["false_allow_rate"] == round(1 / 3, 4)


def test_weights_reweight_precision_to_population():
    y = np.array([1, 0])
    a = np.array(["remove", "remove"])
    # positive oversampled: weight 0.2; negative weight 1.8
    m = core_metrics(y, np.array([0.2, 1.8]), a)
    assert m["precision"] == 0.1


def test_run_eval_writes_reports():
    s = make_settings()
    store = Store(s.db_path)
    recs = read_jsonl(DEV)
    rep = run_eval(
        lambda: Triage(s, store=store, gate=None),
        recs,
        ["single", "full", "cascade"],
        "dev",
        load_policy(s.policy_path),
        s.reports_dir,
        n_boot=50,
        provider="mock",
    )
    assert [r["mode"] for r in rep["results"]] == ["single", "full", "cascade"]
    full = rep["results"][1]
    assert full["n"] == len(recs) and full["metrics"]["precision"] is not None
    assert full["cost"]["list_cost_per_1k_usd"] > 0
    assert rep["sweep"] and rep["sweep_mode"] == "full"
    saved = json.loads((s.reports_dir / "eval_latest.json").read_text())
    assert saved["n_items"] == len(recs)
    assert (s.reports_dir / "eval_latest.md").read_text().startswith("# Eval report")
