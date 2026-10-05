import { DeltaChip } from "./Chip";
import { FreshnessBadge, type Freshness } from "./FreshnessBadge";
import { Skeleton } from "./Skeleton";
import { Sparkline } from "./Sparkline";

export type MetricCardState = "ready" | "loading" | "stale" | "awaiting" | "quarantined" | "no-access";

export interface MetricCardProps {
  label: string;
  /** Already formatted for display; null when nothing is reported. */
  value: string | null;
  target?: string | null;
  delta?: number | null;
  higherIsBetter?: boolean;
  trend?: number[];
  state?: MetricCardState;
  asOf?: string | null;
  feed?: string;
  /** For awaiting: when the feed is due. For no-access: the missing grant. */
  reason?: string;
  small?: boolean;
}

const FRESHNESS: Partial<Record<MetricCardState, Freshness>> = {
  ready: "fresh",
  stale: "stale",
  quarantined: "quarantined",
  awaiting: "awaiting",
};

/** One headline figure with its target, movement and freshness (Design Brief §5.6). */
export function MetricCard({
  label,
  value,
  target,
  delta,
  higherIsBetter = true,
  trend,
  state = "ready",
  asOf,
  feed,
  reason,
  small = false,
}: MetricCardProps) {
  if (state === "loading") {
    return (
      <div className="kg-card" role="status" aria-busy="true">
        <span className="sr-only">Loading {label}</span>
        <div className="kg-eyebrow">{label}</div>
        <Skeleton width="60%" height={small ? 24 : 32} style={{ margin: "12px 0 8px" }} />
        <Skeleton width="40%" height={10} />
      </div>
    );
  }
  const greyed = state === "stale" || state === "awaiting" || state === "no-access";
  const figureClass = `kg-fig${small ? " kg-fig--sm" : ""}`;
  return (
    <article className={`kg-card${greyed ? " kg-card--stale" : ""}`} aria-label={label}>
      <div className="kg-row">
        <h3 className="kg-eyebrow">{label}</h3>
        {state === "ready" && delta !== undefined ? <DeltaChip value={delta} higherIsBetter={higherIsBetter} /> : null}
      </div>
      {state === "no-access" ? (
        <>
          <p className={figureClass} aria-hidden="true">
            –
          </p>
          <p className="kg-cap">{reason ?? "You have no data scope for this figure."}</p>
        </>
      ) : state === "awaiting" || value === null ? (
        <>
          <p className={figureClass} aria-hidden="true">
            –
          </p>
          <p className="kg-cap">{reason ?? "Not reported for this period."}</p>
        </>
      ) : (
        <>
          <p className={`${figureClass} num`}>{value}</p>
          {target ? (
            <p className="kg-cap">
              Target <span className="num">{target}</span>
            </p>
          ) : null}
        </>
      )}
      {trend && state !== "no-access" ? <Sparkline points={trend} /> : null}
      {FRESHNESS[state] ? (
        <div style={{ marginTop: 10 }}>
          <FreshnessBadge state={FRESHNESS[state]!} asOf={asOf} feed={feed} />
        </div>
      ) : null}
    </article>
  );
}
