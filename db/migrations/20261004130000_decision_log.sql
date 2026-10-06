-- What the decision model (Laya, served by the runtime) said, next to what the
-- current logic decided, at every point where it is being tried.
--
-- Laya replaces model calls that are really classifications -- "is this worth
-- remembering?", "is this a request to search the web?" -- with a single
-- forward pass of ~10-15 ms. It is not trusted until it has earned it: each
-- decision point runs in shadow mode first, where Laya's answer is recorded
-- here and the old logic still acts. scripts/laya/evaluate.py reads this table
-- to measure agreement before a point is switched to live.

create table if not exists public.decision_log (
    id          uuid primary key default gen_random_uuid(),
    -- Which decision: 'gate', 'web_intent', 'web_query', 'end_of_turn', 'barge_in'.
    point       text not null,
    -- 'shadow' (recorded only) or 'live' (Laya's answer was acted on).
    mode        text not null check (mode in ('shadow', 'live')),
    chat_id     uuid references public.chats (id) on delete cascade,
    message_id  uuid references public.messages (id) on delete set null,
    -- Exactly what Laya was shown, and what it answered (per question).
    input       jsonb not null,
    laya        jsonb,
    -- What the existing logic decided, for comparison. Filled in when known,
    -- which for the write gate is after its model call returns.
    baseline    jsonb,
    laya_ms     integer,
    error       text,
    created_at  timestamptz not null default now()
);

create index if not exists decision_log_point_idx on public.decision_log (point, created_at desc);

comment on table public.decision_log is
    'Decision-model answers beside the existing logic''s, per decision point. Shadow-mode evidence.';
