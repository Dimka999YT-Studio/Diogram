-- Diogram: правки SQL-функций Frigdmant/баланса и закрытие прямого доступа к БД.
-- Откат: 20261026_frigdmant_and_security.rollback.sql
-- Сервер ходит в Supabase под service_role, поэтому RLS и REVOKE для anon/authenticated его не ломают.

-- ===== 1. Функции =====
create or replace function public.add_balance_atomic(p_user_id bigint, p_amount bigint)
returns table(success boolean) language plpgsql set search_path = public as $$
begin
  if p_amount is null or p_amount <= 0 then return query select false; return; end if;
  update users set balance = balance + p_amount where id = p_user_id;
  return query select found;
end;
$$;

create or replace function public.spend_balance_atomic(p_user_id bigint, p_amount bigint)
returns table(success boolean) language plpgsql set search_path = public as $$
begin
  if p_amount is null or p_amount <= 0 then return query select false; return; end if;
  update users set balance = balance - p_amount where id = p_user_id and balance >= p_amount;
  return query select found;
end;
$$;

create or replace function public.buy_username_atomic(p_listing_id bigint, p_buyer_id bigint)
returns table(success boolean, error_msg text) language plpgsql set search_path = public as $$
declare
  v_listing frigdmant_listings%rowtype;
  v_buyer_balance bigint;
  v_owner_id bigint;
  v_commission bigint;
  v_extra bigint := 0;
  v_seller_gets bigint;
  v_seller_active_username text;
begin
  select * into v_listing from frigdmant_listings where id = p_listing_id and status = 'active' for update;
  if not found then return query select false, 'Лот не найден'; return; end if;
  if v_listing.listing_type is distinct from 'buyout' then return query select false, 'Это аукцион: делайте ставку'; return; end if;
  if v_listing.seller_id = p_buyer_id then return query select false, 'Нельзя купить свой лот'; return; end if;

  select balance into v_buyer_balance from users where id = p_buyer_id for update;
  if not found then return query select false, 'Покупатель не найден'; return; end if;
  if v_buyer_balance < v_listing.price then return query select false, 'Недостаточно средств'; return; end if;

  update users set balance = balance - v_listing.price where id = p_buyer_id;
  v_commission := (v_listing.price * 17) / 100;
  v_seller_gets := v_listing.price - v_commission;
  if v_listing.price > 5200 then
    v_extra := (v_listing.price * 3) / 100;
    v_seller_gets := v_seller_gets - v_extra;
    select id into v_owner_id from users where role = 'owner' order by id limit 1;
    if v_owner_id is not null then update users set balance = balance + v_extra where id = v_owner_id; end if;
  end if;

  update users set balance = balance + v_seller_gets where id = v_listing.seller_id;
  delete from owned_usernames where lower(username) = lower(v_listing.username);
  select username into v_seller_active_username from users where id = v_listing.seller_id;
  if lower(v_seller_active_username) = lower(v_listing.username) then
    update users set username = 'user_' || v_listing.seller_id where id = v_listing.seller_id;
  end if;
  if v_listing.target_type = 'channel' then
    update channels set username = 'ch_' || v_listing.target_id where id = v_listing.target_id;
  end if;

  insert into owned_usernames (user_id, username, purchase_price) values (p_buyer_id, v_listing.username, v_listing.price);
  update frigdmant_listings set status = 'sold', highest_bidder_id = p_buyer_id where id = p_listing_id;
  return query select true, ''::text;
end;
$$;

create or replace function public.place_bid_atomic(p_listing_id bigint, p_bidder_id bigint, p_amount bigint)
returns table(success boolean, error_msg text) language plpgsql set search_path = public as $$
declare
  v_listing frigdmant_listings%rowtype;
  v_bidder_balance bigint;
