/**
 * One placed widget on the Executive dashboard. It reads its own figures for the
 * chosen period (`widget.data`), holds the drill position for a breakdown, and
 * shows the right state: loading, an error with a reference, the server's named
 * empty state (no grant, or genuinely nothing), or the drawn widget.
 */
import { type CSSProperties, useState } from "react";

import type { Output } from "../../api/actions";
import { useQuery } from "../../api/useAction";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { Skeleton } from "../../components/Skeleton";
import { WidgetBody } from "./renderers";

export type PlacedWidget = Output<"widget.dashboard">["widgets"][number];

export function Widget({ placed, periodKey }: { placed: PlacedWidget; periodKey: string }) {
  const [drillTo, setDrillTo] = useState<string | null>(null);
  const [data, reload] = useQuery("widget.data", {
    widget_key: placed.widget_key,
    period_key: periodKey,
    drill_to: drillTo,
  });
  const span = Math.min(12, Math.max(2, placed.layout?.w ?? 4));

  return (
    <section
      className="kg-card kg-widget"
      style={{ "--w": span } as CSSProperties}
      aria-label={placed.title}
    >
      <div className="kg-row">
        <h3 className="kg-section">{placed.title}</h3>
      </div>

      {data.status === "ready" && data.data.dimension && data.data.breadcrumb.length > 0 ? (
        <nav className="kg-crumbs" aria-label={`${placed.title} drill path`}>
          <button type="button" className="kg-linkish" onClick={() => setDrillTo(null)}>
            All {data.data.dimension}
          </button>
          {data.data.breadcrumb.map((c, i) => (
            <span key={c.member_code}>
              <span aria-hidden="true"> › </span>
              {i === data.data.breadcrumb.length - 1 ? (
                <b>{c.member_name}</b>
              ) : (
                <button type="button" className="kg-linkish" onClick={() => setDrillTo(c.member_code)}>
                  {c.member_name}
                </button>
              )}
            </span>
          ))}
        </nav>
      ) : null}

      <div className="kg-widget__body">
        {data.status === "loading" ? (
          <Skeleton height={160} />
        ) : data.status === "error" ? (
          <ErrorPanel error={data.error} retry={reload} what="This widget" />
        ) : data.data.empty ? (
          <EmptyState kind="none" title="Nothing to show here yet" headingLevel={3}>
            {data.data.empty}
          </EmptyState>
        ) : (
          <WidgetBody data={data.data} onDrill={(code) => setDrillTo(code)} />
        )}
      </div>
    </section>
  );
}
