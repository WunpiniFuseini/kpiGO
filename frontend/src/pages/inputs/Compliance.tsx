import { useState } from "react";

import type { Output } from "../../api/actions";
import { useQuery } from "../../api/useAction";
import { Chip, EmptyState, ErrorPanel, Loading, SelectField, TableSkeleton, TextField } from "../../components";
import type { ChipTone } from "../../components/Chip";
import { currentPeriod, formatDate, formatPeriod, shiftPeriod } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

export type Compliance = Output<"input.compliance">;
type Row = Compliance["rows"][number];
type Month = Row["months"][number];

/** Input compliance: who was asked, who submitted, and when (Design Brief §5 ComplianceView, PRD MI-12). */
export function CompliancePage() {
  const me = useMe();
  const blocked = me.no_access.find((n) => n.page_key === "input_compliance");
  const [period, setPeriod] = useState(shiftPeriod(currentPeriod(), -1));
  const [months, setMonths] = useState(6);
  const [data, reload] = useQuery("input.compliance", { period_key: period, months });
  return (
    <Page title="Input compliance">
      {blocked ? (
        <EmptyState kind="no-access" title="You cannot see input compliance" ask={blocked.ask}>
          {blocked.missing}
        </EmptyState>
      ) : (
        <section className="kg-card" aria-labelledby="compliance-heading">
          <div className="kg-sechead">
            <div>
              <h2 id="compliance-heading" className="kg-section">
                Who submits manual inputs on time
              </h2>
              <p className="kg-cap">Each month, every slice a contributor was asked for: on time, late, or never submitted. A late input still counts once it arrives; a later correction does not make it late.</p>
            </div>
            <span className="kg-spacer" />
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <div style={{ width: 170 }}>
                <TextField label="Up to" type="month" value={`${period.slice(0, 4)}-${period.slice(4)}`} onChange={(e) => e.target.value && setPeriod(e.target.value.replace("-", ""))} />
              </div>
              <div style={{ width: 130 }}>
                <SelectField
                  label="Over"
                  value={String(months)}
                  onChange={(e) => setMonths(Number(e.target.value))}
                  options={[3, 6, 12].map((n) => ({ value: String(n), label: `${n} months` }))}
                />
              </div>
            </div>
          </div>
          {data.status === "loading" ? (
            <Loading label="Loading input compliance">
              <TableSkeleton rows={4} columns={months + 2} />
            </Loading>
          ) : data.status === "error" ? (
            <ErrorPanel error={data.error} retry={reload} what="Input compliance" />
          ) : (
            <ComplianceView compliance={data.data} />
          )}
        </section>
      )}
    </Page>
  );
}

function cell(m: Month): { tone: ChipTone; text: string; detail: string } | null {
  if (!m.asked) return null;
  const parts = [m.late ? `${m.late} late` : "", m.missing ? `${m.missing} missing` : "", m.open ? `${m.open} still open` : ""].filter(Boolean);
  const detail = m.asked === 1 ? "1 asked" : `${m.asked} asked`;
  if (m.missing) return { tone: "down", text: parts.join(", "), detail };
  if (m.late) return { tone: "warn", text: parts.join(", "), detail };
  if (m.open) return { tone: "info", text: parts.join(", "), detail };
  return { tone: "up", text: m.asked === 1 ? "On time" : `All ${m.on_time} on time`, detail };
}

const LADDER = ["", "contributor reminded", "line manager told", "stakeholders told"];

function rate(r: Row): string {
  return r.on_time_rate === null ? "Nothing due yet" : `${Math.round(Number(r.on_time_rate) * 100)}% on time`;
}

/** The compliance table for a window of months, from data: what stories and tests render. */
export function ComplianceView({ compliance }: { compliance: Compliance }) {
  const chronic = compliance.rows.filter((r) => r.chronic);
  if (!compliance.rows.length) {
    return (
      <EmptyState kind="none" title="Nobody was asked for manual inputs in these months">
        {compliance.scope === "team"
          ? "None of the people you can see owed a manual input over this window. Contributors outside your team, and those not linked to a person in the hierarchy, are not shown here."
          : "No manual-input metric was assigned to a contributor over this window. Assign them under Scorecard setup → Manual input."}
      </EmptyState>
    );
  }
  return (
    <div className="kg-stack">
      {chronic.length ? (
        <p className="kg-cap" role="status">
          {chronic.length === 1 ? `${chronic[0].name} was` : `${chronic.length} contributors were`} late or missed inputs in {compliance.chronic_months} or more of these {compliance.periods.length} months.
        </p>
      ) : null}
      <div className="kg-table-wrap">
        <table className="kg-table">
          <caption className="sr-only">Manual inputs per contributor and month: on time, late, missing or still open.</caption>
          <thead>
            <tr>
              <th scope="col">Contributor</th>
              {compliance.periods.map((p) => (
                <th scope="col" key={p}>
                  {formatPeriod(p)}
                </th>
              ))}
              <th scope="col">Over the window</th>
            </tr>
          </thead>
          <tbody>
            {compliance.rows.map((r) => (
              <tr key={r.user_id ?? "nobody"}>
                <th scope="row">
                  {r.name}
                  {r.chronic ? (
                    <span className="kg-msub">
                      <Chip tone="down">Late in {r.late_months} of {compliance.periods.length} months</Chip>
                    </span>
                  ) : null}
                </th>
                {r.months.map((m) => {
                  const c = cell(m);
                  return (
                    <td key={m.period_key}>
                      {c ? (
                        <>
                          <Chip tone={c.tone}>{c.text}</Chip>
                          <span className="kg-msub">
                            {c.detail}
                            {m.step ? ` · ${LADDER[m.step]}` : ""}
                            {m.last_submitted_at && (m.late || !m.missing) ? ` · in ${formatDate(m.last_submitted_at)}` : ""}
                          </span>
                        </>
                      ) : (
                        <span className="kg-cap">Not asked</span>
                      )}
                    </td>
                  );
                })}
                <td>
                  {rate(r)}
                  <span className="kg-msub">
                    {r.on_time} on time · {r.late} late · {r.missing} missing
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
