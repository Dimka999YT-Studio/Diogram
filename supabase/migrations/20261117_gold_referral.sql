-- Голда (внутренняя валюта @Gaming: обмен 1 Di = 1 голда, кейсы за голду, вывод в игру по заявке)
-- и реферальная программа (код приглашения, бонус обоим после условий активности, 5% владельцу с купленной голды).
-- Сервер ходит под service_role, RLS включён, прав у anon/authenticated нет.

create table if not exists public.gold_wallets (
  user_id bigint primary key,
  gold int not null default 0 check (gold >= 0),                 -- купленная за Di, выводится свободно (от минимума)
  bonus numeric(14,2) not null default 0 check (bonus >= 0),     -- реферальная: тратится на кейсы, вывод после порога трат
  spent_total int not null default 0 check (spent_total >= 0),   -- всего потрачено на кейсы
  updated_at timestamptz not null default now()
);

create table if not exists public.gold_log (
  id bigserial primary key,
  user_id bigint not null,
  kind text not null,          -- exchange | spend | refund | withdraw | withdraw_refund | ref_welcome | ref_invite | ref_commission | admin
  gold int not null default 0,
  bonus numeric(14,2) not null default 0,
  ref text,
  created_at timestamptz not null default now()
);
create index if not exists gold_log_user on public.gold_log (user_id, created_at desc);
create index if not exists gold_log_kind on public.gold_log (kind, created_at desc);

