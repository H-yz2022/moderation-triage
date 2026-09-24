from conftest import make_settings

from modtriage.policy import load_policy
from modtriage.schemas import Vote


def policy():
    return load_policy(make_settings().policy_path)


def test_policy_loads_and_renders_stably():
    p = policy()
    assert "HAR-1" in p.clauses and "VIO-1" in p.clauses
    assert p.render() == p.render()
    assert {"hate", "violence"} == p.high_severity_categories()
    assert p.dataset_label_map()["threat"] == "violence"


def test_validate_strips_unknown_and_derives_categories():
    v = Vote(agent="text", label="violation", confidence=0.9, clause_ids=["HAR-1", "FAKE-9"], categories=["sexual"])
    out = policy().validate_vote(v, "you idiot")
    assert out.clause_ids == ["HAR-1"]
    assert out.categories == ["harassment"]  # model-supplied categories are ignored
    assert out.valid and "FAKE-9" in out.invalid_reason


def test_violation_without_valid_clause_becomes_invalid_abstain():
    v = Vote(agent="text", label="violation", confidence=0.99, clause_ids=["NOPE-1"])
    out = policy().validate_vote(v, "text")
    assert out.label == "abstain" and not out.valid


def test_ungrounded_evidence_is_dropped():
    v = Vote(agent="text", label="violation", confidence=0.8, clause_ids=["HAR-1"], evidence="words never said")
    out = policy().validate_vote(v, "you are a clown")
    assert out.evidence == "" and "verbatim" in out.invalid_reason
