import { Fragment, useEffect, useRef, useState } from "react";

import { invoke } from "../../api/client";
import { useQuery } from "../../api/useAction";
import { Chip, EmptyState, ErrorPanel, GradeBanner, GradePill, Loading, MetricCard, Notice, SelectField, TableSkeleton, TextField, CardSkeleton } from "../../components";
import { currentPeriod, formatDate, formatDecimal, formatPeriod } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";
import { Conversation, HistoryView, QueryQueue } from "./Conversation";
import {
  STATE_WHY,
  STATE_WORDS,
  TARGET_TYPE_WORDS,
  bandIndex,
  bannerBands,
  figure,
  footing,
  num,
  percent,
  points,
  toneFor,
  type Card,
  type History,
  type MetricRow,
  type Thread,
} from "./model";

function monthValue(periodKey: string): string {
  return `${periodKey.slice(0, 4)}-${periodKey.slice(4)}`;
}

/** Scorecards: one person's month, standing before detail (Design Brief §5.1; App Flow §6). */
export function ScorecardPage() {
  const me = useMe();
  const blocked = me.no_access.find((n) => n.page_key === "scorecards");
  const [period, setPeriod] = useState(currentPeriod());
  const [team] = useQuery("scorecard.period.list", { period_key: period, profile_code: null, limit: 500 });
  const own = me.user.subject_id;
  const [chosen, setChosen] = useState<string | null>(null);
  const others = team.status === "ready" ? team.data.rows.filter((r) => r.subject_id !== own) : [];
  const subject = chosen ?? own ?? (team.status === "ready" ? (team.data.rows[0]?.subject_id ?? null) : null);
  const canResolve = me.permissions.includes("scorecard.query.resolve");

  if (blocked) {
    return (
      <Page title="Scorecards">
        <EmptyState kind="no-access" title="You cannot see scorecards yet" ask={blocked.ask}>
          {blocked.missing}
        </EmptyState>
      </Page>
    );
  }

  return (
    <Page title="Scorecards">
      <div className="kg-sechead">
        <span className="kg-spacer" />
        {others.length ? (
          <div style={{ minWidth: 240 }}>
            <SelectField
              label="Person"
              value={subject ?? ""}
              onChange={(e) => setChosen(e.target.value)}
              options={[
                ...(own ? [{ value: own, label: "My scorecard" }] : []),
                ...others.map((r) => ({ value: r.subject_id, label: `${r.full_name} · ${r.staff_no}` })),
              ]}
            />
          </div>
        ) : null}
        <div style={{ maxWidth: 200 }}>
          <TextField label="Month" type="month" value={monthValue(period)} onChange={(e) => e.target.value && setPeriod(e.target.value.replace("-", ""))} />
        </div>
      </div>
      {subject ? (
        <SubjectScorecard key={`${subject}-${period}`} subjectId={subject} period={period} />
      ) : team.status === "loading" ? (
        <section className="kg-card">
          <Loading label="Finding scorecards">
            <TableSkeleton rows={4} columns={6} />
          </Loading>
        </section>
      ) : team.status === "error" ? (
        <section className="kg-card">
          <ErrorPanel error={team.error} what="Scorecards" />
        </section>
      ) : (
        <EmptyState kind="none" title={`No scorecards for ${formatPeriod(period, true)}`}>
          Nobody you can see holds a role with a scorecard this month. Your own scorecard appears once your account is linked to a person in the hierarchy.
        </EmptyState>
      )}
      {canResolve ? <QueryQueue /> : null}
    </Page>
  );
}

