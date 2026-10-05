import { fireEvent, render, screen, within } from "@testing-library/react";

import { AgentPaceView, LeaderboardView } from "../pages/agents/AgentPerformance";
import { agentPace, leaderboard, leaderboardComposite, leaderboardNoCohorts, leaderboardUnconfigured } from "../stories/agentFixtures";

describe("agent leaderboard", () => {
  it("ranks with the server's ranks, marks you, and lists the unreported unranked", () => {
    render(<LeaderboardView board={leaderboard} />);
    const items = within(screen.getByRole("list", { name: /Leaderboard, Greater Accra/ })).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[2]).toHaveAttribute("aria-current", "true");
    expect(within(items[2]).getByText("you")).toBeInTheDocument();
    expect(within(items[0]).getByText("GHS 1,650")).toBeInTheDocument();
    expect(within(items[0]).getByText(/of pace · Exemplary/)).toBeInTheDocument();
    expect(within(items[4]).getByText("Not ranked")).toBeInTheDocument();
    expect(within(items[4]).getByText("Not reported")).toBeInTheDocument();
    expect(screen.getByText(/Ties are broken by Accounts opened/)).toHaveTextContent("listed unranked, not ranked on zero");
  });

  it("shows the cohort's summary cards for an additive metric", () => {
    render(<LeaderboardView board={leaderboard} />);
    expect(screen.getByRole("article", { name: "Agents on pace" })).toHaveTextContent("2 of 5");
    expect(screen.getByRole("article", { name: "Value booked to date" })).toHaveTextContent("GHS 5,035");
    expect(screen.getByRole("article", { name: "Cohort pace" })).toHaveTextContent("105%");
  });

  it("shows overall pace as a percentage with no total", () => {
    render(<LeaderboardView board={leaderboardComposite} />);
    expect(screen.queryByRole("article", { name: "Cohort pace" })).not.toBeInTheDocument();
    expect(screen.getAllByText("138%").length).toBeGreaterThan(0);
  });

  it("opens an agent from their name", () => {
    const picked: string[] = [];
    render(<LeaderboardView board={leaderboard} onSelect={(id) => picked.push(id)} />);
    fireEvent.click(screen.getByRole("button", { name: /Kwesi Mensah/ }));
    expect(picked).toEqual(["00000000-0000-0000-0000-000000000102"]);
  });

  it("says why it is empty", () => {
    const { rerender } = render(<LeaderboardView board={leaderboardUnconfigured} />);
    expect(screen.getByText(/binds metrics to the module/)).toBeInTheDocument();
    rerender(<LeaderboardView board={leaderboardNoCohorts} />);
    expect(screen.getByText(/No custom cohort has members/)).toBeInTheDocument();
  });

  it("shows one agent's pace on each metric, absent as not reported", () => {
    render(<AgentPaceView pace={agentPace} />);
    expect(screen.getByRole("article", { name: "Value booked" })).toHaveTextContent("110% of pace");
    expect(screen.getByRole("article", { name: "Service TAT" })).toHaveTextContent("Not reported");
  });
});
