drop function if exists public.tgbot_daily_release(bigint);
drop function if exists public.tgbot_daily(bigint, text, int, int);
drop function if exists public.tgbot_act_done(bigint);
drop function if exists public.tgbot_add(bigint, int);
drop function if exists public.tgbot_spend(bigint, int);
alter table public.tgbot_users drop column if exists daily_pending, drop column if exists daily_arm_at, drop column if exists daily_day, drop column if exists promo_acts;
