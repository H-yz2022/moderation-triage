import type { Policy } from "../types";

export default function PolicyPage({ policy }: { policy: Policy | null }) {
  if (!policy) return <div className="card empty">Loading policy…</div>;
  return (
    <div className="stack">
      <div className="row spread">
        <h1>{policy.name}</h1>
        <span className="pill">version {policy.version}</span>
      </div>
      <p className="muted">
        Every violation vote has to cite at least one clause below. A vote that cites an unknown clause is dropped, so it can't change the outcome. High-severity
        categories are never auto-allowed while a specialist still flags them. Those cases go to human review.
      </p>
      {Object.entries(policy.categories).map(([key, cat]) => (
        <div className="card" key={key}>
          <div className="row spread">
            <h2>{cat.label}</h2>
            <span className="row">
              <span className={`pill ${cat.severity === "high" ? "remove" : cat.severity === "low" ? "" : "escalate"}`}>severity: {cat.severity}</span>
              {cat.dataset_labels.length > 0 && <span className="small muted">dataset label: {cat.dataset_labels.join(", ")}</span>}
            </span>
          </div>
          {policy.clauses.filter((c) => c.category === key).map((c) => (
            <div key={c.id} style={{ marginTop: 10 }}>
              <span className="pill clause">{c.id}</span> <b>{c.title}</b>
              <p className="small" style={{ marginTop: 4 }}>{c.text}</p>
            </div>
          ))}
        </div>
      ))}
      <div className="card">
        <h2>Exceptions</h2>
        {policy.exceptions.map((e) => (
          <div key={e.id} style={{ marginTop: 10 }}>
            <span className="pill allow">{e.id}</span> <b>{e.title}</b>
            <p className="small" style={{ marginTop: 4 }}>{e.text}</p>
          </div>
        ))}
      </div>
      {policy.examples?.length > 0 && (
        <div className="card">
          <h2>Worked examples</h2>
          <p className="small muted">
            These go into the policy prompt every agent sees, to show where the boundaries sit. They're synthetic on purpose. Copying eval rows here would
            leak the test set.
          </p>
          {policy.examples.map((ex, i) => (
            <div key={i} style={{ marginTop: 12 }}>
              {ex.parent && <div className="quote small muted">In reply to: {ex.parent}</div>}
              <div className="quote">{ex.text}</div>
              <div className="row" style={{ marginTop: 6 }}>
                <span className={`pill ${ex.label}`}>{ex.label === "violation" ? "violation" : "no violation"}</span>
                {ex.clause_ids.map((c) => <span key={c} className="pill clause">{c}</span>)}
                {ex.exception_ids.map((c) => <span key={c} className="pill allow">{c}</span>)}
                <span className="small">{ex.why}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
