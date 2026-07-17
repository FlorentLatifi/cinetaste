# CineTaste Frontend — Production UX/UI + E2E Audit

**Date:** 2026-07-17  
**Scope:** `frontend/` SPA (React 19 + Vite + React Router)  
**Method:** Code inspection, architecture review, Playwright mocks (`e2e/*`), design critique against Netflix / Letterboxd / Apple TV / Spotify patterns.  
**Shipped through Wave 7:** Waves 1–6 plus `useOnboardingDeck`, stronger Vercel headers, onboarding e2e, frontend README refresh (see § Immediate improvements).

---

## Executive summary

CineTaste has a **strong product thesis** (taste-first, explainable picks, not a TMDb dump) and a **solid foundation**: JWT auth, onboarding swipe, For You + reasons, watchlist/history, taste import/export, contrast mode, ConfirmDialog, axe-backed e2e.

It does **not** yet feel like a finished premium discovery product end-to-end. Before this audit’s implementation pass, **For You was a multi-card poster grid** — catalog energy, not immersive discovery. Auth is a serviceable split layout, not a brand moment. There is **no public marketing landing** (guest hits login). Light mode is absent. Pref/filter “recommendation wizard” (genres chips, year sliders, etc.) is **not productized** — recs are algorithm-driven from onboarding + interactions, which is correct for the thesis but undersold in UI.

**Would I ship publicly today?** Soft-launch / friends & family **yes** with clear “beta” framing. App-store / paid launch **no** until Critical/High items below land.

---

## Scores (honest)

| Dimension | Score | Notes |
|-----------|------:|-------|
| **Overall** | **78 / 100** | Core loop + polish waves; ops remaining for public beta |
| **UI** | **78** | Immersive For You, landing, light theme, skeletons |
| **UX** | **76** | Clear onboarding → one-poster recs; error/empty recovery |
| **Accessibility** | **82** | Axe gates, contrast, tablist keys, forced-colors |
| **Performance** | **78** | Route split, srcset, prefetch, optimistic slate |
| **Responsiveness** | **80** | Bottom nav + poster-first stages |
| **Code quality** | **76** | Feature hooks (`useForYouQueue`), lib/poster, password field |
| **Maintainability** | **74** | Better structure; CSS still monolithic |
| **Production readiness** | **72** | Soft-launch checklist; deploy still operator-owned |

---

## Immediate improvements

### Wave 7 — Onboarding extraction + deploy hardening

- `features/onboarding/useOnboardingDeck.ts` + `constants.ts`
- Vercel: HSTS, Permissions-Policy, long-cache hashed assets
- E2E: onboarding skip advance + rate scale
- Frontend README aligned with current product

### Wave 6 — Architecture + motion + soft-launch doc

- `features/for-you/useForYouQueue.ts` — slate logic extracted from HomePage
- React Router `viewTransition` + shared `title-poster` name (respects reduced motion)
- History “load more” uses catalog skeleton tiles
- `docs/SOFT_LAUNCH_CHECKLIST.md` for operator go-live

### Wave 5 — Skeletons, optimistic recs, responsive images, a11y tabs

- `CatalogSkeleton` / `DetailSkeleton` on Search, History, Watchlist, Title detail
- For You optimistic Pass/Save/Like (rollback on API failure)
- TMDb `srcset` (w185–w780) on PosterCard, For You hero, title detail
- Account tablist: Arrow / Home / End roving focus

### Wave 4 — Account IA, light mode, 404, prefetch

- Account progressive disclosure: Profile / Taste / Appearance / Danger tabs (`?tab=`)
- Light + system color scheme (`data-theme`, FOUC-safe in `index.html`)
- Prefetch next For You poster image
- Real `NotFoundPage` instead of silent redirect to `/`

### Wave 3 — Interaction polish + mobile shell + chaos

- For You keys: `1`/`P` Pass · `2`/`S` Save · `3`/`L` Like · `U` Undo (+ on-screen hints)
- Mobile bottom nav (≤720px); topbar primary nav hidden on phone
- `setSessionExpiredHandler` — 401 + failed refresh clears auth → guest landing
- E2E: keyboard pass, double-click single POST, dead session, bottom nav

