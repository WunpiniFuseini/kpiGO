import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ROUTES, type ActionName } from "../api/actions";
import { setTransport } from "../api/client";
import { ViewBar, type DashboardState } from "../pages/executive/ViewBar";

type ViewRow = { name: string; state: DashboardState; created_at: string; updated_at: string };

function nameFor(url: string): ActionName | undefined {
  return (Object.keys(ROUTES) as ActionName[]).find((n) => ROUTES[n].path === url.split("?")[0]);
}

function serve(initial: ViewRow[]) {
  let views = [...initial];
  const calls: { action: ActionName | undefined; body: Record<string, unknown> }[] = [];
  setTransport(async (url, init) => {
    const action = nameFor(url);
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ action, body });
    if (action === "executive.view.list") return new Response(JSON.stringify({ views }), { status: 200 });
    if (action === "executive.view.save") {
      const saved = { name: body.name, state: body.state, created_at: "", updated_at: "" };
      views = [...views.filter((v) => v.name !== body.name), saved];
      return new Response(JSON.stringify(saved), { status: 200 });
    }
    if (action === "executive.view.delete") {
      views = views.filter((v) => v.name !== body.name);
      return new Response(JSON.stringify({ name: body.name, deleted: true }), { status: 200 });
    }
    return new Response(JSON.stringify({}), { status: 404 });
  });
  return calls;
}

function mount(
  onApply: (s: DashboardState) => void = () => {},
  state: DashboardState = { period_key: "202610", drill: {} },
) {
  render(<ViewBar state={state} onApply={onApply} />);
}

describe("ViewBar", () => {
  it("saves the current state under a name and lists it", async () => {
    const calls = serve([]);
    mount(() => {}, { period_key: "202610", drill: { nps_by_region: "south" } });

    fireEvent.click(screen.getByRole("button", { name: "Save view" }));
    fireEvent.change(screen.getByLabelText("View name"), { target: { value: "Q4 south" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const save = calls.find((c) => c.action === "executive.view.save");
      expect(save?.body).toMatchObject({
        name: "Q4 south",
        state: { period_key: "202610", drill: { nps_by_region: "south" } },
      });
    });
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "Q4 south" })).toBeInTheDocument(),
    );
  });

  it("applies a chosen view to the dashboard", async () => {
    serve([{ name: "Last month", state: { period_key: "202609", drill: {} }, created_at: "", updated_at: "" }]);
    const applied: DashboardState[] = [];
    mount((s) => applied.push(s));

    const picker = await screen.findByLabelText("Saved views");
    fireEvent.change(picker, { target: { value: "Last month" } });
    expect(applied).toEqual([{ period_key: "202609", drill: {} }]);
  });

  it("offers only Save when there are no views", async () => {
    serve([]);
    mount();
    await waitFor(() => expect(screen.getByLabelText("Saved views")).toBeDisabled());
    expect(screen.getByRole("button", { name: "Save view" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });

  it("deletes the selected view", async () => {
    const calls = serve([
      { name: "scratch", state: { period_key: "202610", drill: {} }, created_at: "", updated_at: "" },
    ]);
    mount();
    const picker = await screen.findByLabelText("Saved views");
    fireEvent.change(picker, { target: { value: "scratch" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() =>
      expect(calls.find((c) => c.action === "executive.view.delete")?.body).toMatchObject({
        name: "scratch",
      }),
    );
  });
});
