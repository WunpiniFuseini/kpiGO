import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../../shell/AppShell";
import { useSession, type Me } from "../../session/Session";
import {
  campaignAllDrafts,
  campaignDetail,
  campaignReach,
  campaignReachNothingFed,
  campaignValue,
  campaignValueGross,
  campaignList,
  campaignListEmpty,
  campaignListNoScope,
  campaignManagerMe,
  campaignNoEvents,
  campaignViewerMe,
  estimate,
  estimateNoPopulation,
  estimateNotBrokenDown,
  reference,
} from "../../stories/campaignFixtures";
import { serverError } from "../../stories/fixtures";
import { withApp, type Handlers } from "../../stories/mockApi";
import { CampaignPage, CampaignsPage, NewCampaignPage } from "./Campaigns";

function InShell() {
  const { state } = useSession();
  if (state.status !== "signed-in") return null;
  return (
    <AppShell me={state.me} onSignOut={() => {}}>
      <Routes>
        <Route path="/campaign" element={<CampaignsPage />} />
        <Route path="/campaign/new" element={<NewCampaignPage />} />
        <Route path="/campaign/:campaignId" element={<CampaignPage />} />
      </Routes>
    </AppShell>
  );
}

const at = (path: string, handlers: Handlers, me: Me = campaignManagerMe): Story => ({
  render: () => <InShell />,
  decorators: [withApp({ me, path, handlers })],
});

const meta: Meta = { title: "Pages/Campaigns", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

const DETAIL = "/campaign/c1000000-0000-4000-8000-000000000001";

/** Two running, one scheduled, one draft; one has a budget change waiting for approval. */
export const List: Story = at("/campaign", { "campaign.list": { data: campaignList } });
export const ListReadOnly: Story = at("/campaign", { "campaign.list": { data: campaignList } }, campaignViewerMe);
/** In scope, but nobody has authored a campaign yet. */
export const ListNothingYet: Story = at("/campaign", { "campaign.list": { data: campaignListEmpty } });
/** No Campaign scope grant: names the grant and who to ask. */
export const ListNoScope: Story = at("/campaign", { "campaign.list": { data: campaignListNoScope } }, campaignViewerMe);
export const ListLoading: Story = at("/campaign", { "campaign.list": "pending" });
export const ListFailed: Story = at("/campaign", { "campaign.list": { error: serverError } });

/** The builder: campaign details, then one event block to start. */
export const Builder: Story = at("/campaign/new", { "campaign.builder.reference": { data: reference }, "campaign.audience.estimate": { data: estimate } });
/** No population fed yet: the picker says the audience cannot be sized, and why. */
export const BuilderNoPopulation: Story = at("/campaign/new", { "campaign.builder.reference": { data: reference }, "campaign.audience.estimate": { data: estimateNoPopulation } });
/** The population has no breakdown by a dimension the audience uses. */
export const BuilderNotBrokenDown: Story = at("/campaign/new", { "campaign.builder.reference": { data: reference }, "campaign.audience.estimate": { data: estimateNotBrokenDown } });
/** No dimension has members, so the audience picker says why it cannot offer any. */
export const BuilderNoDimensions: Story = at("/campaign/new", { "campaign.builder.reference": { data: { ...reference, dimensions: [] } } });
export const BuilderLoading: Story = at("/campaign/new", { "campaign.builder.reference": "pending" });
export const BuilderFailed: Story = at("/campaign/new", { "campaign.builder.reference": { error: serverError } });

const reachOk = { "campaign.reach": { data: campaignReach }, "campaign.value": { data: campaignValue }, "campaign.audience.estimate": { data: estimate } } as const;

/** A running event with its reach funnel and a budget change awaiting approval, and a draft ready to publish. */
export const Detail: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.builder.reference": { data: reference }, ...reachOk });
export const DetailReadOnly: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.reach": { data: campaignReach }, "campaign.value": { data: campaignValue } }, campaignViewerMe);
/** The org changed its value basis to gross: the banner says since when. */
export const DetailGrossBasis: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.builder.reference": { data: reference }, ...reachOk, "campaign.value": { data: campaignValueGross } });
export const DetailValueLoading: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.builder.reference": { data: reference }, ...reachOk, "campaign.value": "pending" });
export const DetailValueFailed: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.builder.reference": { data: reference }, ...reachOk, "campaign.value": { error: serverError } });
/** No feed has loaded and the objective counts no metrics: each missing figure says why. */
export const DetailNothingFed: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.builder.reference": { data: reference }, "campaign.reach": { data: campaignReachNothingFed } });
export const DetailReachLoading: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.builder.reference": { data: reference }, "campaign.reach": "pending" });
export const DetailReachFailed: Story = at(DETAIL, { "campaign.get": { data: campaignDetail }, "campaign.builder.reference": { data: reference }, "campaign.reach": { error: serverError } });
/** A draft with no audience cannot be published yet. */
export const DetailDraftOnly: Story = at(DETAIL, { "campaign.get": { data: campaignAllDrafts }, "campaign.builder.reference": { data: reference }, ...reachOk });
export const DetailNoEvents: Story = at(DETAIL, { "campaign.get": { data: campaignNoEvents }, "campaign.builder.reference": { data: reference }, "campaign.reach": { data: { ...campaignReach, events: [] } } });
export const DetailOutOfScope: Story = at(DETAIL, { "campaign.get": { error: { status: 404, error: "not_found", message: "No such campaign in your scope." } } });
export const DetailLoading: Story = at(DETAIL, { "campaign.get": "pending" });
export const DetailFailed: Story = at(DETAIL, { "campaign.get": { error: serverError } });
