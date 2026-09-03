-- Pin/unpin, part of the chat-management menu (rename, pin, move, delete).
-- Rename and move-to-project need no schema change -- they're just updates
-- to columns that already exist (title, project_id). Delete needs none
-- either -- messages and memory_episodes already cascade off chats.id.

alter table public.chats
    add column if not exists pinned boolean not null default false;

comment on column public.chats.pinned is
    'Shown pinned to the top of the sidebar, ahead of recency order.';
