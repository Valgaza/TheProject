#!/bin/bash
# Applies the schema to a brand-new Postgres container.
#
# The official postgres image runs anything in /docker-entrypoint-initdb.d/
# automatically, but ONLY on the very first start, when its data directory is
# still empty. That is the replacement for `supabase db reset`: this runs once,
# when the volume is created, and never again while that volume exists.
#
# It deliberately loops over the whole migrations directory rather than naming
# each file, so a new migration added later is picked up with no change here.
# The glob sorts alphabetically, and the filenames are timestamp-prefixed
# (20260829134744_..., 20260829181500_...), so alphabetical order IS the
# intended order.
set -e

echo "── Applying migrations ──────────────────────────────────"
for migration in /migrations/*.sql; do
    echo "   $(basename "$migration")"
    # ON_ERROR_STOP=1 makes psql exit on the first failure instead of plowing
    # on through the rest of the file — without it a broken migration leaves a
    # half-built schema behind and the container still reports success.
    psql -v ON_ERROR_STOP=1 \
         --username "$POSTGRES_USER" \
         --dbname "$POSTGRES_DB" \
         -f "$migration"
done

# The seed creates the demo user and demo project that DEMO_USER_ID in .env
# points at. Kept separate from the migrations because it is local-development
# data, not schema — a real deployment would apply the migrations and skip this.
if [ -f /seed.sql ]; then
    echo "── Applying seed ────────────────────────────────────────"
    psql -v ON_ERROR_STOP=1 \
         --username "$POSTGRES_USER" \
         --dbname "$POSTGRES_DB" \
         -f /seed.sql
fi

echo "── Database ready ───────────────────────────────────────"
