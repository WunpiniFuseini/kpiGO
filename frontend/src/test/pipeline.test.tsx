import { fireEvent, render, screen, within } from "@testing-library/react";

import { PipelineStagesView, PipelineView } from "../pages/agents/Pipeline";
import { pipeline, pipelineEmpty, pipelineNoStages, pipelineRms, pipelineStages, pipelineStagesNoMetrics, pipelineStagesNone } from "../stories/agentFixtures";
import { installMockApi } from "../stories/mockApi";

describe("sales pipeline", () => {
  it("shows each stage now, its conversion from the stage before, and its movement in words", () => {
    render(<PipelineView pipeline={pipeline} />);
    const funnel = screen.getByRole("table", { name: "Pipeline by stage" });
    const rows = within(funnel).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(["Leads", "Proposals", "Approved"]);
    const proposals = within(rows[1]).getAllByRole("cell");
    expect(proposals[0]).toHaveTextContent("17 deals");
    expect(proposals[1]).toHaveTextContent("28%");
    expect(proposals[2]).toHaveTextContent(/^down /);
    // The first stage has nothing before it.
    expect(within(rows[0]).getAllByRole("cell")[1]).toHaveTextContent("–");
  });

  it("says a region with nothing in a stage has nothing there, not zero", () => {
    render(<PipelineView pipeline={pipeline} />);
    const grid = screen.getByRole("region", { name: "Pipeline by stage and place" });
    const ashanti = within(grid).getAllByRole("row")[1];
    expect(within(ashanti).getAllByRole("cell")[2]).toHaveTextContent("Nothing in stage");
    expect(screen.getByText(/agents with none add nothing, not zero/)).toBeInTheDocument();
  });

  it("drills from a region and back up the breadcrumb", () => {
    const onDrill = vi.fn();
    const { rerender } = render(<PipelineView pipeline={pipeline} onDrill={onDrill} />);
    fireEvent.click(screen.getByRole("button", { name: "Greater Accra" }));
    expect(onDrill).toHaveBeenCalledWith({ level: "branch", region_code: "GA" });
    rerender(<PipelineView pipeline={pipelineRms} onDrill={onDrill} />);
    const crumbs = screen.getByRole("navigation", { name: "Pipeline level" });
    fireEvent.click(within(crumbs).getByRole("button", { name: "All regions" }));
    expect(onDrill).toHaveBeenLastCalledWith({ level: "region" });
    expect(screen.queryByRole("button", { name: "Abena Owusu" })).not.toBeInTheDocument();
  });

  it("says why the pipeline is empty", () => {
    const { rerender } = render(<PipelineView pipeline={pipelineNoStages} />);
    expect(screen.getByText("No pipeline stages yet")).toBeInTheDocument();
    rerender(<PipelineView pipeline={pipelineEmpty} />);
    expect(screen.getByText("Nothing in the pipeline yet")).toBeInTheDocument();
  });
});

describe("pipeline stages", () => {
  it("lists the stages in funnel order with the metrics they read", () => {
    render(<PipelineStagesView stages={pipelineStages} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").textContent)).toEqual(["Leads", "Proposals", "Approved"]);
    expect(within(rows[2]).getAllByRole("cell")[1]).toHaveTextContent("–");
    expect(screen.getByRole("button", { name: "Remove Proposals" })).toBeInTheDocument();
  });

  it("offers only count metrics for deals, and saves once a stage has a metric", async () => {
    const onChanged = vi.fn();
    installMockApi({ "pipeline.stage.set": { data: pipelineStages.stages[1] } });
    render(<PipelineStagesView stages={pipelineStagesNone} onChanged={onChanged} />);
    const deals = screen.getByLabelText("Deals from");
    expect(within(deals).getAllByRole("option").map((o) => o.textContent)).toEqual(["None", "Leads, deals", "Proposals, deals"]);
    const save = screen.getByRole("button", { name: "Save stage" });
    fireEvent.change(screen.getByLabelText("Code"), { target: { value: "proposal" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Proposals" } });
    expect(save).toBeDisabled();
    fireEvent.change(deals, { target: { value: "pipeline_proposal_count" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(await screen.findByText("Stage saved.")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("says what to register when no snapshot metric exists", () => {
    render(<PipelineStagesView stages={pipelineStagesNoMetrics} />);
    expect(screen.getByText("No snapshot metrics to build stages from")).toBeInTheDocument();
  });
});
