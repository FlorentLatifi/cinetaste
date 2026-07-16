import { useId, useState, type ChangeEvent } from "react";
import { evaluatePassword } from "../lib/passwordStrength";

type Props = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  required?: boolean;
  minLength?: number;
  /** Show strength meter + checklist (register / reset). */
  showStrength?: boolean;
  disabled?: boolean;
  name?: string;
};

export function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  required = false,
  minLength = 8,
  showStrength = false,
  disabled = false,
  name,
}: Props) {
  const [visible, setVisible] = useState(false);
  const inputId = useId();
  const strengthId = useId();
  const strength = showStrength ? evaluatePassword(value) : null;

  function handleChange(e: ChangeEvent<HTMLInputElement>) {
    onChange(e.target.value);
  }

  return (
    <div className="password-field">
      <label className="password-field-label" htmlFor={inputId}>
        {label}
      </label>
      <div className="password-field-control">
        <input
          id={inputId}
          name={name}
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          required={required}
          minLength={minLength}
          value={value}
          onChange={handleChange}
          disabled={disabled}
          aria-describedby={showStrength && value ? strengthId : undefined}
        />
        <button
          type="button"
          className="password-toggle"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          aria-controls={inputId}
        >
          {visible ? "Hide" : "Show"}
        </button>
      </div>

      {showStrength && value.length > 0 && strength && (
        <div id={strengthId} className="password-strength" aria-live="polite">
          <div className="password-strength-top">
            <span className="password-strength-label">
              Strength:{" "}
              <strong data-score={strength.score}>{strength.label}</strong>
            </span>
            <div
              className="password-strength-bar"
              role="meter"
              aria-valuemin={0}
              aria-valuemax={4}
              aria-valuenow={strength.score}
              aria-label="Password strength"
            >
              <span
                className="password-strength-fill"
                data-score={strength.score}
                style={{ width: `${(strength.score / 4) * 100}%` }}
              />
            </div>
          </div>
          <ul className="password-strength-checks">
            {strength.checks.map((c) => (
              <li key={c.id} data-met={c.met ? "true" : "false"}>
                <span aria-hidden="true">{c.met ? "✓" : "○"}</span> {c.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
