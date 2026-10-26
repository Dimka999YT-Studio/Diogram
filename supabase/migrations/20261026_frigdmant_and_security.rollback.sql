-- Откат миграции 20261026_frigdmant_and_security.sql (функции — как были до неё).
-- Данные (лоты 4/5, имя Owner) вручную: update frigdmant_listings set status='active' where id in (4,5); delete from owned_usernames where lower(username)='owner' and user_id=1;

drop index if exists public.frigdmant_one_active_per_username;

do $$
declare t record;
begin
  for t in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I disable row level security', t.tablename);
  end loop;
end $$;
grant all on all tables    in schema public to anon, authenticated;
grant all on all sequences in schema public to anon, authenticated;
grant execute on all functions in schema public to public, anon, authenticated;

create or replace function public.add_balance_atomic(p_user_id bigint, p_amount bigint)
returns table(success boolean) language plpgsql as $$
begin
  update users set balance = balance + p_amount where id = p_user_id;
  return query select true;
end; $$;

create or replace function public.spend_balance_atomic(p_user_id bigint, p_amount bigint)
returns table(success boolean) language plpgsql as $$
begin
  update users set balance = balance - p_amount where id = p_user_id and balance >= p_amount;
  if found then return query select true; else return query select false; end if;
end; $$;

create or replace function public.buy_username_atomic(p_listing_id bigint, p_buyer_id bigint)
returns table(success boolean, error_msg text) language plpgsql as $$
declare
  v_listing frigdmant_listings%rowtype; v_buyer_balance bigint; v_owner_id bigint;
  v_commission bigint; v_extra_commission bigint; v_seller_gets bigint; v_seller_active_username text;
begin
  select * into v_listing from frigdmant_listings where id = p_listing_id and status = 'active' for update;
  if not found then return query select false, 'Лот не найден'; return; end if;
  select balance into v_buyer_balance from users where id = p_buyer_id for update;
  if v_buyer_balance < v_listing.price then return query select false, 'Недостаточно средств'; return; end if;
  update users set balance = balance - v_listing.price where id = p_buyer_id;
  v_commission := (v_listing.price * 17) / 100;
  v_seller_gets := v_listing.price - v_commission;
  if v_listing.price > 5200 then
    v_extra_commission := (v_listing.price * 3) / 100;
    v_seller_gets := v_seller_gets - v_extra_commission;
    select id into v_owner_id from users where role = 'owner' limit 1;
    if v_owner_id is not null then update users set balance = balance + v_extra_commission where id = v_owner_id; end if;
  end if;
  update users set balance = balance + v_seller_gets where id = v_listing.seller_id;
  delete from owned_usernames where username = v_listing.username;
  select username into v_seller_active_username from users where id = v_listing.seller_id;
  if v_seller_active_username = v_listing.username then update users set username = 'user_' || v_listing.seller_id where id = v_listing.seller_id; end if;
  if v_listing.target_type = 'channel' then update channels set username = 'ch_' || v_listing.target_id where id = v_listing.target_id; end if;
  insert into owned_usernames (user_id, username, purchase_price) values (p_buyer_id, v_listing.username, v_listing.price);
  update frigdmant_listings set status = 'sold', highest_bidder_id = p_buyer_id where id = p_listing_id;
  return query select true, '';
end; $$;

create or replace function public.place_bid_atomic(p_listing_id bigint, p_bidder_id bigint, p_amount bigint)
returns table(success boolean, error_msg text) language plpgsql as $$
declare v_listing frigdmant_listings%rowtype; v_bidder_balance bigint;
begin
  select * into v_listing from frigdmant_listings where id = p_listing_id and status = 'active' for update;
  if not found then return query select false, 'Лот не найден'; return; end if;
  if v_listing.listing_type != 'auction' then return query select false, 'Это не аукцион'; return; end if;
  if p_amount < v_listing.price then return query select false, 'Ставка меньше стартовой'; return; end if;
  if p_amount <= v_listing.current_bid then return query select false, 'Ставка должна быть больше текущей'; return; end if;
  select balance into v_bidder_balance from users where id = p_bidder_id for update;
  if v_bidder_balance < p_amount then return query select false, 'Недостаточно средств'; return; end if;
  update users set balance = balance - p_amount where id = p_bidder_id;
  if v_listing.highest_bidder_id is not null then update users set balance = balance + v_listing.current_bid where id = v_listing.highest_bidder_id; end if;
  update frigdmant_listings set current_bid = p_amount, highest_bidder_id = p_bidder_id where id = p_listing_id;
  insert into frigdmant_bids (listing_id, bidder_id, amount) values (p_listing_id, p_bidder_id, p_amount);
  return query select true, '';
end; $$;

create or replace function public.close_auction_atomic(p_listing_id bigint)
returns void language plpgsql as $$
declare
  v_listing frigdmant_listings%rowtype; v_owner_id bigint; v_commission bigint;
  v_extra_commission bigint; v_seller_gets bigint; v_seller_active_username text;
begin
  select * into v_listing from frigdmant_listings where id = p_listing_id and status = 'active' for update;
  if not found then return; end if;
  if v_listing.highest_bidder_id is null then update frigdmant_listings set status = 'cancelled' where id = p_listing_id; return; end if;
  v_commission := (v_listing.current_bid * 17) / 100;
  v_seller_gets := v_listing.current_bid - v_commission;
  if v_listing.current_bid > 5200 then
    v_extra_commission := (v_listing.current_bid * 3) / 100;
    v_seller_gets := v_seller_gets - v_extra_commission;
    select id into v_owner_id from users where role = 'owner' limit 1;
    if v_owner_id is not null then update users set balance = balance + v_extra_commission where id = v_owner_id; end if;
  end if;
  update users set balance = balance + v_seller_gets where id = v_listing.seller_id;
  delete from owned_usernames where username = v_listing.username;
  select username into v_seller_active_username from users where id = v_listing.seller_id;
  if v_seller_active_username = v_listing.username then update users set username = 'user_' || v_listing.seller_id where id = v_listing.seller_id; end if;
  if v_listing.target_type = 'channel' then update channels set username = 'ch_' || v_listing.target_id where id = v_listing.target_id; end if;
  insert into owned_usernames (user_id, username, purchase_price) values (v_listing.highest_bidder_id, v_listing.username, v_listing.current_bid);
  update frigdmant_listings set status = 'sold' where id = p_listing_id;
end; $$;
