/**
 * Where people can reach whoever runs this deployment, for privacy requests,
 * terms questions and abuse reports. Set VITE_CONTACT_EMAIL at build time.
 *
 * Without it the pages point to the public issue tracker instead of printing
 * a placeholder address, which is what they used to do.
 */
export function contactEmailFrom(raw: string | undefined): string | null {
  const value = (raw ?? "").trim();
  // A typo or a leftover "TODO" in the setting must not become a mailto link.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) ? value : null;
}

export const CONTACT_EMAIL: string | null = contactEmailFrom(
  import.meta.env.VITE_CONTACT_EMAIL,
);

export const ISSUES_URL = "https://github.com/FlorentLatifi/cinetaste/issues";
