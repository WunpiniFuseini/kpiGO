import { useState } from "react";

import type { Input, Output } from "../../api/actions";
import { useQuery } from "../../api/useAction";
import { CardSkeleton, EmptyState, ErrorPanel, Loading, MetricCard, RankedList, SelectField, Skeleton, type GradeTone, type RankedItem } from "../../components";
import { formatDate, formatValue } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";
import { MatrixSection } from "./Matrix";
import { ProductLinesQuick } from "./ProductLines";
import { PresetSectionView, type Preset } from "./Sections";
import { VisibilityNote, VisibilityQuick } from "./Visibility";

export type Leaderboard = Output<"agent.leaderboard">;
export type AgentPace = Output<"agent.pace">;
type Row = Leaderboard["rows"][number];
type RankKey = NonNullable<Leaderboard["rank_by"]>;
type Product = Input<"agent.leaderboard">["product"];
type CohortType = NonNullable<Input<"agent.leaderboard">["cohort_type"]>;

const PRODUCTS: { value: Product; label: string }[] = [
  { value: "agent_sales", label: "Sales" },
  { value: "agent_service", label: "Service" },
];

const COHORT_TYPES: { value: CohortType; label: string }[] = [
  { value: "profile", label: "Profile" },
  { value: "branch", label: "Branch" },
  { value: "region", label: "Region" },
  { value: "cohort", label: "Custom cohort" },
  { value: "all", label: "Everyone" },
];

const STATE_TEXT: Record<string, string> = {
  not_reported: "Not reported",
  no_target: "No target",
  no_fx_rate: "No exchange rate",
  not_started: "Nothing expected yet",
};

function n(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const v = Number(value);
  return Number.isFinite(v) ? v : null;
}

function tone(position: number | undefined): GradeTone | undefined {
  return position === undefined ? undefined : (Math.min(4, Math.max(1, position)) as GradeTone);
}

function figure(value: string | null, key: Pick<RankKey, "unit" | "decimal_places">, currency?: string | null): string {
  const v = n(value);
  if (v === null) return "–";
  return formatValue(v, { unit: key.unit ?? "count", decimals: key.decimal_places, currency: currency ?? "" });
}

function percent(value: string | null): string | null {
  const v = n(value);
  return v === null ? null : `${Math.round(v * 100)}%`;
}

function cohortLabel(type: string): string {
  return COHORT_TYPES.find((c) => c.value === type)?.label.toLowerCase() ?? type;
}

/** Agent Performance: the Sales or Service preset's sections, in order (App Flow §4.1, PRD AP-4). */
export function AgentPerformancePage() {
  const me = useMe();
  const blocked = me.no_access.find((x) => x.page_key === "agent_performance");
  const [product, setProduct] = useState<Product>("agent_sales");
  const [preset, reload] = useQuery("agent.preset", { product });

  return (
    <Page title="Agent Performance">
      {blocked ? (
        <EmptyState kind="no-access" title="You cannot see Agent Performance" ask={blocked.ask}>
          {blocked.missing}
        </EmptyState>
      ) : (
        <div className="kg-stack">
          <div style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
            <div className="kg-seg" role="group" aria-label="Module">
              {PRODUCTS.map((p) => (
                <button key={p.value} type="button" aria-pressed={product === p.value} onClick={() => setProduct(p.value)}>
                  {p.label}
                </button>
              ))}
            </div>
            <span className="kg-spacer" />
            {me.permissions.includes("agent.config.manage") ? <VisibilityQuick key={product} product={product} /> : null}
            {me.permissions.includes("product_line.manage") ? <ProductLinesQuick /> : null}
          </div>
          {preset.status === "loading" ? (
            <Loading label="Loading Agent Performance">
              <div className="kg-grid">
                {[0, 1, 2, 3].map((i) => (
                  <CardSkeleton key={i} />
                ))}
              </div>
              <Skeleton height={220} style={{ marginTop: 16 }} />
            </Loading>
          ) : preset.status === "error" ? (
            <ErrorPanel error={preset.error} retry={reload} what="Agent Performance" />
          ) : (
            <PresetView key={product} product={product} preset={preset.data} />
          )}
        </div>
      )}
    </Page>
  );
}

/** The preset's sections, each reading its own action. */
export function PresetView({ product, preset }: { product: Product; preset: Preset }) {
  return (
    <>
      <VisibilityNote visibility={preset.visibility} />
      {preset.sections.map((section) =>
        section.kind === "leaderboard" ? (
          <LeaderboardSection key={section.key} product={product} />
        ) : section.kind === "matrix" ? (
          <MatrixSection key={section.key} product={product} title={section.title} caption={section.caption} />
        ) : (
          <PresetSectionView key={section.key} product={product} section={section} />
        ),
      )}
    </>
  );
}

