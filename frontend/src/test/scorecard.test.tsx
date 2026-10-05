import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AckBar, HistoryView } from "../pages/scorecards/Conversation";
import { ProvenancePanel, ScorecardView } from "../pages/scorecards/Scorecard";
import { bandIndex, toneFor } from "../pages/scorecards/model";
import { SessionProvider } from "../session/Session";
import {
  cardClosed,
  cardFuture,
  cardLive,
  cardLiveHidden,
  cardNotGraded,
  cardRestated,
  cardUnassigned,
  history,
  manualEntered,
  rmMe,
  threadAcknowledged,
  threadOpenPeriod,
  threadRestated,
  threadToAcknowledge,
} from "../stories/scorecardViewFixtures";

function view(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <SessionProvider initial={{ status: "signed-in", me: rmMe }}>{ui}</SessionProvider>
    </MemoryRouter>,
  );
}

describe("scorecard", () => {
  it("leads with the grade and groups the matrix by objective", () => {
    view(<ScorecardView card={cardClosed} thread={threadToAcknowledge} history={history} />);
    expect(screen.getByRole("region", { name: "Grade for September 2026" })).toHaveTextContent("On Target");
    const table = screen.getByRole("table", { name: /Metric detail/ });
    expect(within(table).getByRole("columnheader", { name: "Grow the balance sheet" })).toBeInTheDocument();
    expect(within(table).getByText("override applied", { exact: false })).toBeInTheDocument();
    // An excluded metric says so in words, not a blank.
    expect(within(table).getByText("Excluded")).toBeInTheDocument();
    expect(screen.getByText("Closed and published · version 1", { selector: ".kg-chip" })).toBeInTheDocument();
  });

  it("opens provenance from any figure", () => {
    view(<ScorecardView card={cardLive} thread={threadOpenPeriod} history={history} />);
    fireEvent.click(screen.getByRole("button", { name: /^Target for Fee and commission income/ }));
    const panel = screen.getByRole("region", { name: "Target: Fee and commission income" });
    expect(panel).toHaveTextContent("Stored as 2,160,000 (yearly, pro-rated to the cycle)");
    expect(panel).toHaveTextContent("Maternity phase-back");
    expect(panel).toHaveTextContent("Reported as 14,300 USD");
    expect(panel).toHaveFocus();
    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: /Target: Fee/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^Score for Service TAT/ }));
    expect(screen.getByRole("region", { name: "Score: Service TAT" })).toHaveTextContent("target ÷ actual");
  });

  it("says awaiting data is left out, not zero", () => {
    view(<ScorecardView card={cardLive} thread={threadOpenPeriod} history={history} />);
    expect(screen.getByText("1 metric(s) awaiting data.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Actual for Digital activation rate/ }));
    expect(screen.getByRole("region", { name: /Digital activation rate/ })).toHaveTextContent("not counted as zero");
  });

  it("explains every empty state", () => {
    const { rerender } = view(<ScorecardView card={cardUnassigned} thread={null} history={null} />);
    expect(screen.getByRole("heading", { name: /No scorecard for Ama Boateng/ })).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <SessionProvider initial={{ status: "signed-in", me: rmMe }}>
          <ScorecardView card={cardFuture} thread={null} history={null} />
        </SessionProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: "December 2026 has not started" })).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <SessionProvider initial={{ status: "signed-in", me: rmMe }}>
          <ScorecardView card={cardNotGraded} thread={threadOpenPeriod} history={null} />
        </SessionProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText("Not graded yet")).toBeInTheDocument();
  });

  it("shows the restatement reason", () => {
    view(<ScorecardView card={cardRestated} thread={threadRestated} history={history} />);
    expect(screen.getByText(/Corrected CASA balances after the core-banking reversal/)).toBeInTheDocument();
  });
});

describe("manual input on a scorecard", () => {
  it("says a hidden value is hidden, and who entered a visible one", () => {
    const { rerender } = view(<ScorecardView card={cardLiveHidden} thread={threadOpenPeriod} history={history} />);
    expect(screen.getByText("Hidden until close")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Actual for Digital activation rate/ }));
    expect(screen.getByRole("region", { name: /Digital activation rate/ })).toHaveTextContent("hidden until the month closes");
    rerender(
      <MemoryRouter>
        <SessionProvider initial={{ status: "signed-in", me: rmMe }}>
          <ProvenancePanel card={cardClosed} metric={manualEntered} facet="actual" onClose={() => {}} />
        </SessionProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("region", { name: /Digital activation rate/ })).toHaveTextContent("Entered by Kofi Asante on 3 Oct 2026. Note: From the digital team");
  });
});

describe("acknowledgement", () => {
  it("is seen, not agreed, and asked again after a restatement", () => {
    const { rerender } = view(<AckBar thread={threadToAcknowledge} onChanged={() => {}} />);
    expect(screen.getByRole("button", { name: "Acknowledge version 1" })).toBeEnabled();
    expect(screen.getByText(/does not mean agreement/)).toBeInTheDocument();
    rerender(<AckBar thread={threadAcknowledged} onChanged={() => {}} />);
    expect(screen.queryByRole("button", { name: /Acknowledge/ })).not.toBeInTheDocument();
    rerender(<AckBar thread={threadRestated} onChanged={() => {}} />);
    expect(screen.getByText(/restated since/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Acknowledge version 2" })).toBeInTheDocument();
    rerender(<AckBar thread={threadOpenPeriod} onChanged={() => {}} />);
    expect(screen.getByText(/once the period is closed/)).toBeInTheDocument();
  });
});

describe("history and bands", () => {
  it("lists months newest first and names the gaps", () => {
    view(<HistoryView history={history} bands={cardClosed.bands} />);
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Sep 2026");
    expect(screen.getByText(/No scorecard in Apr 2026, May 2026/)).toBeInTheDocument();
  });

  it("maps any scale onto the four-step ramp", () => {
    expect([0, 1, 2, 3].map((i) => toneFor(i, 4))).toEqual([1, 2, 3, 4]);
    expect([0, 1, 2].map((i) => toneFor(i, 3))).toEqual([1, 3, 4]);
    expect(bandIndex(cardClosed.bands, 1.157)).toBe(2);
    expect(bandIndex(cardClosed.bands, 1.25)).toBe(3);
    expect(bandIndex(cardClosed.bands, null)).toBeNull();
  });
});