function SubjectScorecard({ subjectId, period }: { subjectId: string; period: string }) {
  const [card, reloadCard] = useQuery("scorecard.compute", { subject_id: subjectId, period_key: period });
  const [thread, reloadThread] = useQuery("scorecard.interaction.list", { subject_id: subjectId, period_key: period });
  const [history] = useQuery("scorecard.history", { subject_id: subjectId, to_period: period, periods: 12 });

  if (card.status === "loading") {
    return (
      <Loading label="Loading the scorecard">
        <div className="kg-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(168px, 1fr))" }}>
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
        <section className="kg-card">
          <TableSkeleton rows={6} columns={8} />
        </section>
      </Loading>
    );
  }
  if (card.status === "error") {
    return (
      <section className="kg-card">
        <ErrorPanel error={card.error} retry={reloadCard} what="The scorecard" />
      </section>
    );
  }
  return (
    <ScorecardView
      card={card.data}
      thread={thread.status === "ready" ? thread.data : null}
      history={history.status === "ready" ? history.data : null}
      onChanged={() => {
        reloadCard();
        reloadThread();
      }}
    />
  );
}

/** Everything below the pickers, from data: what stories and tests render. */
export function ScorecardView({
  card,
  thread,
  history,
  onChanged = () => {},
}: {
  card: Card;
  thread: Thread | null;
  history: History | null;
  onChanged?: () => void;
}) {
  const [open, setOpen] = useState<{ code: string; facet: Facet } | null>(null);
  const periodLabel = formatPeriod(card.period_key, true);
  const who = `${card.full_name} · ${card.staff_no}`;

  if (card.phase === "future") {
    return (
      <EmptyState kind="awaiting" title={`${periodLabel} has not started`}>
        {who}&apos;s scorecard for {periodLabel} fills in once the month begins and its data arrives.
      </EmptyState>
    );
  }
  if (!card.assigned) {
    return (
      <EmptyState kind="none" title={`No scorecard for ${who} in ${periodLabel}`}>
        {card.statement} Roles and their effective dates are kept under Administer → Hierarchy.
      </EmptyState>
    );
  }
  if (card.metrics.length === 0) {
    return (
      <EmptyState kind="awaiting" title={`Profile ${card.profile_code} has no metrics yet`} ask="an Admin, under Administer → Scorecard setup">
        {who} holds a role on the {card.profile_code} profile, but no scorecard metrics are assigned to it for {periodLabel}.
      </EmptyState>
    );
  }

  const bands = bannerBands(card.bands);
  const current = card.band ? card.bands.findIndex((b) => b.label === card.band?.label) : -1;
  const graded = num(card.graded_score);
  const foot = footing(card);
  const opened = open ? card.metrics.find((m) => m.metric_code === open.code) : undefined;

  return (
    <div className="kg-stack">
      <div className="kg-row" style={{ gap: 10, flexWrap: "wrap" }}>
        <h2 className="kg-section" style={{ margin: 0 }}>
          {who}
        </h2>
        <span className="kg-cap">Profile {card.profile_code}</span>
        <span className="kg-spacer" />
        <Chip tone={foot.tone === "warn" ? "warn" : card.source === "snapshot" ? "up" : "info"}>{foot.text}</Chip>
        <ExportButton card={card} />
      </div>

      {card.restated_at ? (
        <Notice tone="warn" title={`Restated on ${formatDate(card.restated_at)}.`}>
          {card.restatement_reason} Earlier versions stay on record; this is the current one.
        </Notice>
      ) : null}
      {card.source === "live" && card.not_reported > 0 ? (
        <Notice tone="info" title={`${card.not_reported} metric(s) awaiting data.`}>
          They are left out of the total, not counted as zero, and the grade is read from the metrics that have reported.
        </Notice>
      ) : null}

      <GradeBanner
        bands={bands}
        current={current >= 0 ? current : null}
        score={graded === null ? null : graded * 100}
        periodLabel={periodLabel}
        status={foot.text}
        detail={`${points(card.total_score)} of ${figure(card.total_cap, 0)} points · ${card.statement}`}
        emptyReason={card.statement}
      />

      <SummaryCards card={card} history={history} />

      <section className="kg-card" aria-labelledby="matrix-heading">
        <div className="kg-sechead">
          <div>
            <h2 id="matrix-heading" className="kg-section">
              Metric detail
            </h2>
            <p className="kg-cap">Select any figure to see where it came from.</p>
          </div>
        </div>
        <Matrix card={card} onOpen={(code, facet) => setOpen({ code, facet })} />
        <p className="kg-cap" style={{ marginTop: 10 }}>
          {card.statement}
        </p>
      </section>

      {opened && open ? <ProvenancePanel card={card} metric={opened} facet={open.facet} onClose={() => setOpen(null)} /> : null}

      <Conversation card={card} thread={thread} onChanged={onChanged} />

      <HistoryView history={history} bands={card.bands} />
    </div>
  );
}

