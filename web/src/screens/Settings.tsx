import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { WorkspaceSettingsPanel } from "./settings/WorkspaceSettingsPanel";
import { ApiKeysPanel } from "./settings/ApiKeysPanel";
import { MembersPanel } from "./settings/MembersPanel";
import { WebhooksPanel } from "./settings/WebhooksPanel";

const TABS = [
  { to: "workspace", label: "Workspace" },
  { to: "api-keys", label: "API keys" },
  { to: "members", label: "Members" },
  { to: "webhooks", label: "Webhooks" },
];

export function Settings() {
  return (
    <div>
      <h1>Settings</h1>
      <nav className="tabs" aria-label="Settings sections">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="workspace" replace />} />
        <Route path="workspace" element={<WorkspaceSettingsPanel />} />
        <Route path="api-keys" element={<ApiKeysPanel />} />
        <Route path="members" element={<MembersPanel />} />
        <Route path="webhooks" element={<WebhooksPanel />} />
      </Routes>
    </div>
  );
}
