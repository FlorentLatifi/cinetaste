import { useContrast } from "../features/theme/contrast";

type Props = {
  /** Compact control for topbar / auth card footers */
  compact?: boolean;
};

export function ContrastToggle({ compact = false }: Props) {
  const { isHigh, toggle } = useContrast();

  return (
    <button
      type="button"
      className={compact ? "btn ghost contrast-toggle compact" : "btn ghost contrast-toggle"}
      onClick={toggle}
      aria-pressed={isHigh}
      aria-label={isHigh ? "Use standard contrast" : "Use high contrast"}
    >
      {isHigh ? "Standard contrast" : "High contrast"}
    </button>
  );
}
