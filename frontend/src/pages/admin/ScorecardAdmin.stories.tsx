import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { forbidden, serverError } from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import {
  bands,
  batches,
  configMe,
  coverage,
  coverageComplete,
  coverageEmpty,
  drafts,
  noBatches,
  noDrafts,
  noProfiles,
  noTemplate,
  profiles,
  scorecardMetrics,
  scorecardSettings,
  sheetResult,
  template,
  viewerMe,
} from "../../stories/scorecardFixtures";
import { ScorecardSetupPage } from "./ScorecardSetup";
import { SheetFindings, TargetsPage } from "./Targets";

function InShell({ children }: { children: React.ReactNode }) {
  const { state } = useSession();
  if (state.status !== "signed-in") return null;
  return (
    <AppShell me={state.me} onSignOut={() => {}}>
      <Routes>
        <Route path="*" element={children} />
      </Routes>
    </AppShell>
  );
}

const page = (element: React.ReactNode, { me = configMe, path, handlers = {} }: { me?: Me; path: string; handlers?: Handlers }) => ({
  render: () => <InShell>{element}</InShell>,
  decorators: [withApp({ me, path, handlers })],
});

const meta: Meta = { title: "Pages/Scorecards admin", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

const targets: Handlers = {
  "target.coverage": { data: coverage },
  "target.list": { data: drafts },
  "target.batch.list": { data: batches },
};

export const Targets: Story = page(<TargetsPage />, { path: "/admin/targets", handlers: targets });
export const TargetsComplete: Story = page(<TargetsPage />, {
  path: "/admin/targets",
  handlers: { ...targets, "target.coverage": { data: coverageComplete }, "target.list": { data: noDrafts } },
});
/** No profile has metrics yet: the grid says where to start. */
export const TargetsNoProfiles: Story = page(<TargetsPage />, {
  path: "/admin/targets",
  handlers: { "target.coverage": { data: coverageEmpty }, "target.list": { data: noDrafts }, "target.batch.list": { data: noBatches } },
});
export const TargetsLoading: Story = page(<TargetsPage />, {
  path: "/admin/targets",
  handlers: { "target.coverage": "pending", "target.list": "pending", "target.batch.list": "pending" },
});
export const TargetsError: Story = page(<TargetsPage />, {
  path: "/admin/targets",
  handlers: { "target.coverage": { error: serverError }, "target.list": { error: serverError }, "target.batch.list": { error: serverError } },
});
/** Read-only: a viewer sees coverage and history but no upload, publish or revert. */
export const TargetsReadOnly: Story = page(<TargetsPage />, { me: viewerMe, path: "/admin/targets", handlers: targets });
export const TargetsNoPermission: Story = page(<TargetsPage />, {
  path: "/admin/targets",
  handlers: { "target.coverage": { error: forbidden }, "target.list": { error: forbidden }, "target.batch.list": { error: forbidden } },
});

export const SheetCheckFindings: Story = {
  render: () => (
    <div style={{ padding: 20 }}>
      <SheetFindings result={sheetResult} />
    </div>
  ),
};

const setup: Handlers = {
  "scorecard.template.get": { data: template },
  "metric.list": { data: scorecardMetrics },
  "scorecard.profile.list": { data: profiles },
  "band.list": { data: bands },
  "scorecard.settings.get": { data: scorecardSettings },
};

export const ScorecardSetup: Story = page(<ScorecardSetupPage />, { path: "/admin/scorecard-setup", handlers: setup });
/** First run: no taxonomy, no profiles yet. Each section says why it is empty. */
export const ScorecardSetupEmpty: Story = page(<ScorecardSetupPage />, {
  path: "/admin/scorecard-setup",
  handlers: { ...setup, "scorecard.template.get": { data: noTemplate }, "scorecard.profile.list": { data: noProfiles } },
});
export const ScorecardSetupReadOnly: Story = page(<ScorecardSetupPage />, { me: viewerMe, path: "/admin/scorecard-setup", handlers: setup });
export const ScorecardSetupLoading: Story = page(<ScorecardSetupPage />, {
  path: "/admin/scorecard-setup",
  handlers: { "scorecard.template.get": "pending", "metric.list": "pending", "scorecard.profile.list": "pending", "band.list": "pending", "scorecard.settings.get": "pending" },
});
export const ScorecardSetupError: Story = page(<ScorecardSetupPage />, {
  path: "/admin/scorecard-setup",
  handlers: {
    "scorecard.template.get": { error: serverError },
    "metric.list": { error: serverError },
    "scorecard.profile.list": { error: serverError },
    "band.list": { error: serverError },
    "scorecard.settings.get": { error: serverError },
  },
});