function SummaryCards({ card, history }: { card: Card; history: History | null }) {
  const onTarget = card.metrics.filter((m) => (num(m.pct_achieved) ?? 0) >= 1).length;
  const at = history ? history.points.findIndex((p) => p.period_key === card.period_key) : -1;
  const before = at > 0 && history ? num(history.points[at - 1].graded_score) : null;
  const now = num(card.graded_score);
  const delta = before !== null && now !== null ? (now - before) * 100 : undefined;
  return (
    <div className="kg-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(168px, 1fr))" }}>
      <MetricCard label="Total score" value={points(card.total_score)} target={`of ${figure(card.total_cap, 0)} points`} delta={delta} deltaSuffix=" pts" />
      <MetricCard label="Score achievement" value={percent(card.achievement_pct)} state={card.achievement_pct === null ? "awaiting" : "ready"} reason="Nothing has been scored yet." />
      <MetricCard label="Metrics at or above target" value={`${onTarget} / ${card.metrics_total}`} />
      <MetricCard
        label="Cycle progress"
        value={card.months_elapsed !== null && card.cycle_months !== null ? `${card.months_elapsed} / ${card.cycle_months}` : null}
        reason="This role has no performance cycle."
      />
    </div>
  );
}

type Facet = "target" | "actual" | "pct" | "weight" | "score" | "cap";

const COLUMNS: { facet: Facet; header: string }[] = [
  { facet: "target", header: "Target" },
  { facet: "actual", header: "Actual" },
  { facet: "pct", header: "% Achieved" },
  { facet: "weight", header: "Weight" },
  { facet: "score", header: "Score" },
  { facet: "cap", header: "Cap" },
];

function cell(m: MetricRow, facet: Facet): string {
  switch (facet) {
    case "target":
      return figure(m.target_value, m.decimal_places);
    case "actual":
      return figure(m.actual_value, m.decimal_places);
    case "pct":
      return percent(m.pct_achieved);
    case "weight":
      return formatDecimal(m.weight);
    case "score":
      return points(m.score);
    case "cap":
      return formatDecimal(m.cap);
  }
}

function groups(metrics: MetricRow[]): { title: string; rows: MetricRow[] }[] {
  const out: { title: string; rows: MetricRow[] }[] = [];
  for (const m of metrics) {
    const title = m.path[0] ?? "Not placed on the scorecard structure";
    const found = out.find((g) => g.title === title);
    if (found) found.rows.push(m);
    else out.push({ title, rows: [m] });
  }
  return out;
}

export function MetricStatus({ card, metric }: { card: Card; metric: MetricRow }) {
  if (metric.score === null) {
    return <Chip tone={metric.state === "excluded" ? "flat" : "warn"}>{STATE_WORDS[metric.state] ?? metric.state}</Chip>;
  }
  const i = bandIndex(card.bands, num(metric.pct_achieved));
  if (i === null) return <Chip tone="flat">{STATE_WORDS[metric.state] ?? metric.state}</Chip>;
  return <GradePill tone={toneFor(i, card.bands.length)} label={card.bands[i].label} />;
}

