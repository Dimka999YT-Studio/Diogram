-- Продажа юзернейма публичного канала (Frigdmant, target_type = 'channel').
-- После продажи канал остаётся без юзернейма (username = null; раньше ставился заменитель ch_<id>),
-- покупатель получает имя в коллекцию. Если за время лота канал сменил имя, канал не трогаем.

create or replace function public.buy_username_atomic(p_listing_id bigint, p_buyer_id bigint)
returns table(success boolean, error_msg text)
language plpgsql set search_path to 'public' as $$
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
  if v_listing.target_type = 'channel' then
    update channels set username = null where id = v_listing.target_id and lower(coalesce(username, '')) = lower(v_listing.username);
  elsif lower(v_seller_active_username) = lower(v_listing.username) then
    update users set username = 'user_' || v_listing.seller_id where id = v_listing.seller_id;
  end if;

  insert into owned_usernames (user_id, username, purchase_price) values (p_buyer_id, v_listing.username, v_listing.price);
  update frigdmant_listings set status = 'sold', highest_bidder_id = p_buyer_id where id = p_listing_id;
  return query select true, ''::text;
end;
$$;

create or replace function public.close_auction_atomic(p_listing_id bigint)
returns void
language plpgsql set search_path to 'public' as $$
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
  if v_listing.target_type = 'channel' then
    update channels set username = null where id = v_listing.target_id and lower(coalesce(username, '')) = lower(v_listing.username);
  elsif lower(v_seller_active_username) = lower(v_listing.username) then
    update users set username = 'user_' || v_listing.seller_id where id = v_listing.seller_id;
  end if;

  insert into owned_usernames (user_id, username, purchase_price) values (v_listing.highest_bidder_id, v_listing.username, v_listing.current_bid);
  update frigdmant_listings set status = 'sold' where id = p_listing_id;
end;
$$;

revoke all on function public.buy_username_atomic(bigint, bigint), public.close_auction_atomic(bigint) from public, anon, authenticated;
grant execute on function public.buy_username_atomic(bigint, bigint), public.close_auction_atomic(bigint) to service_role;
