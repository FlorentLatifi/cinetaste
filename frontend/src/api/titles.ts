import { apiFetch } from "./client";

export type Genre = { id: string; name: string };

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

export type Reason = {
  code: string;
  message: string;
  evidence: Record<string, unknown>;
};

export type RecommendationItem = {
  title: Title;
  score: number;
  reasons: Reason[];
};

export function getForYou(accessToken: string, limit = 20) {
  return apiFetch<{ items: RecommendationItem[] }>(
    `/recommendations/for-you?limit=${limit}`,
    {},
    accessToken,
  );
}

export function getOnboardingCards(accessToken: string) {
  return apiFetch<{ items: Title[] }>("/onboarding/cards", {}, accessToken);
}

export function completeOnboarding(
  accessToken: string,
  reactions: { title_id: string; action: "like" | "dislike" }[],
) {
  return apiFetch(
    "/onboarding/complete",
    {
      method: "POST",
      body: JSON.stringify({ reactions }),
    },
    accessToken,
  );
}

export function interact(
  accessToken: string,
  titleId: string,
  event_type: "like" | "dislike" | "watchlist" | "not_interested" | "skip" | "view",
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

export function searchTitles(accessToken: string, q: string) {
  return apiFetch<Title[]>(`/titles/search?q=${encodeURIComponent(q)}`, {}, accessToken);
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
