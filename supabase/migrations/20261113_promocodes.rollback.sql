drop trigger if exists promo_resale_trg on public.frigdmant_listings;
drop function if exists public.promo_count_resale();
drop function if exists public.promo_release(bigint, bigint);
drop function if exists public.promo_claim(text, bigint);
drop table if exists public.promo_usernames;
drop table if exists public.promo_activations;
drop table if exists public.promo_codes;
