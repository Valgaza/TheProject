-- Local development seed. Runs on `supabase start` and `supabase db reset`.
--
-- The demo user has a fixed uuid so the frontend, the group_id scheme, and the
-- graph data all keep pointing at the same rows across resets. Auth replaces
-- this later; until then DEMO_USER_ID in .env must match.
insert into public.profiles (id, display_name)
values ('00000000-0000-0000-0000-000000000001', 'Demo User')
on conflict (id) do nothing;

-- No sample project. A project is a memory scope, so a seeded one is not an
-- empty folder: it is a graph partition that facts start landing in before
-- anyone has decided it should exist.
