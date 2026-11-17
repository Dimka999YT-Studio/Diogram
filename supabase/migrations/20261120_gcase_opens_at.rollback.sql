-- Откат: убирает кейсы «Зимний» и «Новогодний» (вместе с их предметами) и колонку даты.
delete from public.gcase_cases where title in ('Зимний кейс', 'Новогодний кейс');
alter table public.gcase_cases drop column if exists opens_at;
