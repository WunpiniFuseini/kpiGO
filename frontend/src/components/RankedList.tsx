import { initials } from "../lib/format";
import { Chip } from "./Chip";
import type { GradeTone } from "./GradePill";

export interface RankedItem {
  id: string;
  name: string;
  sub?: string;
  value: string;
  /** Pace against elapsed working days: 1 means exactly on track (Design Brief §5.3). */
  pace: number;
  tone: GradeTone;
  /** The band label for the tone, so the bar is never colour alone. */
  band: string;
  isYou?: boolean;
}

/** Rank · avatar · name and cohort · pace bar · value. */
export function RankedList({ items, label }: { items: RankedItem[]; label: string }) {
  return (
    <ol className="kg-ranked" aria-label={label}>
      {items.map((item, i) => {
        const pct = Math.round(item.pace * 100);
        return (
          <li key={item.id} aria-current={item.isYou ? "true" : undefined}>
            <span className="kg-ranked__rank num">{i + 1}</span>
            <span className="kg-av" aria-hidden="true">
              {initials(item.name)}
            </span>
            <span className="kg-ranked__who">
              <b>
                {item.name} {item.isYou ? <Chip tone="info">you</Chip> : null}
              </b>
              {item.sub ? <small>{item.sub}</small> : null}
            </span>
            <span className="kg-ranked__pace" data-grade={item.tone}>
              <span className="kg-track" aria-hidden="true">
                <i style={{ width: `${Math.min(pct, 100)}%` }} />
              </span>
              <small>
                <span className="num">{pct}%</span> of pace · {item.band}
              </small>
            </span>
            <span className="kg-ranked__val">{item.value}</span>
          </li>
        );
      })}
    </ol>
  );
}
