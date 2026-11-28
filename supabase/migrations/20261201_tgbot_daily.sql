-- Бот @DiogramRobot: активация промокодов внутри бота (счётчик активаций) и ежедневные награды (18 дней по кругу).
alter table public.tgbot_users add column if not exists promo_acts int not null default 0;
alter table public.tgbot_users add column if not exists daily_day int not null default 0;        -- сколько наград уже получено
alter table public.tgbot_users add column if not exists daily_arm_at timestamptz;                -- когда человек начал ожидание
alter table public.tgbot_users add column if not exists daily_pending boolean not null default false; -- ожидание идёт, награда ещё не забрана

-- Списать кристаллы (тысячные): остаток или -1, если не хватает.
create or replace function public.tgbot_spend(p_tg bigint, p_cost int)
returns bigint language plpgsql set search_path = public as $$
declare c bigint;
begin
  update tgbot_users set crystals = crystals - p_cost where tg_id = p_tg and crystals >= p_cost returning crystals into c;
  return coalesce(c, -1);
end $$;

-- Начислить кристаллы (тысячные), возвращает остаток.
create or replace function public.tgbot_add(p_tg bigint, p_amount int)
returns bigint language plpgsql set search_path = public as $$
declare c bigint;
begin
  insert into tgbot_users(tg_id) values (p_tg) on conflict do nothing;
  update tgbot_users set crystals = crystals + p_amount where tg_id = p_tg returning crystals into c;
  return c;
end $$;

-- Засчитать успешную активацию промокода в боте, возвращает число активаций.
create or replace function public.tgbot_act_done(p_tg bigint)
returns int language plpgsql set search_path = public as $$
declare n int;
begin
  update tgbot_users set promo_acts = promo_acts + 1 where tg_id = p_tg returning promo_acts into n;
  return coalesce(n, 0);
end $$;

-- Ежедневная награда. p_action: open | claim.
-- status: not_linked | armed (ожидание только что началось) | waiting | ready (можно забирать) | cooldown (рано начинать новое ожидание) | ok (награда засчитана, day = номер награды).
create or replace function public.tgbot_daily(p_tg bigint, p_action text, p_delay int, p_period int)
returns table(status text, day int, wait_seconds int) language plpgsql set search_path = public as $$
declare u tgbot_users%rowtype; w int;
begin
  insert into tgbot_users(tg_id) values (p_tg) on conflict do nothing;
  select * into u from tgbot_users where tg_id = p_tg for update;
  if u.user_id is null then return query select 'not_linked', u.daily_day, 0; return; end if;
  if u.daily_pending then
    if now() >= u.daily_arm_at + make_interval(secs => p_delay) then
      if p_action = 'claim' then
        update tgbot_users set daily_pending = false, daily_day = daily_day + 1 where tg_id = p_tg returning daily_day into u.daily_day;
        return query select 'ok', u.daily_day, 0; return;
      end if;
      return query select 'ready', u.daily_day + 1, 0; return;
    end if;
    w := ceil(extract(epoch from (u.daily_arm_at + make_interval(secs => p_delay) - now())))::int;
    return query select 'waiting', u.daily_day + 1, w; return;
  end if;
  if u.daily_arm_at is null or now() >= u.daily_arm_at + make_interval(secs => p_period) then
    if p_action = 'open' then
      update tgbot_users set daily_arm_at = now(), daily_pending = true where tg_id = p_tg;
      return query select 'armed', u.daily_day + 1, p_delay; return;
    end if;
    return query select 'cooldown', u.daily_day + 1, 0; return;
  end if;
  w := ceil(extract(epoch from (u.daily_arm_at + make_interval(secs => p_period) - now())))::int;
  return query select 'cooldown', u.daily_day + 1, w;
end $$;

-- Откат награды, если выдача сорвалась: снова можно забрать.
create or replace function public.tgbot_daily_release(p_tg bigint)
returns void language plpgsql set search_path = public as $$
begin
  update tgbot_users set daily_pending = true, daily_day = greatest(daily_day - 1, 0) where tg_id = p_tg and not daily_pending;
end $$;

revoke all on function public.tgbot_spend(bigint, int), public.tgbot_add(bigint, int), public.tgbot_act_done(bigint), public.tgbot_daily(bigint, text, int, int), public.tgbot_daily_release(bigint) from public, anon, authenticated;
grant execute on function public.tgbot_spend(bigint, int), public.tgbot_add(bigint, int), public.tgbot_act_done(bigint), public.tgbot_daily(bigint, text, int, int), public.tgbot_daily_release(bigint) to service_role;
