import { fireEvent, render, screen } from "@testing-library/react";

import { CloseView, VersionsView } from "../pages/admin/PeriodClose";
import { closeBlocked, closeClosed, closeReady, snapshots } from "../stories/scorecardFixtures";

describe("period close", () => {
  it("holds the close while anything blocks it and names who is affected", () => {
    render(<CloseView check={closeBlocked} canClose onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: "Close period" })).toBeDisabled();
    expect(screen.getByText(/E2000, .*E2024 and 6 more/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Exclude fee_income where it is unscored" }));
    expect(screen.getByLabelText("Why exclude it?")).toBeRequired();
    expect(screen.getByRole("button", { name: "Record exclusion" })).toBeDisabled();
  });

  it("closes when ready and restates only a closed month", () => {
    const { rerender } = render(<CloseView check={closeReady} canClose onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: "Close period" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Restate" })).not.toBeInTheDocument();
    rerender(<CloseView check={closeClosed} canClose onChanged={() => {}} />);
    expect(screen.queryByRole("button", { name: /Close/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restate" })).toBeInTheDocument();
  });

  it("keeps every version on record", () => {
    render(<VersionsView snapshots={snapshots.snapshots} period="202609" />);
    expect(screen.getByText("restatement")).toBeInTheDocument();
    expect(screen.getByText("Corrected CASA balances for the Kumasi branches.")).toBeInTheDocument();
  });
});
