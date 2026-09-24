import numpy as np
import pandas as pd
from conftest import DEV, make_settings

from modtriage.data import add_labels, read_jsonl, record_to_item, stratified_sample, to_records
from modtriage.gate import Gate, choose_threshold
from modtriage.policy import load_policy

P = load_policy(make_settings().policy_path)


def frame(n=200, prevalence=0.1, seed=0):
    rng = np.random.default_rng(seed)
    tox = (rng.random(n) < prevalence).astype(float) * 0.8
    return pd.DataFrame(
        {
            "id": [str(i) for i in range(n)],
            "text": [f"comment {i}" for i in range(n)],
            "parent_text": [None if i % 2 else f"parent {i}" for i in range(n)],
            "split": "test",
            "toxicity": tox,
            "insult": tox,
            "threat": 0.0,
            "identity_attack": 0.0,
            "obscene": 0.0,
            "sexual_explicit": 0.0,
            "severe_toxicity": 0.0,
            "article_id": 7,
            "parent_id": [np.nan if i % 2 else 1.0 for i in range(n)],
        }
    )


def test_add_labels_maps_dataset_columns_to_policy_categories():
    df = add_labels(frame(), P)
    pos = df[df.label == 1]
    assert len(pos) > 0 and all(c == ["harassment"] for c in pos.categories)
    # sub-category alone (toxicity < .5) still counts as a violation
    one = frame(1).assign(toxicity=0.3, threat=0.7)
    assert add_labels(one, P).iloc[0]["label"] == 1


def test_stratified_sample_weights_recover_prevalence():
    df = add_labels(frame(2000, 0.08), P)
    sample, w = stratified_sample(df, 300, pos_frac=0.3)
    assert abs(sample.label.mean() - 0.3) < 0.02
    est = (w * sample.label.values).sum() / w.sum()
    assert abs(est - df.label.mean()) < 1e-6


def test_records_round_trip_to_items():
    df = add_labels(frame(10), P)
    recs = to_records(df)
    item = record_to_item(recs[0])
    assert item.parent_text == "parent 0" and item.metadata.has_parent is True
    assert item.metadata.article_id == "7"
    assert record_to_item(recs[1]).parent_text is None


def test_gate_trains_and_threshold_caps_recall_loss():
    rows = read_jsonl(DEV)
    g = Gate.train([r["text"] for r in rows], [r["label"] for r in rows], max_features=2000)
    sc = g.score([r["text"] for r in rows])
    y = np.array([r["label"] for r in rows])
    assert sc[y == 1].mean() > sc[y == 0].mean()
    thr = choose_threshold(sc, y, max_recall_loss=0.1)
    lost = ((sc < thr) & (y == 1)).sum() / y.sum()
    assert lost <= 0.1
