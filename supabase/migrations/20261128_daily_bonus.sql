-- Ежедневный лимитированный бонус: задания с общим лимитом выполнений (кто успел), один раз на человека.
create table if not exists public.daily_tasks (
  id serial primary key,
  kind text not null check (kind in ('clouds', 'owner_chat')),
  title text not null,
  descr text not null default '',
  need int not null default 1,            -- clouds: сколько облачков; owner_chat: сколько своих сообщений
  need2 int not null default 0,           -- owner_chat: сколько ответов владельца
  starts_at timestamptz not null,
  requires_task int references public.daily_tasks(id),   -- откроется, только когда выполнения того задания закончились
  max_completions int not null check (max_completions > 0),
  completions int not null default 0,
  reward_clouds int not null default 0,
  reward_rainbow int not null default 0,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create table if not exists public.daily_claims (
  task_id int not null references public.daily_tasks(id) on delete cascade,
  user_id bigint not null,
  claimed_at timestamptz not null default now(),
  primary key (task_id, user_id)
);
alter table public.daily_tasks enable row level security;
alter table public.daily_claims enable row level security;
revoke all on public.daily_tasks, public.daily_claims from anon, authenticated;
grant all on public.daily_tasks, public.daily_claims to service_role;
grant usage, select on sequence public.daily_tasks_id_seq to service_role;

-- status: ok | not_found | inactive | not_started | locked | exhausted | used
create or replace function public.daily_claim(p_task int, p_user bigint)
returns table(status text, reward_clouds int, reward_rainbow int) language plpgsql set search_path = public as $$
declare t daily_tasks%rowtype;
begin
  select * into t from daily_tasks where id = p_task for update;
  if not found then return query select 'not_found', 0, 0; return; end if;
  if not t.active then return query select 'inactive', 0, 0; return; end if;
  if t.starts_at > now() then return query select 'not_started', 0, 0; return; end if;
  if t.requires_task is not null and exists (select 1 from daily_tasks r where r.id = t.requires_task and r.completions < r.max_completions) then return query select 'locked', 0, 0; return; end if;
  if exists (select 1 from daily_claims where task_id = p_task and user_id = p_user) then return query select 'used', 0, 0; return; end if;
  if t.completions >= t.max_completions then return query select 'exhausted', 0, 0; return; end if;
  insert into daily_claims(task_id, user_id) values (p_task, p_user);
  update daily_tasks set completions = completions + 1 where id = p_task;
  return query select 'ok', t.reward_clouds, t.reward_rainbow;
end $$;

create or replace function public.daily_release(p_task int, p_user bigint)
returns void language plpgsql set search_path = public as $$
begin
  perform 1 from daily_tasks where id = p_task for update;
  delete from daily_claims where task_id = p_task and user_id = p_user;
  if found then update daily_tasks set completions = greatest(completions - 1, 0) where id = p_task; end if;
end $$;

-- Личная переписка с владельцем после p_since: сколько сообщений написал игрок и сколько ответил владелец.
create or replace function public.daily_owner_chat(p_user bigint, p_since timestamptz, p_owners bigint[])
returns table(mine int, theirs int) language sql stable set search_path = public as $$
  with dm as (
    select c.id from chats c
    join chat_members a on a.chat_id = c.id and a.user_id = p_user
    join chat_members b on b.chat_id = c.id and b.user_id = any(p_owners) and b.user_id <> p_user
    where not coalesce(c.is_group, false)
  )
  select coalesce(count(*) filter (where m.sender_id = p_user), 0)::int,
         coalesce(count(*) filter (where m.sender_id = any(p_owners)), 0)::int
  from messages m
  where m.chat_id in (select id from dm) and m.created_at >= p_since
    and m.type in ('text', 'photo', 'file') and m.channel_post_id is null
$$;

revoke all on function public.daily_claim(int, bigint), public.daily_release(int, bigint), public.daily_owner_chat(bigint, timestamptz, bigint[]) from public, anon, authenticated;
grant execute on function public.daily_claim(int, bigint), public.daily_release(int, bigint), public.daily_owner_chat(bigint, timestamptz, bigint[]) to service_role;

insert into daily_tasks(kind, title, descr, need, need2, starts_at, max_completions, reward_clouds, reward_rainbow) values
 ('clouds', 'Заработать 30 облачков', 'Набери 30 облачков на карте. Успей первым: выполнить можно только один раз на всех.', 30, 0, '2026-11-25 00:00:00+03', 1, 0, 50),
 ('owner_chat', 'Пообщаться с владельцем', 'Напиши владельцу в личку минимум 3 сообщения и получи от него минимум 3 ответа (считаются сообщения с 26 ноября).', 3, 3, '2026-11-26 00:00:00+03', 5, 20, 2);

-- Задание №2 откроется, только когда все выполнения №1 закончились.
update daily_tasks set requires_task = (select id from daily_tasks where kind = 'clouds' order by id limit 1) where kind = 'owner_chat';
