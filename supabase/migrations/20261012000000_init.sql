-- Kru database schema.
--
-- Who can do what:
--   * The backend (API + worker) uses the SERVICE ROLE key. It skips Row
--     Level Security, so it can read and write everything.
--   * Logged-in users (the browser, with the ANON key + their login token)
--     can only READ their own rows. They cannot insert, update, or delete
--     directly: every change goes through the API.
--   * Not-logged-in visitors can do nothing.

-- ------------------------------------------------------------------ tables

create table public.books (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users (id) on delete cascade,
  title         text not null,
  author        text not null default 'Unknown',
  file_type     text not null check (file_type in ('pdf', 'epub', 'txt')),
  -- Where the uploaded file is in storage. Set to null when the file is
  -- deleted after processing (we keep only the summary).
  file_path     text,
  word_count    integer,
  page_estimate integer,
  book_type     text,
  subject       text,
  reader_level  text,
  -- The whole book check result (also holds chapters and skip_sections),
  -- saved so it runs only once per book.
  book_check    jsonb,
  status        text not null default 'queued',
  total_tokens  bigint not null default 0,
  total_seconds double precision not null default 0,
  created_at    timestamptz not null default now()
);
create index books_user_id_idx on public.books (user_id, created_at desc);

-- One job per book. A retry puts the same job back in the queue.
create table public.jobs (
  id               uuid primary key default gen_random_uuid(),
  book_id          uuid not null unique references public.books (id) on delete cascade,
  status           text not null default 'queued' check (status in (
                     'queued', 'extracting', 'analyzing', 'summarizing', 'merging',
                     'finalizing', 'done', 'failed', 'too_long', 'scanned_not_supported')),
  progress_current integer not null default 0,
  progress_total   integer not null default 0,
  progress_message text not null default '',
  error            text,
  attempts         integer not null default 0,
  -- When the job (re-)entered the queue. The queue is served oldest first.
  queued_at        timestamptz not null default now(),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index jobs_queue_idx on public.jobs (queued_at) where status = 'queued';

-- The chunk plan is saved as 'pending' rows before summarizing starts; each
-- row becomes 'done' when its notes are saved. That is how a job resumes.
create table public.chunks (
  id             uuid primary key default gen_random_uuid(),
  book_id        uuid not null references public.books (id) on delete cascade,
  chunk_index    integer not null,
  chapter_titles jsonb not null default '[]',
  word_count     integer not null default 0,
  notes          text,
  carryover      text,
  status         text not null default 'pending' check (status in ('pending', 'done')),
  seconds_taken  double precision,
  unique (book_id, chunk_index)
);

create table public.summaries (
  id         uuid primary key default gen_random_uuid(),
  book_id    uuid not null unique references public.books (id) on delete cascade,
  markdown   text not null,
  model      text not null,
  created_at timestamptz not null default now()
);

-- Keep jobs.updated_at current without the code having to remember.
create function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;

create trigger jobs_set_updated_at before update on public.jobs
for each row execute function public.set_updated_at();

-- ------------------------------------------------------------------ row level security

alter table public.books     enable row level security;
alter table public.jobs      enable row level security;
alter table public.chunks    enable row level security;
alter table public.summaries enable row level security;

-- Users can read their own books...
create policy "read own books" on public.books
  for select to authenticated
  using (user_id = (select auth.uid()));

-- ...and anything that belongs to one of their books.
create policy "read jobs of own books" on public.jobs
  for select to authenticated
  using (exists (select 1 from public.books b
                 where b.id = jobs.book_id and b.user_id = (select auth.uid())));

create policy "read chunks of own books" on public.chunks
  for select to authenticated
  using (exists (select 1 from public.books b
                 where b.id = chunks.book_id and b.user_id = (select auth.uid())));

create policy "read summaries of own books" on public.summaries
  for select to authenticated
  using (exists (select 1 from public.books b
                 where b.id = summaries.book_id and b.user_id = (select auth.uid())));

-- No insert/update/delete policies on purpose: with RLS on, "no policy"
-- means "not allowed". Only the backend's service role can change data.
-- Take away the table-level rights too, as a second lock.
revoke all on public.books, public.jobs, public.chunks, public.summaries from anon, authenticated;
grant select on public.books, public.jobs, public.chunks, public.summaries to authenticated;

-- ------------------------------------------------------------------ job queue

-- The worker calls this to take the oldest waiting job.
-- "for update skip locked" means: lock the row I pick, and skip rows another
-- worker has already locked. So two workers can never take the same job.
create function public.claim_next_job() returns setof public.jobs
language plpgsql security definer set search_path = public as $$
begin
  return query
  update public.jobs
     set status = 'extracting',
         attempts = attempts + 1,
         error = null,
         progress_message = 'Starting'
   where id = (
           select id from public.jobs
            where status = 'queued'
            order by queued_at
            limit 1
            for update skip locked)
  returning *;
end $$;

-- Only the backend may claim jobs.
revoke execute on function public.claim_next_job() from public, anon, authenticated;
grant execute on function public.claim_next_job() to service_role;

-- ------------------------------------------------------------------ file storage

-- A private bucket for uploaded books. No storage policies are created, so
-- only the backend (service role) can read or write files. The 50 MB limit
-- here is a backstop; the API checks MAX_FILE_MB first.
insert into storage.buckets (id, name, public, file_size_limit)
values ('books', 'books', false, 52428800)
on conflict (id) do nothing;
