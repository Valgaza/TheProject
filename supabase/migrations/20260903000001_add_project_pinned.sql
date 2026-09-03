-- Pin/unpin for projects, same idea as chats.pinned (20260903000000).

alter table public.projects
    add column if not exists pinned boolean not null default false;

comment on column public.projects.pinned is
    'Shown pinned to the top of the projects grid, ahead of recency order.';
