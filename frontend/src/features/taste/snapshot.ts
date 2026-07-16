/**
 * Client-side validation for exported taste snapshots
 * (schema: cinetaste.taste_snapshot.v1 from GET /me/taste/export).
 */

export type SnapshotFeature = {
  key: string;
  family: string;
  label: string;
  weight: number;
};

export type SnapshotAnchor = {
  name: string;
  year?: number | null;
};

export type TasteSnapshotV1 = {
  schema: "cinetaste.taste_snapshot.v1";
  exported_at: string;
  profile_version: number;
  updated_at: string | null;
  has_vector: boolean;
  feature_count: number;
  anchor_count: number;
  likes: SnapshotFeature[];
  dislikes: SnapshotFeature[];
  anchors: SnapshotAnchor[];
  text?: string;
};

export type ParseSnapshotResult =
  | { ok: true; data: TasteSnapshotV1 }
  | { ok: false; error: string };

function isFeature(row: unknown): row is SnapshotFeature {
  if (!row || typeof row !== "object") return false;
  const r = row as Record<string, unknown>;
  return (
    typeof r.key === "string" &&
    typeof r.family === "string" &&
    typeof r.label === "string" &&
    typeof r.weight === "number" &&
    Number.isFinite(r.weight)
  );
}

function isAnchor(row: unknown): row is SnapshotAnchor {
  if (!row || typeof row !== "object") return false;
  const r = row as Record<string, unknown>;
  if (typeof r.name !== "string" || !r.name.trim()) return false;
  if (r.year != null && typeof r.year !== "number") return false;
  return true;
}

/** Parse JSON text or object from a previously downloaded export. */
export function parseTasteSnapshot(raw: unknown): ParseSnapshotResult {
  let data: unknown = raw;
  if (typeof raw === "string") {
    try {
      data = JSON.parse(raw);
    } catch {
      return { ok: false, error: "File is not valid JSON." };
    }
  }
  if (!data || typeof data !== "object") {
    return { ok: false, error: "Snapshot must be a JSON object." };
  }
  const o = data as Record<string, unknown>;
  if (o.schema !== "cinetaste.taste_snapshot.v1") {
    return {
      ok: false,
      error: 'Unsupported schema — expected "cinetaste.taste_snapshot.v1".',
    };
  }
  if (typeof o.exported_at !== "string") {
    return { ok: false, error: "Missing exported_at." };
  }
  if (typeof o.profile_version !== "number") {
    return { ok: false, error: "Missing profile_version." };
  }
  if (!Array.isArray(o.likes) || !o.likes.every(isFeature)) {
    return { ok: false, error: "Invalid likes list." };
  }
  if (!Array.isArray(o.dislikes) || !o.dislikes.every(isFeature)) {
    return { ok: false, error: "Invalid dislikes list." };
  }
  const anchors = Array.isArray(o.anchors) ? o.anchors : [];
  if (!anchors.every(isAnchor)) {
    return { ok: false, error: "Invalid anchors list." };
  }

  return {
    ok: true,
    data: {
      schema: "cinetaste.taste_snapshot.v1",
      exported_at: o.exported_at,
      profile_version: o.profile_version,
      updated_at: typeof o.updated_at === "string" ? o.updated_at : null,
      has_vector: Boolean(o.has_vector),
      feature_count: typeof o.feature_count === "number" ? o.feature_count : 0,
      anchor_count: typeof o.anchor_count === "number" ? o.anchor_count : anchors.length,
      likes: o.likes,
      dislikes: o.dislikes,
      anchors,
      text: typeof o.text === "string" ? o.text : undefined,
    },
  };
}

export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
