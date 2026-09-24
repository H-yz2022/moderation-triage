# Content Moderation Triage

Specialist AI agents review each comment. They vote on whether it breaks a written policy, and every violation vote has to **cite the policy clause** it relies on. Clear cases are decided automatically. **Uncertain or high-severity cases go to a human review queue.** An eval harness measures **precision, recall, escalation rate and cost per 1,000 items** for each routing strategy on public toxicity data.

```
comment ─► $0 gate (TF-IDF) ─► text agent ─► context agent ─► weighted vote ─► remove / allow
            │ clearly benign      (Haiku)        (Haiku)          │ split?
            ▼                                                     ▼
          allow                                          arbiter (Sonnet) ─► unsure? ─► human queue
                         metadata agent (rules, $0): spam + risk prior
```

## Why it's built this way

| Design choice | Reason |
|---|---|
| **Specialists see different evidence.** Text only; text plus parent comment; metadata only. | N copies of one prompt make correlated mistakes. Splitting the evidence decorrelates errors, and the eval reports text/context agreement so you can check. |
| **Citations are enforced in code.** Clause ids are checked against `policy/community_policy.yaml`. | A violation vote without a real clause is downgraded to an invalid abstain. A model that invents a policy can't remove anything. The category comes from the clause, never from the model. Evidence quotes must appear verbatim in the input. |
| **Asymmetric guards.** | A hate or violence flag is never auto-allowed, even when the vote and the arbiter lean "allow". A high metadata risk blocks auto-allowing a split case. LLM errors, timeouts and an exhausted budget all **fail closed to escalation**. |
| **The arbiter only runs on disagreement.** | The expensive model is called for a minority of items. If it isn't confident it abstains, and the item goes to a human. |
| **Cost control in layers.** | A $0 local gate skips the panel for clearly benign items. The cascade calls the context agent only when needed. The metadata agent is rules ($0). Responses are cached by prompt hash (repeats and re-runs are free). The policy is sent as a `cache_control` system prefix. Every run has a hard USD budget. The API has a daily LLM-call cap and degrades to gate-only mode when it's hit. |
| **The same code path runs offline.** | `MockClient` returns the same `cast_vote` payload from heuristics. Tests, CI and the UI all run with no API key, and costs are *simulated* from token estimates. |

## Quickstart (Windows PowerShell; macOS/Linux is the same minus `.venv\Scripts`)

```powershell
cd moderation-triage
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements-dev.txt; pip install --no-deps -e .
copy .env.example .env          # add ANTHROPIC_API_KEY, or leave empty for mock mode
pytest -q                       # offline, no key needed

# 1) data: Civil Comments v1.2 (~430 MB zip, includes parent_text for the context agent)
python -m modtriage.cli download
python -m modtriage.cli prepare --eval-n 600 --pos-frac 0.3
python -m modtriage.cli train-gate          # prints a suggested MODTRIAGE_GATE_ALLOW_BELOW

# 2) eval: mock first (free), then real
python -m modtriage.cli eval --data data/processed/eval_dev.jsonl        # mock if no key
python -m modtriage.cli eval --limit 300 --yes                           # real API, hard $5 cap/mode

# 3) app
python -m modtriage.cli serve               # API on :8000
cd frontend; npm install; npm run dev       # UI on :5173 (proxies /api)
```

If the zip URL is unavailable, use `download --source hf` (Hugging Face `google/civil_comments`, text and labels only, no parent comments). You can also download Kaggle's *Jigsaw Unintended Bias* `all_data.csv` yourself and run `prepare --csv path\to\all_data.csv`. That file also has reaction counts for the metadata agent. Its `rating` column is dropped on purpose because it leaks the moderation outcome.

Docker: `docker build -t modtriage . && docker run -p 8000:8000 --env-file .env modtriage`. One-click Render deploy is in `render.yaml`.

## Evaluation methodology

