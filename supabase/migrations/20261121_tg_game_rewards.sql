-- Награды голдой за задания Telegram-игры (Arrow Puzzle): привязка Telegram к аккаунту Diogram и выдача.
-- Игра сообщает о задании своим сервером (по секретному ключу), Diogram запоминает выдачу и платит один раз.

create table if not exists public.tg_links (
  user_id bigint primary key,
  tg_id bigint not null unique,
  linked_at timestamptz not null default now()
);

-- Одноразовые коды привязки (хранится только хэш), живут несколько минут.
create table if not exists public.tg_link_codes (
  code_hash text primary key,
  user_id bigint not null,
  expires_at timestamptz not null,
  used boolean not null default false
);
create index if not exists tg_link_codes_user on public.tg_link_codes (user_id);

-- Задания ведёт owner: once = один раз на Telegram-аккаунт, daily = раз в сутки (по МСК).
create table if not exists public.tg_tasks (
  id text primary key check (id ~ '^[a-z0-9_]{2,40}$'),
  title text not null,
  gold int not null check (gold between 1 and 1000),
  kind text not null default 'once' check (kind in ('once', 'daily')),
  active boolean not null default true,
  created_at timestamptz not null default now()
);

-- Выдачи привязаны к Telegram-аккаунту, а не к аккаунту Diogram: перепривязка не даёт получить задание второй раз.
-- Для once день фиксированный (2000-01-01), для daily это сегодняшний день по МСК.
create table if not exists public.tg_task_claims (
  tg_id bigint not null,
  task_id text not null,
  day date not null,
  user_id bigint not null,
  gold int not null,
  created_at timestamptz not null default now(),
  primary key (tg_id, task_id, day)
);
create index if not exists tg_task_claims_user on public.tg_task_claims (user_id, created_at desc);

alter table public.tg_links enable row level security;
alter table public.tg_link_codes enable row level security;
alter table public.tg_tasks enable row level security;
alter table public.tg_task_claims enable row level security;
revoke all on public.tg_links, public.tg_link_codes, public.tg_tasks, public.tg_task_claims from anon, authenticated;
grant all on public.tg_links, public.tg_link_codes, public.tg_tasks, public.tg_task_claims to service_role;

-- Привязка по коду: одна к одной, код одноразовый. status: ok | bad_code | tg_taken | user_linked
create or replace function public.tg_link(p_code_hash text, p_tg bigint)
returns text language plpgsql set search_path = public as $$
declare c tg_link_codes%rowtype;
begin
  select * into c from tg_link_codes where code_hash = p_code_hash and not used and expires_at > now() for update;
  if not found then return 'bad_code'; end if;
  if exists (select 1 from tg_links where tg_id = p_tg) then return 'tg_taken'; end if;
  if exists (select 1 from tg_links where user_id = c.user_id) then return 'user_linked'; end if;
  insert into tg_links (user_id, tg_id) values (c.user_id, p_tg);
  update tg_link_codes set used = true where code_hash = p_code_hash;
  return 'ok';
end $$;

-- Выдача награды за задание. status: ok | not_linked | too_new | no_task | already | cap
-- Лимит cap считается на Telegram-аккаунт за сутки по МСК. Всё в одной транзакции под блокировкой по tg_id.
create or replace function public.tg_claim(p_tg bigint, p_task text, p_cap int, p_min_days int)
returns table(status text, gold int, today int, uid bigint, title text) language plpgsql set search_path = public as $$
declare l tg_links%rowtype; t tg_tasks%rowtype; u_created timestamptz; v_day date; v_claim_day date; v_today int; v_rows int;
begin
  perform pg_advisory_xact_lock(p_tg);
  select * into l from tg_links where tg_id = p_tg;
  if not found then return query select 'not_linked'::text, 0, 0, null::bigint, null::text; return; end if;
  select created_at into u_created from users where id = l.user_id;
  if u_created is null or u_created > now() - make_interval(days => p_min_days) then
    return query select 'too_new'::text, 0, 0, l.user_id, null::text; return; end if;
  select * into t from tg_tasks where id = p_task and active;
  if not found then return query select 'no_task'::text, 0, 0, l.user_id, null::text; return; end if;
  v_day := (now() at time zone 'Europe/Moscow')::date;
  v_claim_day := case t.kind when 'once' then date '2000-01-01' else v_day end;
  if exists (select 1 from tg_task_claims where tg_id = p_tg and task_id = t.id and day = v_claim_day) then
    return query select 'already'::text, 0, 0, l.user_id, t.title; return; end if;
  select coalesce(sum(c.gold), 0) into v_today from tg_task_claims c where c.tg_id = p_tg and (c.created_at at time zone 'Europe/Moscow')::date = v_day;
  if v_today + t.gold > p_cap then return query select 'cap'::text, 0, v_today, l.user_id, t.title; return; end if;
  insert into tg_task_claims (tg_id, task_id, day, user_id, gold) values (p_tg, t.id, v_claim_day, l.user_id, t.gold);
  perform gold_add(l.user_id, 0, t.gold, 'game', t.id);
  return query select 'ok'::text, t.gold, v_today + t.gold, l.user_id, t.title;
end $$;

revoke all on function public.tg_link(text, bigint), public.tg_claim(bigint, text, int, int) from public, anon, authenticated;
grant execute on function public.tg_link(text, bigint), public.tg_claim(bigint, text, int, int) to service_role;
