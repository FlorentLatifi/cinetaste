# Soft-launch checklist

Use this before inviting friends-and-family users. Frontend polish through Wave 6 is in place; this is **ops + smoke**, not more UI.

## 1. Infrastructure

- [ ] Staging API up (`render.staging.yaml` or equivalent)
- [ ] Staging SPA uses `VITE_API_BASE_URL=/api/v1` with the `vercel.json`
      rewrite pointed at the staging API — an absolute cross-origin URL
      breaks the refresh cookie on Safari *and* the SPA's `connect-src`
- [ ] Production secrets set: `JWT_SECRET`, `CORS_ORIGINS`, `TMDB_API_KEY`
- [ ] `TRUSTED_HOSTS` lists the domains the API is reached *through* (its own
      platform hostname and loopback are detected). A missing one answers 400,
      so add a custom domain before pointing DNS at it
- [ ] `curl -sI <api>/api/v1/health | grep -i strict-transport` returns a header
- [ ] Postgres `vector` extension + migrations applied
- [ ] Redis reachable **or** `REDIS_URL` empty on purpose — one worker counts
      in process just as well. (Auth fails closed only when a *configured*
      store is unreachable, not when there is none.)
- [ ] Sentry DSN optional but recommended
- [ ] `API_BASE_URL` and `APP_BASE_URL` repository variables set, so the Uptime
      workflow starts probing (it skips silently until they exist)
- [ ] One backup taken and one restore drilled — `backend/tools/backup_database.sh`,
      then the steps in [RUNBOOK.md](RUNBOOK.md#5-database-backup-and-restore)
- [ ] Contact address filled into the privacy page (it ships with a placeholder)
- [ ] TMDb logo dropped at `frontend/public/tmdb.svg` (the required wording is
      already in the footer; the mark hides itself if the file is absent)

## 2. Smoke (manual, 10 minutes)

| Step | Expect |
|------|--------|
| Open guest `/` | Marketing landing, Start free |
| Register | Onboarding deck loads |
| Finish onboarding | Immersive For You with reasons |
| Pass / Save / Like | Toast + undo works |
| Search a title | Poster grid + detail |
| Account → Taste | Export / import snapshot |
| Account → Appearance | Light theme persists after refresh |
| Unknown URL | Real 404, not a silent home redirect |
| Mobile width | Bottom nav; For You still one poster |

## 3. Automated gates (CI or local)

```powershell
cd frontend
npm run build
npx playwright test
```

All Playwright specs must pass (a11y + interactions).

## 4. Beta framing

- [ ] README / landing copy notes **beta** if catalog is partial
- [ ] Support email or Discord listed for feedback
- [ ] Rate limits / abuse path known (Redis-backed)

## 5. Done when

Friends can complete **Register → Onboard → For You → Save → Watchlist** without you on call, and `/ready` stays green.
