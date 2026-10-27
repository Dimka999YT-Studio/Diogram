-- Комментарии к постам канала (через привязанную группу) и опросы в каналах. Только добавления, обратно совместимо.
alter table public.channels add column if not exists linked_chat_id bigint;
create unique index if not exists channels_linked_chat_unique on public.channels (linked_chat_id) where linked_chat_id is not null;

alter table public.messages add column if not exists reply_to_message_id bigint;
alter table public.messages add column if not exists channel_post_id bigint;
create index if not exists messages_reply_to_idx on public.messages (reply_to_message_id) where reply_to_message_id is not null;

alter table public.channel_messages add column if not exists poll jsonb;
alter table public.channel_messages add column if not exists discussion_message_id bigint;

create table if not exists public.channel_poll_votes (
  id bigserial primary key,
  message_id bigint not null references public.channel_messages(id) on delete cascade,
  user_id bigint not null,
  option_index int not null check (option_index >= 0 and option_index < 10),
  created_at timestamptz default now(),
  unique (message_id, user_id, option_index)
);
create index if not exists channel_poll_votes_msg_idx on public.channel_poll_votes (message_id);
alter table public.channel_poll_votes enable row level security;
revoke all on public.channel_poll_votes from anon, authenticated;
revoke all on sequence public.channel_poll_votes_id_seq from anon, authenticated;
grant all on public.channel_poll_votes to service_role;
grant all on sequence public.channel_poll_votes_id_seq to service_role;