### Wave 2 — Landing, auth UX, code splitting

- Guest `/` → `LandingPage` (poster-first demo, How it works, Start free)
- Auth `/` → immersive For You (`RootRoute` in `App.tsx`)
- `PasswordField`: show/hide + strength (register/reset); show/hide (login)
- `lib/poster.ts` shared helpers; authenticated routes lazy-loaded

### Wave 1 — For You → immersive single-poster stage

**Before:** `HomePage` rendered `ul.poster-grid.for-you` with many `PosterCard`s — dashboard of random cards.  
**After:** One focused pick at a time:

```
┌─────────────────────────────────────┐
│  FOR YOU · N left in this slate     │
│                                     │
│         ┌─────────────┐             │
│         │             │             │
│         │   POSTER    │  ← primary  │
│         │   (2:3)     │             │
│         └─────────────┘             │
│                                     │
│           Title (serif)             │
│        year · type · ★ · genres     │
│     ┌─ Why this pick ─────────┐     │
│     │ reason lines…           │     │
│     └─────────────────────────┘     │
│   [ Pass ] [ Save ] [ Like ]        │
│        Full details · N more        │
└─────────────────────────────────────┘
```

**Files:**
- `frontend/src/pages/HomePage.tsx` — queue model (`items[0]`), exit/enter animation, poster-centric layout, skeleton, empty slate
- `frontend/src/styles/global.css` — `.fy-*` system (ambient blur, actions, shimmer, reduced-motion, forced-colors)

**UX why:** Attention is a scarce resource. Netflix/Letterboxd put **one image** in the emotional center. Actions become decisive (Pass/Save/Like) instead of scanning a grid of equal weight. Reasons sit under the title like a sommelier note, not a card footer.

Catalog pages (Search / Watchlist / History) **correctly stay grids** — browsing vs deciding.

---

## Part 1 — User journey (as first-time user)

| Step | Path | Feel | Notes |
|------|------|------|-------|
| Landing | `/` → redirect login | Functional, not premium | No marketing page; brand lives only in auth aside |
| Login | `/login` | Professional enough | No password visibility toggle; no OAuth |
| Register | `/register` | OK | Basic validation |
| Forgot / Reset | `/forgot-password`, `/reset-password` | Works | Depends on email delivery ops |
| Onboarding | `/onboarding` | **Strongest surface** | Immersive poster + rate scale; progress clear |
| For You | `/` | **Now immersive** | Was grid; now single focus + undo toast |
| Search | `/search` | Catalog | Poster grid; filters minimal |
| Title detail | `/titles/:id` | Solid | Rate panel, similar, where-to-watch |
| Watchlist / History | | Solid | Infinite scroll history; clear actions |
| Account / taste | `/account` | Power-user | Import/export advanced; may overwhelm |
| Settings | — | **Missing as product surface** | Contrast on account/auth only; no prefs page |
| OAuth | — | **None** | |
| Modals | ConfirmDialog | Good | Focus trap used for merge/clear |
| Toasts | ActionToast | Good | Undo on For You / detail |
| Loading | Mixed | Spinner vs skeleton | For You now skeleton; others spinner |
| Empty | Mixed | Callouts | For You empty improved |
| Errors | Form alerts | Generic | Few retry CTAs on API fail |

**Does it feel professional / modern / premium / finished / confidence-inspiring?**  
Onboarding + (new) For You: **yes-ish**. Auth + Account: **professional, not premium**. Overall product: **beta-premium** — trust for taste logic is higher than visual finish on secondary surfaces.

---

## Part 2 — Visual design (page scores /10)

| Page | Score | Why |
|------|------:|-----|
| Login / Register | 6.5 | Clean split layout; aside is marketing copy without poster/cinema texture |
| Forgot / Reset | 6.5 | Consistent auth chrome; thin brand story |
| Onboarding | **8.5** | Ambient glow, poster hero, serif titles, progress — closest to “Apple + Letterboxd” |
| For You (new) | **8.0** | Centered poster hierarchy; could still add keyboard shortcuts + peek of next |
| Search | 7.0 | Poster grid works; search bar plain |
| Title detail | 7.5 | Info-rich; not as cinematic as For You |
| Watchlist | 7.0 | Consistent cards |
| History | 7.0 | Filters + infinite scroll; badge clutter risk |
| Account | 6.5 | Dense power UI; needs hierarchy simplification |
| App shell / nav | 7.0 | Clear; mobile horizontal scroll OK; no bottom nav |

