# Taste signal policy

How user actions update the CineTaste **taste profile** (sparse feature weights + dense embedding vector) and related feed behavior.

**Code source of truth:** [`backend/app/domain/taste_signals.py`](../backend/app/domain/taste_signals.py)  
Do not hard-code weights in random call sites — import from that module.

---

## Mental model

```
User action
    → InteractionEvent (append-only log, stores event_type + weight)
    → UserTitleState = resolve_title_state(this title's events)  (current relationship)
    → TasteService.recompute_profile()
         • sparse: feature_snapshot[key] × weight  (genres, people, tone, …)
         • dense:  blend title embeddings by weight
         • explain memory: strong positives become “because you liked X” anchors
```

| Question | Rule |
|----------|------|
| Does it update taste? | Only if policy `updates_taste` and `abs(weight) ≥ ε` |
| Positive vs negative? | Sign of `weight` |
| How strong? | Magnitude of `weight` (see table) |
| Hide from For You? | `exclude_from_feed` on resulting **state** |
| Cite in explanations? | Strong positive + `explain_anchor_eligible` |

---

## Policy table

| Action | `event_type` | Weight | Polarity | Updates taste vector / features? | `user_title_state` | Exclude from For You? | Special handling |
|--------|--------------|--------|----------|----------------------------------|--------------------|------------------------|------------------|
| **Bad** | `rate_1` | **−0.90** | negative | **Yes** — strong | `dislike` | Yes | Seen + disliked. Pushes away shared features. |
| **It's ok** | `rate_2` | **+0.30** | positive | **Yes** — weak | `rated` | Yes | Seen, mild like. Not an explain anchor alone. |
| **Good** | `rate_3` | **+1.00** | positive | **Yes** | `like` | Yes | Primary positive rating. Explain anchor. |
| **Favorite** | `rate_4` | **+1.55** | positive | **Yes** — strongest | `like` | Yes | Highest boost. Preferred explain anchor. |
| **Haven't seen it** | `haven't_seen` | **0.00** | zero | **No** | `haven't_seen` | **No** | Unfamiliarity ≠ dislike. Log only; may still recommend. |
| **Not interested** | `not_interested` | **−0.40** | negative | **Yes** — mild | `not_interested` | Yes | Rejection without full rating scale. Milder than Bad. |
| **Watchlist / Save** | `watchlist` | **+0.45** | positive | **Yes** — mild | `watchlist` | Yes | Intent, not quality. Weaker than Good. |
| **Like** (feed shortcut) | `like` | **+1.00** | positive | **Yes** | `like` | Yes | Same strength as Good (`rate_3`). |
| **Dislike / Pass** (feed) | `dislike` | **−0.85** | negative | **Yes** | `dislike` | Yes | ≈ Bad; prefer `rate_1` when on rating UI. |
| **Undo / clear** | `clear` | **0.00** | zero | **No** | `none` | **No** | Soft undo: title can reappear; recompute ignores events for that title up to the clear. |
| **Skip** | `skip` | **−0.15** | negative | **Yes** — tiny | *(no state)* | No | Soft dismiss; barely moves profile. |
| **View** | `view` | **+0.05** | neutral | **Yes** — near-zero | *(no state)* | No | Impression analytics; never treat as a like. |
| **Watched (no rating)** | `watched` | **+0.20** | positive | Yes — weak | `watched` | Yes | Prefer `rate_1`–`rate_4` after watch when possible. |
| **Watched + liked** | `watched_liked` | **+1.10** | positive | Yes | `like` | Yes | Convenience; slightly above Good. |
| **Watched + disliked** | `watched_disliked` | **−0.95** | negative | Yes | `dislike` | Yes | Strong negative — they invested time. |

### Taste snapshot export / import

| Action | Endpoint | Behavior |
|--------|----------|----------|
| Export | `GET /me/taste/export` | Human-readable likes/dislikes + anchor names; **no** dense embedding |
| Import merge | `POST /me/taste/import` | Soft-scales chips into `__import_overlay__` (survives recompute), then recomputes |
| Clear import | `DELETE /me/taste/import` | Drops overlay and recomputes from live events only |

Import does **not** rewrite interaction history. Overlay weights are capped and
scaled (`IMPORT_MERGE_SCALE`) so live ratings still dominate. For You slate cache
is invalidated after merge/clear. Account shows **Clear imported snapshot** when
an overlay is present.

### History page

