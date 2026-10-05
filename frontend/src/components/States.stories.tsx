import type { Meta, StoryObj } from "@storybook/react-vite";

import { EmptyState } from "./EmptyState";
import { FreshnessBadge } from "./FreshnessBadge";
import { Notice } from "./Notice";
import { QuarantineBanner } from "./QuarantineBanner";
import { CardSkeleton, TableSkeleton } from "./Skeleton";

/** The cross-cutting states every data surface implements (App Flow §9). */
const meta: Meta = { title: "Primitives/States" };
export default meta;
type Story = StoryObj;

export const AwaitingData: Story = {
  render: () => (
    <EmptyState kind="awaiting" title="Deposits data hasn't arrived yet">
      The finance deposits feed is due at 06:00 daily. Figures appear once it loads.
    </EmptyState>
  ),
};
export const NoResults: Story = {
  render: () => (
    <EmptyState
      kind="none"
      title="No campaigns match these filters"
      action={
        <button type="button" className="kg-btn">
          Clear filters
        </button>
      }
    />
  ),
};
export const NoAccess: Story = {
  render: () => (
    <EmptyState kind="no-access" title="You cannot see Executive data yet" ask="an Admin, under Administer → Users & access">
      You have no Executive data-scope grant.
    </EmptyState>
  ),
};
export const NothingDue: Story = {
  render: () => (
    <EmptyState kind="good" title="Nothing due">
      You have no inputs to submit for October. We will tell you when the next one opens.
    </EmptyState>
  ),
};
export const Error: Story = {
  render: () => (
    <EmptyState
      kind="error"
      title="Scorecard could not be loaded"
      reference="7F3A9C21B04E"
      action={
        <button type="button" className="kg-btn">
          Try again
        </button>
      }
    >
      Something went wrong on the server.
    </EmptyState>
  ),
};
export const NotBuilt: Story = {
  render: () => <EmptyState kind="not-built" title="Campaign Manager is not in this version yet">Its screens arrive with the module&apos;s release.</EmptyState>,
};
export const Quarantined: Story = {
  render: () => (
    <QuarantineBanner feed="finance deposits" rejectedAt="2026-10-04T06:04:00Z" rows={312} priorAsOf="2026-10-03T06:00:00Z" runHref="#" />
  ),
};
export const QuarantinedNoPriorData: Story = {
  render: () => <QuarantineBanner feed="cards daily" rejectedAt="2026-10-04T06:04:00Z" rows={12} priorAsOf={null} />,
};
export const Freshness: Story = {
  render: () => (
    <div className="kg-stack">
      <FreshnessBadge state="fresh" asOf="2026-10-04T06:00:00Z" feed="finance_deposits_daily" />
      <FreshnessBadge state="stale" asOf="2026-10-01T06:00:00Z" feed="finance_deposits_daily" />
      <FreshnessBadge state="quarantined" asOf="2026-10-03T06:00:00Z" feed="finance_deposits_daily" />
      <FreshnessBadge state="refreshing" asOf="2026-10-04T06:00:00Z" />
      <FreshnessBadge state="awaiting" due="06:00 daily" feed="finance_deposits_daily" />
    </div>
  ),
};
export const Notices: Story = {
  render: () => (
    <div className="kg-stack">
      <Notice title="Provisional.">October is open; grades can still move until the period closes.</Notice>
      <Notice tone="warn" title="Licence.">The licence expired on 1 Oct 2026. 27 days left.</Notice>
      <Notice tone="neg" title="kpiGo is updating.">Changes are paused; figures stay readable.</Notice>
    </div>
  ),
};
export const Loading: Story = {
  render: () => (
    <div className="kg-stack" role="status" aria-busy="true">
      <span className="sr-only">Loading</span>
      <div className="kg-grid">
        <CardSkeleton />
        <CardSkeleton />
        <CardSkeleton />
      </div>
      <div className="kg-card">
        <TableSkeleton />
      </div>
    </div>
  ),
};