**System tokens:** Dark cinema (`#0b0c10`, gold accent `#e8c27a`), DM Sans + Instrument Serif — good brand base. Gaps: no light theme, spacing scale not formalized, buttons mixed (`btn` vs `ob-btn` vs `fy-act`).

---

## Part 3 — Landing page critique + redesign

### Current state
There is **no dedicated landing**. Guests land on **Login** (`GuestOnly` → `/login`). The “landing” is `auth-aside` copy:

> Movies & TV that match how you actually watch.

### Critique
- No poster as hero — fails the brief’s primary focus
- No demo of For You / reasons
- No social proof or “how it works”
- CTA is “Sign in,” not “Discover”

### Proposed public landing (`/welcome` or unauth `/`)

```
┌──────────────────────────────────────────────┐
│  CineTaste                    Sign in · Join │
│                                              │
│              [ blurred ambient poster ]      │
│                 ┌──────────┐                 │
│                 │  POSTER  │                 │
│                 └──────────┘                 │
│               Film Title (serif)             │
│         “Because you love quiet dramas…”     │
│     [ Pass ]  [ Save ]  [ Like ]  (preview)  │
│                                              │
│   Taste-first discovery — not popularity.    │
│        [ Start free → Register ]             │
│                                              │
│   01 Rate what you know                      │
│   02 Get one pick at a time                  │
│   03 Every pick explains why                 │
└──────────────────────────────────────────────┘
```

**Implementation sketch:** `LandingPage.tsx` + route before auth gate; use static demo titles (no API) or public `/health` + sample. Keep login/register secondary.

---

## Part 4 — Recommendation experience (ideal flow)

### Current product model (correct)
1. Onboarding rates → taste vector  
2. For You returns scored titles + **reasons**  
3. Like / Save / Pass → interactions → profile updates  

No multi-step preference wizard — **good** for “taste from behavior.” Optional filters can be advanced, not required.

### Ideal flow (design)

| Phase | UI | Interaction |
|-------|-----|-------------|
| A. Onboarding | Single poster (exists) | Haven’t seen / Not interested / 1–4 stars |
| B. First For You | Immersive stage (**shipped**) | Pass / Save / Like + undo |
| C. Ongoing | Same stage; optional “Refine” drawer | Chips: genres to lean away; year range; language |
| D. Deep dive | Title detail | Rate, similar, providers |

**Preference UI guidance (if added later):**
- Genres → chips (multi)  
- Moods → icon chips (max 3)  
- Language → pills  
- Years / runtime → dual range sliders  
- Actors → autocomplete (search API)  
- Popularity → toggle “Hidden gems only” (maps to existing reason codes)

**Do not** replace onboarding with a 12-field form. Keep **one page, one decision**.

---

## Part 5 — Interaction design

| Area | Current | Improve |
|------|---------|---------|
| Hover | Cards lift; buttons subtle | For You Like already elevates; unify press states |
| Focus | `:focus-visible` gold | Ensure toast Undo is always keyboard reachable |
| Loading | Spinner pages | Extend shimmer skeletons to Search/History |
| Disabled | opacity | Keep aria-disabled + label “Working…” where long |
| Page transitions | Hard route change | Optional View Transitions API for title detail |
| Optimistic UI | For You removes after API | Consider optimistic remove + rollback on error (faster feel) |
| Keyboard For You | Focus title | Add `1` Pass / `2` Save / `3` Like (document in hint) |
| Touch | OK | Ensure 44px targets on `fy-act` (currently padded well) |
| Double-click | Disabled while busy | E2E should spam-click Pass — already guarded |

---

## Part 6 — Forms

| Form | Issues | Fix |
|------|--------|-----|
| Login | No show-password; no inline email format msg | Toggle + `aria-invalid` + describedby |
| Register | Password strength not shown | Strength meter + requirements list |
| Forgot | OK if email sent copy is honest | Never leak whether email exists (backend) |
| Reset | Token in URL | Confirm expiry UX |
| Search | Debounce? | Confirm debounce; empty submit should no-op |
| Account import | File input labeled | Good; keep confirm dialogs |
| Onboarding rate | Excellent | Model for other forms |

