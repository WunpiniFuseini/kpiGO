import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { chartTokens } from "../lib/chartTheme";

const css = readFileSync(resolve(__dirname, "../styles/tokens.css"), "utf8");
const tokens = Object.fromEntries([...css.matchAll(/(--[a-z0-9-]+):\s*(#[0-9a-f]{6})/gi)].map((m) => [m[1], m[2].toLowerCase()]));

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

describe("design tokens", () => {
  it("keeps the Design Brief §4 values verbatim", () => {
    const brief: Record<string, string> = {
      "--canvas": "#ffffff", "--canvas-exec": "#a9b5fc", "--surface": "#ffffff", "--sunken": "#f9fafb",
      "--rail": "#f5f6f8", "--rail-hover": "#edeff3", "--rail-active": "#e3e6ec",
      "--ink": "#101828", "--ink-2": "#475467", "--ink-3": "#98a2b3", "--line": "#eaecf0", "--line-2": "#f2f4f7",
      "--grade-1": "#e5484d", "--grade-2": "#f5a524", "--grade-3": "#17b26a", "--grade-4": "#05603a",
      "--accent": "#4361ee", "--accent-soft": "#eef2ff", "--pos": "#12b76a", "--pos-soft": "#ecfdf3",
      "--neg": "#f04438", "--neg-soft": "#fef3f2", "--warn": "#f79009", "--warn-soft": "#fffaeb",
    };
    for (const [name, value] of Object.entries(brief)) expect(tokens[name], name).toBe(value);
  });

  // WCAG 2.1 AA (Design Brief §8): 4.5:1 for every pairing that carries small text.
  const textPairs: [string, string][] = [
    ["--ink", "--surface"], ["--ink-2", "--surface"], ["--ink-cap", "--surface"], ["--ink-cap", "--sunken"],
    ["--ink-cap", "--rail"], ["--ink-2", "--rail-active"], ["--accent-strong", "--rail-active"], ["--accent", "--surface"],
    ["--accent-ink", "--accent-soft"], ["--pos-ink", "--pos-soft"], ["--neg-ink", "--neg-soft"], ["--warn-ink", "--warn-soft"],
    ["--grade-1-ink", "--grade-1-tint"], ["--grade-2-ink", "--grade-2-tint"], ["--grade-3-ink", "--grade-3-tint"], ["--grade-4-ink", "--grade-4-tint"],
  ];
  it.each(textPairs)("%s on %s reaches 4.5:1", (fg, bg) => {
    expect(contrast(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(4.5);
  });

  it("white on the primary button reaches 4.5:1", () => {
    expect(contrast("#ffffff", tokens["--accent"])).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps grade ramp and state accents distinct", () => {
    const grades = new Set(["--grade-1", "--grade-2", "--grade-3", "--grade-4"].map((t) => tokens[t]));
    for (const t of ["--pos", "--neg", "--warn", "--accent"]) expect(grades.has(tokens[t])).toBe(false);
  });

  it("gives the chart theme the same values as the tokens", () => {
    expect(chartTokens.ink).toBe(tokens["--ink"]);
    expect(chartTokens.ink2).toBe(tokens["--ink-2"]);
    expect(chartTokens.inkCap).toBe(tokens["--ink-cap"]);
    expect(chartTokens.line).toBe(tokens["--line"]);
    expect(chartTokens.line2).toBe(tokens["--line-2"]);
    expect(chartTokens.accent).toBe(tokens["--accent"]);
  });
});
