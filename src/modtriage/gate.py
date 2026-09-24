"""Tier-0 cost gate: a local TF-IDF + logistic-regression toxicity scorer.

Runs in ~0.1 ms per item for $0. In `cascade` mode, items the gate scores as
clearly benign skip the LLM panel entirely; everything else goes to the agents.
Its score is also a feature for the agents' mock and the arbiter prompt.
The auto-allow threshold is chosen on a validation split to cap the recall we
are willing to lose (see `choose_threshold`)."""

from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline


class Gate:
    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline

    @classmethod
    def train(cls, texts: list[str], labels: list[int], max_features: int = 200_000) -> Gate:
        feats = FeatureUnion(
            [
                (
                    "word",
                    TfidfVectorizer(
                        ngram_range=(1, 2),
                        min_df=2,
                        max_features=max_features,
                        sublinear_tf=True,
                        strip_accents="unicode",
                    ),
                ),
                (
                    "char",
                    TfidfVectorizer(
                        analyzer="char_wb", ngram_range=(3, 5), min_df=2, max_features=max_features, sublinear_tf=True
                    ),
                ),
            ]
        )
        pipe = Pipeline(
            [
                ("tfidf", feats),
                ("lr", LogisticRegression(C=4.0, max_iter=2000, class_weight="balanced")),
            ]
        )
        pipe.fit(texts, labels)
        return cls(pipe)

    def score(self, texts: list[str]) -> np.ndarray:
        return self.pipeline.predict_proba(texts)[:, 1]

    def score_one(self, text: str) -> float:
        return float(self.score([text])[0])

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, path)

    @classmethod
    def load(cls, path: str | Path) -> Gate | None:
        p = Path(path)
        return cls(joblib.load(p)) if p.exists() else None


def choose_threshold(scores: np.ndarray, labels: np.ndarray, max_recall_loss: float = 0.02) -> float:
    """Largest threshold t such that auto-allowing all items with score < t
    drops at most `max_recall_loss` of true violations."""
    pos = np.sort(scores[labels == 1])
    if len(pos) == 0:
        return 0.0
    k = int(np.floor(max_recall_loss * len(pos)))
    return float(pos[k]) if k < len(pos) else float(pos[-1])
