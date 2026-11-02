-- Архив чатов: пользователь скрывает свои чаты, группы и каналы из основного списка (у каждого свой архив).
create table if not exists public.user_archive (
  user_id bigint not null,
  kind text not null check (kind in ('chat', 'channel')),   -- chat: личные чаты и группы (chats.id), channel: каналы (channels.id)
  target_id bigint not null,
  created_at timestamptz default now(),
  primary key (user_id, kind, target_id)
);
alter table public.user_archive enable row level security;
revoke all on public.user_archive from anon, authenticated;
grant all on public.user_archive to service_role;
