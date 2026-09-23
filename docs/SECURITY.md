# Security

What this application defends against, how, and what it deliberately does not
try to do. Written after a full audit of the codebase; every claim below points
at code or a test rather than at an intention.

## Threat model

CineTaste holds an email address, a password hash, and a record of what someone
has watched and how they rated it. The viewing history is the interesting part:
it is personal, and unlike a password it cannot be rotated after a leak.

Worth defending against:

- **Credential stuffing and brute force** — the most likely attack on any app
  with a login form.
- **Session theft** — a stolen token should be short-lived and revocable.
- **One user reading another's data** — the taste profile is the product.
- **Injection** into the catalog search, which is the only place free text
  reaches a query.

Explicitly out of scope:

- A determined attacker with database access. Password and token hashes limit
  the damage; they do not prevent it.
- Denial of service beyond per-IP and per-account rate limits. Absorbing a real
  flood is the platform's job, not the application's.
- Multi-tenancy. There are no roles, no admin surface, no shared documents.

## Authentication

| Concern | Decision | Where |
|---|---|---|
| Password hashing | bcrypt, cost 12, run in a worker thread so it never blocks the event loop | [`core/security.py`](../backend/app/core/security.py) |
| Access token | Short-lived JWT, **in memory only** — never `localStorage` | [`api/tokenStore.ts`](../frontend/src/api/tokenStore.ts) |
| Refresh token | httpOnly cookie, `SameSite=Lax`, scoped to `/api/v1/auth` | [`core/cookies.py`](../backend/app/core/cookies.py) |
| Rotation | Every refresh issues a new token and revokes the old one | [`auth_service.py`](../backend/app/application/auth_service.py) |
| Theft detection | Reusing a revoked token revokes the whole token family | same |
| Account enumeration | Unknown emails cost the same as real ones (a dummy hash is verified) | same |

**Why the refresh token is a cookie and the access token is not.** JavaScript
cannot read an httpOnly cookie, so an XSS bug cannot steal a long-lived
credential. The access token is readable by design, which is why it expires in
15 minutes and is never written to storage that survives a reload.

**Why `SameSite=Lax` and not `None`.** The SPA reaches the API through a
same-origin rewrite, so the cookie is first-party. `None` would require the
browser to accept a third-party cookie, and Safari does not — sessions would
silently end on every reload for those users.

**Why rotation needs a grace window.** Two tabs refreshing at the same moment
present the same cookie. Without a window that looks exactly like token theft,
and the honest user gets logged out. A token that was rotated within the last
20 seconds yields a sibling instead of revoking the family.

### Concurrent refresh

The client funnels every refresh through a single in-flight promise
([`api/client.ts`](../frontend/src/api/client.ts)). Two tabs are separate
JavaScript contexts and cannot share it, which is what the server-side grace
window is for. The two mechanisms cover different halves of the same problem.

## Rate limiting

Two independent limiters, because neither is sufficient alone.

**Per IP** ([`core/middleware.py`](../backend/app/core/middleware.py)) — fixed
window, separate budgets for login, password reset, other auth routes, and the
rest of the API. Families do not share a counter: refresh traffic must not be
able to spend the login budget.

Deriving the client IP behind a proxy is genuinely hard, and this is where the
audit found its worst bug. Each proxy appends the address it received from, so
the entry `TRUSTED_PROXY_HOPS` from the right is the one our outermost trusted
proxy wrote. That reasoning holds only while the request actually passes
through the full chain — and the origin host stays publicly reachable, so it
often will not. The old code fell back to the *left-most* entry in that case,
which is the one the caller wrote:

```
X-Forwarded-For: 1.1.1.1, 2.2.2.2   peer: 198.51.100.66   ->  bucket "1.1.1.1"
```

Rotating one header per request gave a fresh budget every time. `client_ip` now
returns the socket peer whenever the chain is shorter than configured, and
rejects entries that are not addresses.

That closes the short chain. It does **not** close a forged full-length chain
from a caller that reaches the origin directly, and no amount of header parsing
will — which is why there is a second limiter.

**Per account** ([`core/throttle.py`](../backend/app/core/throttle.py)) —
counts failed logins and reset requests against the email in the request.
Neither header forgery nor a botnet changes that key. Only failures count, so
an active user is never locked out. Identifiers are hashed before they become
keys, so no address reaches Redis or a log line.

Both fail **closed** on credential routes: if the counter store is unreachable
we cannot distinguish an attacker from a user, and an open login form is worse
than a brief outage. Everything else fails open.

## Authorization

