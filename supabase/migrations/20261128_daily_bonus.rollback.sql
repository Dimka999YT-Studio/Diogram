drop function if exists public.daily_owner_chat(bigint, timestamptz, bigint[]);
drop function if exists public.daily_release(int, bigint);
drop function if exists public.daily_claim(int, bigint);
drop table if exists public.daily_claims;
drop table if exists public.daily_tasks;
