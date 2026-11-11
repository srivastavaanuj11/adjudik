import { useState } from "react";
import type { ReactNode } from "react";
import { titleCase } from "./format";

type Variant = "ok" | "warn" | "danger" | "neutral";

const STATUS_VARIANT: Record<string, Variant> = {
  // evaluation
  decided: "ok",
  processing: "warn",
  pending_review: "warn",
  cancelled: "neutral",
  // review
  open: "warn",
  claimed: "warn",
  completed: "ok",
  // version
  active: "ok",
  draft: "neutral",
  retired: "neutral",
  // policy
  proposed: "warn",
  rejected: "danger",
  deactivated: "neutral",
  superseded: "neutral",
  // delivery
  pending: "warn",
  delivering: "warn",
  delivered: "ok",
  failed: "danger",
  dead: "danger",
};

export function StatusBadge({ status }: { status: string }) {
  const variant = STATUS_VARIANT[status] ?? "neutral";
  const cls = variant === "neutral" ? "badge" : `badge ${variant}`;
  return (
    <span className={cls}>
      {titleCase(status)}
    </span>
  );
}

export function Badge({ children, variant = "neutral" }: { children: ReactNode; variant?: Variant }) {
  const cls = variant === "neutral" ? "badge" : `badge ${variant}`;
  return <span className={cls}>{children}</span>;
}

/** Cursor pagination control: a "Load more" button when next_cursor is set. */
export function LoadMore({
  hasMore,
  loading,
  onMore,
}: {
  hasMore: boolean;
  loading: boolean;
  onMore: () => void;
}) {
  if (!hasMore) return null;
  return (
    <div style={{ marginTop: "0.75rem" }}>
      <button type="button" onClick={onMore} disabled={loading}>
        {loading ? "Loading…" : "Load more"}
      </button>
    </div>
  );
}

/** Collapsible JSON tree. `highlight` marks leaf paths (e.g. fields sent to the model). */
export function JsonView({
  value,
  highlight,
  path = "",
  defaultOpen = true,
}: {
  value: unknown;
  highlight?: Set<string>;
  path?: string;
  defaultOpen?: boolean;
}) {
  if (value === null) return <span className="mono muted">null</span>;
  if (typeof value !== "object") {
    const marked = highlight?.has(path);
    return (
      <span className="mono" style={marked ? { fontWeight: 700 } : undefined}>
        {typeof value === "string" ? `"${value}"` : String(value)}
        {marked && (
          <span className="muted" title="Sent to the model">
            {" "}
            ◂ model
          </span>
        )}
      </span>
    );
  }
  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as const)
    : Object.entries(value as Record<string, unknown>);
  return <JsonNode entries={entries} highlight={highlight} path={path} defaultOpen={defaultOpen} />;
}

function JsonNode({
  entries,
  highlight,
  path,
  defaultOpen,
}: {
  entries: readonly (readonly [string, unknown])[];
  highlight?: Set<string>;
  path: string;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (entries.length === 0) return <span className="mono muted">{"{}"}</span>;
  return (
    <div style={{ marginLeft: path ? "1rem" : 0 }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{ border: "none", background: "none", padding: 0, color: "var(--text-muted)" }}
      >
        {open ? "▾" : "▸"} {entries.length} field{entries.length === 1 ? "" : "s"}
      </button>
      {open && (
        <ul style={{ listStyle: "none", margin: 0, paddingLeft: "1rem" }}>
          {entries.map(([k, v]) => {
            const childPath = path ? `${path}/${k}` : `/${k}`;
            return (
              <li key={k}>
                <span className="mono" style={{ color: "var(--text-muted)" }}>
                  {k}:
                </span>{" "}
                <JsonView value={v} highlight={highlight} path={childPath} defaultOpen={false} />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
