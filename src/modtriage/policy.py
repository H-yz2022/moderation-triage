"""Policy loading, prompt rendering and citation validation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .schemas import Vote


@dataclass(frozen=True)
class Clause:
    id: str
    category: str
    title: str
    text: str


@dataclass(frozen=True)
class Policy:
    version: str
    name: str
    categories: dict[str, dict]
    clauses: dict[str, Clause]
    exceptions: dict[str, dict]
    examples: tuple[dict, ...] = ()

    # -- lookups ------------------------------------------------------------
    def category_of(self, clause_id: str) -> str | None:
        c = self.clauses.get(clause_id)
        return c.category if c else None

    def severity(self, category: str) -> str:
        return self.categories.get(category, {}).get("severity", "medium")

    def high_severity_categories(self) -> set[str]:
        return {k for k, v in self.categories.items() if v.get("severity") == "high"}

    def dataset_label_map(self) -> dict[str, str]:
        """dataset label column -> policy category."""
        out: dict[str, str] = {}
        for cat, spec in self.categories.items():
            for lab in spec.get("dataset_labels", []) or []:
                out[lab] = cat
        return out

    # -- prompt -------------------------------------------------------------
    def render(self, include_examples: bool = True) -> str:
        """Stable text rendering. Kept byte-identical across calls so it can be
        prompt-cached as the system prefix."""
        lines = [f"# {self.name} (version {self.version})", "", "## Violation clauses"]
        for c in self.clauses.values():
            sev = self.severity(c.category)
            lines.append(f"- [{c.id}] ({c.category}, severity={sev}) {c.title}: {c.text}")
        lines += ["", "## Exceptions (content matching these is allowed)"]
        for eid, e in self.exceptions.items():
            lines.append(f"- [{eid}] {e['title']}: {e['text']}")
        if include_examples and self.examples:
            lines += ["", "## Worked examples (how the clauses and exceptions apply)"]
            for i, ex in enumerate(self.examples, 1):
                cites = ", ".join(ex["clause_ids"] + ex["exception_ids"]) or "none"
                lines.append(f"{i}. Comment: {ex['text']!r}")
                if ex.get("parent"):
                    lines.append(f"   In reply to: {ex['parent']!r}")
                lines.append(f"   Verdict: {ex['label']} (cites: {cites}). Why: {ex['why']}")
        return "\n".join(lines)

    # -- validation ---------------------------------------------------------
    def validate_vote(self, vote: Vote, text: str) -> Vote:
        """Ground a vote in the policy. Violation votes must cite >=1 known
        clause; unknown ids are stripped; categories are derived from clauses
        (never trusted from the model). If nothing valid remains the vote is
        downgraded to an abstain and flagged, so hallucinated citations can't
        swing the outcome."""
        known = [cid for cid in vote.clause_ids if cid in self.clauses]
        unknown = [cid for cid in vote.clause_ids if cid not in self.clauses]
        exc = [e for e in vote.exception_ids if e in self.exceptions]
        v = vote.model_copy(deep=True)
        v.clause_ids = known
        v.exception_ids = exc
        v.categories = sorted({self.clauses[c].category for c in known})
        reasons = []
        if unknown:
            reasons.append(f"unknown clause ids {unknown}")
        if v.label == "violation" and not known:
            v.label = "abstain"
            v.valid = False
            reasons.append("violation vote without a valid clause citation")
        if v.label == "no_violation":
            v.clause_ids, v.categories = [], []
        if v.evidence and text and v.evidence.strip().lower() not in text.lower():
            # evidence must be a verbatim span of the input (or its parent);
            # we keep the vote but record that the quote was not grounded.
            reasons.append("evidence is not a verbatim span of the input")
            v.evidence = ""
        v.invalid_reason = "; ".join(reasons)
        return v


def load_policy(path: str | Path) -> Policy:
    return _load(str(Path(path).resolve()))


@lru_cache(maxsize=8)
def _load(path: str) -> Policy:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    clauses = {c["id"]: Clause(c["id"], c["category"], c["title"], c["text"]) for c in raw["clauses"]}
    cats = raw["categories"]
    for c in clauses.values():
        if c.category not in cats:
            raise ValueError(f"clause {c.id} has unknown category {c.category}")
    exceptions = {e["id"]: {"title": e["title"], "text": e["text"]} for e in raw.get("exceptions", [])}
    examples = tuple(_example(e, clauses, exceptions) for e in raw.get("examples") or [])
    return Policy(raw["version"], raw["name"], cats, clauses, exceptions, examples)


def _example(e: dict, clauses: dict[str, Clause], exceptions: dict[str, dict]) -> dict:
    """Worked examples are part of the prompt, so they get the same grounding
    rules as a vote: violations cite a known clause, ids must exist."""
    ex = {
        "text": str(e["text"]),
        "parent": e.get("parent"),
        "label": e["label"],
        "clause_ids": list(e.get("clause_ids") or []),
        "exception_ids": list(e.get("exception_ids") or []),
        "why": str(e.get("why", "")).strip(),
    }
    if ex["label"] not in ("violation", "no_violation"):
        raise ValueError(f"example {ex['text']!r}: label must be violation or no_violation")
    unknown = [c for c in ex["clause_ids"] if c not in clauses] + [
        x for x in ex["exception_ids"] if x not in exceptions
    ]
    if unknown:
        raise ValueError(f"example {ex['text']!r} cites unknown ids {unknown}")
    if ex["label"] == "violation" and not ex["clause_ids"]:
        raise ValueError(f"example {ex['text']!r}: a violation example must cite a clause")
    return ex
