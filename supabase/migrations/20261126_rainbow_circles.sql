-- Радужные круги: валюта чата в играх (1 круг за сообщение). Покупаются за Di, первый одиночный круг бесплатно (один раз на аккаунт).
create table if not exists public.rainbow_wallets (
  user_id bigint primary key,
  circles int not null default 0 check (circles >= 0),
  bought_total int not null default 0,
  free_used boolean not null default false,
  created_at timestamptz not null default now()
);
create table if not exists public.rainbow_log (
  id bigserial primary key,
  user_id bigint not null,
  delta int not null,
  kind text not null,           -- free | buy | spend | refund
  price numeric,
  created_at timestamptz not null default now()
);
create index if not exists rainbow_log_user on public.rainbow_log (user_id, created_at desc);
alter table public.rainbow_wallets enable row level security;
alter table public.rainbow_log enable row level security;
revoke all on public.rainbow_wallets, public.rainbow_log from anon, authenticated;
grant all on public.rainbow_wallets, public.rainbow_log to service_role;
grant usage, select on sequence public.rainbow_log_id_seq to service_role;

-- Покупка: списание Di (дробное, баланс numeric) и зачисление кругов одним вызовом под блокировкой строки кошелька.
create or replace function public.rainbow_buy(p_user bigint, p_qty int, p_price numeric, p_free boolean)
returns table(ok boolean, err text, circles int, balance numeric)
language plpgsql set search_path = public as $$
declare w public.rainbow_wallets; bal numeric;
begin
  if p_qty is null or p_qty <= 0 or p_price is null or p_price < 0 then return query select false, 'bad', 0, 0::numeric; return; end if;
  insert into rainbow_wallets(user_id) values (p_user) on conflict do nothing;
  select * into w from rainbow_wallets where user_id = p_user for update;
  if p_free then
    if w.free_used then return query select false, 'free_used', w.circles, (select u.balance from users u where u.id = p_user); return; end if;
    update rainbow_wallets set free_used = true where user_id = p_user;
  else
    update users set balance = users.balance - p_price where id = p_user and users.balance >= p_price returning users.balance into bal;
    if not found then return query select false, 'no_money', w.circles, (select u.balance from users u where u.id = p_user); return; end if;
  end if;
  update rainbow_wallets set circles = rainbow_wallets.circles + p_qty, bought_total = bought_total + p_qty where user_id = p_user returning rainbow_wallets.circles into w.circles;
  insert into rainbow_log(user_id, delta, kind, price) values (p_user, p_qty, case when p_free then 'free' else 'buy' end, p_price);
  return query select true, null::text, w.circles, (select u.balance from users u where u.id = p_user);
end $$;

-- Списание кругов за сообщение: возвращает остаток или -1, если не хватило.
create or replace function public.rainbow_spend(p_user bigint, p_qty int)
returns int language plpgsql set search_path = public as $$
declare left_ int;
begin
  if p_qty is null or p_qty <= 0 then return -1; end if;
  update rainbow_wallets set circles = rainbow_wallets.circles - p_qty where user_id = p_user and rainbow_wallets.circles >= p_qty returning rainbow_wallets.circles into left_;
  if not found then return -1; end if;
  return left_;
end $$;

create or replace function public.rainbow_refund(p_user bigint, p_qty int)
returns int language plpgsql set search_path = public as $$
declare left_ int;
begin
  if p_qty is null or p_qty <= 0 then return -1; end if;
  update rainbow_wallets set circles = rainbow_wallets.circles + p_qty where user_id = p_user returning rainbow_wallets.circles into left_;
  return coalesce(left_, -1);
end $$;
revoke all on function public.rainbow_buy(bigint, int, numeric, boolean), public.rainbow_spend(bigint, int), public.rainbow_refund(bigint, int) from public, anon, authenticated;
grant execute on function public.rainbow_buy(bigint, int, numeric, boolean), public.rainbow_spend(bigint, int), public.rainbow_refund(bigint, int) to service_role;
