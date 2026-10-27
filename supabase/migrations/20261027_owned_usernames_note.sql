-- Пометка у выданного вручную юзернейма (кем выдан). Показывается в окне информации об имени.
alter table public.owned_usernames add column if not exists note text;
