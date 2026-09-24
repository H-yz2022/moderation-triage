from conftest import make_settings

from modtriage.policy import load_policy
from modtriage.schemas import Vote
from modtriage.voting import aggregate, majority

P = load_policy(make_settings().policy_path)


def V(agent, label, conf, clauses=(), cats=(), risk=None):
    return Vote(agent=agent, label=label, confidence=conf, clause_ids=list(clauses), categories=list(cats), risk=risk)


def test_unanimous_violation_removes_with_clauses():
    t = aggregate(
        [
            V("text", "violation", 0.9, ["HAR-1"], ["harassment"]),
            V("context", "violation", 0.8, ["HAR-1", "HAR-3"], ["harassment"]),
        ],
        P,
    )
    assert t.outcome == "remove"
    assert t.clause_ids[0] == "HAR-1" and "harassment" in t.categories


def test_split_panel_goes_to_arbiter():
    t = aggregate([V("text", "violation", 0.7, ["HAR-1"], ["harassment"]), V("context", "no_violation", 0.8)], P)
    assert t.outcome == "arbiter"


def test_no_valid_votes_escalates():
    t = aggregate([V("text", "abstain", 0.0)], P)
    assert t.outcome == "escalate"


def test_high_severity_guard_blocks_allow():
    from modtriage.voting import Tally, apply_guards

    votes = [V("text", "violation", 0.6, ["VIO-1"], ["violence"]), V("context", "no_violation", 0.95)]
    t = apply_guards(Tally("allow", -0.3, 0.8), votes, P, high_sev_conf=0.5)
    assert t.outcome == "escalate"
    # low-severity flags don't trigger the guard
    votes = [V("text", "violation", 0.9, ["PROF-1"], ["profanity"]), V("context", "no_violation", 0.95)]
    assert apply_guards(Tally("allow", -0.3, 0.8), votes, P, 0.5).outcome == "allow"


def test_metadata_agent_can_only_assert_spam():
    votes = [V("metadata", "violation", 0.9, ["HAR-1"], ["harassment"]), V("text", "no_violation", 0.9)]
    assert aggregate(votes, P).outcome == "allow"
    votes = [V("metadata", "violation", 0.9, ["SPAM-1"], ["spam"])]
    assert aggregate(votes, P).outcome == "remove"


def test_majority_baseline_tie_escalates():
    t = majority([V("text", "violation", 0.9, ["HAR-1"]), V("context", "no_violation", 0.9)], P)
    assert t.outcome == "escalate"
