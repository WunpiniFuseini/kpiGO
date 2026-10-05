import { fireEvent, render, screen, within } from "@testing-library/react";

import { ManualInputView } from "../pages/admin/ManualInput";
import { InputsView } from "../pages/inputs/MyInputs";
import { NOW, assignments, manualMetrics, noAssignments, people, tasksDue, tasksLocked, tasksNone, tasksRestating } from "../stories/inputFixtures";

const noop = () => {};

describe("my inputs", () => {
  it("shows what is due, by when, and where each stands", () => {
    render(<InputsView tasks={tasksDue} onChanged={noop} onPeriod={noop} now={NOW} />);
    expect(screen.getByRole("heading", { name: "2 of 3 input(s) still to submit for September 2026" })).toBeInTheDocument();
    expect(screen.getByText("3 day(s) left")).toBeInTheDocument();
    expect(screen.getByText(/they see it only once the month closes/)).toBeInTheDocument();
    const grid = screen.getByRole("table");
    expect(within(grid).getByText("Not started")).toBeInTheDocument();
    expect(within(grid).getByText(/Reminder sent/)).toBeInTheDocument();
    expect(within(grid).getByText("Lands on 31 people")).toBeInTheDocument();
    // Only the draft is filled in, so one goes on submit and nothing new to save.
    expect(screen.getByRole("button", { name: "Submit 1" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  });

  it("validates a value before it is sent", () => {
    render(<InputsView tasks={tasksDue} onChanged={noop} onPeriod={noop} now={NOW} />);
    const csat = screen.getByRole("textbox", { name: "Value for Customer satisfaction, Branch ACC" });
    fireEvent.change(csat, { target: { value: "4.25" } });
    expect(screen.getByRole("button", { name: "Submit 2" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeEnabled();
    fireEvent.change(csat, { target: { value: "four" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a number");
    expect(screen.getByRole("button", { name: /Submit/ })).toBeDisabled();
    const esg = screen.getByRole("textbox", { name: "Value for ESG compliance, Everyone on sme_rm" });
    fireEvent.change(csat, { target: { value: "4.2" } });
    fireEvent.change(esg, { target: { value: "140" } });
    expect(screen.getByRole("alert")).toHaveTextContent("between 0 and 100");
  });

  it("locks at the deadline and reopens only for a restatement", () => {
    const { rerender } = render(<InputsView tasks={tasksLocked} onChanged={noop} onPeriod={noop} now={NOW} />);
    expect(screen.getByText("The deadline has passed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Submit/ })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: /Value for Customer satisfaction/ })).toHaveAttribute("readonly");
    rerender(<InputsView key="r" tasks={tasksRestating} onChanged={noop} onPeriod={noop} now={NOW} />);
    expect(screen.getByText("September 2026 is being restated.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
  });

  it("says nothing due is good news", () => {
    render(<InputsView tasks={tasksNone} onChanged={noop} onPeriod={noop} now={NOW} />);
    expect(screen.getByRole("heading", { name: "Nothing due for September 2026" })).toBeInTheDocument();
  });
});

describe("manual input assignments", () => {
  it("names who owes each slice and what nobody is asked for", () => {
    render(<ManualInputView list={assignments} metrics={manualMetrics} users={people} onChanged={noop} />);
    expect(screen.getByText("Nobody: no line manager")).toBeInTheDocument();
    expect(screen.getByText(/ESG compliance\. Until someone is/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Assign a contributor" }));
    expect(screen.getByRole("form", { name: "Assign a contributor" })).toBeInTheDocument();
    // "Their line manager" is offered only for a one-person slice.
    expect(screen.queryByRole("option", { name: /Their line manager/ })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Slice" }), { target: { value: "subject" } });
    expect(screen.getByRole("option", { name: /Their line manager/ })).toBeInTheDocument();
  });

  it("says whether reminders also go by email", () => {
    const { rerender } = render(<ManualInputView list={assignments} metrics={manualMetrics} users={people} onChanged={noop} />);
    expect(screen.getByText(/one reminder, in kpiGo and by email/)).toBeInTheDocument();
    rerender(<ManualInputView list={{ ...assignments, email_reminders: false }} metrics={manualMetrics} users={people} onChanged={noop} />);
    expect(screen.getByText(/Email is off because no mail relay is set/)).toBeInTheDocument();
  });

  it("explains an empty registry", () => {
    render(<ManualInputView list={noAssignments} metrics={[]} users={people} onChanged={noop} />);
    expect(screen.getByText(/Set a metric's collection to manual input/)).toBeInTheDocument();
  });
});
