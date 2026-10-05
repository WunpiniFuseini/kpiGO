export type GradeTone = 1 | 2 | 3 | 4;

/** A band's standing as a pill: colour plus the band's own label, never colour alone. */
export function GradePill({ tone, label }: { tone: GradeTone; label: string }) {
  return (
    <span className="kg-grade-pill" data-grade={tone}>
      {label}
    </span>
  );
}