/** Section: the leaderboard and its summary cards (PRD AP-3). */
function LeaderboardSection({ product }: { product: Product }) {
  const [cohortType, setCohortType] = useState<CohortType | undefined>(undefined);
  const [cohortCode, setCohortCode] = useState<string | undefined>(undefined);
  const [rankBy, setRankBy] = useState<string | undefined>(undefined);
  const [selected, setSelected] = useState<string | null>(null);
  const [data, reload] = useQuery("agent.leaderboard", {
    product,
    ...(cohortType ? { cohort_type: cohortType } : {}),
    ...(cohortCode ? { cohort_code: cohortCode } : {}),
    ...(rankBy ? { rank_by: rankBy } : {}),
  });
  const board = data.status === "ready" ? data.data : null;

  return (
    <>
      <section className="kg-card" aria-labelledby="leaderboard-heading">
        <div className="kg-sechead">
          <div>
            <h2 id="leaderboard-heading" className="kg-section">
              Leaderboard
            </h2>
            <p className="kg-cap">{board ? windowText(board) : "Ranked within a peer group, to date."}</p>
          </div>
          <span className="kg-spacer" />
          {board ? (
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <div style={{ width: 150 }}>
                <SelectField
                  label="Compare within"
                  value={board.cohort_type}
                  onChange={(e) => {
                    setCohortType(e.target.value as CohortType);
                    setCohortCode(undefined);
                  }}
                  options={COHORT_TYPES}
                />
              </div>
              {board.cohorts.length > 1 ? (
                <div style={{ width: 180 }}>
                  <SelectField
                    label={COHORT_TYPES.find((c) => c.value === board.cohort_type)?.label ?? "Cohort"}
                    value={board.cohort?.code ?? ""}
                    onChange={(e) => setCohortCode(e.target.value)}
                    options={board.cohorts.map((c) => ({ value: c.code, label: `${c.name} (${c.agents})` }))}
                  />
                </div>
              ) : null}
              {board.rank_options.length ? (
                <div style={{ width: 180 }}>
                  <SelectField
                    label="Rank by"
                    value={board.rank_by?.key ?? ""}
                    onChange={(e) => setRankBy(e.target.value)}
                    options={board.rank_options.map((o) => ({ value: o.key, label: o.display_name }))}
                  />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        {data.status === "loading" ? (
          <Loading label="Loading the leaderboard">
            <div className="kg-grid">
              {[0, 1, 2, 3].map((i) => (
                <CardSkeleton key={i} />
              ))}
            </div>
            <Skeleton height={220} style={{ marginTop: 16 }} />
          </Loading>
        ) : data.status === "error" ? (
          <ErrorPanel error={data.error} retry={reload} what="The leaderboard" />
        ) : (
          <LeaderboardView board={data.data} onSelect={(id) => setSelected(id)} />
        )}
      </section>
      {selected && board ? <AgentDetail product={product} subjectId={selected} asOf={board.window.as_of} onClose={() => setSelected(null)} /> : null}
    </>
  );
}

function windowText(b: Leaderboard): string {
  const w = b.window;
  const span = w.kind === "week" ? "Week to date" : "Month to date";
  return `${span} to ${formatDate(w.as_of)}, working day ${w.working_day} of ${w.working_days}.`;
}

function item(r: Row, key: RankKey, currency: string | null): RankedItem {
  const composite = key.key === "composite";
  const place = [r.agent.branch_code, r.agent.region_code].filter(Boolean).join(" · ");
  return {
    id: r.agent.subject_id,
    name: r.agent.full_name,
    sub: place || r.agent.profile_code,
    value: composite ? (percent(r.value) ?? "–") : figure(r.value, key, currency),
    pace: n(r.pace),
    tone: tone(r.band?.ramp_position),
    band: r.band?.label,
    noPace: STATE_TEXT[r.state] ?? "No pace yet",
    isYou: r.is_you,
    rank: r.rank,
  };
}

/** The summary cards and the ranked list, from data: what stories and tests render. */
export function LeaderboardView({ board, onSelect }: { board: Leaderboard; onSelect?: (subjectId: string) => void }) {
  const key = board.rank_by;
  if (!key || !board.rank_options.length) {
    return (
      <EmptyState kind="none" title="Nobody is measured in this module yet">
        No profile carries a {board.product === "agent_service" ? "Service" : "Sales"} metric on {formatDate(board.window.as_of)}. An Admin binds metrics to the module in the Metric registry and assigns them to a profile under Scorecard setup.
      </EmptyState>
    );
  }
  if (!board.rows.length) {
    return (
      <EmptyState kind="none" title={board.cohort ? `Nobody in ${board.cohort.name} is ranked on ${key.display_name}` : `No ${cohortLabel(board.cohort_type)} has agents`}>
        {board.cohort_type === "cohort" && !board.cohorts.length
          ? "No custom cohort has members on this day. An Admin sets them up under Agent Performance settings, or compare within a profile, branch or region instead."
          : `None of these agents' profiles carries ${key.display_name}. Rank by another metric, or by overall pace.`}
      </EmptyState>
    );
  }
  const s = board.summary;
  const unranked = board.rows.filter((r) => r.rank === null).length;
  const hidden = board.total - board.rows.length;
  const additive = s.value_to_date !== null;
  return (
    <div className="kg-stack">
      <div className="kg-grid">
        <MetricCard label="Agents on pace" value={`${s.on_pace} of ${s.agents}`} small />
        {additive ? (
          <>
            <MetricCard label={`${key.display_name} to date`} value={figure(s.value_to_date, key, s.currency_code)} target={s.target_to_date ? figure(s.target_to_date, key, s.currency_code) : null} small />
            <MetricCard label="Cohort pace" value={percent(s.pace)} reason="No target to pace against." small />
            <MetricCard
              label={`On ${formatDate(board.window.as_of)}`}
              value={s.day_value === null ? null : figure(s.day_value, key, s.currency_code)}
              reason="Nothing reported on the day."
              small
            />
          </>
        ) : null}
      </div>
      {additive && n(s.short_of_pace) ? (
        <p className="kg-cap" role="status">
          The cohort is {figure(s.short_of_pace, key, s.currency_code)} short of where the target expects it today.
        </p>
      ) : null}
      <RankedList
        label={`Leaderboard, ${board.cohort?.name ?? "everyone"}, ranked by ${key.display_name}`}
        items={board.rows.map((r) => item(r, key, s.currency_code))}
        onSelect={onSelect ? (i) => onSelect(i.id) : undefined}
      />
      <p className="kg-cap">
        {board.tiebreak ? `Ties are broken by ${board.tiebreak.display_name}; agents still level share a rank.` : "Agents level on the ranking figure share a rank."}
        {unranked ? ` ${unranked === 1 ? "One agent has" : `${unranked} agents have`} nothing reported on it yet and ${unranked === 1 ? "is" : "are"} listed unranked, not ranked on zero.` : ""}
        {hidden > 0 ? ` ${hidden} more not shown.` : ""}
      </p>
    </div>
  );
}

function AgentDetail({ product, subjectId, asOf, onClose }: { product: Product; subjectId: string; asOf: string; onClose: () => void }) {
  const [data, reload] = useQuery("agent.pace", { product, subject_id: subjectId, as_of: asOf });
  return (
    <section className="kg-card" aria-labelledby="agent-detail-heading">
      <div className="kg-sechead">
        <h2 id="agent-detail-heading" className="kg-section">
          {data.status === "ready" ? data.data.agent.full_name : "Agent"}
        </h2>
        <span className="kg-spacer" />
        <button type="button" className="kg-btn" onClick={onClose}>
          Close
        </button>
      </div>
      {data.status === "loading" ? (
        <Loading label="Loading the agent's pace">
          <Skeleton height={120} />
        </Loading>
      ) : data.status === "error" ? (
        <ErrorPanel error={data.error} retry={reload} what="This agent's pace" />
      ) : (
        <AgentPaceView pace={data.data} />
      )}
    </section>
  );
}

/** One agent's pace on every metric of their profile. */
export function AgentPaceView({ pace }: { pace: AgentPace }) {
  return (
    <div className="kg-grid">
      {pace.metrics.map((m) => (
        <MetricCard
          key={m.metric_code}
          label={m.display_name}
          value={m.actual === null ? null : figure(m.actual, m, m.currency_code)}
          target={m.target_to_date === null ? null : `${figure(m.target_to_date, m, m.currency_code)} by now${m.pace ? ` · ${percent(m.pace)} of pace` : ""}`}
          reason={STATE_TEXT[m.state] ?? undefined}
          small
        />
      ))}
    </div>
  );
}
