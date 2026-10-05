import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { DeltaChip } from "../components/Chip";
import { EmptyState } from "../components/EmptyState";
import { GradeBanner } from "../components/GradeBanner";
import { safeNext } from "../pages/Login";
import { SessionProvider } from "../session/Session";
import { staffMe } from "../stories/fixtures";
import { installMockApi } from "../stories/mockApi";

describe("DeltaChip", () => {
  it("says the movement in words, not only colour and arrow", () => {
    render(<DeltaChip value={-3.1} />);
    expect(screen.getByText("down 3.1% since last period")).toBeInTheDocument();
  });

  it("colours a fall as an improvement when lower is better", () => {
    const { container } = render(<DeltaChip value={-8} higherIsBetter={false} />);
    expect(container.firstChild).toHaveClass("kg-chip--up");
  });
});

describe("GradeBanner", () => {
  const bands = [
    { label: "Needs Focus", from: 0, tone: 1 as const },
    { label: "On Target", from: 85, tone: 3 as const },
  ];

  it("names the band and marks it on the scale", () => {
    render(<GradeBanner bands={bands} current={0} score={61} periodLabel="October 2026" provisional />);
    expect(screen.getAllByText("Needs Focus")[0]).toBeInTheDocument();
    expect(screen.getByText(/Provisional/)).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: true })).toHaveTextContent("Needs Focus");
    expect(screen.getByText("24.0")).toBeInTheDocument();
  });

  it("explains why there is no grade", () => {
    render(<GradeBanner bands={bands} current={null} score={null} periodLabel="October 2026" emptyReason="The finance feed is due at 06:00." />);
    expect(screen.getByText("Not graded yet")).toBeInTheDocument();
    expect(screen.getByText("The finance feed is due at 06:00.")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("names who to ask and the reference to quote", () => {
    render(<EmptyState kind="error" title="Failed" ask="an Admin" reference="ABC123" />);
    expect(screen.getByText("To change this, ask an Admin.")).toBeInTheDocument();
    expect(screen.getByText("ABC123")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});

describe("safeNext", () => {
  it.each([
    ["/admin/users", "/admin/users"],
    [null, "/"],
    ["https://evil.example", "/"],
    ["//evil.example", "/"],
    ["/\\evil.example", "/"],
  ])("%s → %s", (given, expected) => expect(safeNext(given)).toBe(expected));
});

describe("routing", () => {
  it("sends a page the user's roles do not show to their home", async () => {
    installMockApi({});
    render(
      <MemoryRouter initialEntries={["/admin/users"]}>
        <SessionProvider initial={{ status: "signed-in", me: staffMe }}>
          <App />
        </SessionProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "Scorecards" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Users & access" })).not.toBeInTheDocument();
  });

  it("sends a signed-out visitor to sign in, keeping where they were going", async () => {
    installMockApi({ "auth.providers": "pending", "setup.status": "pending" });
    render(
      <MemoryRouter initialEntries={["/admin/health"]}>
        <SessionProvider initial={{ status: "signed-out", reason: "none" }}>
          <App />
        </SessionProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});
