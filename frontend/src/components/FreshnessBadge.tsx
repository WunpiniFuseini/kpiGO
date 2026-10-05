import { formatDateTime } from "../lib/format";

export type Freshness = "fresh" | "stale" | "quarantined" | "refreshing" | "awaiting";

const WORDS: Record<Freshness, string> = {
  fresh: "Up to date",
  stale: "Stale",
  quarantined: "Last load rejected",
  refreshing: "Updating",
  awaiting: "Awaiting first load",
};

/** How current a figure is, in words as well as colour (App Flow §9). */
export function FreshnessBadge({
  state,
  asOf,
  feed,
  due,
}: {
  state: Freshness;
  asOf?: string | null;
  feed?: string;
  due?: string;
}) {
  const parts = [WORDS[state]];
  if (asOf) parts.push(`as of ${formatDateTime(asOf)}`);
  if (state === "awaiting" && due) parts.push(`due ${due}`);
  if (feed) parts.push(feed);
  return <span className={`kg-fresh kg-fresh--${state}`}>{parts.join(" · ")}</span>;
}
