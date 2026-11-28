-- Telegram-бот @DiogramRobot: привязка к аккаунту Diogram, кристаллы, выдача промокодов из пула.
-- Бот ходит в Diogram по ключу TG_BOT_KEY, вся логика и данные здесь. Кристаллы хранятся в тысячных (1000 = 1 кристалл).
create table if not exists public.tgbot_users (
  tg_id bigint primary key,
  user_id bigint unique,
  crystals bigint not null default 0 check (crystals >= 0),
  last_claim timestamptz,
  last_double timestamptz,
  promo_ready_at timestamptz,
  linked_at timestamptz,
  created_at timestamptz not null default now()
);
create table if not exists public.tgbot_codes (
  tg_id bigint primary key,
  user_id bigint not null,
  code_hash text not null,
  attempts int not null default 0,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);
create table if not exists public.tgbot_promos (
  id bigserial primary key,
  code text not null,
  given_to_tg bigint,
  given_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index if not exists tgbot_promos_code_lower on public.tgbot_promos (lower(code));
alter table public.tgbot_users enable row level security;
alter table public.tgbot_codes enable row level security;
alter table public.tgbot_promos enable row level security;
revoke all on public.tgbot_users, public.tgbot_codes, public.tgbot_promos from anon, authenticated;
grant all on public.tgbot_users, public.tgbot_codes, public.tgbot_promos to service_role;
grant usage, select on sequence public.tgbot_promos_id_seq to service_role;

-- Привязка: ok | tg_taken (этот Telegram уже у другого аккаунта) | user_taken (аккаунт уже у другого Telegram)
create or replace function public.tgbot_link(p_tg bigint, p_user bigint)
returns text language plpgsql set search_path = public as $$
declare cur bigint;
begin
  insert into tgbot_users(tg_id) values (p_tg) on conflict do nothing;
  select user_id into cur from tgbot_users where tg_id = p_tg for update;
  if cur is not null and cur <> p_user then return 'tg_taken'; end if;
  if exists (select 1 from tgbot_users where user_id = p_user and tg_id <> p_tg) then return 'user_taken'; end if;
  update tgbot_users set user_id = p_user, linked_at = coalesce(linked_at, now()) where tg_id = p_tg;
  return 'ok';
end $$;

-- Забрать кристаллы раз в p_every секунд
create or replace function public.tgbot_claim(p_tg bigint, p_amount int, p_every int)
returns table(ok boolean, crystals bigint, wait_seconds int) language plpgsql set search_path = public as $$
declare u tgbot_users%rowtype; w int;
begin
  insert into tgbot_users(tg_id) values (p_tg) on conflict do nothing;
  select * into u from tgbot_users where tg_id = p_tg for update;
  if u.last_claim is not null and u.last_claim + make_interval(secs => p_every) > now() then
    w := ceil(extract(epoch from (u.last_claim + make_interval(secs => p_every) - now())))::int;
    return query select false, u.crystals, w; return;
  end if;
  update tgbot_users set crystals = tgbot_users.crystals + p_amount, last_claim = now() where tg_id = p_tg returning tgbot_users.crystals into u.crystals;
  return query select true, u.crystals, 0;
end $$;

-- Удвоить или потерять всё; p_win решает сервер. status: empty | wait | win | lose
create or replace function public.tgbot_double(p_tg bigint, p_win boolean, p_every int)
returns table(status text, crystals bigint, wait_seconds int) language plpgsql set search_path = public as $$
declare u tgbot_users%rowtype; w int; c bigint;
begin
  insert into tgbot_users(tg_id) values (p_tg) on conflict do nothing;
  select * into u from tgbot_users where tg_id = p_tg for update;
  if u.crystals <= 0 then return query select 'empty', u.crystals, 0; return; end if;
  if u.last_double is not null and u.last_double + make_interval(secs => p_every) > now() then
    w := ceil(extract(epoch from (u.last_double + make_interval(secs => p_every) - now())))::int;
    return query select 'wait', u.crystals, w; return;
  end if;
  c := case when p_win then u.crystals * 2 else 0 end;
  update tgbot_users set crystals = c, last_double = now() where tg_id = p_tg;
  return query select case when p_win then 'win' else 'lose' end, c, 0;
end $$;

-- Запуск ожидания промокода: not_linked | started | waiting | ready
create or replace function public.tgbot_promo_start(p_tg bigint, p_delay int)
returns table(status text, wait_seconds int) language plpgsql set search_path = public as $$
declare u tgbot_users%rowtype;
begin
  insert into tgbot_users(tg_id) values (p_tg) on conflict do nothing;
  select * into u from tgbot_users where tg_id = p_tg for update;
  if u.user_id is null then return query select 'not_linked', 0; return; end if;
  if u.promo_ready_at is null then
    update tgbot_users set promo_ready_at = now() + make_interval(secs => p_delay) where tg_id = p_tg;
    return query select 'started', p_delay; return;
  end if;
  if u.promo_ready_at > now() then return query select 'waiting', ceil(extract(epoch from (u.promo_ready_at - now())))::int; return; end if;
  return query select 'ready', 0;
end $$;

-- Забрать промокод: not_linked | not_started | waiting | no_crystals | empty | ok
create or replace function public.tgbot_promo_take(p_tg bigint, p_cost int)
returns table(status text, code text, crystals bigint, wait_seconds int) language plpgsql set search_path = public as $$
declare u tgbot_users%rowtype; p tgbot_promos%rowtype;
begin
  insert into tgbot_users(tg_id) values (p_tg) on conflict do nothing;
  select * into u from tgbot_users where tg_id = p_tg for update;
  if u.user_id is null then return query select 'not_linked', null::text, u.crystals, 0; return; end if;
  if u.promo_ready_at is null then return query select 'not_started', null::text, u.crystals, 0; return; end if;
  if u.promo_ready_at > now() then return query select 'waiting', null::text, u.crystals, ceil(extract(epoch from (u.promo_ready_at - now())))::int; return; end if;
  if u.crystals < p_cost then return query select 'no_crystals', null::text, u.crystals, 0; return; end if;
  select * into p from tgbot_promos where given_to_tg is null order by id limit 1 for update skip locked;
  if not found then return query select 'empty', null::text, u.crystals, 0; return; end if;
  update tgbot_promos set given_to_tg = p_tg, given_at = now() where id = p.id;
  update tgbot_users set crystals = tgbot_users.crystals - p_cost, promo_ready_at = null where tg_id = p_tg returning tgbot_users.crystals into u.crystals;
  return query select 'ok', p.code, u.crystals, 0;
end $$;

revoke all on function public.tgbot_link(bigint, bigint), public.tgbot_claim(bigint, int, int), public.tgbot_double(bigint, boolean, int), public.tgbot_promo_start(bigint, int), public.tgbot_promo_take(bigint, int) from public, anon, authenticated;
grant execute on function public.tgbot_link(bigint, bigint), public.tgbot_claim(bigint, int, int), public.tgbot_double(bigint, boolean, int), public.tgbot_promo_start(bigint, int), public.tgbot_promo_take(bigint, int) to service_role;
