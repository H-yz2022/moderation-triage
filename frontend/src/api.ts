import type { DecisionOut, EvalReport, Health, Policy, Sample, StoredDecision } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () => req<Health>("/api/health"),
  policy: () => req<Policy>("/api/policy"),
  samples: () => req<Sample[]>("/api/samples"),
  moderate: (body: { text: string; parent_text?: string | null; metadata?: Record<string, unknown>; mode?: string }) =>
    req<DecisionOut>("/api/moderate", { method: "POST", body: JSON.stringify(body) }),
  decisions: (status?: string, limit = 100) =>
    req<StoredDecision[]>(`/api/decisions?limit=${limit}${status ? `&status=${status}` : ""}`),
  review: (id: number, action: "remove" | "allow", clause_ids: string[], note: string) =>
    req<StoredDecision>(`/api/queue/${id}/review`, { method: "POST", body: JSON.stringify({ action, clause_ids, note }) }),
  queueStats: () => req<Record<string, number>>("/api/queue/stats"),
  usage: () => req<Record<string, unknown>>("/api/usage"),
  evalLatest: () => req<EvalReport>("/api/eval/latest"),
};
