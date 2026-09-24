"""Triage orchestrator.

Modes (also used as eval ablations):
  gate_only  local TF-IDF model only, threshold 0.5              ($0 baseline)
  single     one LLM text agent decides alone                     (1 call)
  majority   text + context + metadata, plain majority, no arbiter
  full       text + context + metadata every time, weighted vote, arbiter on disagreement
  cascade    gate -> text agent -> (context agent if needed) -> arbiter if split
             cheapest path that still escalates hard cases        (default)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .agents import Arbiter, ContextAgent, MetadataAgent, TextAgent
from .config import Settings, get_settings
from .gate import Gate
from .llm import AnthropicClient, BudgetLedger, CachedClient, LLMClient
from .mock import MockClient
from .policy import load_policy
from .schemas import CallUsage, Decision, Item, Vote
from .store import Store
from .voting import Tally, aggregate, apply_guards, majority

MODES = ("gate_only", "single", "majority", "full", "cascade")


class Triage:
    def __init__(
        self,
        settings: Settings | None = None,
        client: LLMClient | None = None,
        store: Store | None = None,
        gate: Gate | None | bool = True,
        ledger: BudgetLedger | None = None,
        weights: dict[str, float] | None = None,
    ):
        self.s = s = settings or get_settings()
        self.policy = load_policy(s.policy_path)
        self.store = store
        if client is None:
            self.provider = s.resolved_provider
            client = MockClient() if self.provider == "mock" else AnthropicClient(s.anthropic_api_key, s.llm_timeout_s)
        else:
            self.provider = type(client).__name__
        if s.use_cache and store is not None:
            client = CachedClient(client, store)
        self.client = client
        self.gate: Gate | None = Gate.load(s.gate_path) if gate is True else (gate or None)
        self.ledger = ledger or BudgetLedger(s.run_budget_usd)
        self.weights = weights
        p, c, m = self.policy, client, self.ledger
        self.text_agent = TextAgent(p, c, s.specialist_model, m)
        self.context_agent = ContextAgent(p, c, s.specialist_model, m)
        self.metadata_agent = MetadataAgent(p, c, s.specialist_model, m, use_llm=s.metadata_agent_uses_llm)
        self.arbiter = Arbiter(p, c, s.arbiter_model, m)

    # ------------------------------------------------------------------
    def moderate(self, item: Item, mode: str | None = None, save: bool = True, llm_allowed: bool = True) -> Decision:
        mode = mode or self.s.mode
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        item = item.model_copy(update={"text": item.text[: self.s.max_text_chars]})
        d = self._decide(item, mode, llm_allowed)
        if save and self.store is not None:
            self.store.save_decision(item, d)
        return d

    def _decide(self, item: Item, mode: str, llm_allowed: bool) -> Decision:
        s = self.s
        gate_score = round(self.gate.score_one(item.text), 4) if self.gate else None
        ctx: dict = {"gate_score": gate_score}
        votes: list[Vote] = []
        usage: list[CallUsage] = []

        def run(agent) -> Vote:
            v, u = agent.run(item, ctx)
            votes.append(v)
            if u is not None:
                usage.append(u)
            return v

        def done(action: str, by: str, reasons: list[str], t: Tally | None = None, clauses=None, cats=None):
            return Decision(
                item_id=item.id,
                action=action,
                decided_by=by,
                reasons=reasons,
                clause_ids=(clauses if clauses is not None else (t.clause_ids if t and action != "allow" else [])),
                categories=(cats if cats is not None else (t.categories if t and action != "allow" else [])),
                score=t.score if t else 0.0,
                confidence=t.confidence if t else 0.0,
                votes=votes,
                usage=usage,
                gate_score=gate_score,
                policy_version=self.policy.version,
            )

        meta_vote = run(self.metadata_agent)
        meta_spam = meta_vote.valid and meta_vote.label == "violation"

        # --- baselines ---------------------------------------------------
        if mode == "gate_only" or not llm_allowed:
            if gate_score is None:
                return done("escalate", "budget_guard", ["no gate model and LLM calls disabled"])
            if mode == "gate_only":
                act = "remove" if gate_score >= 0.5 else "allow"
                return done(act, "gate", [f"gate score {gate_score:.3f} vs 0.5"])
            if gate_score < s.gate_allow_below and not meta_spam:
                return done("allow", "gate", [f"gate score {gate_score:.3f} < {s.gate_allow_below}"])
            return done("escalate", "budget_guard", ["daily LLM cap reached; gate not confident"])

        # --- tier 0: cheap gate -------------------------------------------
        if mode == "cascade" and gate_score is not None and gate_score < s.gate_allow_below and not meta_spam:
            return done("allow", "gate", [f"gate score {gate_score:.3f} < {s.gate_allow_below}: skipped LLM panel"])

        # --- tier 1: specialists --------------------------------------------
        if mode == "single":
            v = run(self.text_agent)
            if not v.valid or v.label == "abstain":
                return done("escalate", "single", ["text agent abstained/invalid"])
            act = "remove" if v.label == "violation" else "allow"
            return done(
                act,
                "single",
                [f"single agent: {v.label} ({v.confidence:.2f})"],
                clauses=v.clause_ids if act == "remove" else [],
                cats=v.categories if act == "remove" else [],
            )

        if mode in ("full", "majority"):
            with ThreadPoolExecutor(max_workers=2) as ex:
                results = list(ex.map(lambda a: a.run(item, ctx), [self.text_agent, self.context_agent]))
            for v, u in results:
                votes.append(v)
                if u is not None:
                    usage.append(u)
        else:  # cascade
            tv = run(self.text_agent)
            gate_agrees = gate_score is None or ((gate_score >= 0.5) == (tv.label == "violation"))
            early_exit = (
                tv.valid and tv.label != "abstain" and tv.confidence >= 0.85 and not item.parent_text and gate_agrees
            )
            if not early_exit:
                run(self.context_agent)

        if mode == "majority":
            t = majority(votes, self.policy)
            return done(t.outcome, "panel", t.reasons, t)

        t = aggregate(
            votes, self.policy, s.remove_threshold, s.allow_threshold, s.high_severity_escalate_conf, self.weights
        )
        if any(v.invalid_reason == "budget exhausted" for v in votes) and t.outcome in ("escalate", "arbiter"):
            return done("escalate", "budget_guard", t.reasons + ["run budget exhausted"], t)
        if t.outcome != "arbiter":
            by = "panel" if t.outcome in ("remove", "allow") else "policy_guard"
            return done(t.outcome, by, t.reasons, t)

        # --- tier 2: arbiter on disagreement --------------------------------
        ctx["votes"] = list(votes)
        av = run(self.arbiter)
        reasons = list(t.reasons)
        if av.invalid_reason == "budget exhausted":
            return done("escalate", "budget_guard", reasons + ["run budget exhausted before arbiter"], t)
        if not av.valid or av.label == "abstain" or av.confidence < s.arbiter_min_confidence:
            return done(
                "escalate",
                "arbiter",
                reasons + [f"arbiter unsure ({av.label}, {av.confidence:.2f}) -> human review"],
                t,
            )
        if av.label == "violation":
            return done(
                "remove",
                "arbiter",
                reasons + [f"arbiter: violation ({av.confidence:.2f})"],
                t,
                clauses=av.clause_ids,
                cats=av.categories,
            )
        # arbiter says allow: still apply the high-severity + risk guards
        t2 = Tally(
            "allow",
            t.score,
            t.confidence,
            t.clause_ids,
            t.categories,
            reasons + [f"arbiter: no violation ({av.confidence:.2f})"],
            t.risk,
        )
        t2 = apply_guards(t2, votes, self.policy, s.high_severity_escalate_conf)
        if t2.outcome == "allow" and t.risk >= 0.7:
            t2.outcome = "escalate"
            t2.reasons.append(f"metadata risk {t.risk:.2f} blocks auto-allow of a split case")
        by = "arbiter" if t2.outcome == "allow" else "policy_guard"
        return done(t2.outcome, by, t2.reasons, t2)
