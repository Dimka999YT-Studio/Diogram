-- Резерв юзернеймов: имена, которые владелец держит у себя и выдаёт по ID (раздел админки «Резерв имён»).
alter table public.owned_usernames add column if not exists reserved boolean not null default false;
