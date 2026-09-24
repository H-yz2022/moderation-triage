"""Typed data contracts shared by agents, the pipeline, the API and the eval harness."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

VoteLabel = Literal["violation", "no_violation", "abstain"]
Action = Literal["remove", "allow", "escalate"]


class Metadata(BaseModel):
    """Optional context about the post/author. Every field is optional because
    public datasets only carry a subset (Civil Comments: dates, ids, reactions)."""

    created_date: str | None = None
    article_id: str | None = None
    publication_id: str | None = None
    has_parent: bool | None = None
    likes: int | None = None
    disagree: int | None = None
    funny: int | None = None
    sad: int | None = None
    wow: int | None = None
    account_age_days: float | None = None
    prior_strikes: int | None = None
    report_count: int | None = None
    posts_last_hour: int | None = None


class Item(BaseModel):
    id: str = ""
    text: str
    parent_text: str | None = None
    metadata: Metadata = Field(default_factory=Metadata)


class Vote(BaseModel):
    agent: str
    label: VoteLabel
    confidence: float = Field(ge=0.0, le=1.0)
    categories: list[str] = Field(default_factory=list)
    clause_ids: list[str] = Field(default_factory=list)
    exception_ids: list[str] = Field(default_factory=list)
    evidence: str = ""
    rationale: str = ""
    model: str = ""
    risk: float | None = None  # metadata agent: prior risk in [0,1], not a verdict
    valid: bool = True
    invalid_reason: str = ""


class CallUsage(BaseModel):
    agent: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cached: bool = False  # served from our response cache (no API call)
    simulated: bool = False  # mock provider: cost is an estimate of what it *would* cost


class Decision(BaseModel):
    item_id: str
    action: Action
    categories: list[str] = Field(default_factory=list)
    clause_ids: list[str] = Field(default_factory=list)
    score: float = 0.0  # weighted vote in [-1, 1]; + means violation
    confidence: float = 0.0
    decided_by: str = ""  # gate | panel | arbiter | policy_guard | budget_guard
    reasons: list[str] = Field(default_factory=list)
    votes: list[Vote] = Field(default_factory=list)
    usage: list[CallUsage] = Field(default_factory=list)
    gate_score: float | None = None
    policy_version: str = ""

    @property
    def cost_usd(self) -> float:
        """Actual spend (cache hits are free)."""
        return round(sum(u.cost_usd for u in self.usage if not u.cached), 6)

    @property
    def list_cost_usd(self) -> float:
        """What this decision costs without our response cache (for comparing configs)."""
        return round(sum(u.cost_usd for u in self.usage), 6)

    @property
    def llm_calls(self) -> int:
        return sum(1 for u in self.usage if not u.cached)
