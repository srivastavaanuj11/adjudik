import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Route, Routes, useParams } from "react-router-dom";
import { decisions } from "../api/endpoints";
import { QueryBoundary } from "../ui/states";
import { StatusBadge } from "../ui/components";
import { MetricsTab } from "./MetricsTab";
import { OverviewTab } from "./decision/OverviewTab";
import { RulesTab } from "./decision/RulesTab";
import { ModelStepTab } from "./decision/ModelStepTab";
import { DefinitionTab } from "./decision/DefinitionTab";
import { VersionsTab } from "./decision/VersionsTab";
import { PoliciesTab } from "./decision/PoliciesTab";
import { TestTab } from "./decision/TestTab";
import type { VersionSummary } from "../api/types";

export function activeVersion(versions: VersionSummary[]): VersionSummary | null {
  return versions.find((v) => v.status === "active") ?? versions[0] ?? null;
}

const TABS = [
  { to: ".", label: "Overview", end: true },
  { to: "rules", label: "Rules" },
  { to: "model", label: "Model step" },
  { to: "definition", label: "Definition" },
  { to: "versions", label: "Versions" },
  { to: "policies", label: "Policies" },
  { to: "test", label: "Test" },
  { to: "metrics", label: "Metrics" },
];

export function DecisionDetail() {
  const { key = "" } = useParams();
  const query = useQuery({ queryKey: ["decision", key], queryFn: () => decisions.get(key) });

  return (
    <div>
      <p>
        <Link to="/decisions">← Decisions</Link>
      </p>
      <QueryBoundary query={query}>
        {(d) => {
          const active = activeVersion(d.versions);
          return (
            <>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <h1>
                  {d.name} <span className="mono muted">{d.key}</span>
                </h1>
                {active && <StatusBadge status={active.status} />}
              </div>

              <nav className="tabs" aria-label="Decision sections">
                {TABS.map((t) => (
                  <NavLink key={t.to} to={t.to} end={t.end}>
                    {t.label}
                  </NavLink>
                ))}
              </nav>

              <Routes>
                <Route index element={<OverviewTab decisionKey={key} active={active} />} />
                <Route path="rules" element={<RulesTab decisionKey={key} active={active} />} />
                <Route path="model" element={<ModelStepTab decisionKey={key} active={active} />} />
                <Route path="definition" element={<DefinitionTab decisionKey={key} active={active} />} />
                <Route path="versions" element={<VersionsTab decisionKey={key} versions={d.versions} />} />
                <Route path="policies" element={<PoliciesTab decisionKey={key} active={active} />} />
                <Route path="test" element={<TestTab decisionKey={key} active={active} />} />
                <Route path="metrics" element={<MetricsTab decisionKey={key} />} />
              </Routes>
            </>
          );
        }}
      </QueryBoundary>
    </div>
  );
}
