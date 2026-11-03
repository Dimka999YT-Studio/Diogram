-- Сезонный турнир: облачка, дневные попытки, кейсы и призы (юзернеймы-коллекционки и аккаунты-секретки).
-- Все таблицы закрыты RLS, права только у service_role. Сезон хранится текстом (например 'autumn_2026').

-- Облачка и бонусные игры игрока за сезон. Облачки с карты и из кейсов считаются раздельно, в топ идёт их сумма.
create table if not exists public.game_players (
  user_id bigint not null,
  season text not null,
  clouds_map int not null default 0 check (clouds_map >= 0),
  clouds_case int not null default 0 check (clouds_case >= 0),
  bonus_games int not null default 0 check (bonus_games >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, season)
);
create index if not exists game_players_top_idx on public.game_players (season, (clouds_map + clouds_case) desc);

-- Сколько раундов игрок сыграл в день (день по Москве, считает сервер).
create table if not exists public.game_daily (
  user_id bigint not null,
  day date not null,
  played int not null default 0 check (played >= 0),
  primary key (user_id, day)
);

-- Журнал открытия кейсов.
create table if not exists public.game_case_opens (
  id bigserial primary key,
  user_id bigint not null,
  season text not null,
  case_key text not null,
  price int not null,
  prize_kind text not null,
  prize_amount int not null default 0,
  prize_label text not null,
  created_at timestamptz not null default now()
);
create index if not exists game_case_opens_user_idx on public.game_case_opens (user_id, created_at desc);

-- Аккаунты-секретки (твинки): владелец загружает списком в админке, сервер выдаёт случайный свободный.
create table if not exists public.game_prize_accounts (
  id bigserial primary key,
  email text not null unique,
  password text not null,
  given_to bigint,
  given_at timestamptz,
  created_at timestamptz not null default now()
);

-- Коллекционные юзернеймы турнира. Имя при добавлении кладётся в резерв владельца (owned_usernames.reserved),
-- при выдаче переходит победителю. Запись остаётся навсегда: по ней имя считается личным (его нельзя продать)
-- и показывается номер в серии.
create table if not exists public.game_prize_usernames (
  username text primary key,
  series text not null default 'autumn_2026',
  pool text not null default 'case' check (pool in ('case', 'top')),  -- case: выпадает из кейса по порядку sort_no; top: только награда за топ
  sort_no bigserial,
  series_no int,
  given_to bigint,
  given_at timestamptz,
  created_at timestamptz not null default now()
);
create unique index if not exists game_prize_usernames_lower_idx on public.game_prize_usernames (lower(username));

alter table public.game_players enable row level security;
alter table public.game_daily enable row level security;
alter table public.game_case_opens enable row level security;
alter table public.game_prize_accounts enable row level security;
alter table public.game_prize_usernames enable row level security;
revoke all on public.game_players, public.game_daily, public.game_case_opens, public.game_prize_accounts, public.game_prize_usernames from anon, authenticated;
revoke all on sequence public.game_case_opens_id_seq, public.game_prize_accounts_id_seq, public.game_prize_usernames_sort_no_seq from anon, authenticated;
grant all on public.game_players, public.game_daily, public.game_case_opens, public.game_prize_accounts, public.game_prize_usernames to service_role;
grant all on sequence public.game_case_opens_id_seq, public.game_prize_accounts_id_seq, public.game_prize_usernames_sort_no_seq to service_role;

-- Атомарно прибавляет облачки / бонусные игры (создаёт строку игрока, если её нет).
create or replace function public.game_add(p_user bigint, p_season text, p_map int, p_case int, p_games int)
returns table(clouds_map int, clouds_case int, bonus_games int)
language sql set search_path = public as $$
  insert into game_players as g (user_id, season, clouds_map, clouds_case, bonus_games, updated_at)
  values (p_user, p_season, p_map, p_case, p_games, now())
  on conflict (user_id, season) do update
    set clouds_map = g.clouds_map + p_map, clouds_case = g.clouds_case + p_case,
        bonus_games = g.bonus_games + p_games, updated_at = now()
  returning g.clouds_map, g.clouds_case, g.bonus_games
$$;

