import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import SamplePicker from "../components/SamplePicker";
import { Clauses, Verdict, VoteCard, fmtPct } from "../components/Votes";
import type { DecisionOut, Health, Policy, Sample } from "../types";

// Shown on first visit: a reply with context that the panel removes with clause
// citations, so the opening screen shows every part of the system.
const DEFAULT_SAMPLE_ID = "dev-002";

const META_FIELDS: { key: string; label: string }[] = [
  { key: "account_age_days", label: "Account age (days)" },
  { key: "prior_strikes", label: "Prior strikes" },
  { key: "report_count", label: "User reports" },
  { key: "posts_last_hour", label: "Posts last hour" },
];

export default function Triage({ health, policy }: { health: Health | null; policy: Policy | null }) {
  const [text, setText] = useState("");
  const [parent, setParent] = useState("");
  const [meta, setMeta] = useState<Record<string, string>>({});
  const [mode, setMode] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [selected, setSelected] = useState<Sample | null>(null);
  const [result, setResult] = useState<DecisionOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reqId = useRef(0); // ignore responses that arrive after a newer request

  useEffect(() => {
    api
      .samples()
      .then((list) => {
        setSamples(list);
        const first = list.find((x) => x.id === DEFAULT_SAMPLE_ID) ?? list[0];
        if (first) loadSample(first);
      })
      .catch(() => setSamples([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const clauseTitle = (id: string) => policy?.clauses.find((c) => c.id === id)?.title;

  function toMeta(m: Record<string, string>) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(m)) if (v.trim() !== "" && !Number.isNaN(Number(v))) out[k] = Number(v);
    return out;
  }

  // Picking an example fills the form and shows its result right away as a
  // preview (not saved, so browsing doesn't fill the review queue).
  function loadSample(s: Sample) {
    const m: Record<string, string> = {};
    for (const f of META_FIELDS) if (s.metadata[f.key] != null) m[f.key] = String(s.metadata[f.key]);
    setSelected(s);
    setText(s.text);
    setParent(s.parent_text ?? "");
    setMeta(m);
    run(s.text, s.parent_text ?? "", m, false);
  }

  async function run(t: string, p: string, m: Record<string, string>, save: boolean) {
    const my = ++reqId.current;
    setBusy(true);
    setErr(null);
    try {
      const metadata = toMeta(m);
      if (p.trim()) metadata.has_parent = true;
      const r = await api.moderate({ text: t, parent_text: p.trim() || null, metadata, mode: mode || undefined, save });
      if (my === reqId.current) setResult(r);
    } catch (e) {
      if (my === reqId.current) setErr((e as Error).message);
    } finally {
      if (my === reqId.current) setBusy(false);
    }
  }

  const submit = () => run(text, parent, meta, true);
  const edited = !selected || text !== selected.text || parent !== (selected.parent_text ?? "");

  return (
    <div className="grid side">
      <div className="card stack">
        <h2>Submit content</h2>
        <p className="small muted" style={{ margin: 0 }}>
          Paste <b>one user comment</b> (a forum reply, a product review, a chat message; up to 4,000 characters). The panel checks it against the{" "}
          <a href="#policy">community policy</a> and decides whether to remove it, allow it, or send it to a human reviewer. Adding the comment it replies to
          helps it tell quoting and counter-speech from attacks.
        </p>
        {samples.length > 0 && <SamplePicker samples={samples} selected={edited ? null : selected} onSelect={loadSample} />}
        <div>
          <label className="field" htmlFor="text">Comment</label>
          <textarea id="text" value={text} onChange={(e) => setText(e.target.value)}
            placeholder="e.g. “Thanks, this helped a lot!” or “You clearly have no idea what you’re talking about, idiot.”" />
        </div>
        <div>
          <label className="field" htmlFor="parent">In reply to (optional)</label>
          <textarea id="parent" value={parent} onChange={(e) => setParent(e.target.value)} style={{ minHeight: 56 }}
            placeholder="The comment this one answers. Helps spot pile-ons, quotes and counter-speech." />
        </div>
        <details className="meta-details" open={Object.keys(meta).length > 0 || undefined}>
          <summary className="field" style={{ margin: 0 }}>Account signals (optional)</summary>
          <div className="grid two" style={{ gap: 8, marginTop: 8 }}>
            {META_FIELDS.map((f) => (
              <div key={f.key}>
                <label className="field" htmlFor={f.key}>{f.label}</label>
                <input id={f.key} inputMode="decimal" value={meta[f.key] ?? ""}
                  onChange={(e) => setMeta({ ...meta, [f.key]: e.target.value })} />
              </div>
            ))}
          </div>
        </details>
        <div>
          <label className="field" htmlFor="mode">Routing mode</label>
          <select id="mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">default ({health?.mode ?? "cascade"})</option>
            {(health?.modes ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <button className="btn primary" disabled={busy || !text.trim()} onClick={submit}>
          {busy ? "Running panel…" : "Moderate and save"}
        </button>
        {err && <div className="error">{err}</div>}
      </div>

      <div className="stack">
        {!result && <div className="card empty">Submit a comment to see each specialist's vote, the cited policy clauses, and what it cost.</div>}
        {result && (
          <>
            {!result.llm_allowed && <div className="banner">Daily LLM cap reached — running in gate-only degraded mode.</div>}
            {result.id == null && (
              <p className="small muted" style={{ margin: 0 }}>
                Preview of the selected example. It isn't saved; press <b>Moderate and save</b> to store it (escalations go to the review queue).
              </p>
            )}
            <Verdict action={result.action} by={result.decided_by} />
            <div className="card stack">
              <div className="row spread">
                <h3>Why</h3>
                <span className="small muted">
                  panel score {result.score >= 0 ? "+" : ""}{result.score.toFixed(2)} · gate {result.gate_score == null ? "n/a" : fmtPct(result.gate_score)}
                </span>
              </div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {result.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
              <div className="row"><strong className="small">Cited clauses:</strong> <Clauses ids={result.clause_ids} title={clauseTitle} /></div>
              {result.clause_ids.map((id) => {
                const c = policy?.clauses.find((x) => x.id === id);
                return c ? <div key={id} className="quote small"><b>{c.id} {c.title}.</b> {c.text}</div> : null;
              })}
            </div>
            <div className="card">
              <h3>Votes</h3>
              <div className="votes">{result.votes.map((v, i) => <VoteCard key={i} v={v} title={clauseTitle} />)}</div>
            </div>
            <div className="card">
              <div className="row spread">
                <h3>Cost</h3>
                <span className="small muted">
                  spend ${result.cost_usd.toFixed(5)} · list ${result.list_cost_usd.toFixed(5)}
                  {result.usage.some((u) => u.simulated) && " · simulated (mock provider)"}
                </span>
              </div>
              {result.usage.length === 0 ? (
                <p className="small muted">No LLM calls — decided by rules/gate for $0.</p>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>agent</th><th>model</th><th className="num">in</th><th className="num">cache read</th><th className="num">out</th><th className="num">USD</th><th className="num">ms</th></tr></thead>
                    <tbody>
                      {result.usage.map((u, i) => (
                        <tr key={i}>
                          <td>{u.agent}{u.cached ? " (cached)" : ""}</td><td className="mono">{u.model}</td>
                          <td className="num">{u.input_tokens}</td><td className="num">{u.cache_read_tokens}</td>
                          <td className="num">{u.output_tokens}</td><td className="num">{u.cost_usd.toFixed(5)}</td>
                          <td className="num">{u.latency_ms.toFixed(0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
