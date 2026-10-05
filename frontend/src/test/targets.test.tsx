import { render, screen } from "@testing-library/react";

import { CoverageMark, DraftsView, WeightMark } from "../pages/admin/Targets";
import { formatPeriod, shiftPeriod } from "../lib/format";
import { coverage, drafts } from "../stories/scorecardFixtures";

describe("coverage grid", () => {
  it("says each state in words, not colour alone", () => {
    const partial = coverage.cells.find((c) => c.state === "partial")!;
    render(<CoverageMark cell={partial} />);
    expect(screen.getByText(/set for some people only, 0 of 5 people published, 3 in draft/)).toBeInTheDocument();
  });

  it("marks a weight sum that is off and says what it must be", () => {
    const off = coverage.weights.find((w) => w.complete && !w.ok)!;
    render(<WeightMark weight={off} />);
    expect(screen.getByText(/but must sum to 100/)).toBeInTheDocument();
  });

  it("offers publish only to a publisher", () => {
    const { rerender } = render(<DraftsView drafts={drafts.targets} periods={["202707"]} canManage canPublish={false} onChanged={() => {}} />);
    expect(screen.queryByRole("button", { name: /Publish/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Discard drafts" })).toBeInTheDocument();
    rerender(<DraftsView drafts={drafts.targets} periods={["202707"]} canManage canPublish onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: "Publish 3 draft(s)" })).toBeInTheDocument();
    expect(screen.getByText("revises v1")).toBeInTheDocument();
  });
});

describe("period helpers", () => {
  it("format and shift period keys", () => {
    expect(formatPeriod("202607")).toBe("Jul 2026");
    expect(formatPeriod("202607", true)).toBe("July 2026");
    expect(shiftPeriod("202612", 1)).toBe("202701");
    expect(shiftPeriod("202601", -13)).toBe("202412");
  });
});
