type Series = { key: string; label: string; color: string };

const W = 560;
const H = 220;
const PAD = { l: 36, r: 8, t: 8, b: 28 };

function yTicks() {
  return [0, 0.25, 0.5, 0.75, 1];
}

export function Legend({ series }: { series: Series[] }) {
  return (
    <div className="legend">
      {series.map((s) => (
        <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
      ))}
    </div>
  );
}

/** Grouped bars: one group per row, one bar per series, values in [0,1]. */
export function GroupedBars({ rows, series, labelKey }: { rows: Record<string, number | string | null>[]; series: Series[]; labelKey: string }) {
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const gw = iw / Math.max(rows.length, 1);
  const bw = Math.min(22, (gw - 12) / series.length);
  const y = (v: number) => PAD.t + ih - v * ih;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Metric comparison by mode">
      {yTicks().map((t) => (
        <g key={t}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--border)" />
          <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end">{Math.round(t * 100)}%</text>
        </g>
      ))}
      {rows.map((r, i) => {
        const gx = PAD.l + i * gw + (gw - bw * series.length) / 2;
        return (
          <g key={i}>
            {series.map((s, j) => {
              const v = typeof r[s.key] === "number" ? (r[s.key] as number) : 0;
              return (
                <rect key={s.key} x={gx + j * bw} y={y(v)} width={bw - 2} height={Math.max(0, y(0) - y(v))} rx={2} fill={s.color}>
                  <title>{`${r[labelKey]} · ${s.label}: ${(v * 100).toFixed(1)}%`}</title>
                </rect>
              );
            })}
            <text x={PAD.l + i * gw + gw / 2} y={H - 8} textAnchor="middle">{String(r[labelKey])}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Lines over a numeric x in [xmin,xmax], y in [0,1]. */
export function Lines({ rows, xKey, series }: { rows: Record<string, number | null>[]; xKey: string; series: Series[] }) {
  if (!rows.length) return null;
  const xs = rows.map((r) => r[xKey] as number);
  const xmin = Math.min(...xs);
  const xmax = Math.max(...xs);
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const x = (v: number) => PAD.l + ((v - xmin) / Math.max(xmax - xmin, 1e-9)) * iw;
  const y = (v: number) => PAD.t + ih - v * ih;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Threshold sweep">
      {yTicks().map((t) => (
        <g key={t}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--border)" />
          <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end">{Math.round(t * 100)}%</text>
        </g>
      ))}
      {xs.map((v) => (
        <text key={v} x={x(v)} y={H - 8} textAnchor="middle">{v}</text>
      ))}
      {series.map((s) => {
        const pts = rows.filter((r) => r[s.key] != null).map((r) => `${x(r[xKey] as number)},${y(r[s.key] as number)}`);
        return (
          <g key={s.key}>
            <polyline points={pts.join(" ")} fill="none" stroke={s.color} strokeWidth={2} />
            {rows.filter((r) => r[s.key] != null).map((r, i) => (
              <circle key={i} cx={x(r[xKey] as number)} cy={y(r[s.key] as number)} r={3} fill={s.color}>
                <title>{`threshold ${r[xKey]} · ${s.label}: ${((r[s.key] as number) * 100).toFixed(1)}%`}</title>
              </circle>
            ))}
          </g>
        );
      })}
    </svg>
  );
}
