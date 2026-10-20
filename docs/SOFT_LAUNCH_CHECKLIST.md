# Launch checklist

What has to be true before CineTaste is opened to people who are not on the
team. Section 1 needs accounts and secrets, so only the owner can do it; the
rest is already in the code and is listed so it can be checked, not redone.

## 1. Owner actions (before inviting anyone)

- [ ] **Database on a paid plan.** Render deletes free databases after 30 days
      and does not back them up. Upgrade `cinetaste-db` before real accounts
      exist, then take one backup and drill one restore —
      `backend/tools/backup_database.sh` and
      [RUNBOOK.md](RUNBOOK.md#5-database-backup-and-restore).
- [ ] **Email.** Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
      `SMTP_FROM` on the API (a Gmail app password works to start). Check that
      *Forgot password* delivers a mail, **then** set
      `REQUIRE_EMAIL_VERIFICATION=true`. With it on, sign-up is verify-first:
      no account exists until the mailed link is opened with the chosen
      password (see [DEPLOY.md](DEPLOY.md#requiring-a-confirmed-email)).
- [ ] **Contact address.** Set `VITE_CONTACT_EMAIL` in the Vercel project and
      redeploy. The privacy and terms pages show it; without it they link to the
      GitHub issue tracker.
- [ ] **Site URL.** `frontend/.env.production` sets `VITE_SITE_URL`, which link
      previews (WhatsApp, Facebook, X, Slack) use for the share image. Update it
      if the site moves to its own domain.
- [ ] **Catalog.** Repository secrets `DATABASE_URL` and `TMDB_API_KEY`, then
      Actions → Catalog → `seed-only`, then `popular`. `/api/v1/ready` must say
      `"catalog":"ok"`.
- [ ] **Monitoring.** Repository variables `API_BASE_URL` and `APP_BASE_URL`
      switch on the Uptime workflow (it also keeps the free API from sleeping).
      `SENTRY_DSN` (API) and `VITE_SENTRY_DSN` (SPA) are optional; the CSP
      already allows Sentry.
- [ ] **TMDb logo** at `frontend/public/tmdb.svg`. The required wording is
      already in the footer; the mark appears once the file exists.
- [ ] **Own domain (optional).** Add it to `TRUSTED_HOSTS`, `CORS_ORIGINS` and
      `PUBLIC_APP_URL` on the API and to `VITE_SITE_URL` before pointing DNS.

## 2. Already in place

| Area | What protects it |
|------|------------------|
| Accounts | Verify-first sign-up (when enabled); bcrypt; password reset revokes every session and proves the mailbox; deletion with password + `DELETE` |
| Sessions | Access token in memory only; rotating refresh token in an httpOnly, Secure, SameSite=Lax cookie scoped to `/api/v1/auth`; reuse of a rotated token kills the family |
| Abuse | Per-IP limits per route family (auth, guest, API) plus per-account throttles on login, reset, sign-up and confirmation; request bodies over 256 KB refused unread |
| Browser | CSP with no `unsafe-inline` scripts (the one inline script is pinned by hash, and a unit test fails if it drifts), HSTS, `nosniff`, `frame-ancestors 'none'`; fonts self-hosted, so no visitor IP goes to Google |
| Privacy | Guest mode stores nothing server-side; export and delete in Account; privacy and terms pages describe what is actually stored |
| Supply chain | Dependency audit workflow (weekly and on dependency changes): `pip-audit` and `npm audit` |
| Ops | `/health` and `/ready`, structured request logs with request ids, Uptime workflow, CI on every PR |

## 3. Smoke test on the live site (10 minutes, phone first)

| Step | Expect |
|------|--------|
| Open `/` in a private window | Landing with the poster wall, instantly (no spinner even if the API sleeps) |
| *Try it now* → rate 3 posters → *Show my picks* | A row of explained picks; *Want to watch* counts up |
| *Keep my picks* → sign up | "Check your inbox" (with verification on); the link asks for the password and continues to onboarding with the guest answers carried over |
| Finish onboarding | For You: swipeable picks, at most 3 labelled "Hidden gem" |
| Open a pick | Detail shows *Want to watch / Seen it / Not for me*; after saving, a reload still says "In your watchlist" |
| Watchlist, History, Search | Saved titles listed; history shows the ratings; search finds by title |
| Sign out | Back to the landing page |
| Forgot password | Mail arrives; reset works; old sessions are signed out |
| Account → Delete | Landing page confirms the deletion; signing in again fails |
| Unknown URL | Real 404 page |
| Share the site link in a chat | Preview card with the CineTaste image |

## 4. Automated gates

CI must be green: backend lint, unit and integration tests (with and without
Redis) and the coverage gate; frontend typecheck, unit tests with coverage,
build, and the Playwright suite including axe accessibility checks.

## 5. Done when

Someone who has never heard of it can open the link on a phone, get picks
without signing up, create an account, and come back the next day to a
For You that remembers them — without anyone on call — and `/ready` stays green.
