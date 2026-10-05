import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

interface FieldBits {
  label: string;
  hint?: ReactNode;
  error?: string | null;
}

function describedBy(hintId: string, errorId: string, hint: unknown, error: unknown): string | undefined {
  return [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
}

export function TextField({ label, hint, error, ...input }: FieldBits & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className="kg-field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="kg-input"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(`${id}-hint`, `${id}-error`, hint, error)}
        {...input}
      />
      {hint ? (
        <span id={`${id}-hint`} className="kg-hint">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={`${id}-error`} className="kg-field-error">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function SelectField({
  label,
  hint,
  error,
  options,
  ...select
}: FieldBits & SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }) {
  const id = useId();
  return (
    <div className="kg-field">
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        className="kg-select"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(`${id}-hint`, `${id}-error`, hint, error)}
        {...select}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint ? (
        <span id={`${id}-hint`} className="kg-hint">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={`${id}-error`} className="kg-field-error">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function CheckboxGroup({
  legend,
  options,
  value,
  onChange,
  error,
}: {
  legend: string;
  options: { value: string; label: string }[];
  value: string[];
  onChange: (next: string[]) => void;
  error?: string | null;
}) {
  const id = useId();
  return (
    <div className="kg-field">
      <fieldset aria-describedby={error ? `${id}-error` : undefined}>
        <legend>{legend}</legend>
        {options.map((o) => (
          <label key={o.value} className="kg-check">
            <input
              type="checkbox"
              checked={value.includes(o.value)}
              onChange={(e) =>
                onChange(e.target.checked ? [...value, o.value] : value.filter((v) => v !== o.value))
              }
            />
            {o.label}
          </label>
        ))}
      </fieldset>
      {error ? (
        <span id={`${id}-error`} className="kg-field-error">
          {error}
        </span>
      ) : null}
    </div>
  );
}
