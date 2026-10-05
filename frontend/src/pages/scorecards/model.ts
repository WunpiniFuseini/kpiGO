/** Shared types and arithmetic for the scorecard screens. Nothing here scores: the server did. */
import type { Output } from "../../api/actions";
import type { Band, GradeTone } from "../../components";

export type Card = Output<"scorecard.compute">;
export type MetricRow = Card["metrics"][number];
export type ScoreBand = Card["bands"][number];
export type Thread = Output<"scorecard.interaction.list">;
export type Interaction = Thread["queries"][number];
export type History = Output<"scorecard.history">;
export type QueryList = Output<"scorecard.query.list">;

export function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** A band's place on the four-step grade ramp, by rank: a client's own scale may have two to six. */
export function toneFor(index: number, count: number): GradeTone {
  if (count <= 1) return 3;
  return Math.min(4, Math.max(1, Math.round(1 + (index * 3) / (count - 1)))) as GradeTone;
}

export function bannerBands(bands: ScoreBand[]): Band[] {
  return bands.map((b, i) => ({ label: b.label, from: Math.round(Number(b.threshold) * 1000) / 10, tone: toneFor(i, bands.length) }));
}

/** The band a ratio falls in on the client's scale (threshold 1.0 = on target), or null. */
export function bandIndex(bands: ScoreBand[], ratio: number | null): number | null {
  if (ratio === null) return null;
  let found: number | null = null;
  bands.forEach((b, i) => {
    if (ratio >= Number(b.threshold)) found = i;
  });
  return found;
}

/** Score out of 1.0 as points (×100), one decimal. */
export function points(value: string | null | undefined): string {
  const n = num(value);
  return n === null ? "–" : (n * 100).toLocaleString("en-GB", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

export function percent(value: string | null | undefined, decimals = 1): string {
  const n = num(value);
  return n === null ? "–" : `${(n * 100).toLocaleString("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}%`;
}

export function figure(value: string | null | undefined, decimals: number): string {
  const n = num(value);
  return n === null ? "–" : n.toLocaleString("en-GB", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export const STATE_WORDS: Record<string, string> = {
  scored: "Scored",
  zero_actual: "Scored at zero",
  not_reported: "Awaiting data",
  no_target: "No target",
  no_fx_rate: "No FX rate",
  excluded: "Excluded",
};

/** Why a metric has no score, in a sentence the subject can act on. */
export const STATE_WHY: Record<string, string> = {
  not_reported: "No figure has been loaded for this month yet. It is left out of the total (not counted as zero) until it arrives.",
  no_target: "No published target covers this metric for this month, so it cannot be scored.",
  no_fx_rate: "The actual is in a different currency from the target and there is no average rate for the month.",
  excluded: "An Admin left this metric out of the month.",
  zero_actual: "An explicit zero was reported, so it scores as zero.",
};

export const TARGET_TYPE_WORDS: Record<string, string> = {
  monthly: "monthly",
  yearly: "yearly, pro-rated to the cycle",
  cumulative: "cumulative to date",
  quarterly: "quarterly",
  prorated: "pro-rated",
};

export function footing(card: Card): { text: string; tone: "info" | "warn" } {
  if (card.source === "snapshot") {
    return card.restated_at
      ? { text: `Restated · version ${card.snapshot_version}`, tone: "warn" }
      : { text: `Closed and published · version ${card.snapshot_version}`, tone: "info" };
  }
  if (card.period_status === "restating") return { text: "Being restated: provisional until it closes again", tone: "warn" };
  return { text: "Provisional until the month closes", tone: "info" };
}
