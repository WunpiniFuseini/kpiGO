import { fireEvent, render, screen, within } from "@testing-library/react";

import { ApprovalCard, ApprovalsView, PayloadFacts } from "../pages/admin/Approvals";
import { approvalQueue, decidedApprovals, myProposals, myProposalsSecondPerson } from "../stories/fixtures";

describe("approvals", () => {
  it("spells out the exact payload, key by key", () => {
    render(<PayloadFacts payload={{ metric_code: "TAT", rows: 12, where: { period: "202611" } }} />);
    expect(screen.getByText("metric_code")).toBeInTheDocument();
    expect(screen.getByText("TAT")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    // Nested values are shown as JSON, not "[object Object]".
    expect(screen.getByText('{"period":"202611"}')).toBeInTheDocument();
  });

  it("says when an action takes no parameters", () => {
    render(<PayloadFacts payload={{}} />);
    expect(screen.getByText("This action takes no parameters.")).toBeInTheDocument();
  });

  it("offers approve and reject to a decider, and marks an assistant proposal", () => {
    render(<ApprovalCard request={approvalQueue.requests[0]} busy={false} onDecide={() => {}} />);
    expect(screen.getByRole("button", { name: /^Approve/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Reject/ })).toBeInTheDocument();
    expect(screen.getByText("Assistant proposal")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Confirm/ })).not.toBeInTheDocument();
  });

  it("offers confirm and withdraw on one's own proposal", () => {
    render(<ApprovalCard request={myProposals.requests[0]} busy={false} onDecide={() => {}} />);
    expect(screen.getByRole("button", { name: /^Confirm/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Withdraw/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Approve/ })).not.toBeInTheDocument();
  });

  it("explains an own request that only a second person can approve", () => {
    render(<ApprovalCard request={myProposalsSecondPerson.requests[0]} busy={false} onDecide={() => {}} />);
    expect(screen.getByRole("button", { name: /^Withdraw/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Confirm/ })).not.toBeInTheDocument();
    expect(screen.getByText("Someone else must approve this.")).toBeInTheDocument();
  });

  it("asks why before a rejection, and needs a real reason", () => {
    render(<ApprovalsView requests={approvalQueue.requests} scope="org" status="pending" onChanged={() => {}} />);
    fireEvent.click(within(screen.getByRole("article", { name: /Register a new metric/ })).getByRole("button", { name: /^Reject/ }));
    expect(screen.getByLabelText("Why?")).toBeRequired();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("shows who decided a request and why it was rejected", () => {
    render(<ApprovalsView requests={decidedApprovals.requests} scope="org" status="approved" onChanged={() => {}} />);
    expect(screen.getByText(/Approved by Efua Owusu/)).toBeInTheDocument();
    expect(screen.getByText(/already exists under a different code/)).toBeInTheDocument();
  });

  it("says why the queue is empty, differently for a decider and for one's own", () => {
    const { rerender } = render(<ApprovalsView requests={[]} scope="org" status="pending" onChanged={() => {}} />);
    expect(screen.getByText("Nothing is waiting")).toBeInTheDocument();
    expect(screen.getByText(/a decider approves or rejects/)).toBeInTheDocument();
    rerender(<ApprovalsView requests={[]} scope="own" status="pending" onChanged={() => {}} />);
    expect(screen.getByText(/changes you or your assistant propose/i)).toBeInTheDocument();
  });
});
