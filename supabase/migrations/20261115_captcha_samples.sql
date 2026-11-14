-- Капча «нарисуй круг»: сохраняем сам рисунок (штрихи) БЕЗ привязки к аккаунту, IP и email.
-- Нужна для отбора хороших образцов владельцем. Сервер ходит под service_role, RLS включён.
create table if not exists public.captcha_samples (
  id bigserial primary key,
  kind text not null default 'circle',
  trigger text,                        -- login | register | send
  strokes jsonb not null,              -- [[[x,y,t],...],...]; x,y в 0..1000, t в мс от начала
  points int not null,
  duration_ms int not null,
  device text,                         -- touch | mouse
  passed boolean not null,
  score real,
  quality text check (quality in ('good', 'bad')),   -- отметка владельца
  policy_version int not null default 1,
  created_at timestamptz not null default now()
);
create index if not exists captcha_samples_quality on public.captcha_samples (quality, created_at desc);
alter table public.captcha_samples enable row level security;
revoke all on public.captcha_samples from anon, authenticated;
grant all on public.captcha_samples to service_role;
grant usage, select on sequence public.captcha_samples_id_seq to service_role;
