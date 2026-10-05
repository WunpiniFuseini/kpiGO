import { render, screen, within } from "@testing-library/react";

import { ProductLinesView } from "../pages/agents/ProductLines";
import { registry, registryEmpty, registryFresh } from "../stories/agentFixtures";

describe("product lines", () => {
  it("lists the matrix's lines in order with their groups and what is retiring", () => {
    render(<ProductLinesView registry={registry} canEdit full />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual([
      "MortgagesMORTGAGE",
      "Personal loansLOANS · RAG 110% / 90%",
      "CardsCARDS · retiring 1 Nov 2026",
    ]);
    expect(within(rows[2]).getByText("Cards and payments")).toBeInTheDocument();
    // A line already retiring has nothing to switch off; the first cannot move up.
    expect(within(rows[2]).queryByRole("button", { name: "Switch off" })).not.toBeInTheDocument();
    expect(within(rows[0]).getByRole("button", { name: "Move Mortgages up" })).toBeDisabled();
  });

  it("offers what the feed made available, with when it was detected", () => {
    render(<ProductLinesView registry={registry} canEdit />);
    const form = screen.getByRole("form", { name: "Switch on BANCA" });
    expect(within(form).getByText("detected 3 Oct 2026")).toBeInTheDocument();
    // Two groups: one has to be chosen before the line can be switched on.
    expect(within(form).getByRole("button", { name: "Switch on" })).toBeDisabled();
  });

  it("puts the first line in a new Products group when there are none", () => {
    render(<ProductLinesView registry={registryFresh} canEdit />);
    const form = screen.getByRole("form", { name: "Switch on CARDS" });
    expect(within(form).getByRole("combobox", { name: "Group" })).toHaveDisplayValue("Products (new)");
    expect(within(form).getByRole("button", { name: "Switch on" })).toBeEnabled();
  });

  it("says why it is empty", () => {
    render(<ProductLinesView registry={registryEmpty} canEdit />);
    expect(screen.getByText(/gets its columns from lines the actuals feed carries/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing new in the feed/)).toBeInTheDocument();
  });

  it("is read-only without the permission", () => {
    render(<ProductLinesView registry={registry} canEdit={false} />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
