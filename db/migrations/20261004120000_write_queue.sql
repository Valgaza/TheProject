-- The memory write path becomes a durable queue, and every gate decision is
-- kept.
--
-- Until now a turn's write gate ran as a FastAPI background task: lost if core
-- restarted mid-write, competing with the next turn for the model, and leaving
-- no trace when it declined. Now a finished turn inserts a gate_decisions row
-- in 'pending'; one worker in core drains them oldest first, pausing while a
-- reply is being generated, and records what the gate decided either way.
--
-- Declines are kept on purpose this time. They are most rows, and they are the
-- training data for replacing the gate's model call with a decision model
-- (Laya, milestone Phase 4): a classifier cannot learn "not worth
-- remembering" from a table that only holds the yeses.

create table if not exists public.gate_decisions (
    id                uuid primary key default gen_random_uuid(),
    chat_id           uuid not null references public.chats (id) on delete cascade,
    message_id        uuid references public.messages (id) on delete set null,
    user_id           uuid not null references public.profiles (id) on delete cascade,
    -- The chat's project when the turn happened; null for a loose chat.
    project_id        uuid references public.projects (id) on delete cascade,
    -- The turn as the gate sees it, stored so the job survives a restart and
    -- so a decision can be read back next to what it was deciding about.
    user_message      text not null,
    assistant_message text not null,
    recalled_facts    jsonb not null default '[]'::jsonb,
    status            text not null default 'pending'
                      check (status in ('pending', 'running', 'done', 'failed')),
    -- The decision itself, once made.
    persist           boolean,
    facts             jsonb,
    reason            text,
    -- What made it: "prefilter", "llm" or, later, "laya".
    decided_by        text,
    gate_ms           integer,
    attempts          integer not null default 0,
    error             text,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

-- The worker's query: oldest unfinished first.
create index if not exists gate_decisions_queue_idx
    on public.gate_decisions (created_at)
    where status in ('pending', 'running');

-- One episode per turn and tier now carries every accepted fact of that tier,
-- so the episode text is stored with it: a pending row can be re-ingested
-- after a restart, and "what exactly went into the graph" is answerable.
alter table public.memory_episodes add column if not exists body text;
alter table public.memory_episodes
    add column if not exists decision_id uuid references public.gate_decisions (id) on delete set null;

comment on table public.gate_decisions is
    'Every write-gate decision, accepted or declined. Doubles as the durable queue of turns awaiting the gate.';
