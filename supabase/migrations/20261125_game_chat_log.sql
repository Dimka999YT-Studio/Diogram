-- Журнал чата лобби в прятках: нужен владельцу для модерации. Хранится 30 дней (чистит сервер), читает только owner.
create table if not exists public.game_chat_log (
  id bigserial primary key,
  created_at timestamptz not null default now(),
  season text not null,
  lobby_no int not null,
  user_id bigint not null,
  name text not null,
  role text not null,            -- seeker | hider
  text text not null
);
create index if not exists game_chat_log_created on public.game_chat_log (created_at desc);
alter table public.game_chat_log enable row level security;
revoke all on public.game_chat_log from anon, authenticated;
grant all on public.game_chat_log to service_role;
grant usage, select on sequence public.game_chat_log_id_seq to service_role;
