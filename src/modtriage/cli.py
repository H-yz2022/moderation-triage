"""Command line: download -> prepare -> train-gate -> eval / moderate / serve.

python -m modtriage.cli download
python -m modtriage.cli prepare --eval-n 600
python -m modtriage.cli train-gate
python -m modtriage.cli eval --modes gate_only,single,full,cascade --limit 300
python -m modtriage.cli moderate "you are an idiot" --parent "I like trains"
python -m modtriage.cli serve
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .config import ROOT, get_settings
from .policy import load_policy

DEV_SAMPLE = ROOT / "data" / "sample" / "dev_sample.jsonl"


def _processed(s) -> Path:
    return Path(s.data_dir) / "processed"


def cmd_download(a, s):
    from . import data

    if a.csv:
        print(f"using local CSV {a.csv}")
        path = Path(a.csv)
    elif a.source == "hf":
        path = data.download_hf(s.data_dir)
    else:
        try:
            path = data.download_civil_comments(s.data_dir)
        except Exception as e:  # the Google-hosted zip has gone private (HTTP 403)
            print(f"\nOriginal Civil Comments zip unavailable ({e}).")
            print("Falling back to the Hugging Face copy (text + labels; no parent comments).\n")
            path = data.download_hf(s.data_dir)
    print(f"raw data at {path}")


def cmd_prepare(a, s):
    from . import data

    raw = Path(a.csv) if a.csv else Path(s.data_dir) / "raw" / "civil_comments.csv"
    if not raw.exists():
        alt = Path(s.data_dir) / "raw" / "civil_comments_hf.csv"
        raw = alt if alt.exists() else raw
    if not raw.exists():
        sys.exit(f"{raw} not found - run `download` first (or pass --csv)")
    policy = load_policy(s.policy_path)
    print(f"loading {raw} ...")
    df = data.add_labels(data.load_raw(raw, nrows=a.nrows), policy)
    print(df.groupby("split")["label"].agg(["count", "mean"]).to_string())
    out = _processed(s)
    tr = df[df.split == "train"]
    tr = tr.sample(min(len(tr), a.gate_train_n), random_state=1)
    data.write_jsonl(
        ({"text": t, "label": int(y)} for t, y in zip(tr.text, tr.label, strict=True)), out / "gate_train.jsonl"
    )
    va = df[df.split == "validation"]
    va = va.sample(min(len(va), 20_000), random_state=2)
    data.write_jsonl(
        ({"text": t, "label": int(y)} for t, y in zip(va.text, va.label, strict=True)), out / "gate_val.jsonl"
    )
    te = df[df.split == "test"]
    if a.context_only and "parent_text" in te.columns:
        te = te[te["parent_text"].notna()]
    sample, w = data.stratified_sample(te, a.eval_n, a.pos_frac, seed=a.seed)
    data.write_jsonl(data.to_records(sample, w), out / "eval_test.jsonl")
    # small dev split from validation for prompt iteration (never tune on test!)
    dev = df[df.split == "validation"]
    dsample, dw = data.stratified_sample(dev, min(200, a.eval_n), a.pos_frac, seed=a.seed + 1)
    data.write_jsonl(data.to_records(dsample, dw), out / "eval_dev.jsonl")
    print(
        f"wrote gate_train ({len(tr)}), gate_val ({len(va)}), eval_test ({len(sample)}), eval_dev ({len(dsample)}) to {out}"
    )


def cmd_train_gate(a, s):
    from sklearn.metrics import average_precision_score, roc_auc_score

    from .data import read_jsonl
    from .gate import Gate, choose_threshold

    out = _processed(s)
    if a.dev or not (out / "gate_train.jsonl").exists():
        print("training gate on the tiny dev fixture (demo only - run `prepare` for the real one)")
        rows = read_jsonl(DEV_SAMPLE)
        tr = va = rows
    else:
        tr, va = read_jsonl(out / "gate_train.jsonl"), read_jsonl(out / "gate_val.jsonl")
    gate = Gate.train(
        [r["text"] for r in tr], [r["label"] for r in tr], max_features=a.max_features if not a.dev else 5000
    )
    ys = np.array([r["label"] for r in va])
    sc = gate.score([r["text"] for r in va])
    thr = choose_threshold(sc, ys, a.max_recall_loss)
    skipped = float((sc < thr).mean())
    meta = {
        "train_n": len(tr),
        "val_n": len(va),
        "roc_auc": round(float(roc_auc_score(ys, sc)), 4),
        "avg_precision": round(float(average_precision_score(ys, sc)), 4),
        "allow_below": round(thr, 4),
        "max_recall_loss": a.max_recall_loss,
        "val_share_auto_allowed": round(skipped, 4),
    }
    gate.save(s.gate_path)
    Path(s.gate_path).with_suffix(".json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta, indent=1))
    print(
        f"\nSet MODTRIAGE_GATE_ALLOW_BELOW={meta['allow_below']} in .env to skip the LLM panel for "
        f"~{skipped:.0%} of traffic at <= {a.max_recall_loss:.0%} recall loss."
    )


def _make_triage_factory(s, store_path=None):
    from .pipeline import Triage
    from .store import Store

    store = Store(store_path or s.db_path)
    return lambda: Triage(s, store=store), store


def cmd_moderate(a, s):
    from .schemas import Item, Metadata

    make, _ = _make_triage_factory(s)
    tri = make()
    md = Metadata(**json.loads(a.metadata)) if a.metadata else Metadata()
    d = tri.moderate(Item(id="cli", text=a.text, parent_text=a.parent, metadata=md), mode=a.mode)
    out = d.model_dump()
    out["cost_usd"], out["provider"] = d.cost_usd, tri.provider
    print(json.dumps(out, indent=1))


def cmd_eval(a, s):
    from .data import read_jsonl
    from .evaluation import run_eval

    path = Path(a.data) if a.data else _processed(s) / "eval_test.jsonl"
    if not path.exists():
        print(f"{path} not found; falling back to the dev fixture")
        path = DEV_SAMPLE
    records = read_jsonl(path, limit=a.limit, seed=0 if a.limit else None)
    s.run_budget_usd = a.budget
    make, _ = _make_triage_factory(s, a.db)
    probe = make()
    provider = probe.provider
    modes = a.modes.split(",")
    if probe.gate is None and "gate_only" in modes:
        print("no trained gate found (run `train-gate`); skipping gate_only and the cascade gate tier")
        modes = [m for m in modes if m != "gate_only"]
    if provider != "mock" and not a.yes:
        est = len(records) * len(modes) * 0.0025
        print(
            f"About to call the real API: ~{len(records)} items x {a.modes}. Rough ceiling ~${est:.2f} "
            f"(hard budget per mode: ${a.budget}). Re-run with --yes to proceed."
        )
        return
    report = run_eval(
        make,
        records,
        modes,
        path.name,
        load_policy(s.policy_path),
        Path(s.reports_dir),
        n_boot=a.boot,
        provider=provider,
    )
    from .evaluation import to_markdown

    print(to_markdown(report))


def cmd_serve(a, s):
    import uvicorn

    uvicorn.run("modtriage.api.app:app", host=a.host, port=a.port, reload=False)


def main(argv=None):
    p = argparse.ArgumentParser(prog="modtriage")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("download")
    d.add_argument("--source", choices=["tfds", "hf"], default="tfds")
    d.add_argument("--csv")
    d.set_defaults(fn=cmd_download)

    pr = sub.add_parser("prepare")
    pr.add_argument("--csv")
    pr.add_argument("--nrows", type=int)
    pr.add_argument("--eval-n", type=int, default=600)
    pr.add_argument("--pos-frac", type=float, default=0.3)
    pr.add_argument("--gate-train-n", type=int, default=200_000)
    pr.add_argument("--context-only", action="store_true", help="eval only on replies (have parent_text)")
    pr.add_argument("--seed", type=int, default=13)
    pr.set_defaults(fn=cmd_prepare)

    g = sub.add_parser("train-gate")
    g.add_argument("--dev", action="store_true")
    g.add_argument("--max-recall-loss", type=float, default=0.02)
    g.add_argument("--max-features", type=int, default=200_000)
    g.set_defaults(fn=cmd_train_gate)

    m = sub.add_parser("moderate")
    m.add_argument("text")
    m.add_argument("--parent")
    m.add_argument("--metadata", help="JSON, e.g. '{\"account_age_days\": 0.5}'")
    m.add_argument("--mode")
    m.set_defaults(fn=cmd_moderate)

    e = sub.add_parser("eval")
    e.add_argument("--data")
    e.add_argument("--limit", type=int)
    e.add_argument("--modes", default="gate_only,single,majority,full,cascade")
    e.add_argument("--budget", type=float, default=5.0, help="hard USD cap per mode")
    e.add_argument("--boot", type=int, default=500)
    e.add_argument("--db", help="SQLite path for the response cache (default: data/modtriage.db)")
    e.add_argument("--yes", action="store_true")
    e.set_defaults(fn=cmd_eval)

    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    a.fn(a, get_settings())


if __name__ == "__main__":
    main()
