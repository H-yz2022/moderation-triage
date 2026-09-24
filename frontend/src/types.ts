export type Action = "remove" | "allow" | "escalate";

export interface Vote {
  agent: string;
  label: "violation" | "no_violation" | "abstain";
  confidence: number;
  categories: string[];
  clause_ids: string[];
  exception_ids: string[];
  evidence: string;
  rationale: string;
  model: string;
  risk: number | null;
  valid: boolean;
  invalid_reason: string;
}

export interface Usage {
  agent: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_usd: number;
  latency_ms: number;
  cached: boolean;
  simulated: boolean;
}

export interface DecisionOut {
  id: number;
  item_id: string;
  action: Action;
  categories: string[];
  clause_ids: string[];
  score: number;
  confidence: number;
  decided_by: string;
  reasons: string[];
  votes: Vote[];
  usage: Usage[];
  gate_score: number | null;
  cost_usd: number;
  list_cost_usd: number;
  provider: string;
  llm_allowed: boolean;
}

export interface StoredDecision {
  id: number;
  created_at: string;
  text: string;
  parent_text: string | null;
  metadata: Record<string, unknown>;
  action: Action;
  categories: string[];
  clause_ids: string[];
  score: number;
  confidence: number;
  decided_by: string;
  reasons: string[];
  votes: Vote[];
  cost_usd: number;
  gate_score: number | null;
  review_status: "auto" | "pending" | "reviewed";
  reviewer_action: string | null;
  reviewer_clauses: string[];
  reviewer_note: string | null;
}

export interface Clause {
  id: string;
  category: string;
  title: string;
  text: string;
}

export interface Policy {
  version: string;
  name: string;
  categories: Record<string, { label: string; severity: string; dataset_labels: string[] }>;
  clauses: Clause[];
  exceptions: { id: string; title: string; text: string }[];
}

export interface Health {
  provider: string;
  mode: string;
  gate_loaded: boolean;
  policy_version: string;
  specialist_model: string;
  arbiter_model: string;
  modes: string[];
}

export interface Metrics {
  precision: number | null;
  auto_recall: number | null;
  system_recall: number | null;
  recall_on_auto: number | null;
  f1_auto: number | null;
  escalation_rate: number | null;
  automation_rate: number | null;
}

export interface ModeResult {
  mode: string;
  n: number;
  positives: number;
  metrics: Metrics;
  ci95: Record<string, [number, number]>;
  per_category: Record<string, { support: number; precision: number | null; recall: number | null }>;
  citation_accuracy: number | null;
  agents: Record<string, Record<string, number | null>>;
  decided_by: Record<string, number>;
  cost: { list_cost_per_1k_usd: number; actual_spend_usd: number; llm_calls_per_item: number; arbiter_rate: number | null; cache_hit_rate?: number | null };
  latency_ms: { p50: number; p95: number };
}

export interface EvalReport {
  dataset: string;
  generated_at: string;
  provider: string;
  policy_version: string;
  n_items: number;
  weighted: boolean;
  results: ModeResult[];
  sweep_mode: string | null;
  sweep: { threshold: number; precision: number | null; auto_recall: number | null; system_recall: number | null; escalation_rate: number | null }[];
}

export interface Sample {
  id: string;
  text: string;
  parent_text: string | null;
  metadata: Record<string, unknown>;
  label: number;
  categories: string[];
}
