-- Количество экземпляров предмета в кейсе: пусто = без ограничения, 0 = разыгран (в кейсе его больше нет).
alter table public.gcase_items add column if not exists stock integer;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'gcase_items_stock_check') then
    alter table public.gcase_items add constraint gcase_items_stock_check check (stock is null or stock >= 0);
  end if;
end $$;
