-- Промокоды: временные (до даты, без лимита) и постоянные с лимитом активаций (1..∞).
-- Награды: Di, дни PDG, облачка, кейсы, юзернейм (из набора, привязанного к коду).
-- Сервер ходит под service_role, RLS включён, прав у anon/authenticated нет.

create table if not exists public.promo_codes (
  id bigserial primary key,
  code text not null,
  kind text not null check (kind in ('timed', 'limited')),
  expires_at timestamptz,
  max_activations int check (max_activations is null or max_activations >= 1),
  activations int not null default 0,
  active boolean not null default true,
  rewards jsonb not null default '{}'::jsonb,
  note text,
  created_by bigint,
  created_at timestamptz not null default now(),
  check (kind <> 'timed' or expires_at is not null)
);
create unique index if not exists promo_codes_code_lower on public.promo_codes (lower(code));

create table if not exists public.promo_activations (
  code_id bigint not null references public.promo_codes(id) on delete cascade,
  user_id bigint not null,
  created_at timestamptz not null default now(),
  primary key (code_id, user_id)
);
create index if not exists promo_activations_user on public.promo_activations (user_id);

create table if not exists public.promo_usernames (
  username text primary key,
  code_id bigint not null references public.promo_codes(id) on delete cascade,
  sort_no int not null default 0,
  series_no int,
  given_to bigint,
  given_at timestamptz,
  resales int not null default 0
);
create index if not exists promo_usernames_code on public.promo_usernames (code_id, sort_no);

alter table public.promo_codes enable row level security;
alter table public.promo_activations enable row level security;
alter table public.promo_usernames enable row level security;
revoke all on public.promo_codes, public.promo_activations, public.promo_usernames from anon, authenticated;
grant all on public.promo_codes, public.promo_activations, public.promo_usernames to service_role;
grant usage, select on sequence public.promo_codes_id_seq to service_role;

-- Атомарная активация: проверки, строка активации, счётчик и выдача юзернейма (если он в наградах).
-- status: ok | not_found | inactive | expired | exhausted | used | no_username
create or replace function public.promo_claim(p_code text, p_user bigint)
returns table(status text, code_id bigint, rewards jsonb, username text, series_no int, uname_no int, uname_total int)
language plpgsql set search_path = public as $$
declare
  c promo_codes%rowtype;
  v_name text; v_no int; v_total int;
begin
  select * into c from promo_codes where lower(promo_codes.code) = lower(p_code) for update;
  if not found then return query select 'not_found', null::bigint, null::jsonb, null::text, null::int, null::int, null::int; return; end if;
  if not c.active then return query select 'inactive', c.id, c.rewards, null::text, null::int, null::int, null::int; return; end if;
  if c.kind = 'timed' and c.expires_at <= now() then return query select 'expired', c.id, c.rewards, null::text, null::int, null::int, null::int; return; end if;
  if c.kind = 'limited' and c.max_activations is not null and c.activations >= c.max_activations then
    return query select 'exhausted', c.id, c.rewards, null::text, null::int, null::int, null::int; return;
  end if;
  if exists (select 1 from promo_activations a where a.code_id = c.id and a.user_id = p_user) then
    return query select 'used', c.id, c.rewards, null::text, null::int, null::int, null::int; return;
  end if;

  if coalesce((c.rewards ->> 'username')::boolean, false) then
    select x.username into v_name from promo_usernames x
      where x.code_id = c.id and x.given_to is null order by x.sort_no, x.username limit 1 for update skip locked;
    if v_name is null then return query select 'no_username', c.id, c.rewards, null::text, null::int, null::int, null::int; return; end if;
    update owned_usernames set user_id = p_user, reserved = false
      where lower(owned_usernames.username) = lower(v_name) and reserved = true;
    if not found then raise exception 'username % is not in reserve', v_name; end if;
    select count(*) + 1 into v_no from promo_usernames x where x.code_id = c.id and x.given_to is not null;
    update promo_usernames x set given_to = p_user, given_at = now(), series_no = v_no where x.username = v_name;
    select count(*) into v_total from promo_usernames x where x.code_id = c.id;
  end if;

  insert into promo_activations (code_id, user_id) values (c.id, p_user);
  update promo_codes set activations = activations + 1 where id = c.id;
  return query select 'ok', c.id, c.rewards, v_name, v_no, v_no, v_total;
end $$;

-- Откат активации, если выдать остальные награды не удалось (юзернейм возвращается в резерв владельца).
create or replace function public.promo_release(p_code_id bigint, p_user bigint)
returns void language plpgsql set search_path = public as $$
declare v_name text; v_owner bigint;
begin
  perform 1 from promo_codes where id = p_code_id for update;
  delete from promo_activations where code_id = p_code_id and user_id = p_user;
  if found then update promo_codes set activations = greatest(activations - 1, 0) where id = p_code_id; end if;
  select x.username into v_name from promo_usernames x where x.code_id = p_code_id and x.given_to = p_user and x.resales = 0 limit 1;
  if v_name is not null then
    select id into v_owner from users where role = 'owner' order by id limit 1;
    update owned_usernames set user_id = v_owner, reserved = true where lower(username) = lower(v_name) and user_id = p_user;
    update promo_usernames set given_to = null, given_at = null, series_no = null where username = v_name;
  end if;
end $$;

-- Счётчик перепродаж: лот с промо-юзернеймом перешёл к покупателю.
create or replace function public.promo_count_resale() returns trigger
language plpgsql set search_path = public as $$
begin
  if new.status = 'sold' and old.status is distinct from 'sold' then
    update promo_usernames set resales = resales + 1 where lower(username) = lower(new.username);
  end if;
  return new;
end $$;

drop trigger if exists promo_resale_trg on public.frigdmant_listings;
create trigger promo_resale_trg after update of status on public.frigdmant_listings
  for each row execute function public.promo_count_resale();

revoke all on function public.promo_claim(text, bigint), public.promo_release(bigint, bigint), public.promo_count_resale()
  from public, anon, authenticated;
grant execute on function public.promo_claim(text, bigint), public.promo_release(bigint, bigint), public.promo_count_resale() to service_role;
