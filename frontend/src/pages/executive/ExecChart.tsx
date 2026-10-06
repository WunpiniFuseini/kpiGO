/**
 * The one ECharts surface the Executive widgets draw on. It registers just the
 * chart types the bundle needs (bar, line, pie, gauge, funnel), uses the SVG
 * renderer and the shared kpiGo theme, turns animation off (a number that
 * animates looks uncertain, §10), and disposes cleanly.
 *
 * Every chart has its table one click away: an accessibility requirement and
 * what an analyst wants. The frame owns that Chart/Table toggle, gives the
 * drawing `role="img"` with a descriptive label, and renders the caller's table
 * in the other view.
 */
import { BarChart, FunnelChart, GaugeChart, LineChart, PieChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { type ReactNode, useEffect, useRef, useState } from "react";

import { kpigoTheme } from "../../lib/chartTheme";

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GaugeChart,
  FunnelChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  SVGRenderer,
]);
echarts.registerTheme("kpigo", kpigoTheme);

export function ExecChart({
  title,
  option,
  ariaLabel,
  table,
  onSelect,
  height = 240,
}: {
  title: string;
  option: echarts.EChartsCoreOption;
  /** What the drawing shows, for a screen reader; ends with the Table hint. */
  ariaLabel: string;
  /** The same figures as a real table, shown in the Table view. */
  table: ReactNode;
  /** A mark was clicked (its category name): used to drill a breakdown. */
  onSelect?: (name: string) => void;
  height?: number;
}) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const ref = useRef<HTMLDivElement>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    if (view !== "chart" || !ref.current) return;
    const chart = echarts.init(ref.current, "kpigo", { renderer: "svg" });
    chart.setOption({ animation: false, ...option });
    chart.on("click", (p: { name?: string }) => {
      if (p.name && selectRef.current) selectRef.current(p.name);
    });
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [view, option]);

  return (
    <div className="kg-exec-chart">
      <div className="kg-row">
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
          key="chart"
          ref={ref}
          style={{ height, marginTop: 10 }}
          role="img"
          aria-label={`${ariaLabel} Switch to Table for the figures.`}
        />
      ) : (
        <div key="table" className="kg-table-wrap" style={{ marginTop: 10 }}>
          {table}
        </div>
      )}
    </div>
  );
}
