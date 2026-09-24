-- Runs once, when the postgres volume is first created.
--
-- The integration suite TRUNCATEs every table, so it must never share a
-- database with the dev server. tests/conftest.py rewrites DATABASE_URL to
-- "<name>_test" automatically; this is the database it lands in.
--
-- Already have a volume from before this file existed? Create it by hand:
--   docker compose exec db psql -U cinetaste -d postgres \
--     -c "CREATE DATABASE cinetaste_test OWNER cinetaste"
--   docker compose exec db psql -U cinetaste -d cinetaste_test \
--     -c "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"

CREATE DATABASE cinetaste_test OWNER cinetaste;

\connect cinetaste_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

\connect cinetaste
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