`GET /me/history` returns a page of current `UserTitleState` rows in  
`like | dislike | watchlist | not_interested | rated | watched`  
(newest `updated_at`, then `title_id` DESC).  

| Query | Meaning |
|-------|---------|
| `limit` | Page size (default 20, max 100) |
| `state` | Optional filter to one history state |
| `cursor` | Opaque keyset cursor from previous `next_cursor` |

Response: `{ items, next_cursor, has_more }`. SPA appends pages via **infinite
scroll** (IntersectionObserver) with a **Load more** fallback; filter chips
reset the cursor. **Clear** on a row sends `clear` so the title can return to
For You and prior events for that title are superseded on recompute.

### Post-watch UI (title detail)

1. User taps **Watched** → rate panel (Bad / It's ok / Good / Favorite).  
2. Choosing a rating records `rate_1`…`rate_4` (full learning + feed exclusion).  
3. **Watched — skip rating** records `watched` only (weak positive, still excluded from For You).  
4. Once a title has a rating the page shows it ("Seen it · Loved it") and no
   longer offers **Want to watch**, **Not for me** or **skip rating** — none of
   them would change the title's state (see *Durable title state*), and their
   un-toggle would have sent `clear`.
5. Undo re-sends the previous event when that is enough (rating → other
   rating); it sends `clear` + the previous event only to step back from a
   rating to an unrated state, so it never erases a rating.

---

## Detailed rules

### Rating scale (Bad → Favorite)

Use when the user **has seen** (or knows) the title:

1. Each step writes an `InteractionEvent` with the policy weight.
2. **One signal per title decides the profile.** The event log is collapsed to
   the latest event of the strongest *tier* (opinion > intent > implicit) after
   the last `clear`. So a later "Didn't like it" overrides an earlier "Loved it",
   marking a rated title as watched does not weaken the rating, and repeated
   views count once. Summing every event ever recorded let stale and
   contradictory clicks pile up.
3. Sparse features of that title (genres, directors, cast, keywords, tone, lang, country, decade) are multiplied by the weight and summed into the profile.
4. **Positive** signals blend into the dense taste vector. Negatives act only
   through the sparse penalty: subtracting disliked titles pointed the vector
   away from everything and retrieved arbitrary neighbours.
5. Signals decay with age — half their influence after `TASTE_HALF_LIFE_DAYS`
   (default 365, `0` disables) — because taste drifts.
6. `rate_3` / `rate_4` / `like` (weight ≥ **0.85**) become **explain anchors** for human reasons.

Onboarding requires **≥ 6** real ratings (`rate_1`, `mid`, `rate_2`–`rate_4`) and **≥ 2** positive among `rate_2`–`rate_4` / `like`.  
`haven't_seen` does **not** count toward either gate, and repeating the same
title counts once.

### Durable title state (`user_title_state`)

`UserTitleState.state` is **derived from the title's whole event log**, not
from the newest event alone: `resolve_title_state()` in `taste_signals.py`,
called by `TasteService.record_interaction` (reads only that user + title's
events) and by the detail endpoint's `my_state` / `my_rating`.

Same collapsing rule as the profile:

1. Events at or before the latest `clear` are ignored; `clear` alone → `none`.
2. Strongest tier wins — opinion > intent > none (`haven't_seen`); within a
   tier the latest wins. `view` / `skip` set no state.

| Sequence | State | `my_rating` | Why |
|----------|-------|-------------|-----|
| `rate_4` → `watchlist` | `like` | `rate_4` | Intent never replaces an opinion |
| `rate_4` → `watched` | `like` | `rate_4` | "Seen it, skip rating" keeps the rating |
| `rate_3` → `haven't_seen` | `like` | `rate_3` | `haven't_seen` never replaces anything — stays out of For You |
| `watchlist` → `rate_3` | `like` | `rate_3` | An opinion replaces an intent |
| `watchlist` → `watched` | `watched` | — | Latest intent wins |
| `rate_4` → `rate_1` / `not_interested` | `dislike` / `not_interested` | `rate_1` / — | Latest opinion wins |
| `rate_4` → `clear` → `rate_1` | `dislike` | `rate_1` | Only events after the clear count |

**Watchlist and a rating do not coexist.** Once a title is rated, the state
is the rating: a rated title is a seen title, the watchlist is for things to
watch next, and keeping one state per title keeps feed exclusion, History and
the detail page in agreement. The SPA therefore shows the rating instead of
"Want to watch" on a rated title. A `watchlist` event on a rated title is still
logged (and still ignored by the profile, which keeps the rating).

Rows written before this rule can be recomputed with
`python -m app.scripts.repair_title_states` (dry run by default; `--apply` to
write). It is idempotent and preserves `updated_at`.

### Undo / clear (feed toast)

| | |
|--|--|
| Taste features | Prior events for that **title** are ignored after `clear` (append-only undo) |
| Taste vector | Same — recompute skips superseded events |
| UserTitleState | Set to **`none`** (not excluded from For You) |
| When to use | SPA “Undo” after Pass / Not interested / Like / Save; History **Clear**; un-toggling “In your watchlist” / “Not for you” on an **unrated** title |
| Never | To remove an intent from a rated title — `clear` would erase the rating too |

For You's Undo waits for the action's own request to finish before sending
`clear`; a `clear` that arrived first would be applied first and the action
would stick.

A later rating after `clear` applies normally (only events *after* the latest clear count).

### Haven't seen it → **zero signal**

| | |
|--|--|
| Taste features | **No change** |
| Taste vector | **No change** |
| Explain anchors | Not added |
| For You | Still eligible (user may love a rec of something they haven’t seen) |
| Why log it? | Onboarding progress, avoid re-showing the same card, analytics |

### Not interested → mild negative

Use when the user rejects the title (or its vibe) without using the full scale.  
Stronger avoidance than skip, **weaker** than Bad — they may not have deep knowledge of the film.

### Watchlist → mild positive intent

Saving means “I might watch this,” not “this defines my taste.”  
Weight **+0.45** so a large watchlist cannot dominate true Favorites.  
While `watchlist`, the title is hidden from For You to reduce duplicate cards.

### Like / Dislike shortcuts

Home-feed buttons map to `like` / `dislike` for speed. Strength matches Good / ~Bad.  
Onboarding prefers the explicit 1–4 scale + haven’t seen / not interested.

### Future: watched

When “mark as watched” ships:

1. Prefer collecting a rating immediately (`rate_*`).
2. If only “watched” is available, apply weak **+0.20** and exclude from For You.
3. `watched_liked` / `watched_disliked` are optional one-shot convenience events.

---

## Recompute guarantees

Implemented in `TasteService.recompute_profile`:

1. Load all `InteractionEvent`s for the user (append-only history).
2. Skip events where `affects_taste(event_type, weight)` is false  
   (includes **haven't_seen** and any `abs(weight) < ε`).
3. Accumulate sparse features; blend embeddings.
4. Cap and **family-normalize** features so keywords don’t drown directors.
5. Build explain memory from strong positive anchors.
6. Bump profile `version` (invalidates cached For You slates).

Negative weights contribute to sparse features and the vector blend so scoring can **penalize** matching directors/tones the user dislikes.

---

## For You exclusion

Titles in these **states** are not re-recommended on the main slate:

`like`, `dislike`, `not_interested`, `watchlist`, `rated`, `watched` (when used)

**Not excluded:** `haven't_seen`, impression-only (`view`/`skip` with no state).

Source: `FEED_EXCLUDE_STATES` derived from policies where `exclude_from_feed=True`.

---

## API surface

| Endpoint | Allowed actions |
|----------|-----------------|
| `POST /onboarding/complete` | `haven't_seen`, `not_interested`, `rate_1`…`rate_4` (+ legacy `like`/`dislike` → `rate_3`/`rate_1`) |
| `POST /titles/{id}/interactions` | Active set: ratings, watchlist, not_interested, skip, view, haven’t_seen, like, dislike |

Future `watched*` types are defined in code; add them to the request schema when the UI ships.

---

## Consistency checklist

When adding a new action:

1. Add a `SignalPolicy` row in `backend/app/domain/taste_signals.py`.
2. Update this doc’s tables.
3. If user-facing now: add to `ACTIVE_INTERACTION_EVENT_TYPES` and API schema regex.
4. Extend unit tests in `tests/test_taste_signals.py`.
5. Never fork a second weight map in `recommendation_service` or the frontend.

---

## Related

* Sparse features & embeddings: `backend/app/recommendation/embeddings.py`
* Scoring + reasons: `pipeline.py`, `explanations.py`
* Profile recompute: `backend/app/application/taste_service.py`
* Feed filters: `recommendation_service.for_you` uses `FEED_EXCLUDE_STATES`
