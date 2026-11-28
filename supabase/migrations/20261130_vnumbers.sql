-- Виртуальные номера Diogram: +999 XXXX XXXX. Номер принадлежит аккаунту, по нему можно войти (вместе с паролем).
-- source: buy (куплен за Di) | gift (выдал владелец) | reserve (резерв владельца, купить нельзя).
create table if not exists public.vnumbers (
  number text primary key check (number ~ '^[0-9]{8}$'),
  user_id bigint not null,
  is_main boolean not null default false,
  source text not null default 'buy',
  price int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists vnumbers_user on public.vnumbers (user_id);
create unique index if not exists vnumbers_one_main on public.vnumbers (user_id) where is_main;
alter table public.vnumbers enable row level security;
revoke all on public.vnumbers from anon, authenticated;
grant all on public.vnumbers to service_role;

-- Взять номер: ok | taken | limit. Первый номер аккаунта становится основным.
create or replace function public.vnumber_take(p_user bigint, p_number text, p_source text, p_price int, p_max int)
returns text language plpgsql set search_path = public as $$
declare n int;
begin
  perform pg_advisory_xact_lock(hashtext('vnumber_user:' || p_user));
  if p_source <> 'reserve' then
    select count(*) into n from vnumbers where user_id = p_user and source <> 'reserve';
    if n >= p_max then return 'limit'; end if;
  end if;
  begin
    insert into vnumbers(number, user_id, is_main, source, price)
    values (p_number, p_user, p_source <> 'reserve' and not exists (select 1 from vnumbers where user_id = p_user and is_main), p_source, p_price);
  exception when unique_violation then
    return 'taken';
  end;
  return 'ok';
end $$;
revoke all on function public.vnumber_take(bigint, text, text, int, int) from public, anon, authenticated;
grant execute on function public.vnumber_take(bigint, text, text, int, int) to service_role;

-- Запас номеров на продажу за звёзды Telegram (пополняет owner в боте) и журнал продаж (идемпотентность по номеру платежа).
create table if not exists public.vnumber_stock (
  number text primary key check (number ~ '^[0-9]{8}$'),
  tier text not null default 'common',
  created_at timestamptz not null default now(),
  sold_at timestamptz,
  sold_to bigint
);
create index if not exists vnumber_stock_free on public.vnumber_stock (tier) where sold_at is null;
create table if not exists public.vnumber_sales (
  charge_id text primary key,
  tg_id bigint not null,
  user_id bigint not null,
  number text not null,
  stars int not null,
  created_at timestamptz not null default now()
);
alter table public.vnumber_stock enable row level security;
alter table public.vnumber_sales enable row level security;
revoke all on public.vnumber_stock, public.vnumber_sales from anon, authenticated;
grant all on public.vnumber_stock, public.vnumber_sales to service_role;

-- Продажа после оплаты: ok | dup (этот платёж уже обработан) | limit | empty (нет такого свободного номера).
-- p_number пусто = случайный свободный обычный из запаса.
create or replace function public.vnumber_sell(p_tg bigint, p_user bigint, p_number text, p_stars int, p_charge text, p_max int)
returns table(status text, number text) language plpgsql set search_path = public as $$
declare n int; s vnumber_stock%rowtype; old vnumber_sales%rowtype;
begin
  perform pg_advisory_xact_lock(hashtext('vnumber_user:' || p_user));
  select * into old from vnumber_sales where charge_id = p_charge;
  if found then return query select 'dup', old.number; return; end if;
  select count(*) into n from vnumbers v where v.user_id = p_user and v.source <> 'reserve';
  if n >= p_max then return query select 'limit', null::text; return; end if;
  if p_number is null then
    select * into s from vnumber_stock where sold_at is null and tier = 'common' order by random() limit 1 for update skip locked;
  else
    select * into s from vnumber_stock where vnumber_stock.number = p_number and sold_at is null for update skip locked;
  end if;
  if not found then return query select 'empty', null::text; return; end if;
  begin
    insert into vnumbers(number, user_id, is_main, source, price)
    values (s.number, p_user, not exists (select 1 from vnumbers v2 where v2.user_id = p_user and v2.is_main), 'buy', p_stars);
  exception when unique_violation then
    update vnumber_stock set sold_at = now() where vnumber_stock.number = s.number;
    return query select 'empty', null::text; return;
  end;
  update vnumber_stock set sold_at = now(), sold_to = p_user where vnumber_stock.number = s.number;
  insert into vnumber_sales(charge_id, tg_id, user_id, number, stars) values (p_charge, p_tg, p_user, s.number, p_stars);
  return query select 'ok', s.number;
end $$;
revoke all on function public.vnumber_sell(bigint, bigint, text, int, text, int) from public, anon, authenticated;
grant execute on function public.vnumber_sell(bigint, bigint, text, int, text, int) to service_role;
