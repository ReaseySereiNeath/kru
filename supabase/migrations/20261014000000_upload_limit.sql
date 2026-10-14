-- A log of uploads, used for the daily limit (BOOKS_PER_DAY).
--
-- Why not just count rows in `books`? Because deleting a book would give
-- the upload back, and the limit could be dodged by upload, delete, repeat.
-- A row here stays when the book is deleted.

create table public.uploads (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);
create index uploads_user_time_idx on public.uploads (user_id, created_at desc);

-- Only the backend uses this table: RLS on, no policies, no rights for users.
alter table public.uploads enable row level security;
revoke all on public.uploads from anon, authenticated;
