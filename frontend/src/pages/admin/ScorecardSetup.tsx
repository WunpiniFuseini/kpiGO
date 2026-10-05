import { useState, type FormEvent } from "react";

import type { Output } from "../../api/actions";
import { ApiError, invoke, isProposal } from "../../api/client";
import { useQuery } from "../../api/useAction";
import {
  CheckboxGroup,
  Chip,
  DataTable,
  EmptyState,
  ErrorPanel,
  GradePill,
  Loading,
  Notice,
  SelectField,
  TableSkeleton,
  TextField,
  type GradeTone,
} from "../../components";
import { formatDecimal } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";

type Template = NonNullable<Output<"scorecard.template.get">["template"]>;
type TemplateNode = Template["nodes"][number];
type Profiles = Output<"scorecard.profile.list">;
type Bands = Output<"band.list">;
type Metric = Output<"metric.list">["metrics"][number];

/** The result line under a form: what happened, or why not, in words. */
type Outcome = { tone: "info" | "neg"; text: string } | null;

async function attempt(run: () => Promise<unknown>, done: (out: unknown) => string): Promise<Outcome> {
  try {
    const out = await run();
    return { tone: "info", text: isProposal(out) ? out.message : done(out) };
  } catch (e) {
    return { tone: "neg", text: e instanceof ApiError ? e.message : "That did not work." };
  }
}

function OutcomeLine({ outcome }: { outcome: Outcome }) {
  if (!outcome) return null;
  return (
    <Notice tone={outcome.tone} role={outcome.tone === "neg" ? "alert" : "status"}>
      {outcome.text}
    </Notice>
  );
}

/** Administer → Scorecard setup: taxonomy, profiles' metrics, bands and settings (PRD SC-1, SC-4). */
export function ScorecardSetupPage() {
  const me = useMe();
  const canManage = me.permissions.includes("scorecard.config.manage");
  const [template, reloadTemplate] = useQuery("scorecard.template.get", {});
  const [metrics] = useQuery("metric.list", { product: "scorecards", status: "active" });
  const metricList = metrics.status === "ready" ? metrics.data.metrics : [];
  return (
    <Page title="Scorecard setup">
      <section className="kg-card" aria-labelledby="taxonomy-heading">
        <h2 id="taxonomy-heading" className="kg-section">
          Taxonomy
        </h2>
        <p className="kg-cap">
          One to three levels above the metric, named the way your organisation talks. Every scorecard, summary and PDF groups by it.
        </p>
        {template.status === "loading" ? (
          <Loading label="Loading the taxonomy">
            <TableSkeleton rows={4} columns={2} />
          </Loading>
        ) : template.status === "error" ? (
          <ErrorPanel error={template.error} retry={reloadTemplate} what="The taxonomy" />
        ) : template.data.template ? (
          <TaxonomyEditor template={template.data.template} metrics={metricList} canManage={canManage} onChanged={reloadTemplate} />
        ) : (
          <NoTemplate canManage={canManage} onCreated={reloadTemplate} />
        )}
      </section>
      <ProfilesSection metrics={metricList} canManage={canManage} />
      <BandsSection canManage={canManage} />
      <SettingsSection canManage={canManage} />
    </Page>
  );
}

// ── taxonomy ────────────────────────────────────────────────────────────────

function NoTemplate({ canManage, onCreated }: { canManage: boolean; onCreated: () => void }) {
  const [name, setName] = useState("Scorecard");
  const [levels, setLevels] = useState("Strategic objective");
  const [outcome, setOutcome] = useState<Outcome>(null);
  const create = async (event: FormEvent) => {
    event.preventDefault();
    const labels = levels.split(">").map((l) => l.trim()).filter(Boolean);
    setOutcome(await attempt(() => invoke("scorecard.template.save", { name, levels: labels }, { allowProposal: true }), () => "Created."));
    onCreated();
  };
  return (
    <>
      <EmptyState kind="none" title="No taxonomy is set up yet">
        Until one is active, scorecards list their metrics without grouping.{" "}
        {canManage ? "Name the levels below to start." : "An Admin or Metric Owner sets it up here."}
      </EmptyState>
      {canManage ? (
        <form className="kg-form" onSubmit={create}>
          <div className="kg-form-row">
            <TextField label="Template name" value={name} onChange={(e) => setName(e.target.value)} required />
            <TextField
              label="Levels, top first"
              hint="Separate levels with >, e.g. Perspective > Objective. One to three."
              value={levels}
              onChange={(e) => setLevels(e.target.value)}
              required
            />
          </div>
          <OutcomeLine outcome={outcome} />
          <div>
            <button type="submit" className="kg-btn kg-btn--primary">
              Create taxonomy
            </button>
          </div>
        </form>
      ) : null}
    </>
  );
}

