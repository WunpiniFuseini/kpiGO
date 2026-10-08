/** Where each page key from auth.me lives in the app (App Flow §1). */
export const PAGE_PATHS: Record<string, string> = {
  scorecards: "/scorecards",
  agent_performance: "/agent-performance",
  campaign: "/campaign",
  executive: "/executive",
  my_inputs: "/my-inputs",
  input_compliance: "/input-compliance",
  "admin.users": "/admin/users",
  "admin.metrics": "/admin/metrics",
  "admin.targets": "/admin/targets",
  "admin.scorecard_setup": "/admin/scorecard-setup",
  "admin.calendar": "/admin/calendar",
  "admin.product_lines": "/admin/product-lines",
  "admin.data_integration": "/admin/data-integration",
  "admin.imports": "/admin/imports",
  "admin.widgets": "/admin/widgets",
  "admin.integrations": "/admin/integrations",
  "admin.health": "/admin/health",
  "admin.audit": "/admin/audit",
};

export function pathFor(pageKey: string): string {
  return PAGE_PATHS[pageKey] ?? "/";
}

export function pageKeyFor(pathname: string): string | undefined {
  return Object.entries(PAGE_PATHS).find(([, p]) => pathname === p || pathname.startsWith(`${p}/`))?.[0];
}
