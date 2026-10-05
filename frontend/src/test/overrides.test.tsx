import { fireEvent, render, screen } from "@testing-library/react";

import { OverridesView } from "../pages/admin/Overrides";
import { overridesPending } from "../stories/scorecardFixtures";

describe("overrides", () => {
  it("never offers the requester their own approval", () => {
    render(<OverridesView overrides={overridesPending.overrides} status="pending" canApprove onChanged={() => {}} />);
    // a1 and a3 were requested by someone else; a2 is mine.
    expect(screen.getAllByRole("button", { name: /^Approve/ })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: /^Withdraw/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: /^Withdraw the weight override for sme_rm/ })).toBeInTheDocument();
  });

  it("asks why before a rejection", () => {
    render(<OverridesView overrides={overridesPending.overrides} status="pending" canApprove onChanged={() => {}} />);
    fireEvent.click(screen.getAllByRole("button", { name: /^Reject/ })[0]);
    expect(screen.getByLabelText("Why?")).toBeRequired();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("says why an empty queue is empty", () => {
    render(<OverridesView overrides={[]} status="pending" canApprove onChanged={() => {}} />);
    expect(screen.getByText("Nothing is waiting for approval")).toBeInTheDocument();
  });
});
