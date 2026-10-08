import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { EmptyState, Loading, Skeleton } from "./components";
import { AdminPlaceholder } from "./pages/ModulePage";
import { AgentPerformancePage } from "./pages/agents/AgentPerformance";
import { ExecutiveDashboardPage } from "./pages/executive/Dashboard";
import { CampaignPage, CampaignsPage, NewCampaignPage } from "./pages/campaigns/Campaigns";
import { ProductLinesPage } from "./pages/agents/ProductLines";
import { CompliancePage } from "./pages/inputs/Compliance";
import { MyInputsPage } from "./pages/inputs/MyInputs";
import { ScorecardPage } from "./pages/scorecards/Scorecard";
import { HealthPage } from "./pages/admin/Health";
import { ImportsPage } from "./pages/admin/Imports";
import { IntegrationsPage } from "./pages/admin/Integrations";
import { WidgetsPage } from "./pages/admin/Widgets";
import { MetricsPage } from "./pages/admin/Metrics";
import { PeriodClosePage } from "./pages/admin/PeriodClose";
import { ScorecardSetupPage } from "./pages/admin/ScorecardSetup";
import { TargetsPage } from "./pages/admin/Targets";
import { UsersPage } from "./pages/admin/Users";
import { InviteAccept } from "./pages/InviteAccept";
import { Login } from "./pages/Login";
import { OidcCallback } from "./pages/OidcCallback";
import { Setup } from "./pages/Setup";
import { AppShell } from "./shell/AppShell";
import { pageKeyFor, pathFor } from "./shell/pages";
import { useSession } from "./session/Session";

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/invite" element={<InviteAccept />} />
      <Route path="/setup" element={<Setup />} />
      <Route path="/auth/callback" element={<OidcCallback />} />
      <Route element={<RequireSession />}>
        <Route index element={<Home />} />
        <Route path="/scorecards" element={<ScorecardPage />} />
        <Route path="/agent-performance" element={<AgentPerformancePage />} />
        <Route path="/campaign" element={<CampaignsPage />} />
        <Route path="/campaign/new" element={<NewCampaignPage />} />
        <Route path="/campaign/:campaignId" element={<CampaignPage />} />
        <Route path="/executive" element={<ExecutiveDashboardPage />} />
        <Route path="/my-inputs" element={<MyInputsPage />} />
        <Route path="/input-compliance" element={<CompliancePage />} />
        <Route path="/admin/users" element={<UsersPage />} />
        <Route path="/admin/metrics" element={<MetricsPage />} />
        <Route path="/admin/health" element={<HealthPage />} />
        <Route path="/admin/integrations" element={<IntegrationsPage />} />
        <Route path="/admin/imports" element={<ImportsPage />} />
        <Route path="/admin/targets" element={<TargetsPage />} />
        <Route path="/admin/scorecard-setup" element={<ScorecardSetupPage />} />
        <Route path="/admin/calendar" element={<PeriodClosePage />} />
        <Route path="/admin/product-lines" element={<ProductLinesPage />} />
        <Route path="/admin/data-integration" element={<AdminPlaceholder title="Data integration" what="Connections, feeds, runs and rejections." />} />
        <Route path="/admin/widgets" element={<WidgetsPage />} />
        <Route path="/admin/audit" element={<AdminPlaceholder title="Audit log" what="Every action, who ran it and when." />} />
        <Route path="*" element={<Home />} />
      </Route>
    </Routes>
  );
}

/** Signed-in routes. A page the user's roles do not show redirects to their home (App Flow §9). */
function RequireSession() {
  const { state, signOut, refresh } = useSession();
  const location = useLocation();
  if (state.status === "loading") {
    return (
      <Loading label="Loading kpiGo">
        <div className="kg-shell" aria-hidden="true">
          <div className="kg-rail">
            <Skeleton height={26} width={120} />
          </div>
          <div className="kg-main">
            <div className="kg-topbar">
              <Skeleton height={18} width={180} />
            </div>
          </div>
        </div>
      </Loading>
    );
  }
  if (state.status === "error") {
    return (
      <main className="kg-auth" id="main">
        <div className="kg-auth__card">
          <EmptyState
            kind="error"
            headingLevel={1}
            title="kpiGo could not start"
            reference={state.error.reference}
            action={
              <button type="button" className="kg-btn" onClick={refresh}>
                Try again
              </button>
            }
          >
            {state.error.message}
          </EmptyState>
        </div>
      </main>
    );
  }
  if (state.status === "signed-out") {
    const next = location.pathname + location.search;
    return <Navigate to={next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`} replace />;
  }
  const me = state.me;
  const key = pageKeyFor(location.pathname);
  if (key && !me.pages.some((p) => p.page_key === key)) {
    return <Navigate to="/" replace />;
  }
  return (
    <AppShell me={me} onSignOut={() => void signOut()}>
      <Outlet />
    </AppShell>
  );
}

function Home() {
  const { state } = useSession();
  if (state.status !== "signed-in") return null;
  const { me } = state;
  if (me.home) return <Navigate to={pathFor(me.home)} replace />;
  return (
    <main id="main" className="kg-view">
      <EmptyState kind="no-access" headingLevel={1} title="Your account has no pages yet" ask="an Admin, under Administer → Users & access">
        You are signed in as {me.user.email}, but none of your roles opens a page in kpiGo.
      </EmptyState>
    </main>
  );
}
