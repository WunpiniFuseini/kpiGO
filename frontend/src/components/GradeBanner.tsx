import type { GradeTone } from "./GradePill";

export interface Band {
  label: string;
  /** Where the band starts, in the scorecard's score units. */
  from: number;
  tone: GradeTone;
}

export interface GradeBannerProps {
  /** Ordered lowest to highest, from the client's rating_band configuration. */
  bands: Band[];
  /** The band the subject is in, or null when nothing has been scored yet. */
  current: number | null;
  score: number | null;
  periodLabel: string;
  /** Open period: the grade can still move. */
  provisional?: boolean;
  restatedOn?: string | null;
  /** Why there is no grade, when current is null. */
  emptyReason?: string;
}

/**
 * Standing before detail (Design Brief §5.1): the grade, where it sits on the
 * client's scale, and how far the next band is. Sits above the scorecard matrix.
 */
export function GradeBanner({
  bands,
  current,
  score,
  periodLabel,
  provisional = false,
  restatedOn = null,
  emptyReason = "No metrics have been scored for this period yet.",
}: GradeBannerProps) {
  const band = current === null ? null : bands[current];
  const next = current === null ? null : bands[current + 1];
  const status = provisional ? "Provisional: the period is open" : restatedOn ? `Restated on ${restatedOn}` : "Final";

  if (band === null || band === undefined || score === null) {
    return (
      <section className="kg-banner kg-banner--none" aria-label={`Grade for ${periodLabel}`}>
        <div className="kg-banner__bar" />
        <div className="kg-banner__body">
          <div>
            <div className="kg-cap">{periodLabel}</div>
            <div className="kg-banner__grade">Not graded yet</div>
            <div className="kg-cap">{emptyReason}</div>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="kg-banner" data-grade={band.tone} aria-label={`Grade for ${periodLabel}`}>
      <div className="kg-banner__bar" />
      <div className="kg-banner__body">
        <div>
          <div className="kg-cap">
            {periodLabel} · {status}
          </div>
          <div className="kg-banner__grade">{band.label}</div>
          <div className="kg-cap">
            Score <span className="num">{score.toFixed(1)}</span>
          </div>
        </div>
        <ol className="kg-scale" aria-label="Rating bands">
          {bands.map((b, i) => (
            <li
              key={b.label}
              data-grade={b.tone}
              aria-current={i === current ? "true" : undefined}
            >
              <i />
              <small>
                {b.label}
                <span className="sr-only">{i === current ? ", current band" : ""}</span>
                <br />
                <span className="num">from {b.from}</span>
              </small>
            </li>
          ))}
        </ol>
        <div className="kg-banner__next">
          {next ? (
            <>
              <b className="num">{(next.from - score).toFixed(1)}</b>
              <span className="kg-cap">points to {next.label}</span>
            </>
          ) : (
            <>
              <b>Top band</b>
              <span className="kg-cap">No higher band on this scale</span>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