---

## Part 7 — Responsiveness

| Breakpoint | Issues found in code |
|------------|----------------------|
| Desktop | For You max-width 720 — intentional focus |
| Tablet | Onboarding 2-col → 1-col at 720 |
| Phone | Topbar nav scrolls; user chip hidden | Bottom nav would be clearer for For You / Search / Watchlist |
| Landscape phone | Poster max-height needed on onboarding — exists; For You poster may dominate height — cap with `max-height: min(62vh, 520px)` if needed |

---

## Part 8 — Performance

| Metric / area | Observation | Recommendation |
|---------------|-------------|----------------|
| Bundle | ~300 kB JS / 92 kB gzip (single chunk) | `React.lazy` routes: Account, History, Search |
| Posters | Lazy on `PosterCard`; For You uses high fetchPriority | Keep w780 only on focus card |
| Re-renders | Page-local state — fine | Extract `useForYouQueue` hook if logic grows |
| API | For You one shot | Prefetch next poster image when queue advances |
| Images | TMDb remote | `srcset` w342/w500/w780 |
| Memory | Toast timers cleaned | OK |

---

## Part 9 — Accessibility

**Strengths:** skip link, landmark main, contrast toggle, forced-colors CSS, ConfirmDialog, form labels, aria on actions, Playwright axe specs.

**Gaps:**
- No live region for “slate count” changes beyond polite queue (OK)
- Auth pages: ensure error `role="alert"` (present)
- Dialogs: verify return focus after close (ConfirmDialog)
- Color: gold on dark — recheck with axe in high contrast
- For You: poster link has empty decorative `alt` + separate title — good
- Missing: accessible name for brand mark alone (has `aria-label` on link)

**WCAG estimate:** AA for many flows; full AA audit still needed for all Account controls.

---

## Part 10 — Code quality / architecture

```
frontend/src/
  api/           # thin fetch wrappers
  components/    # AppShell, PosterCard, ConfirmDialog, ActionToast, ContrastToggle
  features/auth/ # AuthContext
  pages/         # route-level (heavy)
  styles/global.css  # monolith
```

**Anti-patterns / debt:**
1. **CSS monolith** — split by domain (`fy.css`, `ob.css`, `auth.css`) or CSS modules  
2. **Duplicated poster URL helpers** — `PosterCard.posterSrc` + `HomePage.heroPosterUrl` + `OnboardingPage.heroPosterUrl` → single `lib/poster.ts`  
3. **No design system package** — buttons diverge (`btn` / `ob-btn` / `fy-act`)  
4. **Page components own too much** — extract `ForYouStage`, `OnboardingDeck`  
5. **No React Query / SWR** — manual `useEffect` fetch; harder caching  
6. **Catch-all `*` → `/`** — masks 404 UX  

**Recommended architecture:**
- `components/ui/` primitives  
- `components/title/` PosterCard, TitleMeta  
- `features/for-you/` Home + hook  
- `features/onboarding/`  
- TanStack Query for server state  

---

## Part 11 — E2E / chaos (Playwright-style)

Covered today (mocks): For You pass/undo, History clear/filter/scroll, Account merge/clear confirm, Title detail rate.

**Additional cases to add:**

| Case | Expected |
|------|----------|
| Double-click Pass | Single interact call |
| Refresh mid-toast | Toast gone; state from server |
| Back from title detail | Same focus card if not acted |
| `/titles/not-a-uuid` | Friendly not found |
| 500 on For You | Alert + Retry |
| 401 | Logout / login redirect |
| Empty `items: []` | Empty slate (now) |
| Malformed item (null name) | Guard render |
| Offline | User-visible error |
| Slow API | Skeleton ≥ 200ms |

---

## Part 12 — UX critique (honest)

