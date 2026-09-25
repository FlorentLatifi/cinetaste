import { apiFetch } from "./client";
import type { OnboardingReaction, RecommendationItem, Title } from "./titles";

/**
 * Guest mode: no token is sent and nothing is stored server-side. The answers
 * travel with each request; the browser keeps them (features/guest/guestDraft)
 * so they can be replayed into onboarding if the guest signs up.
 */
export function getGuestCards(opts?: { limit?: number; exclude?: string[] }) {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  for (const id of opts?.exclude ?? []) params.append("exclude", id);
  const qs = params.toString();
  return apiFetch<{ items: Title[] }>(`/guest/cards${qs ? `?${qs}` : ""}`, {}, null);
}

export function getGuestRecommendations(reactions: OnboardingReaction[], limit = 20) {
  return apiFetch<{ items: RecommendationItem[] }>(
    "/guest/recommendations",
    { method: "POST", body: JSON.stringify({ reactions, limit }) },
    null,
  );
}
