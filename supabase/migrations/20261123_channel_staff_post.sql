-- Канал, где писать может весь персонал (owner, developer, moderator), а не только владелец канала.
-- Включается флагом по каналу; по умолчанию выключен. Включить: update channels set staff_post = true where id = <id>;
alter table public.channels add column if not exists staff_post boolean not null default false;