| Question | Answer |
|----------|--------|
| Would users enjoy it? | Yes for film people who finish onboarding |
| Trust? | Reasons + taste export help; marketing landing would help more |
| Recommend? | After immersive For You — more likely |
| Return? | If slate stays fresh and undo is reliable |
| Senior product design review? | **Not yet** — needs landing + settings + empty/error craft |
| FAANG frontend interview? | Architecture mid; a11y good; performance incomplete |
| Startup MVP review? | **Pass** for closed beta |
| Ship production? | Soft-launch yes; broad launch after Critical/High |

---

## Part 13 — Production readiness (prioritized)

### Critical
1. ~~Immersive For You (not multi-card dashboard)~~ **Done this pass**  
2. Honest error + **Retry** on For You / Search load failures  
3. Auth session edge cases (expired refresh mid-action) fully tested  
4. Env/config for production API URL + CORS documented (see `docs/DEPLOY.md`)

### High
5. Public marketing landing with poster focus  
6. Password visibility + strength on register  
7. Route-level code splitting  
8. Skeleton loaders on Search / History / Detail  
9. Keyboard shortcuts on For You  
10. Bottom nav (mobile) for primary destinations  
11. Expand Playwright chaos suite (401/500/empty/double-submit)

### Medium
12. Light mode or system `prefers-color-scheme`  
13. Extract `lib/poster.ts` + shared Title meta  
14. Prefetch next For You poster  
15. View transitions to detail  
16. Account page progressive disclosure  
17. 404 page instead of silent redirect  

### Low
18. Micro-copy pass (brand voice)  
19. Optional refine drawer (genres/year)  
20. Motion design tokens documentation  
21. Storybook for PosterCard / ConfirmDialog  

---

## Part 14 — Deliverables checklist

| Deliverable | Location |
|-------------|----------|
| Executive summary + scores | This doc |
| Bug / inconsistency list | Parts 5–11 + Critical/High |
| Recommendation redesign | Part 4 + Immediate improvements |
| Landing redesign | Part 3 |
| Wireframes | ASCII in Parts 3–4 + Immediate |
| Code-level recs | Parts 8–10, roadmap |
| Implementation roadmap | Part 13 |

### Prioritized roadmap (implementation order)

1. **Critical:** Immersive For You ✅  
2. **Critical:** Load error Retry UI on HomePage  
3. **High:** Landing page + guest marketing  
4. **High:** Form password UX  
5. **High:** Lazy routes + poster util extract  
6. **High:** E2E chaos + a11y re-run  
7. **Medium:** Mobile bottom nav + light mode  
8. **Medium:** Account simplification  
9. **Low:** Refine filters, Storybook  

---

## Component redesign suggestions (specific)

| Component | Change |
|-----------|--------|
| `HomePage` | ✅ Immersive stage; next: `useForYouQueue` hook + keyboard |
| `PosterCard` | Keep for catalog; optional `size="hero"` variant if reused |
| `AppShell` | Add optional hide-nav for onboarding focus mode |
| `LoginPage` | Poster collage or single ambient title behind form |
| `AccountPage` | Tabs: Profile / Taste / Data |
| `ActionToast` | Fixed bottom-center under For You actions on mobile |

---

## Visual redesign tokens (proposal)

```css
/* Hierarchy for discovery surfaces */
--poster-radius: 18px;
--poster-shadow: 0 28px 64px rgba(0,0,0,.55);
--title-serif: "Instrument Serif", Georgia, serif;
--action-primary: linear-gradient(145deg, var(--accent), #c99a4a);
--focus-ring: 2px solid var(--accent-strong);
```

Spacing scale: 4 / 8 / 12 / 16 / 24 / 32 / 48.  
Max content: catalog `1200px`, discovery stage `720px`.

---

## Sign-off

| Role lens | Verdict |
|-----------|---------|
| Staff FE | Ship immersive For You; split routes next |
| Principal UI/UX | Direction right; landing + form polish required |
| Product (Netflix/Spotify) | Core loop viable; onboarding→one-pick is correct |
| A11y | Above average for MVP; keep axe CI |
| QA | Expand failure-path e2e |
| Performance | Fine for MVP; don’t ignore splitting |
| React architect | Extract features modules before more pages |

**Next engineering step after this commit:** Retry-on-error for For You + extract `lib/poster.ts` + Playwright double-click / empty slate cases.
