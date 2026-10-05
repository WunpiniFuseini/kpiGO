/** Number and date formatting. Figures use tabular numerals in CSS; this decides the text. */

export type Unit = "currency" | "count" | "percent" | "ratio" | "days" | "minutes" | string;

export function formatValue(
  value: number,
  { unit = "count", decimals = 0, currency = "" }: { unit?: Unit; decimals?: number; currency?: string } = {},
): string {
  const fixed = value.toLocaleString("en-GB", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  if (unit === "percent") return `${fixed}%`;
  if (unit === "currency" && currency) return `${currency} ${fixed}`;
  return fixed;
}

/** "2.4M" style for headline figures where the full value is in the provenance. */
export function formatCompact(value: number): string {
  return new Intl.NumberFormat("en-GB", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function formatDelta(value: number, decimals = 1): string {
  const abs = Math.abs(value).toFixed(decimals);
  if (value > 0) return `+${abs}`;
  if (value < 0) return `−${abs}`;
  return abs;
}

export function formatDateTime(value: string | Date): string {
  const date = typeof value === "string" ? new Date(value) : value;
  return date.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDate(value: string | Date): string {
  const date = typeof value === "string" ? new Date(value) : value;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[parts.length - 1][0] : (parts[0] ?? "?").slice(0, 2);
  return letters.toUpperCase();
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "202607" → "Jul 2026" (or "July 2026" with `long`). */
export function formatPeriod(periodKey: string, long = false): string {
  const month = Number(periodKey.slice(4, 6)) - 1;
  const names = long ? MONTHS_LONG : MONTHS;
  return `${names[month] ?? periodKey.slice(4, 6)} ${periodKey.slice(0, 4)}`;
}

/** Move a YYYYMM period key by whole months. */
export function shiftPeriod(periodKey: string, months: number): string {
  const index = Number(periodKey.slice(0, 4)) * 12 + Number(periodKey.slice(4, 6)) - 1 + months;
  return `${Math.floor(index / 12)}${String((index % 12) + 1).padStart(2, "0")}`;
}

/** Today's period key in the browser's calendar. */
export function currentPeriod(today: Date = new Date()): string {
  return `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, "0")}`;
}

/** A decimal string from the API ("40.000") as a short figure ("40"). */
export function formatDecimal(value: string | number | null | undefined, decimals?: number): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("en-GB", {
    minimumFractionDigits: decimals ?? 0,
    maximumFractionDigits: decimals ?? 4,
  });
}
