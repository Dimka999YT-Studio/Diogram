-- Кейсы @Gaming с датой открытия: до неё кейс виден с плашкой, но не открывается. Пусто = доступен сразу.
alter table public.gcase_cases add column if not exists opens_at timestamptz;

-- Зимний (с 1 декабря 2026) и Новогодний (с 10 декабря 2026), 00:00 по Москве. Цены временные, правятся в админке.
insert into public.gcase_cases (title, price, active, sort_no, opens_at)
select 'Зимний кейс', 40, true, 999998, '2026-12-01 00:00:00+03'
where not exists (select 1 from public.gcase_cases where title = 'Зимний кейс');
insert into public.gcase_cases (title, price, active, sort_no, opens_at)
select 'Новогодний кейс', 60, true, 999999, '2026-12-10 00:00:00+03'
where not exists (select 1 from public.gcase_cases where title = 'Новогодний кейс');