-- Тратит одну попытку: сначала дневные (до p_limit), потом бонусные. Возвращает 'daily', 'bonus' или 'none'.
create or replace function public.game_use_attempt(p_user bigint, p_season text, p_day date, p_limit int)
returns text language plpgsql set search_path = public as $$
begin
  insert into game_daily (user_id, day, played) values (p_user, p_day, 0) on conflict do nothing;
  update game_daily set played = played + 1 where user_id = p_user and day = p_day and played < p_limit;
  if found then return 'daily'; end if;
  update game_players set bonus_games = bonus_games - 1, updated_at = now()
    where user_id = p_user and season = p_season and bonus_games > 0;
  if found then return 'bonus'; end if;
  return 'none';
end $$;

-- Топ сезона: только обычные пользователи (сотрудники и забаненные не участвуют). Ничья: кто раньше набрал.
create or replace function public.game_top(p_season text, p_limit int)
returns table(user_id bigint, clouds_map int, clouds_case int, total int)
language sql stable set search_path = public as $$
  select g.user_id, g.clouds_map, g.clouds_case, g.clouds_map + g.clouds_case
  from game_players g join users u on u.id = g.user_id
  where g.season = p_season and u.role = 'user' and coalesce(u.banned, false) = false and g.clouds_map + g.clouds_case > 0
  order by 4 desc, g.updated_at asc, g.user_id asc
  limit p_limit
$$;

-- Место игрока в топе (null, если он не участвует: сотрудник, бан или 0 облачек).
create or replace function public.game_rank(p_season text, p_user bigint)
returns int language sql stable set search_path = public as $$
  with me as (
    select g.clouds_map + g.clouds_case as total, g.updated_at from game_players g join users u on u.id = g.user_id
    where g.season = p_season and g.user_id = p_user and u.role = 'user' and coalesce(u.banned, false) = false and g.clouds_map + g.clouds_case > 0
  )
  select (1 + count(*))::int from game_players g join users u on u.id = g.user_id, me
  where g.season = p_season and u.role = 'user' and coalesce(u.banned, false) = false
    and (g.clouds_map + g.clouds_case > me.total
         or (g.clouds_map + g.clouds_case = me.total and (g.updated_at < me.updated_at or (g.updated_at = me.updated_at and g.user_id < p_user))))
  having exists (select 1 from me)
$$;

-- Выдать случайную свободную секретку (null-строки, если свободных нет). skip locked: два игрока не получат один аккаунт.
create or replace function public.game_take_account(p_user bigint)
returns table(id bigint, email text, password text)
language sql set search_path = public as $$
  update game_prize_accounts a set given_to = p_user, given_at = now()
  where a.id = (select x.id from game_prize_accounts x where x.given_to is null order by random() limit 1 for update skip locked)
  returning a.id, a.email, a.password
$$;

-- Выдать юзернейм-коллекционку: передаёт имя из резерва владельца победителю и ставит номер выдачи в серии.
-- Без p_name берётся следующее по порядку (sort_no) имя из пула кейсов, с p_name - конкретное (награда за топ).
-- Любая ошибка откатывает всё целиком (имя не пропадёт и не окажется выданным без передачи).
create or replace function public.game_take_username(p_user bigint, p_name text default null)
returns table(username text, series_no int)
language plpgsql set search_path = public as $$
declare v_name text; v_no int;
begin
  perform pg_advisory_xact_lock(20261103);
  if p_name is null then
    select x.username into v_name from game_prize_usernames x where x.given_to is null and x.pool = 'case' order by x.sort_no limit 1;
  else
    select x.username into v_name from game_prize_usernames x where x.given_to is null and lower(x.username) = lower(p_name);
  end if;
  if v_name is null then return; end if;
  update owned_usernames set user_id = p_user, reserved = false
    where lower(owned_usernames.username) = lower(v_name) and reserved = true;
  if not found then raise exception 'username % is not in reserve', v_name; end if;
  select coalesce(max(x.series_no), 0) + 1 into v_no from game_prize_usernames x;
  update game_prize_usernames x set given_to = p_user, given_at = now(), series_no = v_no where x.username = v_name;
  return query select v_name, v_no;
end $$;

revoke all on function public.game_add(bigint, text, int, int, int), public.game_use_attempt(bigint, text, date, int),
  public.game_top(text, int), public.game_rank(text, bigint), public.game_take_account(bigint), public.game_take_username(bigint, text)
  from public, anon, authenticated;
grant execute on function public.game_add(bigint, text, int, int, int), public.game_use_attempt(bigint, text, date, int),
  public.game_top(text, int), public.game_rank(text, bigint), public.game_take_account(bigint), public.game_take_username(bigint, text)
  to service_role;
