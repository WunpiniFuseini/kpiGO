import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

import { ROUTES, type ActionName } from "../api/actions";
import { setTransport } from "../api/client";
import { formatDate } from "../lib/format";
import { CampaignBuilder, CampaignListView } from "../pages/campaigns/Campaigns";
import { CampaignDetailView } from "../pages/campaigns/Detail";
import { blankEvent, describeAudience, describeEstimate, percent, validateEvent } from "../pages/campaigns/model";
import {
  campaignAllDrafts,
  campaignDetail,
  campaignList,
  campaignListNoScope,
  campaignReach,
  campaignReachNothingFed,
  campaignValue,
  campaignValueGross,
  estimate,
  eventValue,
  eventValueContaminated,
  estimateNoPopulation,
  estimateNotBrokenDown,
  reference,
} from "../stories/campaignFixtures";

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

/** Answer each action with its own reply, recording every call by action name. */
function serve(replies: Partial<Record<ActionName, unknown>>) {
  const calls: { action: ActionName | undefined; url: string; body: unknown }[] = [];
  setTransport(async (url, init) => {
    const action = (Object.keys(ROUTES) as ActionName[]).find((n) => ROUTES[n].path === url.split("?")[0]);
    calls.push({ action, url, body: init.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(action ? (replies[action] ?? {}) : {}), { status: 200 });
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
    expect(validateEvent({ ...blankEvent("GHS"), holdout_pct: "60" }).holdout_pct).toMatch(/1 to 50%/);
    expect(validateEvent({ ...blankEvent("GHS"), holdout_pct: "10" }).holdout_pct).toBeUndefined();
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
    const calls = serve({ "campaign.create": { campaign_id: "c1" }, "campaign.audience.estimate": estimate });
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
    // The audience is sized as of the first contact day.
    expect(await within(ev).findByText(/Estimated reach: About 48,200 customers of 312,000 \(15%\)/)).toBeInTheDocument();
    expect(calls.find((c) => c.action === "campaign.audience.estimate")?.url).toBe("/api/v1/actions/campaign.audience.estimate?audience=segment%3Aaffluent&on=2026-10-01");
    fireEvent.click(screen.getByRole("button", { name: "Save as draft" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("c1"));
    expect(calls.find((c) => c.action === "campaign.create")?.body).toEqual({
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

describe("reach", () => {
  it("says the estimate, or why there is none", () => {
    expect(describeEstimate(estimate)).toBe(`About 48,200 customers of 312,000 (15%), from the customer population on ${formatDate("2026-09-30")}.`);
    expect(describeEstimate(estimateNoPopulation)).toMatch(/No customer population has been fed yet/);
    expect(describeEstimate(estimateNotBrokenDown, reference.dimensions)).toMatch(/not broken down by Region, so this audience cannot be sized/);
  });

  it("lays the funnel beside the estimate and says what other events won", () => {
    routed(<CampaignDetailView campaign={campaignDetail} canManage={false} reach={{ status: "ready", data: campaignReach, refreshing: false }} />);
    const running = screen.getByRole("region", { name: "Event 1 · October SMS wave" });
    const funnel = within(running).getByRole("list", { name: "Reach funnel" });
    const stages = within(funnel).getAllByRole("listitem").map((li) => li.textContent);
    expect(stages[0]).toBe("Targeted (estimate)48,200 of 312,000 customers");
    expect(stages[1]).toBe("Contacted41,250 86% of targeted");
    expect(stages[4]).toBe("Outcome reach2,310 5% of targeted in the audience, acted in the window");
    expect(stages[5]).toBe("Converted1,985 4% of targeted credited to this event");
    expect(within(running).getByText(/325 customers' outcomes went to another event under the last touch rule/)).toBeInTheDocument();
    expect(within(running).getByText("GHS 4,182,500")).toBeInTheDocument();
    // A draft is sized but counts nothing yet.
    const draft = screen.getByRole("region", { name: "Event 2 · November SMS wave" });
    expect(within(draft).getByText(/Estimated audience: About 61,000 customers/)).toBeInTheDocument();
    expect(within(draft).queryByRole("list", { name: "Reach funnel" })).not.toBeInTheDocument();
  });

  it("says why each figure is missing when nothing is fed", () => {
    routed(<CampaignDetailView campaign={campaignDetail} canManage={false} reach={{ status: "ready", data: campaignReachNothingFed, refreshing: false }} />);
    const running = screen.getByRole("region", { name: "Event 1 · October SMS wave" });
    expect(within(running).getAllByText("Not fed")).toHaveLength(6);
    expect(within(running).getByText(/No contact feed has loaded yet/)).toBeInTheDocument();
    expect(within(running).getByText(/deposit growth objective counts no outcome metrics yet/)).toBeInTheDocument();
    expect(within(running).getByText(/No customer population has been fed yet/)).toBeInTheDocument();
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

describe("value and return", () => {
  const ready = <T,>(data: T) => ({ status: "ready" as const, data, refreshing: false });

  it("leads with incremental, puts gross beside it and names the method", () => {
    routed(<CampaignDetailView campaign={campaignDetail} canManage={false} value={ready(campaignValue)} />);
    const running = screen.getByRole("region", { name: "Event 1 · October SMS wave" });
    const card = within(running).getByRole("region", { name: "Value and return" });
    expect(within(card).getByLabelText("Incremental value of October SMS wave: GHS 572,500")).toBeInTheDocument();
    expect(within(card).getByText("GHS 4,182,500")).toBeInTheDocument();
    expect(within(card).getByText("2190.0%")).toBeInTheDocument();
    expect(within(card).getByText(/same length of time before the event/)).toBeInTheDocument();
    const control = within(card).getByRole("group", { name: "Control group" });
    expect(control).toHaveTextContent("+1.39 points");
    expect(control).toHaveTextContent("4,500 (9.8%, planned 10%)");
    // A draft has no value card.
    const draft = screen.getByRole("region", { name: "Event 2 · November SMS wave" });
    expect(within(draft).queryByRole("region", { name: "Value and return" })).not.toBeInTheDocument();
    expect(screen.queryByText(/The basis was changed/)).not.toBeInTheDocument();
  });

  it("shows a withheld figure as a dash with the reason, never zero", () => {
    const report = { ...campaignValue, events: [eventValueContaminated] };
    routed(<CampaignDetailView campaign={campaignDetail} canManage={false} value={ready(report)} />);
    const card = screen.getByRole("region", { name: "Value and return" });
    expect(within(card).getByLabelText("Incremental value of October SMS wave: withheld")).toHaveTextContent("—");
    expect(within(card).getByText(/another event reached 143 of these customers in the baseline window/)).toBeInTheDocument();
    expect(within(card).getByText(/the contact feed marks no one as held out/)).toBeInTheDocument();
  });

  it("says when the org changed its basis", () => {
    routed(<CampaignDetailView campaign={campaignDetail} canManage={false} value={ready(campaignValueGross)} />);
    expect(screen.getByText("Campaign value leads with gross value.")).toBeInTheDocument();
    expect(screen.getByText(/Reports from before then led with incremental value/)).toBeInTheDocument();
    expect(screen.getByLabelText("Gross value of October SMS wave: GHS 4,182,500")).toBeInTheDocument();
  });

  it("formats a return as a signed percentage", () => {
    expect(percent("-0.9972")).toBe("−99.7%");
    expect(percent("0.25")).toBe("25.0%");
    expect(percent(null)).toBe("—");
    expect(percent(eventValue().roi)).toBe("2190.0%");
  });

  it("sends the control group share, and clears it with 0", async () => {
    const calls = capture(campaignAllDrafts);
    routed(<CampaignDetailView campaign={campaignAllDrafts} reference={reference} canManage />);
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const field = screen.getByLabelText("Control group (%)");
    fireEvent.change(field, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Draft saved.");
    expect(calls[0].body).toMatchObject({ holdout_pct: 0 });
  });
});
