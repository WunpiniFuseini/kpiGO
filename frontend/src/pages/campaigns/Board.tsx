import type { QueryState } from "../../api/useAction";
import { Chip, Loading, Skeleton } from "../../components";
import { formatDate } from "../../lib/format";
import { BASES, count, money, percent, RULES, type Board, type BoardMoney, type BoardRow, type Campaign, type Reconciliation, type Rule } from "./model";

const cash = (amount: string | null, currency: string) => (amount === null ? "—" : money(amount, currency));

/** The quarter at a glance for the campaigns in scope (App Flow §5.4 summary cards). */
export function BoardSummary({ board }: { board: QueryState<Board> }) {
  if (board.status === "loading") {
    return (
      <Loading label="Loading the quarter's campaign figures">
        <Skeleton height={88} />
      </Loading>
    );
  }
  if (board.status === "error") return <p className="kg-cap">The quarter's campaign figures could not be loaded just now.</p>;
  const b = board.data;
  const [main, ...others] = b.money;
  const label = `${formatDate(b.start)} – ${formatDate(b.end)}`;
  const lead = b.basis === "incremental" ? "Incremental value" : "Gross value";
  return (
    <section className="kg-stack" aria-labelledby="board-summary">
      <h2 id="board-summary" className="kg-eyebrow">
        Events in play this quarter · {label}
      </h2>
      {!main ? (
        <p className="kg-cap">No published event runs or attributes in this quarter, so there is nothing to sum yet.</p>
      ) : (
        <div className="kg-grid">
          <Tile label={lead} figure={cash(b.basis === "incremental" ? main.incremental : main.gross, main.currency)} note={valueNote(main, b)} />
          <Tile label="Spend" figure={main.spend === null ? "—" : money(main.spend, main.currency)} note={main.spend === null ? `No spend fed · budget ${money(main.budget, main.currency)}` : `of ${money(main.budget, main.currency)} budget`} />
          <Tile label={`Return (${BASES[b.basis]})`} figure={percent(main.roi)} note={main.gross_roi === null ? "" : `${percent(main.gross_roi)} on gross`} />
          <Tile
            label="Win-backs confirmed"
            figure={b.winbacks ? count(b.winbacks.confirmed) : "—"}
            note={b.winbacks ? `${count(b.winbacks.provisional)} provisional · ${count(b.winbacks.lapsed)} lapsed` : "No win-back feed has loaded yet"}
          />
        </div>
      )}
      {others.length ? <p className="kg-cap">Also budgeted in other currencies, summed apart and never converted: {others.map((m) => `${money(m.budget, m.currency)} budget, ${cash(m.gross, m.currency)} gross`).join(" · ")}.</p> : null}
      <p className="kg-cap">An event counts here when its contact days or attribution window touch the quarter; its value is the whole event's.</p>
    </section>
  );
}

function valueNote(m: BoardMoney, b: Board): string {
  if (!b.outcomes_fed || m.gross === null) return "No outcomes have loaded yet";
  if (b.basis === "gross") return m.incremental === null ? "Incremental withheld" : `${money(m.incremental, m.currency)} incremental`;
  if (m.incremental === null) return `Withheld for ${count(m.withheld_events)} ${m.withheld_events === 1 ? "event" : "events"} · ${money(m.gross, m.currency)} gross`;
  return `${money(m.gross, m.currency)} gross`;
}

function Tile({ label, figure, note }: { label: string; figure: string; note: string }) {
  return (
    <div className="kg-card">
      <div className="kg-eyebrow">{label}</div>
      <div className="kg-fig">{figure}</div>
      {note ? <p className="kg-cap">{note}</p> : null}
    </div>
  );
}

/** A list row's figures from the board: funnel counters, return and any withheld value. */
export function BoardFigures({ row }: { row: BoardRow }) {
  if (!row.events_in_play) return <p className="kg-cap">No event in play this quarter</p>;
  const m = row.money[0];
  return (
    <>
      <p className="kg-cap">
        {row.contacted === null ? "Contacts not fed" : `${count(row.contacted)} contacted`} · {row.converted === null ? "outcomes not fed" : `${count(row.converted)} converted`}
        {row.winbacks ? ` · ${count(row.winbacks.confirmed)} win-backs confirmed, ${count(row.winbacks.provisional)} provisional` : ""}
      </p>
      {m && m.withheld_events ? <Chip tone="warn">Value withheld · low confidence</Chip> : m && m.roi !== null ? <Chip>Return {percent(m.roi)}</Chip> : null}
    </>
  );
}

