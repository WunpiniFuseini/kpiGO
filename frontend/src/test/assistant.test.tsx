import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ChatThread, NotConnected, StepCard, itemsFromMessages } from "../pages/Assistant";
import { assistantAnswer, assistantConversation, assistantOff, assistantProposal } from "../stories/fixtures";

const inRouter = (node: React.ReactNode) => render(<MemoryRouter>{node}</MemoryRouter>);

describe("assistant", () => {
  it("turns a loaded conversation into questions, actions and answers in order", () => {
    const items = itemsFromMessages(assistantConversation.messages);
    expect(items.map((i) => i.kind)).toEqual(["user", "step", "assistant"]);
    inRouter(<ChatThread items={items} />);
    expect(screen.getByText(/Why is Ama's TAT below target/)).toBeInTheDocument();
    expect(screen.getByText("scorecard.compute")).toBeInTheDocument();
    expect(screen.getByText(/4.1 days against a 3-day target/)).toBeInTheDocument();
  });

  it("shows a ran action with its parameters and no confirm button", () => {
    inRouter(<StepCard step={assistantAnswer.steps[0]} />);
    expect(screen.getByText("Ran")).toBeInTheDocument();
    expect(screen.getByText("subject_id")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });

  it("offers confirm and withdraw on a proposed change, and says nothing has changed", () => {
    inRouter(<StepCard step={assistantProposal.steps[0]} />);
    expect(screen.getByText("Proposed")).toBeInTheDocument();
    expect(screen.getByText(/Nothing has changed/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View in Approvals" })).toHaveAttribute("href", "/admin/approvals");
  });

  it("says why the assistant is off, and links a manager to Integrations", () => {
    const { rerender } = inRouter(<NotConnected status={assistantOff} canManage={false} />);
    expect(screen.getByText(/No model is connected/)).toBeInTheDocument();
    expect(screen.getByText(/KPIGO_INFERENCE_PROVIDER is unset/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Integrations" })).not.toBeInTheDocument();
    rerender(<MemoryRouter><NotConnected status={assistantOff} canManage /></MemoryRouter>);
    expect(screen.getByRole("link", { name: "Integrations" })).toHaveAttribute("href", "/admin/integrations");
  });

  it("renders a refused action's reason", () => {
    const refused = { ...assistantAnswer.steps[0], outcome: "refused" as const, error: "You cannot see that subject.", result: null };
    inRouter(<StepCard step={refused} />);
    expect(screen.getByText("Refused")).toBeInTheDocument();
    expect(screen.getByText("You cannot see that subject.")).toBeInTheDocument();
  });

  it("confirms a proposal and reflects the approved state", async () => {
    const { setTransport } = await import("../api/client");
    setTransport(async () => new Response(JSON.stringify({ approval_request_id: "a", action_name: "target.set", status: "approved" }), { status: 200 }));
    inRouter(<StepCard step={assistantProposal.steps[0]} />);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("Approved")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
  });
});
