-- A record of every graph tidy-up.
--
-- The sweep marks rather than deletes, so its decisions are reversible -- but
-- only if you can find them. This is where "what did the maintenance pass do to
-- my graph on Tuesday" gets answered.
--
-- It lives here rather than in Neo4j because core owns the transcript database
-- and memory owns the graph, and giving the memory service a Postgres
-- connection to hold a counter would blur a split that is otherwise clean.

create table if not exists public.maintenance_runs (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid        not null references public.profiles (id) on delete cascade,
    -- Null for the user tier; a project id for a project tier.
    project_id  uuid        references public.projects (id) on delete cascade,
    scope       text        not null check (scope in ('user', 'project')),
    -- The graphiti group this run swept. Kept as text: it is the memory
    -- service's identifier, not a foreign key into anything here.
    group_id    text        not null,
    dry_run     boolean     not null default true,
    -- The full SweepReport, so a run can be read back in detail. Counts alone
    -- cannot answer which fact the pass touched.
    report      jsonb       not null default '{}'::jsonb,
    -- Episodes written to this group at the moment the run fired. The
    -- automatic trigger compares against this rather than a timestamp, because
    -- what matters is how much the graph changed, not how long it has been.
    episodes_at_run integer not null default 0,
    started_at  timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists maintenance_runs_group_idx
    on public.maintenance_runs (group_id, started_at desc);

alter table public.maintenance_runs enable row level security;
