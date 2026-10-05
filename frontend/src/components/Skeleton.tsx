import type { CSSProperties, ReactNode } from "react";

/** Loading placeholders shaped like the final layout; never a full-page spinner (§10). */
export function Skeleton({ width = "100%", height = 12, style }: { width?: number | string; height?: number; style?: CSSProperties }) {
  return <span className="kg-skel" style={{ width, height, ...style }} aria-hidden="true" />;
}

export function Loading({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}

export function CardSkeleton() {
  return (
    <div className="kg-card" aria-hidden="true">
      <Skeleton width="45%" />
      <Skeleton width="60%" height={30} style={{ margin: "14px 0 8px" }} />
      <Skeleton width="35%" height={10} />
    </div>
  );
}

export function TableSkeleton({ rows = 5, columns = 4 }: { rows?: number; columns?: number }) {
  return (
    <div aria-hidden="true" className="kg-stack" style={{ gap: 12 }}>
      {Array.from({ length: rows + 1 }, (_, r) => (
        <div key={r} style={{ display: "grid", gap: 16, gridTemplateColumns: `2fr repeat(${columns - 1}, 1fr)` }}>
          {Array.from({ length: columns }, (_, c) => (
            <Skeleton key={c} height={r === 0 ? 8 : 12} width={r === 0 ? "50%" : "80%"} />
          ))}
        </div>
      ))}
    </div>
  );
}
