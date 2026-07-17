import type { OnboardingAction } from "../../api/titles";

/** Must match backend MIN_ONBOARDING_RATINGS / MIN_ONBOARDING_POSITIVE. */
export const MIN_RATINGS = 6;
export const MIN_POSITIVE = 2;
export const BATCH_SIZE = 15;

export const RATE_OPTIONS: {
  action: OnboardingAction;
  label: string;
  hint: string;
  emoji: string;
  className: string;
}[] = [
  {
    action: "rate_1",
    label: "Bad",
    hint: "Not for me",
    emoji: "👎",
    className: "ob-rate-bad",
  },
  {
    action: "rate_2",
    label: "OK",
    hint: "Fine, not special",
    emoji: "😐",
    className: "ob-rate-ok",
  },
  {
    action: "rate_3",
    label: "Good",
    hint: "I'd recommend it",
    emoji: "👍",
    className: "ob-rate-good",
  },
  {
    action: "rate_4",
    label: "Favorite",
    hint: "Peak taste",
    emoji: "✦",
    className: "ob-rate-fav",
  },
];

export function isRating(action: OnboardingAction): boolean {
  return action.startsWith("rate_");
}

export function isPositive(action: OnboardingAction): boolean {
  return action === "rate_2" || action === "rate_3" || action === "rate_4";
}