create table if not exists public.gold_withdrawals (
  id bigserial primary key,
  user_id bigint not null,
  amount int not null check (amount > 0),
  from_gold int not null default 0,
  from_bonus int not null default 0,
  game_nick text not null,
  status text not null default 'new' check (status in ('new', 'work', 'given', 'rejected')),
  admin_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists gold_withdrawals_user on public.gold_withdrawals (user_id, created_at desc);
create index if not exists gold_withdrawals_status on public.gold_withdrawals (status, created_at);

create table if not exists public.ref_codes (
  id bigserial primary key,
  code text not null,
  owner_id bigint not null,
  kind text not null default 'personal' check (kind in ('personal', 'custom')),
  max_uses int check (max_uses is null or max_uses >= 1),
  uses int not null default 0,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create unique index if not exists ref_codes_code_lower on public.ref_codes (lower(code));
create index if not exists ref_codes_owner on public.ref_codes (owner_id);

create table if not exists public.ref_uses (
  invitee_id bigint primary key,
  code_id bigint not null references public.ref_codes(id) on delete cascade,
  owner_id bigint not null,
  status text not null default 'pending' check (status in ('pending', 'paid')),
  created_at timestamptz not null default now(),
  paid_at timestamptz
);
create index if not exists ref_uses_owner on public.ref_uses (owner_id, status);

alter table public.gold_wallets enable row level security;
alter table public.gold_log enable row level security;
alter table public.gold_withdrawals enable row level security;
alter table public.ref_codes enable row level security;
alter table public.ref_uses enable row level security;
revoke all on public.gold_wallets, public.gold_log, public.gold_withdrawals, public.ref_codes, public.ref_uses from anon, authenticated;
grant all on public.gold_wallets, public.gold_log, public.gold_withdrawals, public.ref_codes, public.ref_uses to service_role;
grant usage, select on all sequences in schema public to service_role;

-- Начисление (голда и/или бонус) + запись в журнал.
create or replace function public.gold_add(p_user bigint, p_gold int, p_bonus numeric, p_kind text, p_ref text default null)
returns void language plpgsql set search_path = public as $$
begin
  insert into gold_wallets (user_id, gold, bonus) values (p_user, p_gold, p_bonus)
    on conflict (user_id) do update set gold = gold_wallets.gold + excluded.gold, bonus = gold_wallets.bonus + excluded.bonus, updated_at = now();
  insert into gold_log (user_id, kind, gold, bonus, ref) values (p_user, p_kind, p_gold, p_bonus, p_ref);
end $$;

-- Списание на кейс: сначала целая часть бонуса, потом голда. Возвращает, сколько откуда взято.
create or replace function public.gold_spend(p_user bigint, p_amount int, p_ref text default null)
returns table(ok boolean, from_bonus int, from_gold int) language plpgsql set search_path = public as $$
declare w gold_wallets%rowtype; v_b int; v_g int;
begin
  if p_amount <= 0 then return query select false, 0, 0; return; end if;
  select * into w from gold_wallets where user_id = p_user for update;
  if not found or floor(w.bonus)::int + w.gold < p_amount then return query select false, 0, 0; return; end if;
  v_b := least(floor(w.bonus)::int, p_amount); v_g := p_amount - v_b;
  update gold_wallets set bonus = bonus - v_b, gold = gold - v_g, spent_total = spent_total + p_amount, updated_at = now() where user_id = p_user;
  insert into gold_log (user_id, kind, gold, bonus, ref) values (p_user, 'spend', -v_g, -v_b, p_ref);
  return query select true, v_b, v_g;
end $$;

-- Возврат, если предмет выдать не удалось.
create or replace function public.gold_refund(p_user bigint, p_bonus int, p_gold int, p_ref text default null)
returns void language plpgsql set search_path = public as $$
begin
  update gold_wallets set bonus = bonus + p_bonus, gold = gold + p_gold, spent_total = greatest(spent_total - p_bonus - p_gold, 0), updated_at = now() where user_id = p_user;
  insert into gold_log (user_id, kind, gold, bonus, ref) values (p_user, 'refund', p_gold, p_bonus, p_ref);
end $$;

-- 5% (p_rate) пригласившему с купленной (не бонусной) голды, потраченной приглашённым; только если приглашение засчитано.
create or replace function public.gold_commission(p_user bigint, p_gold int, p_rate numeric)
returns numeric language plpgsql set search_path = public as $$
declare v_owner bigint; v_c numeric;
begin
  select owner_id into v_owner from ref_uses where invitee_id = p_user and status = 'paid';
  if v_owner is null or p_gold <= 0 then return 0; end if;
  v_c := round(p_gold * p_rate, 2);
  if v_c <= 0 then return 0; end if;
  perform gold_add(v_owner, 0, v_c, 'ref_commission', p_user::text);
  return v_c;
end $$;

-- Заявка на вывод в игру. status: ok | too_small | no_funds
create or replace function public.gold_withdraw_request(p_user bigint, p_amount int, p_nick text, p_min int, p_unlock int)
returns table(status text, id bigint) language plpgsql set search_path = public as $$
declare w gold_wallets%rowtype; v_avail_b int; v_g int; v_b int; v_id bigint;
begin
  if p_amount < p_min then return query select 'too_small', null::bigint; return; end if;
  select * into w from gold_wallets where user_id = p_user for update;
  if not found then return query select 'no_funds', null::bigint; return; end if;
  v_avail_b := case when w.spent_total >= p_unlock then floor(w.bonus)::int else 0 end;
  if w.gold + v_avail_b < p_amount then return query select 'no_funds', null::bigint; return; end if;
  v_g := least(w.gold, p_amount); v_b := p_amount - v_g;
  update gold_wallets set gold = gold - v_g, bonus = bonus - v_b, updated_at = now() where user_id = p_user;
  insert into gold_withdrawals (user_id, amount, from_gold, from_bonus, game_nick) values (p_user, p_amount, v_g, v_b, p_nick) returning gold_withdrawals.id into v_id;
  insert into gold_log (user_id, kind, gold, bonus, ref) values (p_user, 'withdraw', -v_g, -v_b, v_id::text);
  return query select 'ok', v_id;
end $$;

-- Смена статуса заявки владельцем. work | given | rejected (отказ возвращает голду на баланс). Закрытую заявку не меняет.
create or replace function public.gold_withdraw_close(p_id bigint, p_status text, p_note text)
returns table(ok boolean, user_id bigint, amount int) language plpgsql set search_path = public as $$
declare r gold_withdrawals%rowtype;
begin
  if p_status not in ('work', 'given', 'rejected') then return query select false, null::bigint, null::int; return; end if;
  select * into r from gold_withdrawals where gold_withdrawals.id = p_id and gold_withdrawals.status in ('new', 'work') for update;
  if not found then return query select false, null::bigint, null::int; return; end if;
  update gold_withdrawals set status = p_status, admin_note = nullif(p_note, ''), updated_at = now() where gold_withdrawals.id = p_id;
  if p_status = 'rejected' then perform gold_add(r.user_id, r.from_gold, r.from_bonus, 'withdraw_refund', p_id::text); end if;
  return query select true, r.user_id, r.amount;
end $$;

-- Прогресс приглашённого: отправленные сообщения, чаты с живыми собеседниками (ответили не пригласивший и не боты), дни активности (МСК).
create or replace function public.ref_progress(p_user bigint, p_owner bigint, p_bots bigint[])
returns table(msgs int, chats int, days int) language sql stable set search_path = public as $$
  with mine as (
    select chat_id, created_at from messages
    where sender_id = p_user and type in ('text', 'photo', 'file') and channel_post_id is null
  )
  select (select count(*)::int from mine),
         (select count(distinct m.chat_id)::int from mine m where exists (
            select 1 from messages o where o.chat_id = m.chat_id and o.sender_id <> p_user and o.sender_id <> p_owner
              and not (o.sender_id = any(p_bots)) and o.type in ('text', 'photo', 'file'))),
         (select count(distinct (created_at at time zone 'Europe/Moscow')::date)::int from mine)
$$;

-- Ввод кода приглашённым. status: ok | not_found | inactive | exhausted | own | used | too_old | cycle
create or replace function public.ref_activate(p_user bigint, p_code text, p_window_days int)
returns table(status text, owner_id bigint) language plpgsql set search_path = public as $$
declare c ref_codes%rowtype; v_created timestamptz;
begin
  select * into c from ref_codes where lower(ref_codes.code) = lower(p_code) for update;
  if not found then return query select 'not_found', null::bigint; return; end if;
  if not c.active then return query select 'inactive', null::bigint; return; end if;
  if c.owner_id = p_user then return query select 'own', null::bigint; return; end if;
  if c.max_uses is not null and c.uses >= c.max_uses then return query select 'exhausted', null::bigint; return; end if;
  if exists (select 1 from ref_uses where invitee_id = p_user) then return query select 'used', null::bigint; return; end if;
  if exists (select 1 from ref_uses where invitee_id = c.owner_id and ref_uses.owner_id = p_user) then return query select 'cycle', null::bigint; return; end if;
  select created_at into v_created from users where id = p_user;
  if v_created is null or v_created < now() - make_interval(days => p_window_days) then return query select 'too_old', null::bigint; return; end if;
  insert into ref_uses (invitee_id, code_id, owner_id) values (p_user, c.id, c.owner_id);
  update ref_codes set uses = uses + 1 where id = c.id;
  return query select 'ok', c.owner_id;
end $$;

-- Проверка условий и выплата бонуса обоим (один раз). status: none | waiting | limit | paid
create or replace function public.ref_settle(p_invitee bigint, p_bots bigint[], p_min_msgs int, p_min_chats int, p_min_days int, p_reward numeric, p_daily_limit int)
returns table(status text, owner_id bigint) language plpgsql set search_path = public as $$
declare u ref_uses%rowtype; pr record; v_today int;
begin
  select * into u from ref_uses where invitee_id = p_invitee and ref_uses.status = 'pending' for update;
  if not found then return query select 'none', null::bigint; return; end if;
  select * into pr from ref_progress(p_invitee, u.owner_id, p_bots);
  if pr.msgs < p_min_msgs or pr.chats < p_min_chats or pr.days < p_min_days then return query select 'waiting', u.owner_id; return; end if;
  select count(*) into v_today from ref_uses x where x.owner_id = u.owner_id and x.status = 'paid'
    and (x.paid_at at time zone 'Europe/Moscow')::date = (now() at time zone 'Europe/Moscow')::date;
  if v_today >= p_daily_limit then return query select 'limit', u.owner_id; return; end if;
  update ref_uses set status = 'paid', paid_at = now() where invitee_id = p_invitee;
  perform gold_add(p_invitee, 0, p_reward, 'ref_welcome', u.owner_id::text);
  perform gold_add(u.owner_id, 0, p_reward, 'ref_invite', p_invitee::text);
  return query select 'paid', u.owner_id;
end $$;

revoke all on function public.gold_add(bigint, int, numeric, text, text), public.gold_spend(bigint, int, text), public.gold_refund(bigint, int, int, text),
  public.gold_commission(bigint, int, numeric), public.gold_withdraw_request(bigint, int, text, int, int), public.gold_withdraw_close(bigint, text, text),
  public.ref_progress(bigint, bigint, bigint[]), public.ref_activate(bigint, text, int), public.ref_settle(bigint, bigint[], int, int, int, numeric, int)
  from public, anon, authenticated;
grant execute on function public.gold_add(bigint, int, numeric, text, text), public.gold_spend(bigint, int, text), public.gold_refund(bigint, int, int, text),
  public.gold_commission(bigint, int, numeric), public.gold_withdraw_request(bigint, int, text, int, int), public.gold_withdraw_close(bigint, text, text),
  public.ref_progress(bigint, bigint, bigint[]), public.ref_activate(bigint, text, int), public.ref_settle(bigint, bigint[], int, int, int, numeric, int)
  to service_role;
