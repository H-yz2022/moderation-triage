import { useEffect, useState } from "react";
import { api } from "../api";
import { GroupedBars, Legend, Lines } from "../components/Charts";
import { fmtPct } from "../components/Votes";
import type { EvalReport } from "../types";

const SERIES = [
  { key: "precision", label: "Precision", color: "var(--series-1)" },
  { key: "system_recall", label: "System recall", color: "var(--series-3)" },
  { key: "escalation_rate", label: "Escalation rate", color: "var(--series-2)" },
];

export default function Eval() {
  const [rep, setRep] = useState<EvalReport | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.evalLatest().then(setRep).catch((e) => setErr((e as Error).message));
  }, []);

  if (err) return <div className="card empty">{err}</div>;
  if (!rep) return <div className="card empty">Loading…</div>;

  const main = rep.results.find((r) => r.mode === "cascade") ?? rep.results[rep.results.length - 1];
  const bars = rep.results.map((r) => ({ mode: r.mode, ...r.metrics })) as Record<string, number | string | null>[];
  const sweep = rep.sweep as Record<string, number | null>[];

  return (
    <div className="stack">
      {rep.provider === "mock" && (
        <div className="banner">
          This report came from the offline <b>mock</b> provider. It checks that the pipeline works end to end. It does not measure model quality.
          Run <code>modtriage eval --yes</code> with an API key for real numbers.
        </div>
      )}
      <div className="row spread">
        <h1>Evaluation · {rep.dataset}</h1>
        <span className="small muted">{rep.n_items} items{rep.weighted ? " · prevalence-weighted" : ""} · policy {rep.policy_version} · {new Date(rep.generated_at).toLocaleString()}</span>
      </div>

      <div className="kpis">
        <div className="kpi"><div className="l">Precision ({main.mode})</div><div className="v">{fmtPct(main.metrics.precision)}</div></div>
        <div className="kpi"><div className="l">Auto recall</div><div className="v">{fmtPct(main.metrics.auto_recall)}</div></div>
        <div className="kpi"><div className="l">System recall (w/ humans)</div><div className="v">{fmtPct(main.metrics.system_recall)}</div></div>
        <div className="kpi"><div className="l">Escalation rate</div><div className="v">{fmtPct(main.metrics.escalation_rate)}</div></div>
        <div className="kpi"><div className="l">List cost / 1k items</div><div className="v">${main.cost.list_cost_per_1k_usd.toFixed(2)}</div></div>
        <div className="kpi"><div className="l">LLM calls / item</div><div className="v">{main.cost.llm_calls_per_item.toFixed(2)}</div></div>
      </div>

      <div className="grid two">
        <div className="card">
          <div className="row spread"><h3>Quality by configuration</h3><Legend series={SERIES} /></div>
          <GroupedBars rows={bars} series={SERIES} labelKey="mode" />
        </div>
        <div className="card">
          <div className="row spread"><h3>Threshold sweep ({rep.sweep_mode})</h3><Legend series={SERIES} /></div>
          <Lines rows={sweep} xKey="threshold" series={SERIES} />
          <p className="small muted">Re-aggregates the stored votes at each threshold. It makes no new LLM calls, and split cases count as escalations.</p>
        </div>
      </div>

      <div className="card">
        <h3>Configurations</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>mode</th><th className="num">precision</th><th className="num">auto recall</th><th className="num">system recall</th>
                <th className="num">escalation</th><th className="num">F1 (auto)</th><th className="num">$/1k (list)</th>
                <th className="num">calls/item</th><th className="num">arbiter rate</th><th className="num">p95 ms</th>
              </tr>
            </thead>
            <tbody>
              {rep.results.map((r) => (
                <tr key={r.mode}>
                  <td><b>{r.mode}</b></td>
                  <td className="num" title={r.ci95.precision ? `95% CI ${fmtPct(r.ci95.precision[0])}–${fmtPct(r.ci95.precision[1])}` : ""}>{fmtPct(r.metrics.precision)}</td>
                  <td className="num">{fmtPct(r.metrics.auto_recall)}</td>
                  <td className="num">{fmtPct(r.metrics.system_recall)}</td>
                  <td className="num">{fmtPct(r.metrics.escalation_rate)}</td>
                  <td className="num">{r.metrics.f1_auto?.toFixed(3) ?? "–"}</td>
                  <td className="num">{r.cost.list_cost_per_1k_usd.toFixed(3)}</td>
                  <td className="num">{r.cost.llm_calls_per_item.toFixed(2)}</td>
                  <td className="num">{fmtPct(r.cost.arbiter_rate)}</td>
                  <td className="num">{r.latency_ms.p95.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted" style={{ marginTop: 8 }}>
          Auto recall counts only violations removed without a human. System recall also counts violations sent to the review queue, assuming the reviewers decide correctly.
        </p>
      </div>

      <div className="grid two">
        <div className="card">
          <h3>Per category ({main.mode})</h3>
          <table>
            <thead><tr><th>category</th><th className="num">support</th><th className="num">precision</th><th className="num">recall</th></tr></thead>
            <tbody>
              {Object.entries(main.per_category).map(([c, m]) => (
                <tr key={c}><td>{c}</td><td className="num">{m.support}</td><td className="num">{fmtPct(m.precision)}</td><td className="num">{fmtPct(m.recall)}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 8 }}>Citation accuracy: {fmtPct(main.citation_accuracy)}. This is the share of correct removals whose cited clause category matches a gold category.</p>
        </div>
        <div className="card">
          <h3>Specialists ({main.mode})</h3>
          <table>
            <thead><tr><th>agent</th><th className="num">votes</th><th className="num">abstain</th><th className="num">precision</th><th className="num">recall</th></tr></thead>
            <tbody>
              {Object.entries(main.agents).filter(([a]) => !a.startsWith("_")).map(([a, m]) => (
                <tr key={a}><td>{a}</td><td className="num">{m.votes}</td><td className="num">{m.abstain}</td><td className="num">{fmtPct(m.precision)}</td><td className="num">{fmtPct(m.recall)}</td></tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 8 }}>
            Text/context agreement: {fmtPct(main.agents._panel?.text_context_agreement ?? null)} · invalid citation rate: {fmtPct(main.agents._panel?.invalid_citation_rate ?? null)}
          </p>
        </div>
      </div>
    </div>
  );
}