begin
  select * into v_listing from frigdmant_listings where id = p_listing_id and status = 'active' for update;
  if not found then return query select false, 'Лот не найден'; return; end if;
  if v_listing.listing_type is distinct from 'auction' then return query select false, 'Это не аукцион'; return; end if;
  if v_listing.end_time is not null and v_listing.end_time <= now() then return query select false, 'Аукцион уже завершён'; return; end if;
  if v_listing.seller_id = p_bidder_id then return query select false, 'Нельзя делать ставки на свой лот'; return; end if;
  if p_amount is null or p_amount < v_listing.price then return query select false, 'Ставка меньше стартовой'; return; end if;
  if p_amount <= coalesce(v_listing.current_bid, 0) then return query select false, 'Ставка должна быть больше текущей'; return; end if;

  select balance into v_bidder_balance from users where id = p_bidder_id for update;
  if not found then return query select false, 'Пользователь не найден'; return; end if;
  if v_bidder_balance < p_amount then return query select false, 'Недостаточно средств'; return; end if;

  update users set balance = balance - p_amount where id = p_bidder_id;
  if v_listing.highest_bidder_id is not null then
    update users set balance = balance + v_listing.current_bid where id = v_listing.highest_bidder_id;
  end if;
  update frigdmant_listings set current_bid = p_amount, highest_bidder_id = p_bidder_id where id = p_listing_id;
  insert into frigdmant_bids (listing_id, bidder_id, amount) values (p_listing_id, p_bidder_id, p_amount);
  return query select true, ''::text;
end;
$$;

create or replace function public.close_auction_atomic(p_listing_id bigint)
returns void language plpgsql set search_path = public as $$
declare
  v_listing frigdmant_listings%rowtype;
  v_owner_id bigint;
  v_commission bigint;
  v_extra bigint := 0;
  v_seller_gets bigint;
  v_seller_active_username text;
begin
  select * into v_listing from frigdmant_listings where id = p_listing_id and status = 'active' for update;
  if not found then return; end if;

  if v_listing.highest_bidder_id is null then
    -- Ставок нет: имя возвращается в коллекцию продавца (раньше пропадало), если это не его активное имя.
    select username into v_seller_active_username from users where id = v_listing.seller_id;
    if v_listing.seller_id is not null
       and lower(coalesce(v_seller_active_username, '')) <> lower(v_listing.username)
       and not exists (select 1 from channels where lower(username) = lower(v_listing.username))
       and not exists (select 1 from users where lower(username) = lower(v_listing.username)) then
      insert into owned_usernames (user_id, username, purchase_price)
      values (v_listing.seller_id, v_listing.username, 0)
      on conflict do nothing;
    end if;
    update frigdmant_listings set status = 'cancelled' where id = p_listing_id;
    return;
  end if;

  v_commission := (v_listing.current_bid * 17) / 100;
  v_seller_gets := v_listing.current_bid - v_commission;
  if v_listing.current_bid > 5200 then
    v_extra := (v_listing.current_bid * 3) / 100;
    v_seller_gets := v_seller_gets - v_extra;
    select id into v_owner_id from users where role = 'owner' order by id limit 1;
    if v_owner_id is not null then update users set balance = balance + v_extra where id = v_owner_id; end if;
  end if;

  update users set balance = balance + v_seller_gets where id = v_listing.seller_id;
  delete from owned_usernames where lower(username) = lower(v_listing.username);
  select username into v_seller_active_username from users where id = v_listing.seller_id;
  if lower(v_seller_active_username) = lower(v_listing.username) then
    update users set username = 'user_' || v_listing.seller_id where id = v_listing.seller_id;
  end if;
  if v_listing.target_type = 'channel' then
    update channels set username = 'ch_' || v_listing.target_id where id = v_listing.target_id;
  end if;

  insert into owned_usernames (user_id, username, purchase_price) values (v_listing.highest_bidder_id, v_listing.username, v_listing.current_bid);
  update frigdmant_listings set status = 'sold' where id = p_listing_id;
end;
$$;

-- ===== 2. Данные: убрать лоты "Owner" забаненного продавца, отдать имя владельцу =====
update frigdmant_listings set status = 'cancelled'
 where id in (4, 5) and status = 'active' and lower(username) = 'owner' and seller_id = 1000009;

insert into owned_usernames (user_id, username, purchase_price)
select 1, 'Owner', 0
 where not exists (select 1 from owned_usernames where lower(username) = 'owner')
   and not exists (select 1 from users where lower(username) = 'owner')
   and not exists (select 1 from channels where lower(username) = 'owner');

-- ===== 3. Один активный лот на имя =====
create unique index if not exists frigdmant_one_active_per_username
  on public.frigdmant_listings (lower(username)) where status = 'active';

-- ===== 4. Закрыть прямой доступ из публичного API (PostgREST) =====
do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', t.tablename);
  end loop;
end $$;

revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from public, anon, authenticated;
grant  all on all tables    in schema public to service_role;
grant  all on all sequences in schema public to service_role;
grant  execute on all functions in schema public to service_role;

alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from public, anon, authenticated;
