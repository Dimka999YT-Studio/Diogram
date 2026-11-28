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
