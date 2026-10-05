import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { setTransport } from "../api/client";
import { CampaignBuilder, CampaignListView } from "../pages/campaigns/Campaigns";
import { CampaignDetailView } from "../pages/campaigns/Detail";
import { blankEvent, describeAudience, validateEvent } from "../pages/campaigns/model";
import { campaignAllDrafts, campaignDetail, campaignList, campaignListNoScope, reference } from "../stories/campaignFixtures";

function routed(children: ReactNode) {
  return render(<MemoryRouter>{children}</MemoryRouter>);
}

/** Record what the UI sends and answer every call with `reply`. */
function capture(reply: unknown, status = 200) {
  const calls: { url: string; body: unknown }[] = [];
  setTransport(async (url, init) => {
    calls.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(reply), { status });
  });
  return calls;
}

describe("campaign list", () => {
  it("counts each status tab and lists the running campaigns", () => {
    const onTab = vi.fn();
    routed(<CampaignListView list={campaignList} tab="running" onTab={onTab} canAuthor />);
    const tabs = screen.getByRole("group", { name: "Campaign status" });
    expect(within(tabs).getByRole("button", { name: "Running 2" })).toHaveAttribute("aria-pressed", "true");
    expect(within(tabs).getByRole("button", { name: "Scheduled 1" })).toBeInTheDocument();
    const items = within(screen.getByRole("list", { name: "Running campaigns" })).getAllByRole("article");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText("Event 3 of 4 · 1 Oct 2026 – 31 Oct 2026")).toBeInTheDocument();
    expect(within(items[1]).getByText("Budget change awaiting approval")).toBeInTheDocument();
    // State is said in words, not only by colour.
    expect(within(items[0]).getByText("Running")).toBeInTheDocument();
    fireEvent.click(within(tabs).getByRole("button", { name: "Drafts 1" }));
    expect(onTab).toHaveBeenCalledWith("draft");
  });

  it("says why it is empty: no grant, or nothing on this tab", () => {
    const { unmount } = routed(<CampaignListView list={campaignListNoScope} tab="running" onTab={() => {}} canAuthor={false} />);
    expect(screen.getByText("No campaigns are in your scope")).toBeInTheDocument();
    expect(screen.getByText(/ask an Admin/)).toBeInTheDocument();
    unmount();
    const onTab = vi.fn();
    routed(<CampaignListView list={campaignList} tab="paused" onTab={onTab} canAuthor />);
    expect(screen.getByText("No campaigns are paused")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show running (2)" }));
    expect(onTab).toHaveBeenCalledWith("running");
  });
});

describe("event validation", () => {
  it("checks dates, the window and the budget", () => {
    const errors = validateEvent({ ...blankEvent("GHS"), event_name: "Wave", period_start: "2026-10-10", period_end: "2026-10-01", attribution_window_days: "1.5", budget_amount: "12.345" });
    expect(errors.period_end).toMatch(/ends on or after/);
    expect(errors.attribution_window_days).toMatch(/whole days/);
    expect(errors.budget_amount).toMatch(/two decimals/);
    expect(validateEvent({ ...blankEvent("GHS"), event_name: "Wave", period_start: "2026-10-01", period_end: "2026-10-31", budget_amount: "25,000" })).toEqual({});
    // A live event's budget is not part of its edit form.
    expect(validateEvent({ ...blankEvent(""), event_name: "Wave", period_start: "2026-10-01", period_end: "2026-10-31" }, { withBudget: false })).toEqual({});
  });

  it("describes an audience in words, saying where a member has others below it", () => {
    expect(
      describeAudience(
        [
          { dimension_type: "segment", member_code: "retail" },
          { dimension_type: "segment", member_code: "sme" },
          { dimension_type: "region", member_code: "GA" },
        ],
        reference.dimensions,
      ),
    ).toBe("Segment: Retail and below or SME, and Region: Greater Accra");
  });
});

