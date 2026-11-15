-- Кейсы @Gaming: виртуальные предметы. Кейсы и предметы владелец ведёт в админке.
-- Выпавший предмет попадает в инвентарь игрока; «Вывести» = заявка владельцу (статусы ниже).
-- Сервер ходит под service_role, RLS включён, прав у anon/authenticated нет.

create table if not exists public.gcase_cases (
  id bigserial primary key,
  title text not null,
  price int not null check (price >= 1),
  active boolean not null default true,
  sort_no int not null default 0,
  created_at timestamptz not null default now()
);

create table if not exists public.gcase_items (
  id bigserial primary key,
  case_id bigint not null references public.gcase_cases(id) on delete cascade,
  name text not null,
  image_url text,
  info text,
  chance numeric(7,3) not null check (chance > 0 and chance <= 100),
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create index if not exists gcase_items_case on public.gcase_items (case_id);

-- Инвентарь: данные предмета копируются в строку, чтобы правка/удаление предмета в админке не ломала выданное.
-- status: have (лежит) | new (заявка на вывод) | work (владелец связался) | given (выдано) | rejected (отказ)
create table if not exists public.gcase_inventory (
  id bigserial primary key,
  user_id bigint not null,
  item_id bigint,
  case_id bigint,
  case_title text not null,
  price_paid int not null,
  name text not null,
  image_url text,
  info text,
  status text not null default 'have' check (status in ('have', 'new', 'work', 'given', 'rejected')),
  admin_note text,
  created_at timestamptz not null default now(),
  requested_at timestamptz,
  updated_at timestamptz not null default now()
);
create index if not exists gcase_inventory_user on public.gcase_inventory (user_id, created_at desc);
create index if not exists gcase_inventory_status on public.gcase_inventory (status, requested_at);

alter table public.gcase_cases enable row level security;
alter table public.gcase_items enable row level security;
alter table public.gcase_inventory enable row level security;
revoke all on public.gcase_cases, public.gcase_items, public.gcase_inventory from anon, authenticated;
grant all on public.gcase_cases, public.gcase_items, public.gcase_inventory to service_role;
grant usage, select on all sequences in schema public to service_role;

insert into public.gcase_cases (title, price, sort_no)
select 'Крутой кейс', 52, 1 where not exists (select 1 from public.gcase_cases where title = 'Крутой кейс');
insert into public.gcase_cases (title, price, sort_no)
select 'ДГ кейс', 18, 2 where not exists (select 1 from public.gcase_cases where title = 'ДГ кейс');
