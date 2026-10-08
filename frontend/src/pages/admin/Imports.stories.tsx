import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import { adminMe, serverError } from "../../stories/fixtures";
import { applyReport, importDrafts, rosterDraft, scorecardDraft, unknownDraft } from "../../stories/importFixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { ApplyReport, DraftReview, ImportsPage } from "./Imports";

const importMe: Me = { ...adminMe, permissions: [...adminMe.permissions, "import.view", "import.manage"] };
const viewOnlyMe: Me = { ...adminMe, permissions: [...adminMe.permissions, "import.view"] };

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

const page = (element: React.ReactNode, { me = importMe, handlers = {} }: { me?: Me; handlers?: Handlers }) => ({
  render: () => <InShell>{element}</InShell>,
  decorators: [withApp({ me, path: "/admin/imports", handlers })],
});

const meta: Meta = { title: "Pages/Imports", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

const list: Handlers = { "import.draft.list": { data: { drafts: importDrafts } } };

/** The landing screen: an upload card and the list of drafts. */
export const Imports: Story = page(<ImportsPage />, { handlers: list });
/** Nothing imported yet: the list says how to start. */
export const Empty: Story = page(<ImportsPage />, { handlers: { "import.draft.list": { data: { drafts: [] } } } });
/** Someone who may see imports but not upload or apply them. */
export const ViewOnly: Story = page(<ImportsPage />, { me: viewOnlyMe, handlers: list });
export const Loading: Story = page(<ImportsPage />, { handlers: { "import.draft.list": "pending" } });
export const LoadError: Story = page(<ImportsPage />, { handlers: { "import.draft.list": { error: serverError } } });

/** Reviewing a scorecard: inferred metrics to correct, with the guessed fields flagged. */
export const ReviewScorecard: Story = page(
  <DraftReview draft={scorecardDraft} canManage onClose={() => {}} onChanged={() => {}} />,
  { handlers: list },
);
/** Reviewing a roster: one person has no email, so it must be filled before applying. */
export const ReviewRoster: Story = page(
  <DraftReview draft={rosterDraft} canManage onClose={() => {}} onChanged={() => {}} />,
  { handlers: list },
);
/** kpiGo could not tell what the file held, and says what the sheet needs. */
export const ReviewUnknown: Story = page(
  <DraftReview draft={unknownDraft} canManage onClose={() => {}} onChanged={() => {}} />,
  { handlers: list },
);
/** After applying: what was registered, what already existed. */
export const Applied: Story = page(<ApplyReport out={applyReport} onClose={() => {}} />, { handlers: list });
