# Runbook

What to do when something is wrong, written while nothing is. Every command
here has been run against this project's schema at least once.

Deploy topology: the API is a Docker service on Render, the SPA is on Vercel,
Postgres is Render's managed instance. `backend/entrypoint.sh` runs
`alembic upgrade head` on every boot and then starts uvicorn with a single
worker, so migrations apply once per deploy and never race each other.

---

## 1. Triage: which layer is broken

Work outwards. Each step tells you whether to stop.

```bash
# 1. Is the API alive at all?
curl -sS https://<api-host>/api/v1/health
#    {"status":"ok",...}  -> the process is up, go to step 2
#    connection refused   -> the service is down or asleep; check Render logs
#    ~50s then a response -> it was asleep, not broken (free tier)

# 2. Can it reach its dependencies?
curl -sS https://<api-host>/api/v1/ready
#    200 {"database":"ok","cache":"redis","catalog":"ok"}   -> API is fine
#    503 {"database":"error"}                               -> §5, database
#    200 {"catalog":"unembedded"}                           -> §4, empty For You
#    200 {"cache":"memory (redis unavailable)"}             -> degraded, not down

# 3. Is the SPA being served?
curl -sS -o /dev/null -w '%{http_code}\n' https://<app-host>/

# 4. Does the SPA reach the API through its own domain?
curl -sS -o /dev/null -w '%{http_code}\n' https://<app-host>/api/v1/health
#    404 here but 200 in step 1 -> the vercel.json rewrite is wrong or stale
```

The Uptime workflow runs steps 2 and 3 every ten minutes and emails you when
either fails. A failure there is the same investigation, already half done.

---

## 2. Roll back a deploy

**API (Render).** Dashboard → the service → Events → find the previous
successful deploy → **Rollback**. Render redeploys that image.

Careful: rolling the image back does **not** roll the database back. If the
deploy you are undoing added a migration, the old code now runs against a newer
schema. Additive migrations (a new nullable column, a new table) are safe that
way — every migration in this repository so far is additive. A migration that
drops or renames something is not, and needs §3 as well.

**SPA (Vercel).** Dashboard → Deployments → the previous one → **Promote to
Production**. Instant, no build.

---

## 3. Roll back a migration

Every migration in `backend/alembic/versions/` has a real `downgrade()` — none
of them are `pass`. Current chain:

```
20260710_0001  initial schema
20260717_0002  HNSW index on titles.embedding
20260717_0003  password_reset_tokens
20260717_0004  refresh_token_families
20260717_0005  recommendation_impressions
20260717_0006  pg_trgm search index
20260919_0007  refresh rotation + query indexes
20260922_0008  users.password_changed_at
20260923_0009  email verification
```

From a shell on the API service:

```bash
python -m alembic current          # where are we
python -m alembic history --verbose | head -20
python -m alembic downgrade -1     # one step back
python -m alembic downgrade 20260922_0008   # or to a named revision
```

**Take a dump first** (§5). A downgrade that drops a column destroys the data in
it, and `upgrade` afterwards brings the column back empty.

Roll the code back in the same movement — old code against a new schema is
usually fine, new code against an old schema is not.

### A migration failed half-way

Alembic wraps each migration in a transaction on Postgres, so a failure rolls
that migration back and leaves `alembic_version` at the previous revision. The
container then crash-loops, because `entrypoint.sh` will not start uvicorn
after a failed `upgrade`.

```bash
python -m alembic current    # confirm it did not advance
```

If it did advance but the schema is wrong, the migration committed something it
should not have. Restore from backup (§5) rather than patching by hand — the
schema and `alembic_version` have to agree, and guessing which one is lying
costs more than a restore.

The one thing not inside that transaction is `CREATE INDEX` on a large table:
it holds a lock for the duration. On this catalog size it is seconds; if the
catalog grows past a few hundred thousand rows, switch those to
`CREATE INDEX CONCURRENTLY` in a migration of its own, outside a transaction.

---

## 4. For You is empty for everyone

`/ready` reports `"catalog":"unembedded"` and the logs carry
`slate_candidates_unembedded`. It means `ingest_catalog` ran and
`reembed_catalog` did not — ranking drops every title whose embedding is NULL,
so the slate comes back empty with a perfectly healthy 200.