function Matrix({ card, onOpen }: { card: Card; onOpen: (code: string, facet: Facet) => void }) {
  const weight = card.metrics.reduce((sum, m) => sum + (num(m.weight) ?? 0), 0);
  const banded = card.band ? card.bands.findIndex((b) => b.label === card.band?.label) : -1;
  return (
    <div className="kg-table-wrap">
      <table className="kg-table kg-matrix">
        <caption className="sr-only">
          Metric detail for {card.full_name}, {formatPeriod(card.period_key, true)}
        </caption>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            {COLUMNS.map((c) => (
              <th key={c.facet} scope="col" className="is-num">
                {c.header}
              </th>
            ))}
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {groups(card.metrics).map((g) => (
            <Fragment key={g.title}>
              <tr className="kg-grouphead">
                <th scope="colgroup" colSpan={8}>
                  {g.title}
                </th>
              </tr>
              {g.rows.map((m) => (
                <tr key={m.metric_code}>
                  <th scope="row">
                    <span className="kg-mname">{m.display_name}</span>
                    <span className="kg-msub">
                      {m.unit}
                      {m.target_currency ? ` · ${m.target_currency}` : ""} · {m.direction === "lower_is_better" ? "lower is better" : "higher is better"}
                      {m.target_type && m.target_type !== "monthly" ? ` · ${TARGET_TYPE_WORDS[m.target_type] ?? m.target_type} target` : ""}
                      {m.overrides.length ? <b className="kg-msub__flag"> · override applied</b> : null}
                    </span>
                  </th>
                  {COLUMNS.map((c) => (
                    <td key={c.facet} className="is-num">
                      <button
                        type="button"
                        className="kg-figbtn num"
                        aria-label={`${c.header} for ${m.display_name}: ${cell(m, c.facet)}. Show where it came from`}
                        onClick={() => onOpen(m.metric_code, c.facet)}
                      >
                        {cell(m, c.facet)}
                      </button>
                    </td>
                  ))}
                  <td>
                    <MetricStatus card={card} metric={m} />
                  </td>
                </tr>
              ))}
            </Fragment>
          ))}
          <tr className="kg-total">
            <th scope="row">Total</th>
            <td className="is-num">–</td>
            <td className="is-num">–</td>
            <td className="is-num num">{percent(card.achievement_pct)}</td>
            <td className="is-num num">{figure(String(weight), 0)}</td>
            <td className="is-num num">{points(card.total_score)}</td>
            <td className="is-num num">{figure(card.total_cap, 0)}</td>
            <td>{card.band && banded >= 0 ? <GradePill tone={toneFor(banded, card.bands.length)} label={card.band.label} /> : <Chip tone="flat">Not graded</Chip>}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

const FACET_TITLE: Record<Facet, string> = {
  target: "Target",
  actual: "Actual",
  pct: "% achieved",
  weight: "Weight",
  score: "Score",
  cap: "Cap",
};

/** Where one figure came from (PRD SC-8): target, overrides, actual, arithmetic, period. */
export function ProvenancePanel({ card, metric: m, facet, onClose }: { card: Card; metric: MetricRow; facet: Facet; onClose: () => void }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, [m.metric_code, facet]);
  const dp = m.decimal_places;
  const lower = m.direction === "lower_is_better";
  const fx = m.fx_rate !== null && m.actual_currency && m.target_currency && m.actual_currency !== m.target_currency;
  return (
    <section ref={ref} tabIndex={-1} className="kg-card kg-prov" aria-labelledby="prov-heading">
      <div className="kg-sechead">
        <div>
          <h2 id="prov-heading" className="kg-section">
            {FACET_TITLE[facet]}: {m.display_name}
          </h2>
          <p className="kg-cap">
            {formatPeriod(card.period_key, true)} · {card.source === "snapshot" ? `frozen at close, version ${card.snapshot_version}` : `computed now; ${footing(card).text.toLowerCase()}`}
          </p>
        </div>
        <span className="kg-spacer" />
        <button type="button" className="kg-btn" onClick={onClose}>
          Close
        </button>
      </div>
      <dl className="kg-prov__list">
        <div data-on={facet === "target" || undefined}>
          <dt>Target</dt>
          <dd>
            {m.target_value === null ? (
              STATE_WHY.no_target
            ) : (
              <>
                <span className="num">{figure(m.target_value, dp)}</span>
                {m.target_currency ? ` ${m.target_currency}` : ""} for the month.
                {m.base_target !== null && m.base_target !== m.target_value ? (
                  <>
                    {" "}
                    Stored as <span className="num">{figure(m.base_target, dp)}</span> ({TARGET_TYPE_WORDS[m.target_type ?? ""] ?? m.target_type}), adjusted to month{" "}
                    {card.months_elapsed} of a {card.cycle_months}-month cycle.
                  </>
                ) : null}{" "}
                <span className="kg-cap">
                  Target version {m.target_version}, set at {m.target_scope ?? "–"} level.
                </span>
              </>
            )}
          </dd>
        </div>
        <div data-on={facet === "actual" || undefined}>
          <dt>Actual</dt>
          <dd>
            {m.actual_value === null ? (
              STATE_WHY[m.state] ?? "Not reported."
            ) : (
              <>
                <span className="num">{figure(m.actual_value, dp)}</span>
                {m.target_currency ? ` ${m.target_currency}` : ""}.
                {fx ? (
                  <>
                    {" "}
                    Reported as <span className="num">{figure(m.reported_actual, dp)}</span> {m.actual_currency}, converted at the month&apos;s average rate{" "}
                    <span className="num">{figure(m.fx_rate, 6)}</span>.
                  </>
                ) : null}{" "}
                <span className="kg-cap">{m.run_id ? `Loaded by feed run ${m.run_id.slice(0, 8)}.` : "Set by an override, not a feed."}</span>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Overrides</dt>
          <dd>
            {m.overrides.length === 0 ? (
              "None applied. The figure is the published target and the reported actual."
            ) : (
              <ul className="kg-prov__overrides">
                {m.overrides.map((o) => (
                  <li key={o.override_id}>
                    <b>{o.change_type.replace("_", " ")}</b> set to <span className="num">{o.text ?? figure(o.value, dp)}</span> at {o.scope_type} level ({o.scope_code}).{" "}
                    <span className="kg-cap">Reason: {o.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>
        <div data-on={["pct", "weight", "score", "cap"].includes(facet) || undefined}>
          <dt>Arithmetic</dt>
          <dd>
            {m.score === null ? (
              <>
                {STATE_WHY[m.state] ?? "Not scored."}
                {m.exclusion_reason ? ` Reason: ${m.exclusion_reason}` : ""}
              </>
            ) : (
              <>
                % achieved = {lower ? "target ÷ actual" : "actual ÷ target"} = <span className="num">{percent(m.pct_achieved, 2)}</span>
                {lower ? " (lower is better)" : ""}. Score = min(% achieved × weight <span className="num">{figure(m.weight, 0)}</span>, cap{" "}
                <span className="num">{figure(m.cap, 0)}</span>) = <b className="num">{points(m.score)}</b> points.
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Period</dt>
          <dd>
            {card.period_status} ·{" "}
            {card.source === "snapshot"
              ? `read from the frozen snapshot (version ${card.snapshot_version}); later source changes do not move it.`
              : "computed from the latest loaded data each time you open it."}
          </dd>
        </div>
      </dl>
    </section>
  );
}

function ExportButton({ card }: { card: Card }) {
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");
  const download = async () => {
    setState("busy");
    try {
      const out = await invoke("scorecard.export.pdf", { subject_id: card.subject_id, period_key: card.period_key });
      const bytes = Uint8Array.from(atob(out.content_base64), (c) => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: out.media_type }));
      const a = document.createElement("a");
      a.href = url;
      a.download = out.filename;
      a.click();
      URL.revokeObjectURL(url);
      setState("idle");
    } catch {
      setState("error");
    }
  };
  return (
    <>
      <button type="button" className="kg-btn" disabled={state === "busy"} onClick={() => void download()}>
        {state === "busy" ? "Preparing PDF…" : "Export PDF"}
      </button>
      {state === "error" ? (
        <span role="alert" className="kg-field-error">
          The PDF could not be made. Try again.
        </span>
      ) : null}
    </>
  );
}
