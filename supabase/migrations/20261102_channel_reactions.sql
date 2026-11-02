-- Реакции на посты каналов: одна реакция (эмодзи) от пользователя на пост, повторный выбор заменяет её.
create table if not exists public.channel_message_reactions (
  message_id bigint not null references public.channel_messages(id) on delete cascade,
  user_id bigint not null,
  emoji text not null check (char_length(emoji) between 1 and 16),
  created_at timestamptz default now(),
  primary key (message_id, user_id)
);
alter table public.channel_message_reactions enable row level security;
revoke all on public.channel_message_reactions from anon, authenticated;
grant all on public.channel_message_reactions to service_role;