```bash
python -m app.scripts.reembed_catalog          # or --force after a schema bump
```

`"catalog":"empty"` means no titles at all: run `ingest_catalog --pages 10`
first (needs `TMDB_API_KEY`).

---

## 5. Database backup and restore

### Take a backup

```bash
export DATABASE_URL='postgresql://…'     # the plain URL, not +asyncpg
backend/tools/backup_database.sh ./backups 14
```

The script refuses to report success unless `pg_restore --list` can read the
archive and finds `users`, `interaction_events` and `taste_profiles` in it — a
truncated dump fails at backup time rather than at restore time.

Render's free Postgres plan has no automated backups, so until this is on a
schedule somewhere (a cron, a small VPS, a scheduled workflow with the URL as a
secret) the only copy is whatever you took by hand.

### Restore

Never into the live database. Restore into a scratch one, look at it, then
promote by pointing `DATABASE_URL` at it.

```bash
createdb -T template0 cinetaste_restored
pg_restore --dbname=cinetaste_restored --no-owner --no-privileges \
           --clean --if-exists backups/cinetaste-<stamp>.dump

# Extensions live in the database, not the dump's data:
psql -d cinetaste_restored -c 'CREATE EXTENSION IF NOT EXISTS vector'
psql -d cinetaste_restored -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm'

# Sanity before you trust it:
psql -d cinetaste_restored -c 'SELECT count(*) FROM users'
psql -d cinetaste_restored -c 'SELECT count(*) FROM titles WHERE embedding IS NOT NULL'
psql -d cinetaste_restored -c 'SELECT version_num FROM alembic_version'
```

The last one matters: if `version_num` is older than the code you are about to
run, `alembic upgrade head` on the next boot will bring it forward — which is
what you want, as long as you knew it was going to happen.

### Drill it

A restore procedure nobody has executed is a guess. Run the three commands
above against a scratch database once before launch and once a quarter, and
write the date here:

| Date | Dump | Result |
|---|---|---|
| 2026-09-24 | 500 titles, 3 users, 120 interactions, 3 taste profiles | Passed. Row counts identical, `alembic_version` 20260923_0009 on both sides, 384-dimension embeddings intact, all seven indexes on `titles` rebuilt including the HNSW one, and both `ILIKE`/pg_trgm search and a `<=>` nearest-neighbour query ran on the restored copy. |

---

## 6. A secret leaked

1. **Rotate first, investigate second.**
   - `JWT_SECRET` — change it in Render. Every existing access token and every
     signed history cursor becomes invalid, so everyone is signed out. That is
     the intended effect.
   - `TMDB_API_KEY` — revoke at themoviedb.org, set the new one, redeploy.
   - Database — rotate the password in Render; it updates `DATABASE_URL` and
     restarts the service.
2. Check whether it ever reached git: `git log -p --all -S '<the secret>'`.
   Rewriting history is only worth it if the answer is yes *and* the repository
   is public — rotation is what actually fixes it either way.
3. If user data may have been reached, say so to the people affected. The
   privacy policy promises nothing about breaches that this does not keep.

---

## 7. Under unexpected load

- **Redis down.** The cache degrades to in-process for 30 seconds at a time and
  keeps serving. Auth rate limiting fails *closed* — logins return 503 rather
  than going unlimited. That is deliberate.
- **Connection pool exhausted.** `pool_timeout` is 5 seconds, so requests fail
  fast instead of queueing. Raise `DB_POOL_SIZE` only while
  `WEB_CONCURRENCY * (DB_POOL_SIZE + DB_MAX_OVERFLOW)` stays under the host's
  connection limit (~97 on Render free).
- **TMDb rate limit.** Only `/titles/{id}/where-to-watch` calls TMDb from a
  request, and it caches per title and region for 12 hours. If it is still
  being hit hard, raise `WATCH_PROVIDER_CACHE_TTL_SECONDS`; the endpoint soft-
  fails to `available: false` rather than erroring.
- **One user hammering the API.** 120 requests a minute per IP, separate
  budgets for login and password reset, plus a per-account limit that a forged
  `X-Forwarded-For` cannot escape. See [SECURITY.md](SECURITY.md).
