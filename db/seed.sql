-- Local development seed. Runs automatically the first time the Postgres
-- container's volume is created (see docker/postgres-init.sh) -- no longer
-- tied to the Supabase CLI, but the comment below about *why* still holds.
--
-- The demo user has a fixed uuid so the frontend, the group_id scheme, and the
-- graph data all keep pointing at the same rows across resets. Real auth
-- landed 2026-09-02 (see db/migrations/20260902000000_*.sql) -- this
-- account can now actually log in: email demo@example.com, password
-- nexus-demo. DEMO_USER_ID in .env still matters for tests, which
-- authenticate as this exact row rather than going through real signup.
insert into public.profiles (id, display_name, email, password_hash)
values (
    '00000000-0000-0000-0000-000000000001',
    'Demo User',
    'demo@example.com',
    -- bcrypt hash of "nexus-demo" -- a fixture password, not a secret; this
    -- file is committed and every local install gets the identical account.
    '$2b$12$nEpuqHFrHzfZCoHqo8uuNuPtGjASou7f/N/fYTUVq39OW6uxYy6/C'
)
on conflict (id) do nothing;

-- No sample project. A project is a memory scope, so a seeded one is not an
-- empty folder: it is a graph partition that facts start landing in before
-- anyone has decided it should exist.
