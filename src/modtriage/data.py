"""Public-dataset loading, label mapping and eval-set construction.

Primary dataset: Civil Comments (Jigsaw Unintended Bias), ~2M comments with
crowd-rated toxicity sub-labels. The TFDS release (v1.2) also carries
`parent_text`, `article_id`, `created_date` and `publication_id`, which is what
the context and metadata agents need. Labels are fractions of annotators;
we binarise at >= 0.5 (the convention used by the original competition).

Sources supported (first that works):
  1. the Civil Comments v1.2 zip used by TFDS (has parent_text)       [default]
  2. a local CSV you downloaded yourself (Kaggle `all_data.csv` etc.)  [--csv]
  3. Hugging Face `google/civil_comments` parquet (text + labels only)  [--source hf]
"""

from __future__ import annotations

import json
import random
import urllib.request
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .policy import Policy
from .schemas import Item, Metadata

CIVIL_ZIP_URL = (
    "https://storage.googleapis.com/jigsaw-unintended-bias-in-toxicity-classification/civil_comments_v1.2.zip"
)
HF_PARQUET = "https://huggingface.co/api/datasets/google/civil_comments/parquet/default/{split}/{n}.parquet"
LABEL_COLS = ["toxicity", "severe_toxicity", "obscene", "threat", "insult", "identity_attack", "sexual_explicit"]
META_COLS = ["created_date", "publication_id", "article_id", "parent_id", "likes", "disagree", "funny", "sad", "wow"]
# NOTE: Kaggle all_data.csv has a `rating` column (approved/rejected by the
# publisher's moderators). It is a label leak for this task, so we never load it.
THRESHOLD = 0.5


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"downloading {url}\n  -> {dest}")
    request = urllib.request.Request(url, headers={"User-Agent": "modtriage/0.1"})
    with urllib.request.urlopen(request, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310 - fixed https URL
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:,.0f} / {total / 1e6:,.0f} MB", end="", flush=True)
    print()
    tmp.replace(dest)
    return dest


def download_civil_comments(data_dir: Path) -> Path:
    raw = Path(data_dir) / "raw"
    csv = raw / "civil_comments.csv"
    if csv.exists():
        return csv
    z = _download(CIVIL_ZIP_URL, raw / "civil_comments_v1.2.zip")
    with zipfile.ZipFile(z) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        zf.extract(name, raw)
        (raw / name).replace(csv)
    return csv


def download_hf(data_dir: Path) -> Path:
    """Fallback: text + labels only (no parent_text / metadata)."""
    raw = Path(data_dir) / "raw"
    frames = []
    for split in ("train", "validation", "test"):
        for n in range(0, 16):
            try:
                p = _download(HF_PARQUET.format(split=split, n=n), raw / "hf" / f"{split}-{n}.parquet")
            except Exception as e:
                if n == 0:
                    raise RuntimeError(f"could not download the {split} split from Hugging Face: {e}") from e
                break  # no more shards for this split
            df = pd.read_parquet(p)
            df["split"] = split
            frames.append(df)
    out = raw / "civil_comments_hf.csv"
    pd.concat(frames).rename(columns={"text": "comment_text"}).to_csv(out, index=False)
    return out


def load_raw(csv_path: str | Path, nrows: int | None = None) -> pd.DataFrame:
    header = pd.read_csv(csv_path, nrows=0).columns
    text_col = "comment_text" if "comment_text" in header else "text"
    wanted = [c for c in ["id", text_col, "parent_text", "split", *LABEL_COLS, *META_COLS] if c in header]
    df = pd.read_csv(csv_path, usecols=wanted, nrows=nrows, low_memory=False)
    df = df.rename(columns={text_col: "text"})
    df = df[df["text"].notna() & (df["text"].astype(str).str.strip() != "")]
    if "split" not in df.columns:  # e.g. Kaggle train.csv: make a deterministic split
        rng = np.random.default_rng(0)
        df["split"] = rng.choice(["train", "validation", "test"], size=len(df), p=[0.9, 0.05, 0.05])
    if "id" not in df.columns:
        df["id"] = np.arange(len(df))
    df["id"] = df["id"].astype(str)
    return df.reset_index(drop=True)


def add_labels(df: pd.DataFrame, policy: Policy) -> pd.DataFrame:
    df = df.copy()
    df["label"] = (df["toxicity"] >= THRESHOLD).astype(int)
    cmap = policy.dataset_label_map()
    cols = [c for c in cmap if c in df.columns]
    cats = []
    for row in df[cols].itertuples(index=False):
        cats.append([cmap[c] for c, v in zip(cols, row, strict=True) if v is not None and v >= THRESHOLD])
    df["categories"] = cats
    # A comment flagged in a sub-category is a violation even if overall toxicity < 0.5
    df.loc[df["categories"].map(len) > 0, "label"] = 1
    return df


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    md: dict[str, Any] = {}
    for k in ("created_date", "publication_id", "article_id"):
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and np.isnan(v)):
            md[k] = str(v)
    pid = row.get("parent_id")
    if pid is not None:
        md["has_parent"] = not (isinstance(pid, float) and np.isnan(pid))
    for k in ("likes", "disagree", "funny", "sad", "wow"):
        v = row.get(k)
        if v is not None and not (isinstance(v, float) and np.isnan(v)):
            md[k] = int(v)
    return md


def to_records(df: pd.DataFrame, weights: pd.Series | None = None) -> list[dict[str, Any]]:
    out = []
    for i, row in enumerate(df.to_dict(orient="records")):
        parent = row.get("parent_text")
        parent = None if parent is None or (isinstance(parent, float) and np.isnan(parent)) else str(parent)
        rec = {
            "id": str(row["id"]),
            "text": str(row["text"]),
            "parent_text": parent,
            "metadata": _metadata(row),
            "label": int(row["label"]),
            "categories": list(row["categories"]),
        }
        if weights is not None:
            rec["weight"] = float(weights.iloc[i])
        out.append(rec)
    return out


def stratified_sample(df: pd.DataFrame, n: int, pos_frac: float, seed: int = 13) -> tuple[pd.DataFrame, pd.Series]:
    """Oversample violations for statistical power; return per-row weights that
    re-weight metrics back to the population prevalence."""
    prevalence = df["label"].mean()
    pos, neg = df[df.label == 1], df[df.label == 0]
    n_pos = min(len(pos), int(round(n * pos_frac)))
    n_neg = min(len(neg), n - n_pos)
    sample = pd.concat([pos.sample(n_pos, random_state=seed), neg.sample(n_neg, random_state=seed)])
    sample = sample.sample(frac=1, random_state=seed).reset_index(drop=True)
    real_pos = n_pos / len(sample)
    w = np.where(sample["label"] == 1, prevalence / real_pos, (1 - prevalence) / (1 - real_pos))
    return sample, pd.Series(w)


def write_jsonl(rows: Iterable[dict], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def read_jsonl(path: str | Path, limit: int | None = None, seed: int | None = None) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if seed is not None:
        random.Random(seed).shuffle(rows)
    return rows[:limit] if limit else rows


def record_to_item(rec: dict) -> Item:
    md = {k: v for k, v in (rec.get("metadata") or {}).items() if k in Metadata.model_fields}
    return Item(
        id=str(rec.get("id", "")), text=rec["text"], parent_text=rec.get("parent_text"), metadata=Metadata(**md)
    )
