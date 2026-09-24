from conftest import DEV, make_settings

from modtriage.data import read_jsonl, record_to_item
from modtriage.gate import Gate
from modtriage.llm import BudgetLedger, LLMRequest, LLMResponse
from modtriage.mock import MockClient
from modtriage.pipeline import Triage
from modtriage.schemas import CallUsage, Item, Metadata
from modtriage.store import Store


def make(**kw):
    s = make_settings(**{k: v for k, v in kw.items() if k not in ("gate", "client", "ledger")})
    return Triage(s, store=Store(s.db_path), gate=kw.get("gate"), client=kw.get("client"), ledger=kw.get("ledger"))


def dev_gate():
    rows = read_jsonl(DEV)
    return Gate.train([r["text"] for r in rows], [r["label"] for r in rows], max_features=2000)


def test_threat_removed_with_violence_clause():
    d = make().moderate(Item(text="Keep talking and I'll find out where you live.", parent_text="I reported you."))
    assert d.action == "remove"
    assert "VIO-1" in d.clause_ids and "violence" in d.categories
    assert all(v.valid for v in d.votes)


def test_benign_allowed_and_counter_speech_not_removed():
    t = make()
    assert t.moderate(Item(text="Thanks, this was a helpful explanation.")).action == "allow"
    d = t.moderate(
        Item(text="Calling people idiots is not okay, please be respectful.", parent_text="You're all idiots.")
    )
    assert d.action != "remove"


def test_spam_caught_by_metadata_agent():
    d = make().moderate(
        Item(
            text="BUY NOW free money http://a.example http://b.example",
            metadata=Metadata(posts_last_hour=30, account_age_days=0.1),
        )
    )
    assert d.action == "remove" and d.clause_ids == ["SPAM-1"]


def test_cascade_gate_skips_llm_for_clearly_benign():
    t = make(gate=dev_gate(), gate_allow_below=0.99)
    d = t.moderate(Item(text="Let's meet at the library at 6 to discuss the petition."))
    assert d.decided_by == "gate" and d.action == "allow" and d.usage == []


def test_full_mode_calls_both_specialists():
    d = make().moderate(Item(text="You are an idiot.", parent_text="hi"), mode="full")
    agents = {u.agent for u in d.usage}
    assert {"text", "context"} <= agents


def test_split_goes_to_arbiter_or_human():
    d = make().moderate(Item(text='He said "all immigrants are criminals" and that is unacceptable.'), mode="full")
    assert d.action in ("escalate", "allow")
    assert any(v.agent == "arbiter" for v in d.votes)


def test_response_cache_makes_repeat_free():
    t = make()
    item = Item(text="You clown. Nobody takes you seriously.", parent_text="stadium deal is bad")
    d1 = t.moderate(item, mode="full")
    d2 = t.moderate(item, mode="full")
    assert d1.cost_usd > 0 and d2.cost_usd == 0
    assert d2.list_cost_usd == d1.list_cost_usd
    assert d1.action == d2.action


def test_budget_exhaustion_escalates_instead_of_guessing():
    t = make(ledger=BudgetLedger(0.0))
    d = t.moderate(Item(text="You are an idiot and everyone knows it."))
    assert d.action == "escalate" and d.decided_by == "budget_guard"


def test_daily_cap_degrades_to_gate_only():
    t = make(gate=dev_gate(), gate_allow_below=0.0)
    d = t.moderate(Item(text="You are an idiot."), llm_allowed=False)
    assert d.action == "escalate" and d.usage == []


class HallucinatingClient:
    def call(self, req: LLMRequest) -> LLMResponse:
        return LLMResponse(
            data={"label": "violation", "confidence": 0.99, "clause_ids": ["MADE-UP-7"], "rationale": "trust me"},
            usage=CallUsage(agent=req.agent, model=req.model, cost_usd=0.001),
        )


def test_hallucinated_citations_cannot_remove_content():
    d = make(client=HallucinatingClient(), use_cache=False).moderate(Item(text="Nice weather today."))
    assert d.action == "escalate"
    assert all(not v.valid for v in d.votes if v.agent != "metadata")


class BrokenClient:
    def call(self, req):
        raise TimeoutError("upstream timeout")


def test_llm_failure_fails_closed_to_escalation():
    d = make(client=BrokenClient(), use_cache=False).moderate(Item(text="You are an idiot."))
    assert d.action == "escalate"


def test_decisions_persist_and_review_flow():
    t = make()
    d = t.moderate(Item(text='He said "all immigrants are criminals" and that is unacceptable.'), mode="full")
    store = t.store
    rows = store.list_decisions()
    assert rows and rows[0]["action"] == d.action
    if d.action == "escalate":
        pending = store.list_decisions(status="pending")
        r = store.review(pending[0]["id"], "allow", [], "counter-speech, EX-1")
        assert r["review_status"] == "reviewed" and r["reviewer_action"] == "allow"


def test_all_dev_items_produce_valid_decisions():
    t = make()
    for rec in read_jsonl(DEV):
        d = t.moderate(record_to_item(rec), save=False)
        assert d.action in ("remove", "allow", "escalate")
        if d.action == "remove":
            assert d.clause_ids, rec["id"]


def test_mock_client_usage_is_marked_simulated():
    t = make(use_cache=False)
    assert isinstance(t.client, MockClient)
    d = t.moderate(Item(text="You are an idiot.", parent_text="x"), mode="full")
    assert d.usage and all(u.simulated for u in d.usage)
