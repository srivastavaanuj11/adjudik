import type { Role } from "../api/types";

export interface NavItem {
  to: string;
  label: string;
  roles: Role[];
}

// Role → route access (SPEC §12.1). Navigation shows only what the role can use.
export const NAV: NavItem[] = [
  { to: "/reviews", label: "Reviews", roles: ["reviewer", "admin", "auditor"] },
  { to: "/evaluations", label: "Evaluations", roles: ["admin", "author", "auditor"] },
  { to: "/decisions", label: "Decisions", roles: ["admin", "author", "auditor"] },
  { to: "/audit", label: "Audit", roles: ["auditor", "admin"] },
  { to: "/settings/workspace", label: "Settings", roles: ["admin"] },
];

export function canAccess(roles: Role[], role: Role | null): boolean {
  return role !== null && roles.includes(role);
}

/** Where a role lands after login (SPEC §12.1). */
export function landingFor(role: Role | null): string {
  switch (role) {
    case "reviewer":
      return "/reviews";
    case "auditor":
      return "/evaluations";
    case "admin":
    case "author":
      return "/decisions";
    default:
      return "/reviews";
  }
}
