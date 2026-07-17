# CineTaste frontend

React 19 + TypeScript + Vite SPA: guest landing, auth, onboarding, immersive **For You**, search, watchlist, history, account (taste import/export), title detail.

## Scripts

```powershell
npm install
npm run dev          # http://localhost:5173 (proxies /api → :8000)
npm run build
npm run test:a11y    # Playwright + axe (mocks, no backend required)
```

## Environment

Copy `.env.example`:

| Variable | Local | Production (Vercel) |
|----------|-------|---------------------|
| `VITE_API_BASE_URL` | `/api/v1` (proxy) | `https://<api-host>/api/v1` |
| `VITE_SENTRY_DSN` | optional | optional |

See [`../docs/DEPLOY.md`](../docs/DEPLOY.md) and [`../docs/SOFT_LAUNCH_CHECKLIST.md`](../docs/SOFT_LAUNCH_CHECKLIST.md).

## Architecture (SPA)

```
src/
  api/           # fetch wrappers
  components/    # shell, posters, dialogs, skeletons
  features/      # auth, for-you, onboarding, taste, theme
  lib/           # poster URLs, password strength
  pages/         # route surfaces
  styles/        # global design system
```

Deploy: `vercel.json` SPA rewrite + security headers. Root directory on Vercel should be **`frontend`**.

## Status

- Landing, auth, password strength, immersive For You, onboarding deck
- Account tabs (profile / taste / appearance / danger)
- Light / system theme, mobile bottom nav, skeletons, axe e2e
