# Soft-launch checklist

Use this before inviting friends-and-family users. Frontend polish through Wave 6 is in place; this is **ops + smoke**, not more UI.

## 1. Infrastructure

- [ ] Staging API up (`render.staging.yaml` or equivalent)
- [ ] Staging SPA points at staging API (`VITE_API_BASE_URL`)
- [ ] Production secrets set: `JWT_SECRET`, `CORS_ORIGINS`, `TMDB_API_KEY`
- [ ] Postgres `vector` extension + migrations applied
- [ ] Redis reachable (auth rate limits fail closed without it)
- [ ] Sentry DSN optional but recommended

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
