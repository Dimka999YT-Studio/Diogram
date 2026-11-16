-- Откат: тип кода и «кто выдал». Отключённые личные коды вручную не возвращаем.
alter table public.ref_codes drop constraint if exists ref_codes_tier_check;
alter table public.ref_codes drop column if exists created_by;
alter table public.ref_codes drop column if exists tier;
