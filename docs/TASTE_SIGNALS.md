# Taste signal policy

How user actions update the CineTaste **taste profile** (sparse feature weights + dense embedding vector) and related feed behavior.

**Code source of truth:** [`backend/app/domain/taste_signals.py`](../backend/app/domain/taste_signals.py)  
Do not hard-code weights in random call sites — import from that module.

---

## Mental model

```
User action
    → InteractionEvent (append-only log, stores event_type + weight)
    → optional UserTitleState update (current relationship)
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

### History page

`GET /me/history` returns current `UserTitleState` rows in  
`like | dislike | watchlist | not_interested | rated | watched`  
(newest `updated_at` first). Optional `?state=` filters to one of those values.  
SPA History chips sync to the query string. **Clear** on a row sends `clear` so
the title can return to For You and prior events for that title are superseded
on recompute.

### Post-watch UI (title detail)

1. User taps **Watched** → rate panel (Bad / It's ok / Good / Favorite).  
2. Choosing a rating records `rate_1`…`rate_4` (full learning + feed exclusion).  
3. **Watched — skip rating** records `watched` only (weak positive, still excluded from For You).  
4. Undo uses `clear` like other feed actions.

---

## Detailed rules

### Rating scale (Bad → Favorite)

Use when the user **has seen** (or knows) the title:

1. Each step writes an `InteractionEvent` with the policy weight.
2. Sparse features of that title (genres, directors, cast, keywords, tone, lang, country, decade) are multiplied by the weight and summed into the profile.
3. The title embedding is blended into the dense taste vector with the same weight.
4. `rate_3` / `rate_4` / `like` (weight ≥ **0.85**) become **explain anchors** for human reasons.

Onboarding requires **≥ 6** real ratings (`rate_1`–`rate_4`) and **≥ 2** positive among `rate_2`–`rate_4` / `like`.  
`haven't_seen` does **not** count toward either gate.

### Undo / clear (feed toast)

| | |
|--|--|
| Taste features | Prior events for that **title** are ignored after `clear` (append-only undo) |
| Taste vector | Same — recompute skips superseded events |
| UserTitleState | Set to **`none`** (not excluded from For You) |
| When to use | SPA “Undo” after Pass / Not interested / Like / Save |

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
