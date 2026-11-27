drop function if exists public.tgbot_promo_take(bigint, int);
drop function if exists public.tgbot_promo_start(bigint, int);
drop function if exists public.tgbot_double(bigint, boolean, int);
drop function if exists public.tgbot_claim(bigint, int, int);
drop function if exists public.tgbot_link(bigint, bigint);
drop table if exists public.tgbot_promos;
drop table if exists public.tgbot_codes;
drop table if exists public.tgbot_users;
