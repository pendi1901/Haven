-- Haven: optional account sync for the onboarding profile.
-- Run once in the Supabase SQL editor (or `supabase db push`).
--
-- The browser reads and writes this table directly with the publishable key.
-- Row level security limits every signed-in user to their own row; anonymous
-- visitors get no access at all. The FastAPI backend never touches it.

create table if not exists public.profiles (
  user_id uuid primary key references auth.users (id) on delete cascade,
  -- Same shape as the frontend's Profile type (frontend/src/lib/types.ts).
  profile jsonb not null default '{}'::jsonb,
  -- Whether household and health answers were included in `profile`.
  -- When false they stay on the device that collected them.
  include_sensitive boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "Users read their own profile" on public.profiles;
create policy "Users read their own profile" on public.profiles
  for select to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "Users create their own profile" on public.profiles;
create policy "Users create their own profile" on public.profiles
  for insert to authenticated with check ((select auth.uid()) = user_id);

drop policy if exists "Users update their own profile" on public.profiles;
create policy "Users update their own profile" on public.profiles
  for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);

drop policy if exists "Users delete their own profile" on public.profiles;
create policy "Users delete their own profile" on public.profiles
  for delete to authenticated using ((select auth.uid()) = user_id);

-- The project was created with "automatically expose new tables" off, so grant
-- the Data API access explicitly: signed-in users only, never anon.
revoke all on public.profiles from anon;
grant select, insert, update, delete on public.profiles to authenticated;
