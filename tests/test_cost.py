import pytest
from conftest import DEV, make_settings

from modtriage.data import read_jsonl
from modtriage.evaluation import prefetch_batched, run_mode
from modtriage.llm import AnthropicClient, BatchingClient, CachedClient, DeferredCall, LLMRequest
from modtriage.mock import MockClient
from modtriage.pipeline import Triage
from modtriage.schemas import Item
from modtriage.store import Store


def triage(**kw):
    batching = kw.pop("batching", False)
    s = make_settings(**kw)
    return Triage(s, store=Store(s.db_path), gate=False, batching=batching)


def test_examples_go_to_the_arbiter_only_by_default():
    t = triage()
    assert "Worked examples" not in t.text_agent._policy_text
    assert "Worked examples" not in t.context_agent._policy_text
    assert "Worked examples" in t.arbiter._policy_text
    t_all = triage(policy_examples="all")
    assert "Worked examples" in t_all.text_agent._policy_text
    assert "Worked examples" not in triage(policy_examples="none").arbiter._policy_text
    with pytest.raises(ValueError):
        triage(policy_examples="some")


def req(t, agent):
    a = {"text": t.text_agent, "arbiter": t.arbiter}[agent]
    return LLMRequest(
        agent=agent,
        model=a.model,
        system_policy=a._policy_text,
        system_role="role " + agent,
        user="u",
        meta={"text": "you idiot", "parent_text": None, "gate_score": None, "metadata": {}, "votes": []},
    )


def test_simulated_cache_respects_model_minimums():
    t, m = triage(), MockClient()
    haiku = [m.call(req(t, "text")).usage for _ in range(2)]
    assert all(u.cache_read_tokens == u.cache_write_tokens == 0 for u in haiku)  # < 4096: never caches
    first, second = (m.call(req(t, "arbiter")).usage for _ in range(2))
    assert first.cache_write_tokens > 0 and first.cache_read_tokens == 0
    assert second.cache_read_tokens == first.cache_write_tokens and second.cost_usd < first.cost_usd


def test_cache_breakpoint_covers_the_whole_system_prompt():
    t = triage()
    params = AnthropicClient._params(req(t, "arbiter"), [{"role": "user", "content": "u"}])
    assert "cache_control" not in params["system"][0] and "cache_control" in params["system"][-1]


def test_deferred_calls_propagate_instead_of_failing_closed():
    t = triage(batching=True)
    t.batcher.collecting = True
    with pytest.raises(DeferredCall):
        t.moderate(Item(text="You are an idiot."), mode="single", save=False)
    assert len(t.batcher.pending) == 1
    assert t.ledger.spent == 0  # reservation released


def test_unbilled_cache_entries_bill_exactly_once():
    s = make_settings()
    c = CachedClient(BatchingClient(MockClient()), Store(s.db_path))
    r = req(triage(), "text")
    resp = MockClient().run_batch({r.cache_key(): r})[r.cache_key()]
    c.put_unbilled(r.cache_key(), resp)
    c.bill_unbilled = False
    assert c.call(r).usage.cached  # collection pass: not billed yet
    c.bill_unbilled = True
    first, again = c.call(r).usage, c.call(r).usage
    assert not first.cached and first.batch and first.cost_usd == resp.usage.cost_usd
    assert again.cached


@pytest.mark.parametrize("mode", ["single", "full", "cascade"])
def test_batch_eval_matches_sync_decisions_at_half_the_price(mode):
    records = read_jsonl(DEV)
    sync_rows = run_mode(triage(), records, mode, progress=False)
    bt = triage(batching=True)
    stats = prefetch_batched(bt, records, mode)
    batch_rows = run_mode(bt, records, mode, progress=False)

    assert [r.action for r in batch_rows] == [r.action for r in sync_rows]
    assert stats["rounds"] >= 1 and stats["batched_requests"] == sum(r.calls for r in sync_rows)
    assert all(r.cache_hits == 0 for r in batch_rows)  # each batched call is billed once, on the final pass
    sync_cost, batch_cost = sum(r.spend for r in sync_rows), sum(r.spend for r in batch_rows)
    # 50% off; slightly above exactly half because the sync run gets simulated arbiter
    # cache reads that batches (best-effort caching) don't, plus per-decision rounding
    assert batch_cost < 0.55 * sync_cost
    assert bt.ledger.spent == pytest.approx(batch_cost, rel=1e-3)  # decisions round to 6 dp


def test_batching_requires_the_response_cache():
    s = make_settings(use_cache=False)
    with pytest.raises(ValueError):
        Triage(s, store=Store(s.db_path), gate=False, batching=True)


def test_ai_switch_off_by_default_never_reaches_the_api(monkeypatch):
    monkeypatch.delenv("MODTRIAGE_AI_ENABLED", raising=False)
    s = make_settings(provider="auto", anthropic_api_key="sk-test-not-real")
    assert not s.ai_enabled and s.resolved_provider == "mock"
    t = Triage(s, store=Store(s.db_path), gate=False)
    assert t.provider == "mock" and isinstance(t.client.inner, MockClient)
    on = make_settings(provider="auto", anthropic_api_key="sk-test-not-real", ai_enabled=True)
    assert on.resolved_provider == "anthropic"  # resolution only; no client is built, no call made
