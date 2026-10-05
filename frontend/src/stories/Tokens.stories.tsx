import type { Meta, StoryObj } from "@storybook/react-vite";

const groups: { title: string; note: string; tokens: string[] }[] = [
  { title: "Canvas and surface", note: "Working modules on white; the Executive canvas is the one tinted surface.", tokens: ["--canvas", "--canvas-exec", "--surface", "--sunken", "--rail", "--rail-hover", "--rail-active"] },
  { title: "Ink", note: "--ink-3 is for rules, icons and disabled states. Caption text uses --ink-cap.", tokens: ["--ink", "--ink-2", "--ink-cap", "--ink-3", "--line", "--line-2"] },
  { title: "Grade ramp: standing", note: "Ordinal and monotonic, from the client's rating bands. Text on a grade tint uses the -ink token.", tokens: ["--grade-1", "--grade-2", "--grade-3", "--grade-4", "--grade-1-ink", "--grade-2-ink", "--grade-3-ink", "--grade-4-ink"] },
  { title: "State accents: movement", note: "Never interchangeable with the grade ramp. Text on a soft fill uses the -ink token.", tokens: ["--accent", "--accent-soft", "--accent-ink", "--pos", "--pos-soft", "--pos-ink", "--neg", "--neg-soft", "--neg-ink", "--warn", "--warn-soft", "--warn-ink"] },
];

function Tokens() {
  return (
    <div className="kg-stack">
      {groups.map((g) => (
        <section key={g.title} className="kg-card">
          <h2 className="kg-section">{g.title}</h2>
          <p className="kg-cap" style={{ margin: "4px 0 14px" }}>
            {g.note}
          </p>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))" }}>
            {g.tokens.map((t) => (
              <li key={t} style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <span aria-hidden="true" style={{ width: 32, height: 32, borderRadius: 8, background: `var(${t})`, border: "1px solid var(--line)" }} />
                <code style={{ fontSize: 12 }}>{t}</code>
              </li>
            ))}
          </ul>
        </section>
      ))}
      <section className="kg-card">
        <h2 className="kg-section">Type</h2>
        <p className="kg-fig num">1,284,500</p>
        <p className="kg-fig kg-fig--sm num">96.4%</p>
        <p className="kg-title">Title: page titles</p>
        <p className="kg-section">Section: section headings</p>
        <p>Body: table cells and prose</p>
        <p className="kg-eyebrow">Eyebrow: metric labels</p>
        <p className="kg-cap">Caption: comparisons and timestamps</p>
        <p className="kg-micro">Micro: table headers</p>
      </section>
    </div>
  );
}

const meta: Meta = { title: "Foundations/Tokens", component: Tokens };
export default meta;
export const All: StoryObj = {};
