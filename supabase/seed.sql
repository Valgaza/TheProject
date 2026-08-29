-- Local development seed. Runs on `supabase start` and `supabase db reset`.
--
-- The demo user has a fixed uuid so the frontend, the group_id scheme, and the
-- graph data all keep pointing at the same rows across resets. Auth replaces
-- this later; until then DEMO_USER_ID in .env must match.
insert into public.profiles (id, display_name)
values ('00000000-0000-0000-0000-000000000001', 'Demo User')
on conflict (id) do nothing;

insert into public.projects (id, user_id, name, description, domain)
values (
    '00000000-0000-0000-0000-0000000000a1',
    '00000000-0000-0000-0000-000000000001',
    'Knowledge Base Q&A',
    'Technical documentation retrieval and cited Q&A across the engineering knowledge base.',
    'rag'
)
on conflict (id) do nothing;
