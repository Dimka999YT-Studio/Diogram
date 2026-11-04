-- Склад кейсов игроков: кейсы, выданные командой /Case (или оставшиеся) и ещё не открытые.
create table if not exists public.game_case_stock (
  user_id bigint not null,
  case_key text not null,
  qty int not null default 0 check (qty >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, case_key)
);

-- Журнал выдач: кто, кому, какой кейс и сколько.
create table if not exists public.game_case_grants (
  id bigserial primary key,
  admin_id bigint not null,
  user_id bigint not null,
  case_key text not null,
  qty int not null check (qty > 0),
  created_at timestamptz not null default now()
);
create index if not exists game_case_grants_user_idx on public.game_case_grants (user_id, created_at desc);

alter table public.game_case_stock enable row level security;
alter table public.game_case_grants enable row level security;
revoke all on public.game_case_stock, public.game_case_grants from anon, authenticated;
revoke all on sequence public.game_case_grants_id_seq from anon, authenticated;
grant all on public.game_case_stock, public.game_case_grants to service_role;
grant all on sequence public.game_case_grants_id_seq to service_role;

-- Добавить кейсы игроку (возвращает новое количество).
create or replace function public.game_case_add(p_user bigint, p_case text, p_qty int)
returns int language sql set search_path = public as $$
  insert into game_case_stock as s (user_id, case_key, qty, updated_at) values (p_user, p_case, p_qty, now())
  on conflict (user_id, case_key) do update set qty = s.qty + p_qty, updated_at = now()
  returning s.qty
$$;

-- Забрать один кейс со склада: true, если он был (атомарно, в минус не уйти).
create or replace function public.game_case_take(p_user bigint, p_case text)
returns boolean language plpgsql set search_path = public as $$
begin
  update game_case_stock set qty = qty - 1, updated_at = now() where user_id = p_user and case_key = p_case and qty > 0;
  return found;
end $$;

revoke all on function public.game_case_add(bigint, text, int), public.game_case_take(bigint, text) from public, anon, authenticated;
grant execute on function public.game_case_add(bigint, text, int), public.game_case_take(bigint, text) to service_role;
