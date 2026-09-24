import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { ActionPill, Clauses, VoteCard } from "../components/Votes";
import type { Policy, StoredDecision } from "../types";

type Filter = "pending" | "reviewed" | "auto";

export default function Queue({ policy }: { policy: Policy | null }) {
  const [filter, setFilter] = useState<Filter>("pending");
  const [items, setItems] = useState<StoredDecision[]>([]);
  const [stats, setStats] = useState<Record<string, number>>({});
  const [sel, setSel] = useState<StoredDecision | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, s] = await Promise.all([api.decisions(filter), api.queueStats()]);
      setItems(list);
      setStats(s);
      setSel((cur) => list.find((x) => x.id === cur?.id) ?? list[0] ?? null);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

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

  return (
    <div className="grid side">
      <div className="card" style={{ padding: 0 }}>
        <div className="row spread" style={{ padding: 12, borderBottom: "1px solid var(--border)" }}>
          <div className="tabs" role="tablist">
            {(["pending", "reviewed", "auto"] as Filter[]).map((f) => (
              <button key={f} role="tab" aria-selected={filter === f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
                {f === "auto" ? "auto-decided" : f} ({stats[f] ?? 0})
              </button>
            ))}
          </div>
        </div>
        {items.length === 0 && <div className="empty">Nothing here yet.</div>}
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
  );
}
