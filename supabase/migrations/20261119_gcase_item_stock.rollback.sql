alter table public.gcase_items drop constraint if exists gcase_items_stock_check;
alter table public.gcase_items drop column if exists stock;
