/** Where each page key from auth.me lives in the app (App Flow §1). */
export const PAGE_PATHS: Record<string, string> = {
  scorecards: "/scorecards",
  agent_performance: "/agent-performance",
  campaign: "/campaign",
  executive: "/executive",
  my_inputs: "/my-inputs",
  "admin.users": "/admin/users",
  "admin.metrics": "/admin/metrics",
  "admin.targets": "/admin/targets",
  "admin.calendar": "/admin/calendar",
  "admin.product_lines": "/admin/product-lines",
  "admin.data_integration": "/admin/data-integration",
  "admin.widgets": "/admin/widgets",
  "admin.health": "/admin/health",
  "admin.audit": "/admin/audit",
};

export function pathFor(pageKey: string): string {
  return PAGE_PATHS[pageKey] ?? "/";
}

export function pageKeyFor(pathname: string): string | undefined {
  return Object.entries(PAGE_PATHS).find(([, p]) => pathname === p || pathname.startsWith(`${p}/`))?.[0];
}
