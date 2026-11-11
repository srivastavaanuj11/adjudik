import { useQuery } from "@tanstack/react-query";
import { decisions } from "../api/endpoints";
import { QueryBoundary } from "../ui/states";
import { fmtMoney, fmtNumber, fmtPercent, titleCase } from "../ui/format";
import type { Metrics } from "../api/types";

export function MetricsTab({ decisionKey }: { decisionKey: string }) {
  const query = useQuery({
    queryKey: ["decision", decisionKey, "metrics", "full"],
    queryFn: () => decisions.metrics(decisionKey),
    staleTime: 60_000,
  });
  return <QueryBoundary query={query}>{(m) => <MetricsView m={m} />}</QueryBoundary>;
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="label">{sub}</div>}
    </div>
  );
}

function MetricsView({ m }: { m: Metrics }) {
  const agree = m.audit_agreement;
  return (
    <div>
      <p className="muted">
        {m.window.from} → {m.window.to}
        {m.version !== null ? ` · version ${m.version}` : ""}
      </p>

      <div className="stat-grid" style={{ marginBottom: "1rem" }}>
        <Tile label="Volume" value={fmtNumber(m.volume)} />
        <Tile label="Automation rate" value={fmtPercent(m.automation_rate)} />
        <Tile label="Model automation" value={fmtPercent(m.model_automation_rate)} />
        <Tile label="Override rate" value={fmtPercent(m.override_rate)} />
        <Tile
          label="Agreement with reviewers"
          value={fmtPercent(agree.point)}
          sub={
            agree.lower !== null && agree.upper !== null
              ? `95% CI ${fmtPercent(agree.lower)}–${fmtPercent(agree.upper)}`
              : "insufficient data"
          }
        />
        <Tile
          label="Audit completion"
          value={fmtPercent(m.audit_completion_rate.rate)}
          sub={`${m.audit_completion_rate.completed}/${m.audit_completion_rate.created}`}
        />
        <Tile label="Queue depth" value={fmtNumber(m.queue.open + m.queue.claimed)} sub={`${m.queue.overdue} overdue`} />
        <Tile label="p95 latency (model)" value={m.latency_ms.model.p95 !== null ? `${Math.round(m.latency_ms.model.p95)} ms` : "—"} />
        <Tile label="Cost" value={fmtMoney(m.cost_usd)} sub={`${fmtNumber(m.tokens.input + m.tokens.output)} tokens`} />
      </div>

      <section className="card">
        <h2>Decisions by decided-by (daily)</h2>
        <DailyStacked m={m} />
      </section>

      <section className="card">
        <h2>Route reasons</h2>
        <BarList
          data={Object.entries(m.route_reasons).map(([k, v]) => [titleCase(k), v])}
          emptyLabel="No routed reviews in this window."
        />
      </section>

      <section className="card">
        <h2>Daily review turnaround (p50, min)</h2>
        <BarList
          data={m.daily
            .filter((d) => d.turnaround_p50_min !== null)
            .map((d) => [d.date, Math.round(d.turnaround_p50_min as number)])}
          emptyLabel="No completed reviews in this window."
        />
      </section>
    </div>
  );
}

function DailyStacked({ m }: { m: Metrics }) {
  const max = Math.max(1, ...m.daily.map((d) => d.volume));
  if (m.daily.every((d) => d.volume === 0)) return <p className="muted">No volume in this window.</p>;
  const colors: Record<string, string> = {
    rule: "var(--ok)",
    model: "var(--accent)",
    human: "var(--warn)",
  };
  return (
    <div>
      <div className="row" style={{ gap: "1rem", marginBottom: "0.5rem" }}>
        {Object.entries(colors).map(([k, c]) => (
          <span key={k} className="row" style={{ gap: "0.3rem" }}>
            <span style={{ width: 12, height: 12, background: c, borderRadius: 2, display: "inline-block" }} />
            {titleCase(k)}
          </span>
        ))}
      </div>
      <div style={{ display: "flex", gap: 2, alignItems: "flex-end", height: 120 }}>
        {m.daily.map((d) => {
          const total = d.decided_by.rule + d.decided_by.model + d.decided_by.human;
          const h = (d.volume / max) * 110;
          return (
            <div
              key={d.date}
              title={`${d.date}: ${d.volume} (rule ${d.decided_by.rule}, model ${d.decided_by.model}, human ${d.decided_by.human})`}
              style={{ flex: 1, minWidth: 3, height: h || 1, display: "flex", flexDirection: "column-reverse" }}
            >
              {(["rule", "model", "human"] as const).map((k) =>
                total > 0 ? (
                  <div
                    key={k}
                    style={{ height: `${(d.decided_by[k] / total) * 100}%`, background: colors[k] }}
                  />
                ) : null,
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BarList({ data, emptyLabel }: { data: [string, number][]; emptyLabel: string }) {
  if (data.length === 0) return <p className="muted">{emptyLabel}</p>;
  const max = Math.max(1, ...data.map(([, v]) => v));
  return (
    <div>
      {data.map(([label, v]) => (
        <div key={label} className="row" style={{ gap: "0.6rem", marginBottom: 4 }}>
          <span style={{ width: 160 }} className="muted">
            {label}
          </span>
          <span
            style={{ height: 14, width: `${(v / max) * 60 + 2}%`, background: "var(--accent)", borderRadius: 3 }}
          />
          <span className="mono">{v}</span>
        </div>
      ))}
    </div>
  );
}
