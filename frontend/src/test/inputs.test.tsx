import { fireEvent, render, screen, within } from "@testing-library/react";

import { LadderView, ManualInputView } from "../pages/admin/ManualInput";
import { EscalatedView, InputsView } from "../pages/inputs/MyInputs";
import { NOW, assignments, escalated, ladder, ladderNamedNoManager, manualMetrics, noAssignments, nothingEscalated, people, tasksDue, tasksLocked, tasksNone, tasksRestating } from "../stories/inputFixtures";

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
    expect(screen.getByText(/climbs the escalation ladder below, in kpiGo and by email/)).toBeInTheDocument();
    rerender(<ManualInputView list={{ ...assignments, email_reminders: false }} metrics={manualMetrics} users={people} onChanged={noop} />);
    expect(screen.getByText(/Email is off because no mail relay is set/)).toBeInTheDocument();
  });

  it("shows how far each slice has climbed and who hears when it is overdue", () => {
    render(<ManualInputView list={assignments} metrics={manualMetrics} users={people} onChanged={noop} />);
    expect(screen.getByText("Their line manager told")).toBeInTheDocument();
    expect(screen.getByText("Overdue goes to Efua Owusu")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Who hears when Net promoter score for Yaw Mensah · RM-0188 is overdue" }));
    const form = screen.getByRole("form", { name: "Overdue contacts for Net promoter score, Yaw Mensah · RM-0188" });
    // Only people whose role can see escalations are offered; the one already named is ticked.
    expect(within(form).getByRole("checkbox", { name: "Efua Owusu" })).toBeChecked();
    expect(within(form).queryByRole("checkbox", { name: "Kofi Asante" })).not.toBeInTheDocument();
  });

  it("explains an empty registry", () => {
    render(<ManualInputView list={noAssignments} metrics={[]} users={people} onChanged={noop} />);
    expect(screen.getByText(/Set a metric's collection to manual input/)).toBeInTheDocument();
  });
});

describe("escalation ladder", () => {
  it("reads as dates for the month, rung by rung", () => {
    render(<LadderView ladder={ladder} users={people} onChanged={noop} />);
    const table = screen.getByRole("table", { name: "Escalation ladder" });
    expect(within(table).getByText("2 working day(s) before the due day")).toBeInTheDocument();
    expect(within(table).getByText("On the due day")).toBeInTheDocument();
    expect(within(table).getByText(/everyone who manages input: Abena Darko/)).toBeInTheDocument();
    expect(screen.getByText(/one email a day/)).toBeInTheDocument();
  });

  it("shows a rung that is off, and named stakeholders", () => {
    render(<LadderView ladder={ladderNamedNoManager} users={people} onChanged={noop} />);
    const table = screen.getByRole("table", { name: "Escalation ladder" });
    expect(within(table).getAllByText("Off")).toHaveLength(2);
    expect(screen.getByText(/Email is off/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Change the ladder" }));
    expect(screen.getByRole("combobox", { name: "Tell their line manager" })).toHaveValue("off");
  });
});

describe("escalated to you", () => {
  it("lists what others owe and why you were told", () => {
    render(<EscalatedView escalated={escalated} alone={false} />);
    expect(screen.getByText("You are their line manager")).toBeInTheDocument();
    expect(screen.getByText("You are a stakeholder")).toBeInTheDocument();
    expect(screen.getByText("Missed the deadline")).toBeInTheDocument();
    expect(screen.getByText("Nobody: no line manager")).toBeInTheDocument();
  });

  it("says why it is empty when that is all the page shows", () => {
    const { container, rerender } = render(<EscalatedView escalated={nothingEscalated} alone={false} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<EscalatedView escalated={nothingEscalated} alone />);
    expect(screen.getByRole("heading", { name: "Nothing has been escalated to you" })).toBeInTheDocument();
  });
});