function childrenOf(template: Template, parent: string | null): TemplateNode[] {
  return template.nodes.filter((n) => (n.parent_id ?? null) === parent).sort((a, b) => a.sort_order - b.sort_order || a.label.localeCompare(b.label));
}

export function TaxonomyEditor({
  template,
  metrics,
  canManage,
  onChanged,
}: {
  template: Template;
  metrics: Metric[];
  canManage: boolean;
  onChanged: () => void;
}) {
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [adding, setAdding] = useState<{ parent: string | null; label: string } | null>(null);
  const [placing, setPlacing] = useState({ metric_code: "", node_id: "", profile_code: "" });
  const leaves = template.nodes.filter((n) => n.level_no === template.level_count);
  const placed = new Set(template.placements.filter((p) => !p.profile_code).map((p) => p.metric_code));
  const unplaced = metrics.filter((m) => !placed.has(m.metric_code));
  const levelLabel = (no: number) => template.levels.find((l) => l.level_no === no)?.label ?? `Level ${no}`;

  const run = async (call: () => Promise<unknown>, text: string) => {
    setOutcome(await attempt(call, () => text));
    onChanged();
  };

  const addNode = async (event: FormEvent) => {
    event.preventDefault();
    if (!adding) return;
    await run(
      () => invoke("scorecard.node.save", { template_id: template.template_id, parent_id: adding.parent, label: adding.label }, { allowProposal: true }),
      `Added ${adding.label}.`,
    );
    setAdding(null);
  };

  const place = async (event: FormEvent) => {
    event.preventDefault();
    await run(
      () =>
        invoke(
          "scorecard.placement.set",
          {
            template_id: template.template_id,
            node_id: placing.node_id,
            metric_code: placing.metric_code,
            profile_code: placing.profile_code || null,
          },
          { allowProposal: true },
        ),
      "Placed.",
    );
  };

  const renderNode = (node: TemplateNode) => {
    const kids = childrenOf(template, node.node_id);
    const here = template.placements.filter((p) => p.node_id === node.node_id);
    return (
      <li key={node.node_id}>
        <div className="kg-row" style={{ justifyContent: "flex-start" }}>
          <b>{node.label}</b>
          <span className="kg-cap">{levelLabel(node.level_no)}</span>
          {canManage && node.level_no < template.level_count ? (
            <button type="button" className="kg-btn kg-btn--link" onClick={() => setAdding({ parent: node.node_id, label: "" })}>
              Add {levelLabel(node.level_no + 1).toLowerCase()}
            </button>
          ) : null}
        </div>
        {here.length ? (
          <ul className="kg-tree__metrics">
            {here.map((p) => (
              <li key={p.placement_id}>
                {p.metric_name ?? p.metric_code} <span className="kg-mono">{p.metric_code}</span>
                {p.profile_code ? <Chip tone="info">only {p.profile_code}</Chip> : null}
                {canManage ? (
                  <button
                    type="button"
                    className="kg-btn kg-btn--link"
                    onClick={() =>
                      void run(
                        () =>
                          invoke(
                            "scorecard.placement.clear",
                            { template_id: template.template_id, metric_code: p.metric_code, profile_code: p.profile_code },
                            { allowProposal: true },
                          ),
                        "Removed.",
                      )
                    }
                  >
                    Remove<span className="sr-only"> {p.metric_code}</span>
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : node.level_no === template.level_count ? (
          <p className="kg-cap">No metrics here yet.</p>
        ) : null}
        {kids.length ? <ul className="kg-tree">{kids.map(renderNode)}</ul> : null}
      </li>
    );
  };

  return (
    <div className="kg-stack">
      <div className="kg-row" style={{ justifyContent: "flex-start", flexWrap: "wrap" }}>
        <b>{template.name}</b>
        <Chip tone={template.status === "active" ? "up" : "flat"}>{template.status}</Chip>
        <span className="kg-cap">{template.levels.map((l) => l.label).join(" › ")} › metric</span>
        {canManage && template.status !== "active" ? (
          <button
            type="button"
            className="kg-btn"
            onClick={() =>
              void run(() => invoke("scorecard.template.activate", { template_id: template.template_id }, { allowProposal: true }), "Activated.")
            }
          >
            Make active
          </button>
        ) : null}
      </div>
      <OutcomeLine outcome={outcome} />
      {template.nodes.length ? (
        <ul className="kg-tree">{childrenOf(template, null).map(renderNode)}</ul>
      ) : (
        <p className="kg-cap">No {levelLabel(1).toLowerCase()} yet.</p>
      )}
      {canManage ? (
        <button type="button" className="kg-btn" onClick={() => setAdding({ parent: null, label: "" })} style={{ alignSelf: "flex-start" }}>
          Add {levelLabel(1).toLowerCase()}
        </button>
      ) : null}
      {adding ? (
        <form className="kg-form" onSubmit={addNode} aria-label="Add a node">
          <TextField label="Label" value={adding.label} onChange={(e) => setAdding({ ...adding, label: e.target.value })} required />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary" disabled={!adding.label.trim()}>
              Add
            </button>
            <button type="button" className="kg-btn" onClick={() => setAdding(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {canManage && leaves.length ? (
        <form className="kg-form" onSubmit={place} aria-label="Place a metric">
          <h3 className="kg-section">Place a metric</h3>
          {unplaced.length ? (
            <p className="kg-cap">{unplaced.length} Scorecards metric(s) have no default place yet; they show ungrouped until they do.</p>
          ) : null}
          <div className="kg-form-row">
            <SelectField
              label="Metric"
              value={placing.metric_code}
              options={[{ value: "", label: "Choose a metric" }, ...metrics.map((m) => ({ value: m.metric_code, label: m.display_name }))]}
              onChange={(e) => setPlacing({ ...placing, metric_code: e.target.value })}
            />
            <SelectField
              label={`Under ${levelLabel(template.level_count).toLowerCase()}`}
              value={placing.node_id}
              options={[{ value: "", label: "Choose where" }, ...leaves.map((n) => ({ value: n.node_id, label: n.label }))]}
              onChange={(e) => setPlacing({ ...placing, node_id: e.target.value })}
            />
            <TextField
              label="Only for profile (optional)"
              hint="Leave empty for the default place."
              value={placing.profile_code}
              onChange={(e) => setPlacing({ ...placing, profile_code: e.target.value })}
            />
          </div>
          <div>
            <button type="submit" className="kg-btn kg-btn--primary" disabled={!placing.metric_code || !placing.node_id}>
              Place metric
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

// ── profiles ────────────────────────────────────────────────────────────────

function ProfilesSection({ metrics, canManage }: { metrics: Metric[]; canManage: boolean }) {
  const [profiles, reload] = useQuery("scorecard.profile.list", {});
  return (
    <section className="kg-card" aria-labelledby="profiles-heading">
      <h2 id="profiles-heading" className="kg-section">
        Profiles and their metrics
      </h2>
      <p className="kg-cap">A profile&apos;s scorecard is the set of metrics it is measured on, from a date. Earlier months keep theirs.</p>
      {profiles.status === "loading" ? (
        <Loading label="Loading profiles">
          <TableSkeleton rows={3} columns={3} />
        </Loading>
      ) : profiles.status === "error" ? (
        <ErrorPanel error={profiles.error} retry={reload} what="Profiles" />
      ) : (
        <ProfilesView profiles={profiles.data} metrics={metrics} canManage={canManage} onChanged={reload} />
      )}
    </section>
  );
}

export function ProfilesView({
  profiles,
  metrics,
  canManage,
  onChanged,
}: {
  profiles: Profiles;
  metrics: Metric[];
  canManage: boolean;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState<{ profile: string; codes: string[]; from: string } | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setOutcome(
      await attempt(
        () =>
          invoke(
            "scorecard.profile.set_metrics",
            { profile_code: editing.profile, metric_codes: editing.codes, effective_from: editing.from },
            { allowProposal: true },
          ),
        () => `Saved ${editing.profile}'s metrics from ${editing.from}.`,
      ),
    );
    setEditing(null);
    onChanged();
  };
  const firstOfNextMonth = () => {
    const d = new Date();
    const next = new Date(d.getFullYear(), d.getMonth() + 1, 1);
    return `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-01`;
  };
  return (
    <div className="kg-stack">
      <OutcomeLine outcome={outcome} />
      <DataTable
        caption={`Profiles in ${profiles.period_key.slice(4)}/${profiles.period_key.slice(0, 4)}`}
        captionHidden
        columns={[
          { key: "profile", header: "Profile", render: (p) => <span className="kg-mono">{p.profile_code}</span> },
          { key: "members", header: "People", numeric: true, render: (p) => p.member_count },
          {
            key: "metrics",
            header: "Metrics",
            render: (p) =>
              p.metrics.length ? (
                p.metrics.map((m) => (
                  <div key={m.metric_code}>
                    {m.display_name}
                    <span className="kg-cap">
                      {" "}
                      · {m.path.length ? m.path.join(" › ") : "ungrouped"}
                      {m.target_scope === "subject" ? " · target per person" : ""}
                      {m.collection_method === "manual_input" ? " · manual input" : ""}
                    </span>
                  </div>
                ))
              ) : (
                <span className="kg-cap">No metrics: this profile&apos;s scorecards are empty.</span>
              ),
          },
          {
            key: "edit",
            header: "",
            render: (p) =>
              canManage ? (
                <button
                  type="button"
                  className="kg-btn kg-btn--link"
                  onClick={() => setEditing({ profile: p.profile_code, codes: p.metrics.map((m) => m.metric_code), from: firstOfNextMonth() })}
                >
                  Change<span className="sr-only"> {p.profile_code}</span>
                </button>
              ) : null,
          },
        ]}
        rows={profiles.profiles}
        rowKey={(p) => p.profile_code}
        empty={
          <EmptyState kind="awaiting" title="No profiles yet">
            Profiles come from people&apos;s assignments. Load the roster under Administer → Data integration, or import it from the directory.
          </EmptyState>
        }
      />
      {editing ? (
        <form className="kg-form" onSubmit={save} aria-label={`Metrics for ${editing.profile}`}>
          <CheckboxGroup
            legend={`Scorecards metrics for ${editing.profile}`}
            options={metrics.map((m) => ({ value: m.metric_code, label: m.display_name }))}
            value={editing.codes}
            onChange={(codes) => setEditing({ ...editing, codes })}
          />
          <TextField label="From" type="date" value={editing.from} onChange={(e) => setEditing({ ...editing, from: e.target.value })} required />
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary">
              Save
            </button>
            <button type="button" className="kg-btn" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

// ── bands ───────────────────────────────────────────────────────────────────

function BandsSection({ canManage }: { canManage: boolean }) {
  const [bands, reload] = useQuery("band.list", {});
  return (
    <section className="kg-card" aria-labelledby="bands-heading">
      <h2 id="bands-heading" className="kg-section">
        Rating bands
      </h2>
      <p className="kg-cap">The grade a total score lands in. 1.0 is every metric exactly on target.</p>
      {bands.status === "loading" ? (
        <Loading label="Loading bands">
          <TableSkeleton rows={4} columns={3} />
        </Loading>
      ) : bands.status === "error" ? (
        <ErrorPanel error={bands.error} retry={reload} what="Rating bands" />
      ) : (
        <BandsView bands={bands.data} canManage={canManage} onChanged={reload} />
      )}
    </section>
  );
}

export function BandsView({ bands, canManage, onChanged }: { bands: Bands; canManage: boolean; onChanged: () => void }) {
  const [editing, setEditing] = useState<{ label: string; threshold: string; ramp_position: number }[] | null>(null);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setOutcome(await attempt(() => invoke("band.set", { bands: editing }, { allowProposal: true }), () => "Bands saved."));
    setEditing(null);
    onChanged();
  };
  return (
    <div className="kg-stack">
      <OutcomeLine outcome={outcome} />
      <DataTable
        caption="Rating bands, lowest first"
        captionHidden
        columns={[
          { key: "band", header: "Band", render: (b) => <GradePill tone={Math.min(b.ramp_position, 4) as GradeTone} label={b.label} /> },
          { key: "from", header: "From total score", numeric: true, render: (b) => formatDecimal(b.threshold, 2) },
          { key: "step", header: "Ramp step", numeric: true, render: (b) => b.ramp_position },
        ]}
        rows={bands.bands}
        rowKey={(b) => b.label}
        empty={<p className="kg-cap">No bands.</p>}
      />
      {bands.is_default ? <p className="kg-cap">These are the standard four. Change them to match your appraisal scale.</p> : null}
      {canManage && !editing ? (
        <div>
          <button
            type="button"
            className="kg-btn"
            onClick={() => setEditing(bands.bands.map((b) => ({ label: b.label, threshold: String(b.threshold), ramp_position: b.ramp_position })))}
          >
            Change bands
          </button>
        </div>
      ) : null}
      {editing ? (
        <form className="kg-form" onSubmit={save} aria-label="Edit rating bands">
          {editing.map((b, i) => (
            <div className="kg-form-row" key={i}>
              <TextField label={`Band ${i + 1} label`} value={b.label} onChange={(e) => setEditing(editing.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
              <TextField
                label="From total score"
                type="number"
                step="0.01"
                value={b.threshold}
                onChange={(e) => setEditing(editing.map((x, j) => (j === i ? { ...x, threshold: e.target.value } : x)))}
              />
              <TextField
                label="Ramp step"
                type="number"
                min={1}
                max={7}
                value={b.ramp_position}
                onChange={(e) => setEditing(editing.map((x, j) => (j === i ? { ...x, ramp_position: Number(e.target.value) } : x)))}
              />
            </div>
          ))}
          <p className="kg-cap">The lowest band starts at 0. Ramp steps rise with the threshold, so colours stay in order.</p>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" className="kg-btn" onClick={() => setEditing([...editing, { label: "", threshold: "", ramp_position: editing.length + 1 }])}>
              Add a band
            </button>
            <button type="submit" className="kg-btn kg-btn--primary">
              Save bands
            </button>
            <button type="button" className="kg-btn" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

// ── settings ────────────────────────────────────────────────────────────────

function SettingsSection({ canManage }: { canManage: boolean }) {
  const [settings, reload] = useQuery("scorecard.settings.get", {});
  const [outcome, setOutcome] = useState<Outcome>(null);
  const [form, setForm] = useState<{ weight_total: string; weight_tolerance: string; denominator_policy: string } | null>(null);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!form) return;
    setOutcome(
      await attempt(
        () =>
          invoke(
            "scorecard.settings.set",
            {
              weight_total: form.weight_total,
              weight_tolerance: form.weight_tolerance,
              denominator_policy: form.denominator_policy as "reduced" | "redistribute",
            },
            { allowProposal: true },
          ),
        () => "Settings saved.",
      ),
    );
    setForm(null);
    reload();
  };
  return (
    <section className="kg-card" aria-labelledby="settings-heading">
      <h2 id="settings-heading" className="kg-section">
        Scoring settings
      </h2>
      <OutcomeLine outcome={outcome} />
      {settings.status === "loading" ? (
        <Loading label="Loading settings">
          <TableSkeleton rows={2} columns={2} />
        </Loading>
      ) : settings.status === "error" ? (
        <ErrorPanel error={settings.error} retry={reload} what="Scoring settings" />
      ) : form ? (
        <form className="kg-form" onSubmit={save}>
          <div className="kg-form-row">
            <TextField label="Weights sum to" type="number" value={form.weight_total} onChange={(e) => setForm({ ...form, weight_total: e.target.value })} />
            <TextField label="Tolerance, ±" type="number" step="0.1" value={form.weight_tolerance} onChange={(e) => setForm({ ...form, weight_tolerance: e.target.value })} />
            <SelectField
              label="A metric awaiting data"
              value={form.denominator_policy}
              options={[
                { value: "reduced", label: "Leaves the denominator (recommended)" },
                { value: "redistribute", label: "Spreads its weight over the others" },
              ]}
              onChange={(e) => setForm({ ...form, denominator_policy: e.target.value })}
            />
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" className="kg-btn kg-btn--primary">
              Save settings
            </button>
            <button type="button" className="kg-btn" onClick={() => setForm(null)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="kg-stack">
          <p>
            Each profile&apos;s weights must sum to <b>{formatDecimal(settings.data.weight_total)}</b>, within ±{formatDecimal(settings.data.weight_tolerance)}.
            A metric awaiting data{" "}
            {settings.data.denominator_policy === "reduced" ? "leaves the denominator: the score is out of what was scored." : "spreads its weight over the scored metrics."}
          </p>
          {canManage ? (
            <div>
              <button
                type="button"
                className="kg-btn"
                onClick={() =>
                  setForm({
                    weight_total: String(settings.data.weight_total),
                    weight_tolerance: String(settings.data.weight_tolerance),
                    denominator_policy: settings.data.denominator_policy,
                  })
                }
              >
                Change settings
              </button>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
