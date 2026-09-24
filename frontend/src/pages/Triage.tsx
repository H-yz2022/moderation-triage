import { useEffect, useState } from "react";
import { api } from "../api";
import { Clauses, Verdict, VoteCard, fmtPct } from "../components/Votes";
import type { DecisionOut, Health, Policy, Sample } from "../types";

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
  const [goldLabel, setGoldLabel] = useState<string | null>(null);
  const [result, setResult] = useState<DecisionOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.samples().then(setSamples).catch(() => setSamples([]));
  }, []);

  const clauseTitle = (id: string) => policy?.clauses.find((c) => c.id === id)?.title;

  function loadSample(id: string) {
    const s = samples.find((x) => x.id === id);
    if (!s) return;
    setText(s.text);
    setParent(s.parent_text ?? "");
    const m: Record<string, string> = {};
    for (const f of META_FIELDS) if (s.metadata[f.key] != null) m[f.key] = String(s.metadata[f.key]);
    setMeta(m);
    setGoldLabel(s.label ? `violation (${s.categories.join(", ") || "unspecified"})` : "no violation");
    setResult(null);
  }

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      const metadata: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(meta)) if (v.trim() !== "" && !Number.isNaN(Number(v))) metadata[k] = Number(v);
      if (parent.trim()) metadata.has_parent = true;
      setResult(await api.moderate({ text, parent_text: parent.trim() || null, metadata, mode: mode || undefined }));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid side">
      <div className="card stack">
        <h2>Submit content</h2>
        {samples.length > 0 && (
          <div>
            <label className="field" htmlFor="sample">Load a labelled example</label>
            <select id="sample" defaultValue="" onChange={(e) => loadSample(e.target.value)}>
              <option value="" disabled>Choose…</option>
              {samples.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.id} · {s.text.slice(0, 48)}
                </option>
              ))}
            </select>
            {goldLabel && <p className="small muted" style={{ marginTop: 4 }}>Gold label: {goldLabel}</p>}
          </div>
        )}
        <div>
          <label className="field" htmlFor="text">Comment</label>
          <textarea id="text" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste a comment…" />
        </div>
        <div>
          <label className="field" htmlFor="parent">Parent comment (optional context)</label>
          <textarea id="parent" value={parent} onChange={(e) => setParent(e.target.value)} style={{ minHeight: 56 }} />
        </div>
        <div className="grid two" style={{ gap: 8 }}>
          {META_FIELDS.map((f) => (
            <div key={f.key}>
              <label className="field" htmlFor={f.key}>{f.label}</label>
              <input id={f.key} inputMode="decimal" value={meta[f.key] ?? ""}
                onChange={(e) => setMeta({ ...meta, [f.key]: e.target.value })} />
            </div>
          ))}
        </div>
        <div>
          <label className="field" htmlFor="mode">Routing mode</label>
          <select id="mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">default ({health?.mode ?? "cascade"})</option>
            {(health?.modes ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <button className="btn primary" disabled={busy || !text.trim()} onClick={submit}>
          {busy ? "Running panel…" : "Moderate"}
        </button>
        {err && <div className="error">{err}</div>}
      </div>

      <div className="stack">
        {!result && <div className="card empty">Submit a comment to see each specialist's vote, the cited policy clauses, and what it cost.</div>}
        {result && (
          <>
            {!result.llm_allowed && <div className="banner">Daily LLM cap reached — running in gate-only degraded mode.</div>}
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
