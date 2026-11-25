-- Выдача радужных кругов (промокоды): создаёт кошелёк при необходимости, пишет журнал.
create or replace function public.rainbow_grant(p_user bigint, p_qty int, p_kind text default 'promo')
returns int language plpgsql set search_path = public as $$
declare v int;
begin
  if p_qty is null or p_qty <= 0 then return -1; end if;
  insert into rainbow_wallets(user_id, circles) values (p_user, p_qty)
    on conflict (user_id) do update set circles = rainbow_wallets.circles + p_qty
    returning circles into v;
  insert into rainbow_log(user_id, target_id, delta, kind, price) values (p_user, p_user, p_qty, coalesce(p_kind, 'promo'), 0);
  return v;
end $$;
revoke all on function public.rainbow_grant(bigint, int, text) from public, anon, authenticated;
grant execute on function public.rainbow_grant(bigint, int, text) to service_role;