There is no endpoint that takes a user identifier from the client. Every
`/me*` route derives the user from the bearer token, and `/titles/{id}` refers
to the shared public catalog, not to per-user rows. Interactions are always
written under the authenticated user's id.

`get_current_user` loads the user from the database on every request rather
than trusting the token's contents, so deleting an account invalidates its
tokens immediately.

**Access tokens expire with the password.** Resetting a password revokes every
refresh token, but an access token is stateless and used to keep working for
the rest of its lifetime — the attacker the victim had just locked out still
had a session. `users.password_changed_at` records the cut-off and
`_issued_before_password_change` rejects anything older.

## Injection

No SQL is built by string concatenation anywhere in the codebase. Catalog
search — the only place free text reaches a query — escapes LIKE
metacharacters and passes the term as a bound parameter to both `ILIKE` and
`pg_trgm`'s `similarity()`
([`recommendation_service.py`](../backend/app/application/recommendation_service.py)).

The SPA renders everything through React, and there is no
`dangerouslySetInnerHTML`, no `innerHTML` and no `eval` in the source. The
Content-Security-Policy is defence in depth for a dependency compromise, not a
patch for a known sink.

Request bodies reject fields they do not declare. Pydantic's default is to drop
unknown keys, which meant a client sending `new_pasword` got a `200` and an
unchanged password.

## Email verification

Registration accepts an address; verification proves the person owns it. Until
then, `users.email_verified_at` is NULL and the account page says so.

Enforcement is behind `REQUIRE_EMAIL_VERIFICATION`, **off by default**. With it
on, the product surface requires a confirmed address while `/me`, taste export,
resending the link and deleting the account stay reachable — gating those would
leave the user with no way forward and no way out. Production refuses to start
with the flag on and no SMTP configured, because that combination locks out
every account with no path to recovery.

`/auth/resend-verification` requires a session on purpose. An endpoint that
took an email address would mail anyone on request and confirm which addresses
are registered.

The SPA handles the `403 email_not_verified` rather than showing it: the router
sends the user to the account page, which is the only place the state can be
resolved and whose own calls are not gated. A flag that produces a dead end is
a flag nobody will turn on.

## Secrets

No secret has ever been committed: the full git history was scanned for JWT,
hex-32, `sk-` and AWS key shapes. Only `*.example` files are tracked;
`.env` and `.env.*` are ignored with explicit exceptions for the examples.

TMDb v3 authenticates with a query parameter, so the key appears in request
URLs. httpx and httpcore are pinned to `WARNING`
([`core/logging.py`](../backend/app/core/logging.py)) because their INFO lines
log full URLs. A TMDb `401` returns a generic message rather than echoing the
key.

Auth logging records user ids only — never an email, a token or a password.

## Transport and headers

The API sets `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy`, `Permissions-Policy`, a restrictive CSP and HSTS. HSTS keys
off the environment rather than `request.url.scheme`: TLS terminates at the
platform proxy, uvicorn does not trust that proxy by default, and the scheme
therefore stays `http` — which meant production sent no HSTS header at all.

The SPA ships its own CSP through `vercel.json`. `script-src` is `'self'` plus
one hash for the theme script that has to run before first paint;
`connect-src 'self'` works because the API is reached through a same-origin
rewrite. That single setting decides three things at once — the cookie stays
first-party, the CSP stays simple, and CORS stays one explicit origin — so
`VITE_API_BASE_URL` must stay relative.

Production refuses `CORS_ORIGINS="*"`. Starlette answers a credentialed
request by echoing the caller's `Origin` when origins are `*`, which would let
any site read authenticated responses.

## Dependencies

`pip-audit` over `requirements.txt` and `requirements-dev.txt`, and `npm audit`
including dev dependencies, both report zero known vulnerabilities. Dependabot
opens grouped weekly PRs; CI runs with `contents: read` so an update PR cannot
write to the repository.

## Known gaps

Stated rather than hidden:

- **A forged full-length `X-Forwarded-For` still picks its own IP bucket** when
  the caller reaches the origin directly. The per-account limiter is the real
  control; closing this properly needs a shared secret injected by the edge,
  which `vercel.json` rewrites cannot do (it needs Edge Middleware).
- **Registration reveals whether an email is already in use** through its `409`.
  Removing that oracle means making registration always return success and
  moving the "this address is already registered" message into an email, which
  needs verification to be mandatory first.
- **No 2FA.** A single-factor account holding viewing history is a reasonable
  trade for a project of this size; it would not be for one holding payments.
- **HSTS in production is unverified until deploy.** Confirm with
  `curl -sI https://<api-host>/api/v1/health | grep -i strict-transport`.
