import { formatDateTime } from "../lib/format";
import { Notice } from "./Notice";

/**
 * A rejected load (App Flow §9, Quarantined): names the feed and keeps the
 * prior good data on screen, so a rejected file never reads as a bad result.
 */
export function QuarantineBanner({
  feed,
  rejectedAt,
  rows,
  priorAsOf,
  runHref,
}: {
  feed: string;
  rejectedAt: string;
  rows?: number;
  priorAsOf?: string | null;
  runHref?: string;
}) {
  return (
    <Notice tone="warn" title={`The ${feed} load on ${formatDateTime(rejectedAt)} was rejected.`} role="status">
      {rows ? `${rows.toLocaleString("en-GB")} rows failed validation. ` : ""}
      {priorAsOf
        ? `Figures below are the last good load, as of ${formatDateTime(priorAsOf)}. `
        : "No earlier load exists, so these figures are empty until a load passes. "}
      {runHref ? <a href={runHref}>See the rejections</a> : "A Data Steward can see the rejections."}
    </Notice>
  );
}
