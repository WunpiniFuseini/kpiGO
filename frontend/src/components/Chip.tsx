import type { ReactNode } from "react";

import { formatDelta } from "../lib/format";

/**
 * Movement since the last period (Design Brief §4.4). Uses the state accents,
 * never the grade ramp, and always says the direction in words for readers
 * who cannot see the colour or the arrow.
 */
export function DeltaChip({
  value,
  suffix = "%",
  decimals = 1,
  since = "last period",
  higherIsBetter = true,
}: {
  value: number | null;
  suffix?: string;
  decimals?: number;
  since?: string;
  higherIsBetter?: boolean;
}) {
  if (value === null) {
    return (
      <span className="kg-chip kg-chip--flat">
        <span aria-hidden="true">–</span> no prior period
      </span>
    );
  }
  const direction = value > 0 ? "up" : value < 0 ? "down" : "flat";
  // Colour follows good/bad, not up/down: a fall in costs is an improvement.
  const improved = direction === "flat" ? null : (direction === "up") === higherIsBetter;
  const tone = improved === null ? "flat" : improved ? "up" : "down";
  const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "–";
  const words = direction === "flat" ? "unchanged" : direction === "up" ? "up" : "down";
  return (
    <span className={`kg-chip kg-chip--${tone}`}>
      <span aria-hidden="true">{arrow}</span>
      <span aria-hidden="true">
        {formatDelta(value, decimals)}
        {suffix}
      </span>
      <span className="sr-only">
        {words} {Math.abs(value).toFixed(decimals)}
        {suffix} since {since}
      </span>
    </span>
  );
}

export type ChipTone = "flat" | "warn" | "info" | "up" | "down";

export function Chip({ tone = "flat", children }: { tone?: ChipTone; children: ReactNode }) {
  return <span className={`kg-chip kg-chip--${tone}`}>{children}</span>;
}

/** Marks a control or panel only Admins see (Design Brief §5.4). */
export function AdminBadge() {
  return <span className="kg-admin-badge">admin</span>;
}
