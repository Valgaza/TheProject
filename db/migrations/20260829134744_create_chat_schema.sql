-- Chat transcript schema.
--
-- This database stores WHAT WAS SAID: every message, always written. The Neo4j
-- graph stores WHAT WAS LEARNED: gated and distilled by the memory write gate.
-- Keeping them apart is what lets the gate reject a message without losing it
-- from the transcript.
--
-- Auth is deferred, but the shape is auth-ready: the user table is named
-- `profiles` so it can later be keyed to auth.users(id) without a rename,
-- projects carry user_id from the start, and RLS is on everywhere. Adding
-- Supabase Auth later means tightening policies, not migrating data.

-- ── profiles ─────────────────────────────────────────────────────────────────
create table if not exists public.profiles (
    id           uuid primary key default gen_random_uuid(),
    display_name text        not null,
    created_at   timestamptz not null default now()
);

comment on table public.profiles is
    'One row per user. Keyed to auth.users(id) when auth lands.';

-- ── projects ─────────────────────────────────────────────────────────────────
create table if not exists public.projects (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid        not null references public.profiles (id) on delete cascade,
    name        text        not null,
    description text,
    domain      text        not null default 'general',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists projects_user_id_idx
    on public.projects (user_id, updated_at desc);

-- ── chats ────────────────────────────────────────────────────────────────────
create table if not exists public.chats (
    id         uuid primary key default gen_random_uuid(),
    project_id uuid        not null references public.projects (id) on delete cascade,
    title      text        not null default 'New conversation',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists chats_project_id_idx
    on public.chats (project_id, updated_at desc);

-- ── messages ─────────────────────────────────────────────────────────────────
create table if not exists public.messages (
    id         uuid primary key default gen_random_uuid(),
    chat_id    uuid        not null references public.chats (id) on delete cascade,
    role       text        not null check (role in ('user', 'assistant', 'system')),
    content    text        not null,
    -- Which handler answered, retrieved fact ids, token counts, latency.
    metadata   jsonb       not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists messages_chat_id_idx
    on public.messages (chat_id, created_at);

-- ── memory_episodes ──────────────────────────────────────────────────────────
-- graphiti 0.29 declares EpisodicNode.episode_metadata but never persists it,
-- so an episode cannot carry the chat it came from. This table is that link.
create table if not exists public.memory_episodes (
    id           uuid primary key default gen_random_uuid(),
    chat_id      uuid        not null references public.chats (id) on delete cascade,
    message_id   uuid        references public.messages (id) on delete set null,
    -- The graphiti episode uuid, null until ingestion succeeds.
    episode_uuid uuid,
    -- Which graph partition the write gate chose.
    scope        text        not null check (scope in ('user', 'project')),
    status       text        not null default 'pending'
                             check (status in ('pending', 'ingested', 'failed')),
    -- Why the gate decided what it did; the field you read when tuning it.
    reason       text,
    error        text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create index if not exists memory_episodes_chat_id_idx
    on public.memory_episodes (chat_id, created_at desc);
create index if not exists memory_episodes_status_idx
    on public.memory_episodes (status) where status <> 'ingested';

-- ── updated_at maintenance ───────────────────────────────────────────────────
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger projects_touch_updated_at
    before update on public.projects
    for each row execute function public.touch_updated_at();

create trigger chats_touch_updated_at
    before update on public.chats
    for each row execute function public.touch_updated_at();

create trigger memory_episodes_touch_updated_at
    before update on public.memory_episodes
    for each row execute function public.touch_updated_at();

-- A new message counts as activity on its chat, so the recent-conversations
-- list can order by chats.updated_at without a subquery over messages.
create or replace function public.bump_chat_on_message()
returns trigger
language plpgsql
as $$
begin
    update public.chats set updated_at = now() where id = new.chat_id;
    return new;
end;
$$;

create trigger messages_bump_chat
    after insert on public.messages
    for each row execute function public.bump_chat_on_message();

-- ── row level security ───────────────────────────────────────────────────────
-- On from the start so nothing is reachable with the anon key by accident. The
-- core service connects as the table owner / service role, which bypasses RLS.
-- When auth lands, replace these with policies over auth.uid().
alter table public.profiles        enable row level security;
alter table public.projects        enable row level security;
alter table public.chats           enable row level security;
alter table public.messages        enable row level security;
alter table public.memory_episodes enable row level security;
