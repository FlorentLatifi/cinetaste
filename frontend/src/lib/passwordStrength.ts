export type PasswordStrength = {
  score: 0 | 1 | 2 | 3 | 4;
  label: string;
  checks: {
    id: string;
    label: string;
    met: boolean;
  }[];
};

/** Client-side guidance only — server still enforces min length. */
export function evaluatePassword(password: string): PasswordStrength {
  const checks = [
    {
      id: "length",
      label: "At least 8 characters",
      met: password.length >= 8,
    },
    {
      id: "letter",
      label: "Contains a letter",
      met: /[a-zA-Z]/.test(password),
    },
    {
      id: "number",
      label: "Contains a number",
      met: /\d/.test(password),
    },
    {
      id: "symbol",
      label: "Contains a symbol",
      met: /[^a-zA-Z0-9]/.test(password),
    },
  ];

  const met = checks.filter((c) => c.met).length;
  let score: PasswordStrength["score"] = 0;
  if (password.length === 0) score = 0;
  else if (met <= 1) score = 1;
  else if (met === 2) score = 2;
  else if (met === 3) score = 3;
  else score = 4;

  const labels = ["", "Weak", "Fair", "Good", "Strong"] as const;
  return {
    score,
    label: labels[score],
    checks,
  };
}
