import { apiFetch } from "./client";

export type Genre = { id: string; name: string };

export type Credit = {
  name: string;
  credit_type: string;
  job: string | null;
  character: string | null;
  billing_order: number | null;
  profile_url: string | null;
};

export type Title = {
  id: string;
  media_type: string;
  name: string;
  overview: string | null;
  release_date: string | null;
  runtime: number | null;
  popularity: number;
  vote_average: number;
  poster_path: string | null;
  backdrop_path: string | null;
  original_language: string | null;
  genres: Genre[];
  poster_url: string | null;
};

export type TitleDetail = Title & {
  credits: Credit[];
  keywords: string[];
};

export type ProviderOffer = {
  provider_id: number;
  name: string;
  logo_url: string | null;
  display_priority: number;
};

export type WhereToWatch = {
  region: string;
  link: string | null;
  flatrate: ProviderOffer[];
  free: ProviderOffer[];
  ads: ProviderOffer[];
  rent: ProviderOffer[];
  buy: ProviderOffer[];
  available: boolean;
  attribution: string;
  source: string;
};

export type Reason = {
  /** Stable reason kind (because_you_liked, taste_blend, same_director, …). */
  code: string;
  /** User-facing explanation sentence. */
  message: string;
  /** Optional structured support (liked_titles, directors, tones, …). */
  evidence: Record<string, unknown>;
};

export type RecommendationItem = {
  title: Title;
  score: number;
  reasons: Reason[];
};

/** Onboarding / interaction actions that move (or don't move) taste. */
export type OnboardingAction =
  | "haven't_seen"
  | "not_interested"
  | "rate_1"
  | "rate_2"
  | "rate_3"
  | "rate_4";

export type OnboardingReaction = {
  title_id: string;
  action: OnboardingAction;
};

export function getForYou(accessToken: string, limit = 20) {
  return apiFetch<{ items: RecommendationItem[] }>(
    `/recommendations/for-you?limit=${limit}`,
    {},
    accessToken,
  );
}

export function getTitle(accessToken: string, titleId: string) {
  return apiFetch<TitleDetail>(`/titles/${titleId}`, {}, accessToken);
}

export function getSimilarTitles(accessToken: string, titleId: string, limit = 12) {
  return apiFetch<Title[]>(
    `/titles/${titleId}/similar?limit=${limit}`,
    {},
    accessToken,
  );
}

export function getWhereToWatch(
  accessToken: string,
  titleId: string,
  region?: string,
) {
  const qs = region ? `?region=${encodeURIComponent(region)}` : "";
  return apiFetch<WhereToWatch>(
    `/titles/${titleId}/where-to-watch${qs}`,
    {},
    accessToken,
  );
}

export function getOnboardingCards(
  accessToken: string,
  opts?: { limit?: number; exclude?: string[] },
) {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  for (const id of opts?.exclude ?? []) {
    params.append("exclude", id);
  }
  const qs = params.toString();
  return apiFetch<{ items: Title[] }>(
    `/onboarding/cards${qs ? `?${qs}` : ""}`,
    {},
    accessToken,
  );
}

export function completeOnboarding(
  accessToken: string,
  reactions: OnboardingReaction[],
) {
  return apiFetch<import("./auth").User>(
    "/onboarding/complete",
    {
      method: "POST",
      body: JSON.stringify({ reactions }),
    },
    accessToken,
  );
}

export type InteractionEventType =
  | "like"
  | "dislike"
  | "watchlist"
  | "not_interested"
  | "clear"
  | "skip"
  | "view"
  | "haven't_seen"
  | "rate_1"
  | "rate_2"
  | "rate_3"
  | "rate_4"
  | "watched"
  | "watched_liked"
  | "watched_disliked";

export function interact(
  accessToken: string,
  titleId: string,
  event_type: InteractionEventType,
) {
  return apiFetch<void>(
    `/titles/${titleId}/interactions`,
    {
      method: "POST",
      body: JSON.stringify({ event_type }),
    },
    accessToken,
  );
}

export function searchTitles(accessToken: string, q: string, limit = 24) {
  return apiFetch<Title[]>(
    `/titles/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    {},
    accessToken,
  );
}

export function getWatchlist(accessToken: string) {
  return apiFetch<Title[]>("/watchlist", {}, accessToken);
}

export function getCatalogStatus(accessToken: string) {
  return apiFetch<{
    title_count: number;
    with_embeddings: number;
    ready_for_onboarding: boolean;
  }>("/catalog/status", {}, accessToken);
}
