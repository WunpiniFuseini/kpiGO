import { fireEvent, render, screen, within } from "@testing-library/react";

import { MatrixView } from "../pages/agents/Matrix";
import { matrix, matrixGrouped, matrixNoLines, matrixNoMetric, matrixRms } from "../stories/agentFixtures";

describe("product-line matrix", () => {
  it("shows each line's actual, target and state in words, with the total last", () => {
    render(<MatrixView matrix={matrix} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Region", "Mortgages", "Personal loans", "Cards", "All products"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(["Ashanti 3 agents", "Greater Accra 5 agents", "Total"]);
    const ga = within(rows[1]).getAllByRole("cell");
    expect(ga[0]).toHaveTextContent("102% · On track");
    expect(ga[1]).toHaveTextContent("95% · Behind");
    expect(ga[1]).toHaveTextContent("4 of 5 reported");
    // Absent is not zero: nobody in Ashanti sold cards.
    expect(within(rows[0]).getAllByRole("cell")[2]).toHaveTextContent("Not reported");
  });

  it("drills from a region and back up the breadcrumb", () => {
    const onDrill = vi.fn();
    const { rerender } = render(<MatrixView matrix={matrix} onDrill={onDrill} />);
    fireEvent.click(screen.getByRole("button", { name: "Greater Accra" }));
    expect(onDrill).toHaveBeenCalledWith({ level: "branch", region_code: "GA" });
    rerender(<MatrixView matrix={matrixRms} onDrill={onDrill} />);
    const crumbs = screen.getByRole("navigation", { name: "Matrix level" });
    expect(within(crumbs).getByText("Accra Central")).toHaveAttribute("aria-current", "page");
    fireEvent.click(within(crumbs).getByRole("button", { name: "Greater Accra" }));
    expect(onDrill).toHaveBeenLastCalledWith({ level: "branch", region_code: "GA" });
    // RMs are the bottom of the drill.
    expect(screen.queryByRole("button", { name: "Abena Owusu" })).not.toBeInTheDocument();
  });

  it("switches between lines and groups", () => {
    const onView = vi.fn();
    render(<MatrixView matrix={matrixGrouped} onView={onView} />);
    expect(screen.getByRole("button", { name: "Groups" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("columnheader", { name: "Lending" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Lines" }));
    expect(onView).toHaveBeenCalledWith("expanded");
  });

  it("says why it is empty or narrow", () => {
    const { rerender } = render(<MatrixView matrix={matrixNoLines} />);
    expect(screen.getByText(/No product line is switched on/)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Columns" })).not.toBeInTheDocument();
    rerender(<MatrixView matrix={matrixNoMetric} />);
    expect(screen.getByText("No metric adds up across agents")).toBeInTheDocument();
  });
});