/** Attributed value against source totals, and what each event won and lost (PRD CM-20). */
export function ReconciliationPanel({ campaign, reconciliation }: { campaign: Campaign; reconciliation: QueryState<Reconciliation> }) {
  if (reconciliation.status === "loading") {
    return (
      <Loading label="Loading the reconciliation">
        <Skeleton height={96} />
      </Loading>
    );
  }
  if (reconciliation.status === "error") return <p className="kg-cap">The reconciliation could not be loaded just now.</p>;
  const r = reconciliation.data;
  const name = (id: string) => {
    const e = campaign.events.find((x) => x.event_id === id);
    return e ? `Event ${e.sequence_no} · ${e.event_name}` : "Another campaign's event";
  };
  return (
    <section className="kg-card kg-stack" aria-labelledby="reconciliation">
      <h2 id="reconciliation" className="kg-section">
        Reconciliation
      </h2>
      {r.span_start === null || r.span_end === null ? (
        <p className="kg-cap">Nothing is published yet, so there is nothing to reconcile.</p>
      ) : !r.lines.length ? (
        <p className="kg-cap">No outcome of the objective's metrics has loaded for {formatDate(r.span_start)} – {formatDate(r.span_end)}.</p>
      ) : (
        <>
          <p className="kg-cap">
            Every outcome from {formatDate(r.span_start)} to {formatDate(r.span_end)}, the days this campaign's events can attribute, is credited to this campaign, to another, or to none.
            {r.invariant_holds ? " No outcome is credited more than its value." : ""}
          </p>
          <div className="kg-table-wrap">
            <table className="kg-table">
              <caption className="sr-only">Source totals against attributed value</caption>
              <thead>
                <tr>
                  <th scope="col">Metric</th>
                  <th scope="col" className="is-num">
                    Source total
                  </th>
                  <th scope="col" className="is-num">
                    This campaign
                  </th>
                  <th scope="col" className="is-num">
                    Other campaigns
                  </th>
                  <th scope="col" className="is-num">
                    No campaign
                  </th>
                </tr>
              </thead>
              <tbody>
                {r.lines.map((l) => (
                  <tr key={`${l.metric_code}-${l.currency ?? ""}`}>
                    <th scope="row">
                      {l.metric_code}
                      {l.currency ? ` (${l.currency})` : ""}
                      <span className="kg-cap"> · {count(l.source_outcomes)} outcomes</span>
                    </th>
                    <td className="is-num">{amount(l.source_total, l.currency)}</td>
                    <td className="is-num">{amount(l.credited_here, l.currency)}</td>
                    <td className="is-num">{amount(l.credited_elsewhere, l.currency)}</td>
                    <td className="is-num">{amount(l.unattributed, l.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="kg-stack" style={{ listStyle: "none", padding: 0, margin: 0 }} aria-label="By event">
            {r.events.map((e) => (
              <li key={e.event_id}>
                <b>{name(e.event_id)}</b>
                <span className="kg-cap">
                  {" "}
                  credited {count(e.credited_outcomes)} {e.credited_outcomes === 1 ? "outcome" : "outcomes"}, {Number(e.credited).toLocaleString("en-GB", { maximumFractionDigits: 2 })}
                  {e.lost_outcomes ? `; lost ${count(e.lost_outcomes)} worth ${Number(e.lost).toLocaleString("en-GB", { maximumFractionDigits: 2 })} to other events` : ""}
                  {e.held_out_outcomes ? `; ${count(e.held_out_outcomes)} from its control group` : ""}
                  {rulesText(e.by_rule)}
                </span>
              </li>
            ))}
          </ul>
          {r.contaminated_total ? (
            <details>
              <summary>
                {count(r.contaminated_total)} {r.contaminated_total === 1 ? "customer's baseline was" : "customers' baselines were"} contaminated, so incremental value is withheld
              </summary>
              <ul aria-label="Contaminated baselines">
                {r.contaminated.map((c) => (
                  <li key={`${c.event_id}-${c.customer_ref}`}>
                    {c.customer_ref} in {name(c.event_id)}
                    {c.contaminated_by ? `, reached earlier by ${name(c.contaminated_by)}` : ""}
                  </li>
                ))}
              </ul>
              {r.contaminated_total > r.contaminated.length ? <p className="kg-cap">The first {count(r.contaminated.length)} are listed.</p> : null}
            </details>
          ) : null}
        </>
      )}
    </section>
  );
}

function amount(value: string, currency: string | null): string {
  return currency ? money(value, currency) : Number(value).toLocaleString("en-GB", { maximumFractionDigits: 2 });
}

function rulesText(byRule: Reconciliation["events"][number]["by_rule"]): string {
  const decided = (Object.entries(byRule) as [string, number][]).filter(([rule]) => rule !== "single" && rule !== "holdout");
  if (!decided.length) return "";
  return `; collisions settled by ${decided.map(([rule, n]) => `${RULES[rule as Rule]} (${count(n)})`).join(", ")}`;
}
