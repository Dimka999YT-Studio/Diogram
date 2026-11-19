-- Откат: функции и таблицы привязки Telegram и заданий игры. Начисленная голда остаётся в кошельках (журнал gold_log, kind = 'game').
drop function if exists public.tg_claim(bigint, text, int, int);
drop function if exists public.tg_link(text, bigint);
drop table if exists public.tg_task_claims;
drop table if exists public.tg_tasks;
drop table if exists public.tg_link_codes;
drop table if exists public.tg_links;