describe("campaign builder", () => {
  it("shows what needs fixing instead of saving", () => {
    const calls = capture({});
    routed(<CampaignBuilder reference={reference} />);
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Some fields need attention");
    expect(screen.getByText("Name the campaign.")).toBeInTheDocument();
    expect(screen.getByText("Choose the first contact day.")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("creates the campaign with its events and audience criteria", async () => {
    const calls = capture({ campaign_id: "c1" });
    const onCreated = vi.fn();
    routed(<CampaignBuilder reference={reference} onCreated={onCreated} />);
    const details = screen.getByRole("region", { name: "Campaign" });
    fireEvent.change(within(details).getByLabelText("Name"), { target: { value: "Save more" } });
    fireEvent.change(within(details).getByLabelText("Code"), { target: { value: "SAVE-Q4" } });
    fireEvent.change(within(details).getByLabelText("Type"), { target: { value: "seasonal" } });
    fireEvent.change(within(details).getByLabelText("Product"), { target: { value: "savings" } });
    const ev = screen.getByRole("region", { name: "Event 1" });
    // The objective's window is the default, shown before anything is typed.
    expect(within(ev).getByLabelText("Attribution window (days)")).toHaveAttribute("placeholder", "30");
    fireEvent.change(within(ev).getByLabelText("Event name"), { target: { value: "October wave" } });
    fireEvent.change(within(ev).getByLabelText("First contact day"), { target: { value: "2026-10-01" } });
    fireEvent.change(within(ev).getByLabelText("Last contact day"), { target: { value: "2026-10-31" } });
    fireEvent.change(within(ev).getByLabelText("Budget"), { target: { value: "25,000" } });
    fireEvent.click(within(ev).getByLabelText("SMS"));
    fireEvent.change(within(ev).getByLabelText("Member"), { target: { value: "affluent" } });
    fireEvent.click(within(ev).getByRole("button", { name: "Add criterion" }));
    expect(within(ev).getByText("Who it is for: Segment: Affluent.", { exact: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("c1"));
    expect(calls[0].body).toEqual({
      code: "SAVE-Q4",
      name: "Save more",
      campaign_type: "seasonal",
      objective: "deposit_growth",
      product_code: "savings",
      description: "",
      events: [
        {
          event_name: "October wave",
          period_start: "2026-10-01",
          period_end: "2026-10-31",
          budget_amount: "25000",
          budget_currency: "GHS",
          channels: ["sms"],
          audience: [{ dimension_type: "segment", member_code: "affluent" }],
        },
      ],
    });
  });
});

describe("campaign detail", () => {
  it("shows a pending budget change and keeps the current budget", () => {
    routed(<CampaignDetailView campaign={campaignDetail} reference={reference} canManage />);
    const running = screen.getByRole("region", { name: "Event 1 · October SMS wave" });
    expect(within(running).getByText(/GHS 40,000 was requested/)).toBeInTheDocument();
    expect(within(running).getByText(/stays at GHS 25,000/)).toBeInTheDocument();
    expect(within(running).getByText("Segment: Retail and below, and Region: Greater Accra")).toBeInTheDocument();
  });

  it("says a live budget change goes to an approver, and reports the proposal", async () => {
    const calls = capture({ approval_request_id: "a9", action_name: "campaign.event.budget.set", status: "pending", message: "Submitted for approval." }, 202);
    const live = { ...campaignDetail, events: [{ ...campaignDetail.events[0], pending_budget: null }] };
    routed(<CampaignDetailView campaign={live} reference={reference} canManage />);
    fireEvent.click(screen.getByRole("button", { name: "Change budget" }));
    const form = screen.getByRole("form", { name: "Change the budget of October SMS wave" });
    expect(within(form).getByText("This change goes to an approver.")).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText("New budget"), { target: { value: "30000" } });
    fireEvent.click(within(form).getByRole("button", { name: "Send for approval" }));
    await screen.findByText(/Sent for approval. The budget stays at GHS 25,000/);
    expect(calls[0].body).toMatchObject({ event_id: campaignDetail.events[0].event_id, budget_amount: "30000", budget_currency: "GHS" });
  });

  it("will not publish a draft without an audience", () => {
    routed(<CampaignDetailView campaign={campaignAllDrafts} reference={reference} canManage />);
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByText("No audience yet")).toBeInTheDocument();
  });

  it("is read-only without the permission", () => {
    routed(<CampaignDetailView campaign={campaignDetail} canManage={false} />);
    expect(screen.queryByRole("button", { name: /Publish|Change|Pause|Close|Repeat/ })).not.toBeInTheDocument();
    // Without the builder's dimension names, the audience shows member codes.
    expect(screen.getAllByText(/Segment|segment/).length).toBeGreaterThan(0);
  });
});
