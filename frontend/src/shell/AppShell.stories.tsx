import type { Meta, StoryObj } from "@storybook/react-vite";

import { MetricCard } from "../components/MetricCard";
import type { Me } from "../session/Session";
import { adminMe, graceMe, readOnlyMe, staffMe } from "../stories/fixtures";
import { withApp } from "../stories/mockApi";
import { AppShell, Page } from "./AppShell";

function Shell({ me, exec = false }: { me: Me; exec?: boolean }) {
  return (
    <AppShell me={me} onSignOut={() => {}}>
      <Page
        title={exec ? "Executive" : "Scorecards"}
        exec={exec}
        actions={
          <button type="button" className="kg-btn kg-btn--primary">
            Acknowledge
          </button>
        }
      >
        <div className={exec ? "kg-card" : undefined} style={exec ? { boxShadow: "var(--shadow-lift)", borderRadius: "var(--r-lg)" } : undefined}>
          <div className="kg-grid">
            <MetricCard label="Deposits" value="GHS 12.4M" target="GHS 13.0M" delta={4.2} />
            <MetricCard label="Loans" value="GHS 4.6M" target="GHS 4.0M" delta={-1.3} />
            <MetricCard label="NPS" value={null} state="awaiting" reason="The survey input is due on 10 Oct." />
          </div>
        </div>
      </Page>
    </AppShell>
  );
}

const meta: Meta<typeof Shell> = {
  title: "Shell/AppShell",
  component: Shell,
  parameters: { layout: "fullscreen" },
};
export default meta;
type Story = StoryObj<typeof Shell>;

export const Admin: Story = { args: { me: adminMe }, decorators: [withApp({ me: adminMe, path: "/scorecards" })] };
/** A user with one module sees one module, not a rail of locked doors. */
export const OneModule: Story = { args: { me: staffMe }, decorators: [withApp({ me: staffMe, path: "/scorecards" })] };
export const ExecutiveCanvas: Story = { args: { me: adminMe, exec: true }, decorators: [withApp({ me: adminMe, path: "/executive" })] };
export const LicenceGrace: Story = { args: { me: graceMe }, decorators: [withApp({ me: graceMe, path: "/scorecards" })] };
export const LicenceReadOnly: Story = { args: { me: readOnlyMe }, decorators: [withApp({ me: readOnlyMe, path: "/scorecards" })] };
export const Tablet: Story = { ...Admin, globals: { viewport: { value: "tablet" } } };
