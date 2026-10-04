import { useState } from "react";
import { api } from "../api";
import { ActionPill, Clauses } from "../components/Votes";
import type { BatchItem, BatchOut, Health, Policy } from "../types";

const MAX_BATCH = 25;

/** One comment per line, or JSONL rows shaped like {"text": ..., "parent_text": ..., "id": ...}. */
function parse(raw: string): { items: BatchItem[]; error: string | null } {
  const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const items: BatchItem[] = [];
  for (const [i, line] of lines.entries()) {
    if (line.startsWith("{")) {
      try {
        const o = JSON.parse(line);
        if (typeof o.text !== "string" || !o.text.trim()) return { items, error: `line ${i + 1}: JSON row needs a "text" field` };
        items.push({ text: o.text, parent_text: o.parent_text ?? null, id: o.id != null ? String(o.id) : undefined });
      } catch {
        return { items, error: `line ${i + 1}: invalid JSON` };
      }
    } else {
      items.push({ text: line });
    }
  }
  return { items, error: null };
}

function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

export default function Batch({ health, policy }: { health: Health | null; policy: Policy | null }) {
  const [raw, setRaw] = useState("");
  const [mode, setMode] = useState("");
  const [out, setOut] = useState<BatchOut | null>(null);
  const [inputs, setInputs] = useState<BatchItem[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { items, error: parseErr } = parse(raw);
  const tooMany = items.length > MAX_BATCH;
  const clauseTitle = (id: string) => policy?.clauses.find((c) => c.id === id)?.title;

  async function loadFile(f: File | undefined) {
    if (f) setRaw(await f.text());
  }

  async function loadSamples() {
    const s = await api.samples();
    setRaw(s.slice(0, MAX_BATCH).map((x) => JSON.stringify({ id: x.id, text: x.text, parent_text: x.parent_text })).join("\n"));
  }

  async function run() {
    setBusy(true);
    setErr(null);
    try {
      setInputs(items);
      setOut(await api.moderateBatch(items, mode));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function exportResults() {
    if (!out) return;
    const rows = out.results.map((r, i) => ({
      id: r.item_id, text: inputs[i]?.text, action: r.action, decided_by: r.decided_by,
      clause_ids: r.clause_ids, categories: r.categories, score: r.score, reasons: r.reasons, cost_usd: r.cost_usd,
    }));
    download("batch_decisions.jsonl", rows.map((r) => JSON.stringify(r)).join("\n") + "\n", "application/x-ndjson");
  }

  return (
    <div className="stack">
      <div className="card stack">
        <div className="row spread">
          <h2>Batch moderation</h2>
          <span className="small muted">up to {MAX_BATCH} items per run · larger files: <span className="mono">modtriage moderate-file</span></span>
        </div>
        <p className="small muted">
          One comment per line, or JSONL rows like <span className="mono">{'{"text": "…", "parent_text": "…", "id": "…"}'}</span>.
          Escalated items land in the review queue.
        </p>
        <textarea aria-label="Comments, one per line" value={raw} onChange={(e) => setRaw(e.target.value)} style={{ minHeight: 160 }}
          placeholder={"Thanks, that was really helpful.\nYou are an idiot and everyone knows it.\n{\"text\": \"Agreed, what a loser.\", \"parent_text\": \"I like trains\"}"} />
        <div className="row spread">
          <div className="row">
            <label className="btn">
              Load .jsonl / .txt
              <input type="file" accept=".jsonl,.txt,.ndjson" hidden onChange={(e) => loadFile(e.target.files?.[0])} />
            </label>
            <button className="btn" onClick={loadSamples}>Load labelled samples</button>
            <select aria-label="Routing mode" value={mode} onChange={(e) => setMode(e.target.value)} style={{ width: "auto" }}>
              <option value="">mode: default ({health?.mode ?? "cascade"})</option>
              {(health?.modes ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <button className="btn primary" disabled={busy || items.length === 0 || tooMany || !!parseErr} onClick={run}>
            {busy ? `Moderating ${items.length}…` : `Moderate ${items.length} item${items.length === 1 ? "" : "s"}`}
          </button>
        </div>
        {parseErr && <div className="error">{parseErr}</div>}
        {tooMany && <div className="error">{items.length} items: the API takes at most {MAX_BATCH} per request.</div>}
        {err && <div className="error">{err}</div>}
      </div>

      {out && (
        <div className="card stack">
          <div className="kpis">
            <div className="kpi"><div className="v">{out.summary.n}</div><div className="l">items</div></div>
            <div className="kpi"><div className="v" style={{ color: "var(--remove)" }}>{out.summary.actions.remove}</div><div className="l">removed</div></div>
            <div className="kpi"><div className="v" style={{ color: "var(--allow)" }}>{out.summary.actions.allow}</div><div className="l">allowed</div></div>
            <div className="kpi"><div className="v" style={{ color: "var(--escalate)" }}>{out.summary.actions.escalate}</div><div className="l">to human review</div></div>
            <div className="kpi"><div className="v">${out.summary.cost_usd.toFixed(4)}</div><div className="l">{out.summary.llm_calls} LLM calls</div></div>
          </div>
          <div className="row spread">
            <h3>Decisions</h3>
            <button className="btn" onClick={exportResults}>Download JSONL</button>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>comment</th><th>decision</th><th>by</th><th>clauses</th><th className="num">score</th><th className="num">USD</th></tr></thead>
              <tbody>
                {out.results.map((r, i) => (
                  <tr key={r.id}>
                    <td className="mono small">{r.id}</td>
                    <td><div className="clamp" title={r.reasons.join("\n")}>{inputs[i]?.text}</div></td>
                    <td><ActionPill action={r.action} /></td>
                    <td className="small">{r.decided_by.replace("_", " ")}</td>
                    <td><Clauses ids={r.clause_ids} title={clauseTitle} /></td>
                    <td className="num">{r.score >= 0 ? "+" : ""}{r.score.toFixed(2)}</td>
                    <td className="num">{r.cost_usd.toFixed(5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
