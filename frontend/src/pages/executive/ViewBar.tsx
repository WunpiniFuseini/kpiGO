/**
 * Saved personal views of the Executive dashboard (PRD EX-9). A reader names the
 * current filter state — the period and how far each breakdown is drilled — and
 * recalls it later. Views are each reader's own, so this bar touches only the
 * signed-in reader's own saved views; it never changes what anyone else sees.
 *
 * The bar degrades quietly: if the list cannot load it simply offers Save, since
 * saving a view is never blocked by not being able to list them.
 */
import { type FormEvent, useState } from "react";

import { useMutation, useQuery } from "../../api/useAction";
import { SelectField, TextField } from "../../components/Field";

export type DashboardState = { period_key: string; drill: Record<string, string> };

export function ViewBar({
  state,
  onApply,
}: {
  state: DashboardState;
  onApply: (state: DashboardState) => void;
}) {
  const [list, reload] = useQuery("executive.view.list", {});
  const [, save] = useMutation("executive.view.save");
  const [, remove] = useMutation("executive.view.delete");
  const [selected, setSelected] = useState("");
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const views = list.status === "ready" ? list.data.views : [];
  const options = [
    { value: "", label: views.length ? "Saved views…" : "No saved views" },
    ...views.map((v) => ({ value: v.name, label: v.name })),
  ];

  function apply(viewName: string) {
    setSelected(viewName);
    const found = views.find((v) => v.name === viewName);
    if (found) onApply({ period_key: found.state.period_key, drill: found.state.drill ?? {} });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    setError(null);
    const done = await save({ name: trimmed, state });
    if (!done) {
      setError("That view could not be saved.");
      return;
    }
    setNaming(false);
    setName("");
    setSelected(trimmed);
    reload();
  }

  async function del() {
    if (!selected) return;
    await remove({ name: selected });
    setSelected("");
    reload();
  }

  return (
    <div className="kg-viewbar" role="group" aria-label="Saved view controls">
      <SelectField
        label="Saved views"
        value={selected}
        options={options}
        disabled={views.length === 0}
        onChange={(e) => apply(e.target.value)}
      />
      {selected ? (
        <button type="button" className="kg-btn kg-btn--link" onClick={() => void del()}>
          Delete
        </button>
      ) : null}
      {naming ? (
        <form className="kg-viewbar__save" onSubmit={(e) => void submit(e)} noValidate>
          <TextField
            label="View name"
            value={name}
            error={error ?? undefined}
            autoFocus
            onChange={(e) => setName(e.target.value)}
          />
          <button type="submit" className="kg-btn kg-btn--primary" disabled={!name.trim()}>
            Save
          </button>
          <button
            type="button"
            className="kg-btn"
            onClick={() => {
              setNaming(false);
              setError(null);
            }}
          >
            Cancel
          </button>
        </form>
      ) : (
        <button type="button" className="kg-btn" onClick={() => setNaming(true)}>
          Save view
        </button>
      )}
    </div>
  );
}
