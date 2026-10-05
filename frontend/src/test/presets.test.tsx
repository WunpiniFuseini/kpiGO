import { render, screen, within } from "@testing-library/react";

import { DistributionView, HeatmapView, TrendView, shortDay } from "../pages/agents/Sections";
import { VisibilityNote, VisibilityView } from "../pages/agents/Visibility";
import { distribution, distributionEmpty, heatmap, presetRestricted, presetSales, trend, trendAverage, visibilityOpen, visibilityRules } from "../stories/agentFixtures";

describe("preset sections", () => {
  it("says the running total against where the targets expect it", () => {
    render(<TrendView trend={trend} title="Month to date" />);
    expect(screen.getByRole("status")).toHaveTextContent(/to date against .* the targets expect by 17 Jun\. 4 of 5 agents reported\./);
  });

  it("trends an average as the daily mean", () => {
    render(<TrendView trend={trendAverage} title="Queue trend" />);
    expect(screen.getByText(/The mean of the agents who reported each day/)).toBeInTheDocument();
  });

  it("puts each heatmap day's state in words, and a dash where nothing was reported", () => {
    render(<HeatmapView heatmap={heatmap} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    const kumasi = within(rows[0]).getAllByRole("cell");
    expect(kumasi[0]).toHaveTextContent("On track");
    expect(kumasi[1]).toHaveTextContent("Behind");
    expect(kumasi[2]).toHaveTextContent("–");
    expect(kumasi[3]).toHaveTextContent("Off track");
    expect(screen.getByText(/not a zero/)).toBeInTheDocument();
  });

  it("bins agents and says who is not counted", () => {
    render(<DistributionView distribution={distribution} />);
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(5);
    expect(screen.getByRole("status")).toHaveTextContent("8 of 9 agents reported; the other one is not counted as zero.");
  });

  it("says why a distribution is empty", () => {
    render(<DistributionView distribution={distributionEmpty} />);
    expect(screen.getByText("Nothing reported on Average handling time yet")).toBeInTheDocument();
  });

  it("formats days short", () => {
    expect(shortDay("2026-06-02")).toBe("2 Jun");
  });
});

describe("who sees whom", () => {
  it("tells a narrowed reader what they see and why", () => {
    render(<VisibilityNote visibility={presetRestricted.visibility} />);
    expect(screen.getByRole("status")).toHaveTextContent("You see your branch. Agent Performance is narrowed for your role staff.");
  });

  it("says nothing when the module is open", () => {
    const { container } = render(<VisibilityNote visibility={presetSales.visibility} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the rules with a way to remove each", () => {
    render(<VisibilityView rules={visibilityRules} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(["Profile sme_rm", "Role staff"]);
    expect(within(rows[1]).getByText("Their branch")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove the rule for role staff" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save rule" })).toBeDisabled();
  });

  it("says the module is open when there are no rules", () => {
    render(<VisibilityView rules={visibilityOpen} />);
    expect(screen.getByText(/Sales is open: everyone with the page sees every agent/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
