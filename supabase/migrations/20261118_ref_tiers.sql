-- Реферальные коды: тип (партнёр 5% / креатор 10%), кто выдал; авто-коды пользователей отключаются.
alter table public.ref_codes add column if not exists tier text not null default 'partner';
alter table public.ref_codes add column if not exists created_by bigint;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'ref_codes_tier_check') then
    alter table public.ref_codes add constraint ref_codes_tier_check check (tier in ('partner', 'creator'));
  end if;
end $$;
-- Личные коды, созданные автоматически раньше, больше не действуют: коды теперь только от персонала.
update public.ref_codes set active = false where kind = 'personal';
