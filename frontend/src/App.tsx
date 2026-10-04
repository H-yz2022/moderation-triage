import { useEffect, useState } from "react";
import { api } from "./api";
import Batch from "./pages/Batch";
import Eval from "./pages/Eval";
import PolicyPage from "./pages/PolicyPage";
import Queue from "./pages/Queue";
import Triage from "./pages/Triage";
import type { Health, Policy } from "./types";

const TABS = [
  { id: "triage", label: "Triage" },
  { id: "batch", label: "Batch" },
  { id: "queue", label: "Review queue" },
  { id: "eval", label: "Evaluation" },
  { id: "policy", label: "Policy" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function initialTab(): TabId {
  const h = window.location.hash.replace("#", "");
  return (TABS.find((t) => t.id === h)?.id ?? "triage") as TabId;
}

export default function App() {
  const [tab, setTab] = useState<TabId>(initialTab);
  const [health, setHealth] = useState<Health | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setErr((e as Error).message));
    api.policy().then(setPolicy).catch(() => undefined);
  }, []);

  useEffect(() => {
    window.location.hash = tab;
  }, [tab]);

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Moderation Triage</span>
        <nav className="tabs" aria-label="Sections">
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} aria-current={tab === t.id ? "page" : undefined} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </nav>
        <div className="status">
          {health && (
            <>
              <span className="pill">
                {!health.ai_enabled ? "AI off · in testing" : health.provider === "mock" ? "mock provider (offline)" : `${health.specialist_model} → ${health.arbiter_model}`}
              </span>
              <span className="pill">gate {health.gate_loaded ? "on" : "off"}</span>
              <span className="pill">mode {health.mode}</span>
            </>
          )}
        </div>
      </header>
      {health && !health.ai_enabled && (
        <div className="banner" role="status" style={{ marginBottom: 16 }}>
          <b>AI review is still in testing and is turned off.</b> No AI model is called. The votes, decisions and costs you see come from the offline
          simulator (rule-based heuristics), not from Claude.
        </div>
      )}
      {err && <div className="error" style={{ marginBottom: 16 }}>API unreachable: {err}</div>}
      {tab === "triage" && <Triage health={health} policy={policy} />}
      {tab === "batch" && <Batch health={health} policy={policy} />}
      {tab === "queue" && <Queue policy={policy} />}
      {tab === "eval" && <Eval />}
      {tab === "policy" && <PolicyPage policy={policy} health={health} />}
    </div>
  );
}
