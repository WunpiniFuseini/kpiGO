import { initials } from "../lib/format";
import { Chip } from "./Chip";
import type { GradeTone } from "./GradePill";

export interface RankedItem {
  id: string;
  name: string;
  sub?: string;
  value: string;
  /** Pace against elapsed working days: 1 means exactly on track (Design Brief §5.3). Null: no pace. */
  pace: number | null;
  tone?: GradeTone;
  /** The band label for the tone, so the bar is never colour alone. */
  band?: string;
  /** Why there is no pace, in words ("Not reported", "No target"). */
  noPace?: string;
  isYou?: boolean;
  /** The rank from the server, when it is not the position (shared ranks, or unranked as null). */
  rank?: number | null;
}

/** Rank · avatar · name and cohort · pace bar · value. */
export function RankedList({ items, label, onSelect }: { items: RankedItem[]; label: string; onSelect?: (item: RankedItem) => void }) {
  return (
    <ol className="kg-ranked" aria-label={label}>
      {items.map((item, i) => {
        const pct = item.pace === null ? null : Math.round(item.pace * 100);
        const rank = item.rank === undefined ? i + 1 : item.rank;
        const name = (
          <>
            {item.name} {item.isYou ? <Chip tone="info">you</Chip> : null}
          </>
        );
        return (
          <li key={item.id} aria-current={item.isYou ? "true" : undefined}>
            <span className="kg-ranked__rank num">
              {rank === null ? (
                <>
                  <span aria-hidden="true">–</span>
                  <span className="sr-only">Not ranked</span>
                </>
              ) : (
                rank
              )}
            </span>
            <span className="kg-av" aria-hidden="true">
              {initials(item.name)}
            </span>
            <span className="kg-ranked__who">
              {onSelect ? (
                <button type="button" className="kg-ranked__name" onClick={() => onSelect(item)}>
                  {name}
                </button>
              ) : (
                <b>{name}</b>
              )}
              {item.sub ? <small>{item.sub}</small> : null}
            </span>
            <span className="kg-ranked__pace" data-grade={item.tone}>
              {pct === null ? (
                <small>{item.noPace ?? "No pace yet"}</small>
              ) : (
                <>
                  <span className="kg-track" aria-hidden="true">
                    <i style={{ width: `${Math.min(pct, 100)}%` }} />
                  </span>
                  <small>
                    <span className="num">{pct}%</span> of pace{item.band ? ` · ${item.band}` : ""}
                  </small>
                </>
              )}
            </span>
            <span className="kg-ranked__val">{item.value}</span>
          </li>
        );
      })}
    </ol>
  );
}
