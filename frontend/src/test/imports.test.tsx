import { fireEvent, render, screen, within } from "@testing-library/react";

import { ApplyReport, DraftReview, DraftTable, MetricReview, previewActionFor } from "../pages/admin/Imports";
import { applyReport, importDrafts, rosterDraft, scorecardDraft, unknownDraft } from "../stories/importFixtures";

describe("import review", () => {
  it("routes a file to the spreadsheet or the Power BI preview by extension", () => {
    expect(previewActionFor("rm-scorecard.csv")).toBe("import.spreadsheet.preview");
    expect(previewActionFor("book.XLSX")).toBe("import.spreadsheet.preview");
    expect(previewActionFor("report.pbit")).toBe("import.powerbi.preview");
    expect(previewActionFor("model.bim")).toBe("import.powerbi.preview");
    expect(previewActionFor("model.tmdl")).toBe("import.powerbi.preview");
  });

  it("lists drafts with what each proposes and offers review only on a drafted one to a manager", () => {
    const onSelect = vi.fn();
    render(<DraftTable drafts={importDrafts} selectedId={null} onSelect={onSelect} canManage />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("3 metrics")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Review the import rm-scorecard.csv" }));
    expect(onSelect).toHaveBeenCalledWith(scorecardDraft.import_id);
    // The already-applied draft opens read-only ("View", not "Review").
    expect(screen.getByRole("button", { name: /rm-scorecard\.csv/ })).toHaveTextContent("Review");
    const applied = screen.getByRole("button", { name: `Review the import ${importDrafts[2].filename}` });
    expect(applied).toHaveTextContent("View");
  });

  it("says why an empty draft list is empty", () => {
    render(<DraftTable drafts={[]} selectedId={null} onSelect={() => {}} canManage />);
    expect(screen.getByText("No imports yet")).toBeInTheDocument();
  });

  it("flags the fields kpiGo guessed and excludes a metric when unticked", () => {
    const rows = scorecardDraft.proposals.metrics.map((m) => ({ ...m, include: true }));
    const onChange = vi.fn();
    render(<MetricReview rows={rows} editable onChange={onChange} />);
    // Total Deposits read its unit from the sheet, so only aggregation and unit were guessed.
    expect(screen.getByText("Guessed: aggregation, unit")).toBeInTheDocument();
    // Cost to Income had no direction column, so direction is flagged too.
    expect(screen.getAllByText("Guessed: aggregation, direction, unit")).toHaveLength(2);
    fireEvent.click(screen.getByRole("checkbox", { name: "Import the metric Total Deposits" }));
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls[0][0];
    expect(next.find((r: { metric_code: string }) => r.metric_code === "total_deposits").include).toBe(false);
  });

  it("lets a reviewer correct an inferred unit", () => {
    const rows = scorecardDraft.proposals.metrics.map((m) => ({ ...m, include: true }));
    const onChange = vi.fn();
    render(<MetricReview rows={rows} editable onChange={onChange} />);
    const selects = screen.getAllByLabelText("Unit");
    fireEvent.change(selects[0], { target: { value: "count" } });
    const next = onChange.mock.calls[0][0];
    const deposits = next.find((r: { metric_code: string }) => r.metric_code === "total_deposits");
    expect(deposits.unit).toBe("count");
    expect(deposits.is_percentage).toBe(false);
  });

  it("shows an unreadable file's explanation instead of an editable grid", () => {
    render(<DraftReview draft={unknownDraft} canManage onClose={() => {}} onChanged={() => {}} />);
    expect(screen.getByText("kpiGo could not read this file")).toBeInTheDocument();
    expect(screen.queryByText(/Proposed metrics/)).not.toBeInTheDocument();
  });

  it("prompts for a missing subject email on a roster", () => {
    render(<DraftReview draft={rosterDraft} canManage onClose={() => {}} onChanged={() => {}} />);
    expect(screen.getByText(/No email was in the file/)).toBeInTheDocument();
  });

  it("reports what was applied, distinguishing registered from already-existing", () => {
    render(<ApplyReport out={applyReport} onClose={() => {}} />);
    expect(screen.getByText(/2 metrics registered/)).toBeInTheDocument();
    const existing = screen.getByText("total_deposits").closest("tr")!;
    expect(existing).toHaveTextContent("registered");
    const dup = screen.getByText("cost_to_income_ratio").closest("tr")!;
    expect(dup).toHaveTextContent("exists");
  });
});
