/**
 * ECharts theme from the token set (Design Brief §7). Canvas and SVG cannot
 * read CSS custom properties, so the values are repeated here;
 * tokens.test.ts fails if they drift from tokens.css.
 */
export const chartTokens = {
  ink: "#101828",
  ink2: "#475467",
  inkCap: "#667085",
  line: "#eaecf0",
  line2: "#f2f4f7",
  accent: "#4361ee",
  font: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
} as const;

export const kpigoTheme = {
  color: [chartTokens.accent, "#7a5af8", "#0ba5ec", "#ee46bc", "#15b79e", "#875bf7"],
  backgroundColor: "transparent",
  textStyle: { fontFamily: chartTokens.font, color: chartTokens.ink2, fontSize: 11 },
  // No gradients, shadows or 3D on data marks.
  line: { symbol: "none", lineStyle: { width: 2 }, smooth: false },
  bar: { itemStyle: { borderRadius: [3, 3, 0, 0] } },
  categoryAxis: {
    axisLine: { lineStyle: { color: chartTokens.line } },
    axisTick: { show: false },
    axisLabel: { color: chartTokens.inkCap },
    splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: chartTokens.inkCap },
    splitLine: { lineStyle: { color: chartTokens.line2 } },
  },
  tooltip: {
    backgroundColor: "#ffffff",
    borderColor: chartTokens.line,
    textStyle: { color: chartTokens.ink, fontSize: 12 },
  },
};
