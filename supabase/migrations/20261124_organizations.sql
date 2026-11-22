-- Верификация организациями: организация со своим значком, участники (владелец и сотрудники) ставят этот значок пользователям и ботам.
-- Значок icon_url показывается рядом с галочкой у названия, серый note_icon_url стоит в строке «… верифицирован организацией …» в профиле.
-- Верифицировать можно пользователя, бота или канал (target_type user / channel).

create table if not exists public.organizations (
  id bigserial primary key,
  name text not null check (char_length(name) between 2 and 60),
  icon_url text,
  note_icon_url text,
  created_at timestamptz not null default now()
);
create unique index if not exists organizations_name_lower on public.organizations (lower(name));

-- role: owner (владелец организации) или staff (сотрудник). Оба могут верифицировать от имени организации.
create table if not exists public.organization_members (
  org_id bigint not null references public.organizations(id) on delete cascade,
  user_id bigint not null,
  role text not null default 'staff' check (role in ('owner', 'staff')),
  added_at timestamptz not null default now(),
  primary key (org_id, user_id)
);
create index if not exists organization_members_user on public.organization_members (user_id);

create table if not exists public.organization_verified (
  org_id bigint not null references public.organizations(id) on delete cascade,
  target_type text not null default 'user' check (target_type in ('user', 'channel')),
  target_id bigint not null,
  by_user_id bigint,
  created_at timestamptz not null default now(),
  primary key (org_id, target_type, target_id)
);
create index if not exists organization_verified_target on public.organization_verified (target_type, target_id);

alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;
alter table public.organization_verified enable row level security;
revoke all on public.organizations, public.organization_members, public.organization_verified from anon, authenticated;
grant all on public.organizations, public.organization_members, public.organization_verified to service_role;
grant usage, select on sequence public.organizations_id_seq to service_role;