- **Data.** Civil Comments test split. Labels are fractions of crowd raters, binarized at ≥ 0.5 (the original competition's convention). Sub-labels map to policy categories: `insult→harassment`, `identity_attack→hate`, `threat→violence`, `sexual_explicit→sexual`, `obscene→profanity`.
- **Sampling.** A stratified sample oversamples violations to 30% for statistical power. Each row carries a **weight** that re-weights every metric back to the population prevalence (about 8%), so precision isn't inflated.
- **Splits.** The gate is trained on *train* and its skip threshold is picked on *validation*. It's set so that skipping loses at most 2% of violations. Iterate on prompts with `eval_dev.jsonl` (from validation). Touch `eval_test.jsonl` only for the numbers you report.
- **Metrics.**
  - `precision`: share of removals that were real violations (wrongful takedowns).
  - `auto_recall`: violations removed with no human involved.
  - `system_recall`: violations removed *or escalated*, assuming reviewers decide correctly.
  - `escalation_rate`: reviewer workload.
  - `$ per 1k items`: list price, before the response cache.
  - Also reported: calls per item, arbiter rate, p50/p95 latency, bootstrap 95% CIs, per-category precision and recall, citation accuracy, per-agent precision and recall, and the invalid-citation rate.
- **Ablations.** `gate_only` → `single` (one Haiku call) → `majority` (plain vote) → `full` (weighted vote plus arbiter) → `cascade` (cheapest routing). The central question is how much quality each extra dollar buys.
- **Threshold sweep.** Stored votes are re-aggregated at different thresholds, which traces the precision / recall / escalation trade-off with **no new API calls**.

> Mock-provider numbers are a pipeline smoke test, not model quality. Report only numbers from `--yes` runs, and label them with the model ids and date.

### Results (fill in after your real run)

| mode | precision | auto recall | system recall | escalation | $/1k items |
|---|---|---|---|---|---|
| gate_only | | | | | 0 |
| single | | | | | |
| full | | | | | |
| cascade | | | | | |

## Cost notes

Prices in `llm.py` (per million tokens, checked Sep 2026): Haiku 4.5 $1 in / $5 out; Sonnet 5 $2 in / $10 out. A specialist call is roughly 1.2k input and 100 output tokens, about **$0.002**. The mock eval estimates about $1.1 per 1k items for `single`, $2.6 for `full` and $2.0 for `cascade` on the dev fixture *before* the gate. Most real traffic is benign, so the gate should cut these further. Measure it on your data.

- **Prompt caching.** The policy is marked with `cache_control`, but a prefix only caches above the model's minimum cacheable length. Our ~1k-token policy may be below it. Check `cache_read_tokens` in real usage. Adding worked examples to the policy both improves accuracy and gets the prefix over the minimum.
- **Batch API.** The Message Batches API is 50% cheaper and would be the natural next step for offline eval runs.
- **Model retirement.** Anthropic lists Haiku 4.5's retirement as "not sooner than Oct 15 2026". Model ids are env vars, so swap `MODTRIAGE_SPECIALIST_MODEL` when a successor ships.

## Project layout

```
policy/community_policy.yaml   clauses (HAR/HATE/VIO/SEX/PROF/SPAM), exceptions (EX-1..4), severities
src/modtriage/
  agents.py      text / context / metadata specialists + arbiter, prompt-injection-safe wrapping
  voting.py      confidence-weighted vote, high-severity guard, majority baseline
  pipeline.py    routing modes (gate_only | single | majority | full | cascade), budget + failure handling
  policy.py      policy loading, stable prompt rendering, citation validation
  llm.py         Anthropic client (tool-based structured output), response cache, pricing, budget ledger
  mock.py        offline heuristic agents with simulated cost
  gate.py        TF-IDF + logistic regression $0 gate and recall-bounded threshold
  data.py        Civil Comments loaders, label mapping, stratified weighted sampling
  evaluation.py  metrics, bootstrap CIs, ablations, threshold sweep, markdown/JSON reports
  store.py       SQLite: decisions, human review queue, LLM cache, usage ledger
  api/app.py     FastAPI (moderate, queue, review, usage, eval) + serves the built UI
frontend/        React + TS: Triage playground, Review queue, Evaluation dashboard, Policy
tests/           31 offline tests (policy, voting, pipeline, eval, data, gate) + API tests
```

## 2–3 week plan

**Week 1: core and data.** Days 1–2: set up the env, run the tests and the mock UI, get the Civil Comments download and `prepare` working, then train the gate and record its AUC and skip rate. Days 3–4: run `single` mode with a real key on 100 dev items. Read 20 failures and tighten the prompts and policy wording (dev split only). Day 5: first real `full` vs `cascade` run on 300 dev items, with a $5 cap.

**Week 2: rigor.** Run the full ablation on `eval_test.jsonl` (600 items) and fill in the results table. Tune `remove/allow` thresholds and the gate threshold from the sweep. Error analysis: 3–5 failure buckets with examples (sarcasm, quoted slurs, reclaimed language, profanity vs insult, label noise). Optional: a `--context-only` eval on replies, to show what the context agent adds.

**Week 3: product and polish.** Review ~30 escalated cases in the UI and compute how often the reviewer agreed with the arbiter's lean. Deploy to Render. Add a README demo GIF and the results. Stretch goals: Batch API for evals, few-shot policy examples (plus caching), agent-weight calibration from dev results, a ToxiGen or HateXplain cross-dataset check.

## Resume bullets (fill in the numbers after a real run)

- Built a multi-agent content-moderation triage system (FastAPI, React, Claude). Specialist agents cast confidence-weighted votes with policy-clause citations that are validated in code, and a stronger arbiter model is called only on disagreement. Reached **X% precision / Y% system recall** on Civil Comments with **Z% of items** escalated to human review.
- Designed an eval harness (prevalence-weighted precision/recall, bootstrap CIs, 5-way ablation, threshold sweep). It showed that the cascade router matched the full panel's quality at **N% lower cost ($A vs $B per 1k items)**.
- Engineered cost and safety controls: a $0 TF-IDF gate (skips N% of traffic at ≤2% recall loss), a response cache, hard run budgets and daily caps, fail-closed escalation on errors, and a guard that never auto-allows a high-severity flag.

## Limitations (say these out loud in interviews)

- Civil Comments labels are noisy crowd judgments from one publisher platform, and toxicity ≠ policy violation. Criticism of officials (EX-3) is often rated "toxic".
- The public data has thin metadata (no author history). The metadata agent mostly abstains here, and its value would show on platform data.
- "System recall" assumes human reviewers are always right.
- The mock provider is not a model. Its numbers only prove the plumbing works.
