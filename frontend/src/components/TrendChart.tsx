import { LineChart } from "echarts/charts";
import { GridComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useId, useRef, useState } from "react";

import { kpigoTheme } from "../lib/chartTheme";

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, SVGRenderer]);
echarts.registerTheme("kpigo", kpigoTheme);

export interface TrendPoint {
  label: string;
  value: number | null;
}

/**
 * A series over periods, with the target as a dashed reference line, never a
 * competing solid series (Design Brief §7). Every chart has its table one
 * click away: an accessibility requirement and what an analyst wants.
 */
export function TrendChart({
  title,
  points,
  target,
  format = (v) => v.toLocaleString("en-GB"),
  height = 220,
}: {
  title: string;
  points: TrendPoint[];
  target?: number | null;
  format?: (value: number) => string;
  height?: number;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const ref = useRef<HTMLDivElement>(null);
  const headingId = useId();

  useEffect(() => {
    if (view !== "chart" || !ref.current) return;
    const chart = echarts.init(ref.current, "kpigo", { renderer: "svg" });
    chart.setOption({
      animation: false, // a number that animates looks uncertain (§10)
      grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (typeof v === "number" ? format(v) : "–") },
      xAxis: { type: "category", data: points.map((p) => p.label) },
      yAxis: { type: "value", axisLabel: { formatter: (v: number) => format(v) } },
      series: [
        {
          type: "line",
          name: title,
          data: points.map((p) => p.value),
          connectNulls: false,
          markLine:
            target === undefined || target === null
              ? undefined
              : {
                  symbol: "none",
                  silent: true,
                  label: { formatter: `Target ${format(target)}`, position: "insideEndTop", color: "#475467" },
                  lineStyle: { type: "dashed", color: "#475467", width: 1 },
                  data: [{ yAxis: target }],
                },
        },
      ],
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [view, points, target, title, format]);

  return (
    <section className="kg-card" aria-labelledby={headingId}>
      <div className="kg-row">
        <h3 id={headingId} className="kg-section">
          {title}
        </h3>
        <div className="kg-seg" role="group" aria-label={`Show ${title} as`}>
          <button type="button" aria-pressed={view === "chart"} onClick={() => setView("chart")}>
            Chart
          </button>
          <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>
            Table
          </button>
        </div>
      </div>
      {view === "chart" ? (
        <div
          ref={ref}
          style={{ height, marginTop: 12 }}
          role="img"
          aria-label={`${title}: line chart of ${points.length} periods${
            target === undefined || target === null ? "" : `, target ${format(target)}`
          }. Switch to Table for the figures.`}
        />
      ) : (
        <div className="kg-table-wrap" style={{ marginTop: 12 }}>
          <table className="kg-table">
            <caption className="sr-only">{title} by period</caption>
            <thead>
              <tr>
                <th scope="col">Period</th>
                <th scope="col" className="is-num">
                  Value
                </th>
                {target === undefined || target === null ? null : (
                  <th scope="col" className="is-num">
                    Target
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.label}>
                  <th scope="row">{p.label}</th>
                  <td className="is-num">{p.value === null ? "Not reported" : format(p.value)}</td>
                  {target === undefined || target === null ? null : <td className="is-num">{format(target)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
