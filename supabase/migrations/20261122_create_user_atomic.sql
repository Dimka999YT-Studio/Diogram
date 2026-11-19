-- Создание аккаунта одним вызовом: проверка почты/ника и вставка под блокировкой.
-- Раньше сервер сначала проверял почту, потом делал insert; при гонке (двойное нажатие, два запроса)
-- insert падал на уникальном индексе, а счётчик users_id_seq всё равно двигался: в ID появлялись дырки.
-- Здесь занятая почта или ник отсекаются ДО nextval, поэтому номер не сгорает.
-- Возвращает jsonb: строка пользователя, либо {"error":"email_taken"} / {"error":"username_taken"}.

create or replace function public.create_user_atomic(
  p_email text,
  p_password_hash text,
  p_username text default null,
  p_first_name text default null,
  p_last_name text default null
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_email text := lower(trim(p_email));
  v_username text := nullif(trim(coalesce(p_username, '')), '');
  v_row public.users;
begin
  if v_email is null or v_email = '' then
    return jsonb_build_object('error', 'email_required');
  end if;
  -- Один запрос на почту/ник за раз: вторая параллельная регистрация подождёт и увидит уже созданную строку.
  perform pg_advisory_xact_lock(hashtext('create_user:' || v_email));
  if v_username is not null then
    perform pg_advisory_xact_lock(hashtext('create_user_name:' || lower(v_username)));
  end if;

  if exists (select 1 from public.users where lower(email) = v_email) then
    return jsonb_build_object('error', 'email_taken');
  end if;
  if v_username is not null and exists (select 1 from public.users where lower(username) = lower(v_username)) then
    return jsonb_build_object('error', 'username_taken');
  end if;

  insert into public.users (email, password_hash, username, first_name, last_name, role, verified,
                            profile_completed, banned, balance, premium, file_plus, last_seen)
  values (v_email, p_password_hash, v_username, p_first_name, p_last_name, 'user', false,
          false, false, 0, false, false, now())
  returning * into v_row;
  return to_jsonb(v_row);
end;
$$;

revoke all on function public.create_user_atomic(text, text, text, text, text) from public, anon, authenticated;
grant execute on function public.create_user_atomic(text, text, text, text, text) to service_role;
