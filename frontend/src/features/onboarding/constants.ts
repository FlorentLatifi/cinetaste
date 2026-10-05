import type { OnboardingAction } from "../../api/titles";
import { ONBOARDING_RATINGS } from "../taste/ratingScale";

/** Must match backend MIN_ONBOARDING_RATINGS / MIN_ONBOARDING_POSITIVE. */
export const MIN_RATINGS = 6;
export const MIN_POSITIVE = 2;
export const BATCH_SIZE = 15;

/** Ratings shown during onboarding (worst → best), from the shared scale. */
export const RATE_OPTIONS = ONBOARDING_RATINGS.map((option) => ({
  action: option.event as OnboardingAction,
  label: option.label,
  hint: option.hint,
  emoji: option.emoji,
  className: option.className,
}));

export function isRating(action: OnboardingAction): boolean {
  // "mid" (It was ok) counts as a rating for the gate, like rate_1..rate_4.
  return action === "mid" || action.startsWith("rate_");
}

export function isPositive(action: OnboardingAction): boolean {
  return action === "rate_2" || action === "rate_3" || action === "rate_4";
}

/** Must match backend GUEST_MIN_RATINGS / GUEST_MIN_POSITIVE (routes/guest.py). */
export const GUEST_MIN_RATINGS = 3;
export const GUEST_MIN_POSITIVE = 1;
