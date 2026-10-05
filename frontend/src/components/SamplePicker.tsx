import { useMemo, useState } from "react";
import type { Sample } from "../types";

const FILTERS: { id: string; label: string; match: (s: Sample) => boolean }[] = [
  { id: "all", label: "All", match: () => true },
  { id: "harassment", label: "Harassment", match: (s) => s.categories.includes("harassment") },
  { id: "hate", label: "Hate", match: (s) => s.categories.includes("hate") },
  { id: "violence", label: "Threats", match: (s) => s.categories.includes("violence") },
  { id: "sexual", label: "Sexual", match: (s) => s.categories.includes("sexual") },
  { id: "profanity", label: "Profanity", match: (s) => s.categories.includes("profanity") },
  { id: "spam", label: "Spam", match: (s) => s.categories.includes("spam") },
  { id: "clean", label: "No violation", match: (s) => !s.label },
  { id: "context", label: "With reply context", match: (s) => !!s.parent_text },
];

export function goldText(s: Sample) {
  return s.label ? `violation · ${s.categories.join(", ") || "toxic, no sub-label"}` : "no violation";
}

/** Compact example browser: filter chips, a one-line prev / current / next bar,
 * and an inline (not full-screen) searchable list. Replaces a native <select>,
 * which opens a full-screen picker on phones. */
export default function SamplePicker({ samples, selected, onSelect }: { samples: Sample[]; selected: Sample | null; onSelect: (s: Sample) => void }) {
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");

  const counts = useMemo(() => Object.fromEntries(FILTERS.map((f) => [f.id, samples.filter(f.match).length])), [samples]);
  const pool = useMemo(() => samples.filter(FILTERS.find((f) => f.id === filter)!.match), [samples, filter]);
  const listed = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (needle ? pool.filter((s) => s.text.toLowerCase().includes(needle) || s.id.toLowerCase().includes(needle)) : pool).slice(0, 200);
  }, [pool, q]);

  const idx = selected ? pool.findIndex((s) => s.id === selected.id) : -1;
  const step = (d: number) => pool.length && onSelect(pool[(idx + d + pool.length) % pool.length]);
  const shuffle = () => pool.length && onSelect(pool[Math.floor(Math.random() * pool.length)]);

  function pickFilter(id: string) {
    setFilter(id);
    const next = samples.filter(FILTERS.find((f) => f.id === id)!.match);
    if (next.length && !next.some((s) => s.id === selected?.id)) onSelect(next[0]);
  }

  return (
    <div className="picker">
      <div className="row spread">
        <span className="field" style={{ margin: 0 }}>Try a labelled example</span>
        <span className="small muted">{samples.length} comments</span>
      </div>
      <div className="chips" role="tablist" aria-label="Filter examples">
        {FILTERS.filter((f) => counts[f.id] > 0).map((f) => (
          <button key={f.id} role="tab" aria-selected={filter === f.id} className={`chip ${filter === f.id ? "active" : ""}`} onClick={() => pickFilter(f.id)}>
            {f.label} <span className="muted">{counts[f.id]}</span>
          </button>
        ))}
      </div>
      <div className="picker-bar">
        <button className="btn icon" aria-label="Previous example" onClick={() => step(-1)}>‹</button>
        <button className="picker-current" aria-expanded={open} onClick={() => setOpen((o) => !o)} title="Browse and search examples">
          <span className="mono small">{selected?.id ?? "—"}</span>
          <span className="picker-text">{selected?.text ?? "Pick an example"}</span>
          <span aria-hidden className="muted">{open ? "▴" : "▾"}</span>
        </button>
        <button className="btn icon" aria-label="Next example" onClick={() => step(1)}>›</button>
        <button className="btn icon" aria-label="Random example" title="Random example" onClick={shuffle}>⤮</button>
      </div>
      <div className="row spread small muted">
        <span>{selected ? <>Dataset label: <b>{goldText(selected)}</b></> : null}</span>
        <span>{idx >= 0 ? `${idx + 1} / ${pool.length}` : `${pool.length} in filter`}</span>
      </div>
      {open && (
        <div className="picker-list">
          <input type="search" placeholder="Search examples…" aria-label="Search examples" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          <ul role="listbox" aria-label="Examples">
            {listed.map((s) => (
              <li key={s.id} role="option" aria-selected={s.id === selected?.id} className={s.id === selected?.id ? "selected" : ""}
                tabIndex={0} onClick={() => { onSelect(s); setOpen(false); }}
                onKeyDown={(e) => { if (e.key === "Enter") { onSelect(s); setOpen(false); } }}>
                <span className={`dot ${s.label ? "bad" : "ok"}`} aria-hidden />
                <span className="picker-text">{s.text}</span>
              </li>
            ))}
            {listed.length === 0 && <li className="muted small">No examples match.</li>}
          </ul>
          <p className="small muted" style={{ margin: "6px 2px 0" }}>
            Includes real, sometimes offensive comments from the public-domain Civil Comments dataset (labels are crowd ratings and can be noisy).
          </p>
        </div>
      )}
    </div>
  );
}
