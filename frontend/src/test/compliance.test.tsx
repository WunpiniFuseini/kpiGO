import { render, screen, within } from "@testing-library/react";

import { ComplianceView } from "../pages/inputs/Compliance";
import { compliance, complianceEmpty, complianceTeamEmpty } from "../stories/inputFixtures";

describe("input compliance", () => {
  it("flags the chronically late contributor in words, not colour alone", () => {
    render(<ComplianceView compliance={compliance} />);
    const rows = screen.getAllByRole("row").slice(1);
    const first = within(rows[0]);
    expect(first.getByRole("rowheader")).toHaveTextContent("Yaw Boakye");
    expect(first.getByText("Late in 4 of 6 months")).toBeInTheDocument();
    expect(first.getByText("33% on time")).toBeInTheDocument();
    expect(first.getAllByText("1 late")).toHaveLength(3);
    expect(first.getByText("1 missing")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("2 contributors were late or missed inputs in 3 or more of these 6 months.");
  });

  it("shows months not asked and inputs still open", () => {
    render(<ComplianceView compliance={compliance} />);
    const efua = within(screen.getAllByRole("row")[3]);
    expect(efua.getAllByText("Not asked")).toHaveLength(3);
    expect(efua.getByText("1 still open")).toBeInTheDocument();
  });

  it("says why it is empty, for an Admin and for a manager", () => {
    const { rerender } = render(<ComplianceView compliance={complianceEmpty} />);
    expect(screen.getByText(/Assign them under Scorecard setup/)).toBeInTheDocument();
    rerender(<ComplianceView compliance={complianceTeamEmpty} />);
    expect(screen.getByText(/None of the people you can see/)).toBeInTheDocument();
  });
});
