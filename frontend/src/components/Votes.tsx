import type { Action, Vote } from "../types";

const AGENT_LABEL: Record<string, string> = {
  text: "Text specialist",
  context: "Context specialist",
  metadata: "Metadata specialist",
  arbiter: "Arbiter",
};

const ACTION_LABEL: Record<Action, string> = {
  remove: "Remove",
  allow: "Allow",
  escalate: "Escalate to human",
};

export function ActionPill({ action }: { action: Action }) {
  return <span className={`pill ${action}`}>{ACTION_LABEL[action]}</span>;
}

export function Verdict({ action, by }: { action: Action; by: string }) {
  const icon = action === "remove" ? "✕" : action === "allow" ? "✓" : "!";
  return (
    <div className={`verdict ${action}`} role="status">
      <span aria-hidden>{icon}</span>
      <span>{ACTION_LABEL[action]}</span>
      <span className="small muted" style={{ fontWeight: 500, marginLeft: "auto" }}>
        decided by {by.replace("_", " ")}
      </span>
    </div>
  );
}

export function Clauses({ ids, title }: { ids: string[]; title?: (id: string) => string | undefined }) {
  if (!ids.length) return <span className="muted small">none</span>;
  return (
    <span className="row">
      {ids.map((id) => (
        <span key={id} className="pill clause" title={title?.(id)}>
          {id}
        </span>
      ))}
    </span>
  );
}

export function VoteCard({ v, title }: { v: Vote; title?: (id: string) => string | undefined }) {
  const labelText = v.label === "no_violation" ? "no violation" : v.label;
  return (
    <div className={`vote ${v.valid ? "" : "invalid"}`}>
      <div className="row spread">
        <strong>{AGENT_LABEL[v.agent] ?? v.agent}</strong>
        <span className={`pill ${v.label}`}>{labelText}</span>
      </div>
      <div className="small muted mono">{v.model}</div>
      {v.label !== "abstain" && (
        <>
          <div className="conf" aria-label={`confidence ${Math.round(v.confidence * 100)}%`}>
            <span style={{ width: `${Math.round(v.confidence * 100)}%` }} />
          </div>
          <div className="small muted">confidence {(v.confidence * 100).toFixed(0)}%</div>
        </>
      )}
      {v.risk != null && <div className="small">metadata risk: {(v.risk * 100).toFixed(0)}%</div>}
      {v.clause_ids.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <Clauses ids={v.clause_ids} title={title} />
        </div>
      )}
      {v.exception_ids.length > 0 && <div className="small">exceptions: {v.exception_ids.join(", ")}</div>}
      {v.evidence && <div className="quote small" style={{ marginTop: 6 }}>“{v.evidence}”</div>}
      {v.rationale && <p className="small" style={{ marginTop: 6 }}>{v.rationale}</p>}
      {v.invalid_reason && <p className="small" style={{ color: "var(--escalate)" }}>⚠ {v.invalid_reason}</p>}
    </div>
  );
}

export function fmtPct(x: number | null | undefined, digits = 1) {
  return x == null ? "–" : `${(x * 100).toFixed(digits)}%`;
}
