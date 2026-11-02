-- Реакции на сообщения чатов: одна реакция (эмодзи) от пользователя на сообщение, повторный выбор заменяет её.
create table if not exists public.message_reactions (
  message_id bigint not null references public.messages(id) on delete cascade,
  chat_id bigint not null,
  user_id bigint not null,
  emoji text not null check (char_length(emoji) between 1 and 16),
  created_at timestamptz default now(),
  primary key (message_id, user_id)
);
create index if not exists message_reactions_chat_idx on public.message_reactions (chat_id, message_id);
alter table public.message_reactions enable row level security;
revoke all on public.message_reactions from anon, authenticated;
grant all on public.message_reactions to service_role;
