import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ActionPill, Clauses, VoteCard, fmtPct } from "../components/Votes";
import type { Agreement, Policy, StoredDecision } from "../types";

type Filter = "pending" | "reviewed" | "auto";

export default function Queue({ policy }: { policy: Policy | null }) {
  const [filter, setFilter] = useState<Filter>("pending");
  const [items, setItems] = useState<StoredDecision[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [sel, setSel] = useState<StoredDecision | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [agreement, setAgreement] = useState<Agreement | null>(null);

  // debounce the search box so typing doesn't fire a request per keystroke
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const load = useCallback(async () => {
    try {
      const [list, s] = await Promise.all([api.decisions(filter, 100, q), api.queueStats()]);
      setItems(list);
      setStats(s);
      setSel((cur) => list.find((x) => x.id === cur?.id) ?? list[0] ?? null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [filter, q]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api.agreement().then(setAgreement).catch(() => setAgreement(null));
  }, [stats.reviewed]);

  useEffect(() => {
    setPicked(sel?.clause_ids ?? []);
    setNote("");
    setErr(null);
  }, [sel?.id]);

  const clauseTitle = (id: string) => policy?.clauses.find((c) => c.id === id)?.title;

  async function decide(action: "remove" | "allow") {
    if (!sel) return;
    try {
      await api.review(sel.id, action, action === "remove" ? picked : [], note);
      await load();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  // keyboard: j/k move through the list, a = allow, r = remove (needs a clause picked)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (e.ctrlKey || e.metaKey || e.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
      const i = items.findIndex((x) => x.id === sel?.id);
      if (e.key === "j" || e.key === "k") {
        const next = items[Math.min(Math.max(i + (e.key === "j" ? 1 : -1), 0), items.length - 1)];
        if (next) setSel(next);
      } else if (sel?.review_status === "pending" && e.key === "a") {
        decide("allow");
      } else if (sel?.review_status === "pending" && e.key === "r" && picked.length > 0) {
        decide("remove");
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="stack">
      {agreement && agreement.n_reviewed > 0 && <AgreementCard a={agreement} />}
      <div className="grid side">
      <div className="card" style={{ padding: 0 }}>
        <div className="stack" style={{ padding: 12, borderBottom: "1px solid var(--border)", gap: 8 }}>
          <div className="tabs" role="tablist">
            {(["pending", "reviewed", "auto"] as Filter[]).map((f) => (
              <button key={f} role="tab" aria-selected={filter === f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
                {f === "auto" ? "auto-decided" : f} ({stats[f] ?? 0})
              </button>
            ))}
          </div>
          <input type="search" aria-label="Search comments" placeholder="Search comment text…" value={search} onChange={(e) => setSearch(e.target.value)} />
          <div className="row spread small muted">
            <span><kbd>j</kbd>/<kbd>k</kbd> move · <kbd>a</kbd> allow · <kbd>r</kbd> remove</span>
            <span className="row">
              <a href={`/api/export/decisions.csv?status=${filter}`} download>CSV</a>
              <a href="/api/export/reviews.jsonl" download title="Reviewed cases in eval format: modtriage eval --data human_reviews.jsonl">gold set</a>
            </span>
          </div>
        </div>
        {items.length === 0 && <div className="empty">{q ? `No ${filter} items match “${q}”.` : "Nothing here yet."}</div>}
        {items.map((d) => (
          <div key={d.id} className={`queue-item ${sel?.id === d.id ? "selected" : ""}`} onClick={() => setSel(d)}
            role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSel(d)}>
            <div className="row spread small">
              <span className="muted">#{d.id} · {new Date(d.created_at).toLocaleString()}</span>
              {d.review_status === "reviewed" && d.reviewer_action ? <ActionPill action={d.reviewer_action as "remove" | "allow"} /> : <ActionPill action={d.action} />}
            </div>
            <div className="clamp">{d.text}</div>
          </div>
        ))}
      </div>

      <div className="stack">
        {!sel && <div className="card empty">Escalated items appear here for a human decision.</div>}
        {sel && (
          <>
            <div className="card stack">
              <div className="row spread">
                <h2>Case #{sel.id}</h2>
                <span className="row"><span className="small muted">system:</span><ActionPill action={sel.action} /><span className="small muted">by {sel.decided_by}</span></span>
              </div>
              {sel.parent_text && (
                <div>
                  <div className="field small muted">In reply to</div>
                  <div className="quote">{sel.parent_text}</div>
                </div>
              )}
              <div>
                <div className="field small muted">Comment</div>
                <div className="quote" style={{ borderLeftColor: "var(--accent)" }}>{sel.text}</div>
              </div>
              {Object.keys(sel.metadata ?? {}).length > 0 && (
                <div className="small muted mono">{JSON.stringify(sel.metadata)}</div>
              )}
              <ul style={{ margin: 0, paddingLeft: 18 }}>{sel.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
              <div className="row"><strong className="small">Panel-cited clauses:</strong><Clauses ids={sel.clause_ids} title={clauseTitle} /></div>
            </div>

            {sel.review_status === "pending" ? (
              <div className="card stack">
                <h3>Your decision</h3>
                <div>
                  <div className="field small muted">Clauses violated (required to remove)</div>
                  <div className="row">
                    {policy?.clauses.map((c) => (
                      <button key={c.id} className={`pill ${picked.includes(c.id) ? "clause" : ""}`} style={{ cursor: "pointer" }}
                        aria-pressed={picked.includes(c.id)} title={c.text} onClick={() => toggle(c.id)}>
                        {c.id} · {c.title}
                      </button>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="field" htmlFor="note">Note (optional)</label>
                  <input id="note" value={note} onChange={(e) => setNote(e.target.value)} />
                </div>
                <div className="row">
                  <button className="btn danger" disabled={picked.length === 0} onClick={() => decide("remove")}>Remove</button>
                  <button className="btn ok" onClick={() => decide("allow")}>Allow</button>
                </div>
                {err && <div className="error">{err}</div>}
              </div>
            ) : sel.review_status === "reviewed" ? (
              <div className="card">
                <h3>Reviewed</h3>
                <div className="row"><ActionPill action={sel.reviewer_action as "remove" | "allow"} /><Clauses ids={sel.reviewer_clauses} title={clauseTitle} /></div>
                {sel.reviewer_note && <p className="small" style={{ marginTop: 6 }}>{sel.reviewer_note}</p>}
              </div>
            ) : null}

            <div className="card">
              <h3>Votes</h3>
              <div className="votes">{sel.votes.map((v, i) => <VoteCard key={i} v={v} title={clauseTitle} />)}</div>
            </div>
          </>
        )}
      </div>
      </div>
    </div>
  );
}

const AGENT_NAMES: Record<string, string> = { text: "Text", context: "Context", metadata: "Metadata", arbiter: "Arbiter" };

function AgreementCard({ a }: { a: Agreement }) {
  return (
    <details className="card">
      <summary style={{ cursor: "pointer" }}>
        <h3 style={{ display: "inline" }}>Reviewer agreement</h3>
        <span className="small muted" style={{ marginLeft: 12 }}>
          {a.n_reviewed} reviewed · the reviewer agreed with the system's lean {fmtPct(a.lean_agreement.rate, 0)} of the time (n={a.lean_agreement.n})
        </span>
      </summary>
      <div className="stack" style={{ marginTop: 12 }}>
        <p className="small muted">
          Reviewed cases are the escalated, hard ones, so these rates understate accuracy on the whole stream. They show whom to trust when the panel
          disagrees. Run <span className="mono">modtriage calibrate --write</span> to apply the suggested weights.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>agent</th><th className="num">votes</th><th className="num">abstained</th><th className="num">agreed w/ reviewer</th><th className="num">precision</th><th className="num">recall</th><th className="num">weight now</th><th className="num">suggested</th></tr>
            </thead>
            <tbody>
              {Object.entries(a.agents).map(([name, s]) => {
                const cal = a.calibration[name];
                const now = a.active_weights?.[name] ?? cal?.default;
                return (
                  <tr key={name}>
                    <td>{AGENT_NAMES[name] ?? name}</td>
                    <td className="num">{s.n}</td>
                    <td className="num">{s.abstain}</td>
                    <td className="num">{fmtPct(s.accuracy, 0)}</td>
                    <td className="num">{fmtPct(s.precision, 0)}</td>
                    <td className="num">{fmtPct(s.recall, 0)}</td>
                    <td className="num">{now?.toFixed(2) ?? "–"}</td>
                    <td className="num">
                      {!cal ? "–" : cal.calibrated ? cal.weight.toFixed(2) : <span className="muted" title={`needs more reviewed votes (has ${cal.n})`}>{cal.weight.toFixed(2)}*</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="row small">
          <span>Reviewer decisions: {Object.entries(a.reviewer_actions).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>
          {a.clause_agreement.mean_jaccard != null && (
            <span className="muted">· clause overlap on removals {fmtPct(a.clause_agreement.mean_jaccard, 0)} (n={a.clause_agreement.n})</span>
          )}
          {Object.entries(a.by_route).map(([route, r]) => (
            <span key={route} className="muted">· via {route.replace("_", " ")}: {fmtPct(r.rate, 0)} of {r.n}</span>
          ))}
        </div>
        <p className="small muted">* default kept until the agent has enough reviewed votes.</p>
      </div>
    </details>
  );
}
