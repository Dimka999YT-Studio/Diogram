-- Скорость мессенджера: идемпотентная отправка, индексы, сводки чатов/каналов одним запросом. Только добавления.
alter table public.messages add column if not exists client_id text;
create unique index if not exists messages_client_id_unique on public.messages (chat_id, sender_id, client_id) where client_id is not null;
create index if not exists chat_members_user_idx on public.chat_members (user_id);
create index if not exists chat_members_chat_user_idx on public.chat_members (chat_id, user_id);
create index if not exists messages_unread_idx on public.messages (chat_id, sender_id) where read_at is null;

create or replace function public.chat_summaries(p_user_id bigint, p_chat_ids bigint[])
returns table(chat_id bigint, last_text text, last_type text, last_at timestamptz, unread bigint)
language sql stable set search_path = public as $$
  select c.id, lm.text, lm.type, lm.created_at,
         (select count(*) from messages m where m.chat_id = c.id and m.sender_id <> p_user_id and m.read_at is null)
  from unnest(p_chat_ids) as c(id)
  left join lateral (select text, type, created_at from messages where chat_id = c.id order by created_at desc, id desc limit 1) lm on true
$$;

create or replace function public.channel_summaries(p_ids bigint[])
returns table(channel_id bigint, subs_count bigint, last_text text, last_at timestamptz)
language sql stable set search_path = public as $$
  select c.id, (select count(*) from channel_subscribers s where s.channel_id = c.id), lm.text, lm.created_at
  from unnest(p_ids) as c(id)
  left join lateral (select text, created_at from channel_messages where channel_id = c.id order by created_at desc, id desc limit 1) lm on true
$$;

revoke all on function public.chat_summaries(bigint, bigint[]) from public, anon, authenticated;
revoke all on function public.channel_summaries(bigint[]) from public, anon, authenticated;
grant execute on function public.chat_summaries(bigint, bigint[]) to service_role;
grant execute on function public.channel_summaries(bigint[]) to service_role;
