import type { Meta, StoryObj } from "@storybook/react-vite";

import { Chip } from "./Chip";
import { DataTable } from "./DataTable";
import { EmptyState } from "./EmptyState";
import { GradePill } from "./GradePill";

interface Row {
  metric: string;
  sub: string;
  target: string;
  actual: string | null;
  pct: string | null;
  weight: string;
  status: "scored" | "awaiting" | "override";
}

const rows: Row[] = [
  { metric: "Deposits growth", sub: "currency · sum · higher is better", target: "13.0M", actual: "12.4M", pct: "95.4%", weight: "30%", status: "scored" },
  { metric: "Loan disbursement", sub: "currency · sum · higher is better", target: "4.0M", actual: "4.6M", pct: "115.0%", weight: "25%", status: "override" },
  { metric: "Net promoter score", sub: "score · average · higher is better", target: "45", actual: null, pct: null, weight: "15%", status: "awaiting" },
];

const meta: Meta = { title: "Primitives/DataTable" };
export default meta;
type Story = StoryObj;

const empty = (
  <EmptyState kind="awaiting" title="No metrics scored yet">
    The first feed for October is due on the 3rd working day.
  </EmptyState>
);

const table = (data: Row[]) => (
  <div className="kg-card">
    <DataTable
      caption="Scorecard, October 2026"
      columns={[
        {
          key: "m",
          header: "Metric",
          render: (r) => (
            <>
              {r.metric}
              <div className="kg-cap" style={r.status === "override" ? { color: "var(--warn-ink)" } : undefined}>
                {r.sub}
                {r.status === "override" ? " · target override active" : ""}
              </div>
            </>
          ),
        },
        { key: "t", header: "Target", numeric: true, render: (r) => r.target },
        { key: "a", header: "Actual", numeric: true, render: (r) => r.actual ?? <span className="kg-cap">Awaiting data</span> },
        { key: "p", header: "% achieved", numeric: true, render: (r) => r.pct ?? "–" },
        { key: "w", header: "Weight", numeric: true, render: (r) => r.weight },
        {
          key: "s",
          header: "Status",
          render: (r) => (r.status === "awaiting" ? <Chip tone="flat">Not scored</Chip> : <GradePill tone={r.pct === "95.4%" ? 3 : 4} label={r.pct === "95.4%" ? "On Target" : "Exemplary"} />),
        },
      ]}
      rows={data}
      rowKey={(r) => r.metric}
      empty={empty}
      footer={
        <tr className="kg-total">
          <th scope="row" style={{ textAlign: "left" }}>
            Total: 2 of 3 metrics scored
          </th>
          <td className="is-num" colSpan={5}>
            Score 92.1
          </td>
        </tr>
      }
    />
  </div>
);

export const Scorecard: Story = { render: () => table(rows) };
export const Empty: Story = { render: () => table([]) };
