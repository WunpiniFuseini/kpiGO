import { useId, useState } from "react";

import { CheckboxGroup, SelectField, TextField } from "../../components";
import { CHANNELS, describeAudience, type Criterion, type Dimension, type EventDraft, type EventErrors } from "./model";

/**
 * Builds an audience from dimension criteria (Design Brief §5.6, `AudienceCriteriaPicker`).
 * Criteria, never a customer list: kpiGo holds no customer master. Members of
 * one dimension widen the audience; a second dimension narrows it.
 */
export function AudienceCriteriaPicker({
  dimensions,
  value,
  onChange,
  error,
  disabled = false,
}: {
  dimensions: Dimension[];
  value: Criterion[];
  onChange: (next: Criterion[]) => void;
  error?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const [dimension, setDimension] = useState(dimensions[0]?.dimension_type ?? "");
  const options = dimensions.find((d) => d.dimension_type === dimension)?.members ?? [];
  const taken = new Set(value.filter((c) => c.dimension_type === dimension).map((c) => c.member_code));
  const free = options.filter((m) => !taken.has(m.member_code));
  const [member, setMember] = useState("");
  const chosen = free.some((m) => m.member_code === member) ? member : (free[0]?.member_code ?? "");
  const nameOf = (c: Criterion) =>
    dimensions.find((d) => d.dimension_type === c.dimension_type)?.members.find((m) => m.member_code === c.member_code)?.member_name ?? c.member_code;
  const dimName = (type: string) => dimensions.find((d) => d.dimension_type === type)?.display_name ?? type;

  if (!dimensions.length) {
    return (
      <fieldset className="kg-field">
        <legend>Audience</legend>
        <p className="kg-cap">No dimension has members yet, so there is nothing to build an audience from. A Data Steward adds the segments, products and regions an audience is built from.</p>
      </fieldset>
    );
  }

  return (
    <fieldset className="kg-field" aria-describedby={`${id}-summary${error ? ` ${id}-error` : ""}`}>
      <legend>Audience</legend>
      <div className="kg-form-row" style={{ alignItems: "end" }}>
        <SelectField label="Dimension" value={dimension} disabled={disabled} onChange={(e) => setDimension(e.target.value)} options={dimensions.map((d) => ({ value: d.dimension_type, label: d.display_name }))} />
        <SelectField
          label="Member"
          value={chosen}
          disabled={disabled || !free.length}
          onChange={(e) => setMember(e.target.value)}
          options={free.length ? free.map((m) => ({ value: m.member_code, label: m.parent_code ? `${m.member_name} (in ${options.find((o) => o.member_code === m.parent_code)?.member_name ?? m.parent_code})` : m.member_name })) : [{ value: "", label: "All members chosen" }]}
        />
        <div>
          <button type="button" className="kg-btn" disabled={disabled || !chosen} onClick={() => onChange([...value, { dimension_type: dimension, member_code: chosen }])}>
            Add criterion
          </button>
        </div>
      </div>
      {value.length ? (
        <ul aria-label="Audience criteria" style={{ listStyle: "none", padding: 0, margin: "10px 0 0", display: "flex", flexWrap: "wrap", gap: 6 }}>
          {value.map((c) => (
            <li key={`${c.dimension_type}:${c.member_code}`} className="kg-chip kg-chip--info" style={{ gap: 6 }}>
              {dimName(c.dimension_type)}: {nameOf(c)}
              {disabled ? null : (
                <button type="button" className="kg-linkbtn" style={{ color: "inherit", fontWeight: 700 }} aria-label={`Remove ${dimName(c.dimension_type)} ${nameOf(c)}`} onClick={() => onChange(value.filter((x) => x !== c))}>
                  ×
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      <p id={`${id}-summary`} className="kg-cap" style={{ marginTop: 8 }}>
        {value.length ? `Who it is for: ${describeAudience(value, dimensions)}.` : "No criteria yet. Add at least one before publishing."} The reach estimate appears once the campaign outcome feed has loaded customers to count.
      </p>
      {error ? (
        <span id={`${id}-error`} className="kg-field-error">
          {error}
        </span>
      ) : null}
    </fieldset>
  );
}

/**
 * One event block of the builder: period, window, channels, budget, audience.
 * `mode` "live" hides the budget (it changes through approval) and fixes the
 * first day once the event has started.
 */
export function EventFields({
  value,
  onChange,
  errors,
  dimensions,
  currencies,
  defaultWindow,
  mode = "draft",
  started = false,
}: {
  value: EventDraft;
  onChange: (next: EventDraft) => void;
  errors: EventErrors;
  dimensions: Dimension[];
  currencies: string[];
  defaultWindow?: number;
  mode?: "draft" | "live";
  started?: boolean;
}) {
  const set = <K extends keyof EventDraft>(key: K, v: EventDraft[K]) => onChange({ ...value, [key]: v });
  return (
    <div className="kg-stack">
      <div className="kg-form-row">
        <TextField label="Event name" value={value.event_name} maxLength={120} required error={errors.event_name} onChange={(e) => set("event_name", e.target.value)} />
        <TextField label="First contact day" type="date" value={value.period_start} required disabled={started} hint={started ? "Fixed: the event has started" : undefined} error={errors.period_start} onChange={(e) => set("period_start", e.target.value)} />
        <TextField label="Last contact day" type="date" value={value.period_end} required error={errors.period_end} onChange={(e) => set("period_end", e.target.value)} />
        <TextField
          label="Attribution window (days)"
          type="number"
          min={0}
          max={730}
          inputMode="numeric"
          value={value.attribution_window_days}
          placeholder={defaultWindow === undefined ? undefined : String(defaultWindow)}
          hint={defaultWindow === undefined ? "Outcomes count this long after the last contact day" : `Blank: the objective's ${defaultWindow} days`}
          error={errors.attribution_window_days}
          onChange={(e) => set("attribution_window_days", e.target.value)}
        />
      </div>
      {mode === "draft" ? (
        <div className="kg-form-row">
          <TextField label="Budget" inputMode="decimal" value={value.budget_amount} required error={errors.budget_amount} hint="Per event. Editable freely while a draft." onChange={(e) => set("budget_amount", e.target.value)} />
          <SelectField label="Currency" value={value.budget_currency} error={errors.budget_currency} onChange={(e) => set("budget_currency", e.target.value)} options={[...(value.budget_currency ? [] : [{ value: "", label: "Choose" }]), ...currencies.map((c) => ({ value: c, label: c }))]} />
        </div>
      ) : null}
      <CheckboxGroup legend="Channels" options={CHANNELS} value={value.channels} onChange={(next) => set("channels", next)} />
      <AudienceCriteriaPicker dimensions={dimensions} value={value.audience} onChange={(next) => set("audience", next)} error={errors.audience} />
    </div>
  );
}
