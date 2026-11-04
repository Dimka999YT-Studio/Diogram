"""Сезонный турнир: облачки, кейсы, призы. Вся экономика считается на сервере.

Таблицы и SQL-функции: supabase/migrations/20261103_game_tournament.sql.
"""
import secrets
from datetime import datetime, timedelta, timezone

from db import supabase, spend_balance, add_balance

MSK = timezone(timedelta(hours=3))
SEASON = "autumn_2026"
SERIES = "autumn_2026"
DAILY_GAMES = 5

# Кейсы: шансы в процентах, у каждого кейса сумма ровно 100 (проверяется при импорте).
# kind: games (+N бонусных игр), clouds, di, username (следующее по порядку из пула), account (случайная секретка).
CASES = {
    "common": {"title": "Обычный кейс", "price": 1, "drops": [
        (90, "games", 1, "Ещё 1 игра"),
        (10, "clouds", 5, "5 облачков"),
    ]},
    "rare": {"title": "Редкий кейс", "price": 10, "drops": [
        (10, "di", 1, "1 Di"),
        (10, "clouds", 1, "1 облачко"),
        (5, "clouds", 5, "5 облачков"),
        (5, "clouds", 10, "10 облачков"),
        (30, "di", 5, "5 Di"),
        (30, "di", 10, "10 Di"),
        (10, "clouds", 15, "15 облачков"),
    ]},
    "legendary": {"title": "Легендарный кейс", "price": 25, "drops": [
        (2, "clouds", 100, "100 облачков"),
        (5, "clouds", 50, "50 облачков"),
        (5, "clouds", 25, "25 облачков"),
        (50, "clouds", 5, "5 облачков"),
        (20, "di", 10, "10 Di"),
        (10, "di", 20, "20 Di"),
        (5, "di", 25, "25 Di"),
        (3, "di", 30, "30 Di"),
    ]},
    "autumn": {"title": "Осенька кейс", "price": 50, "drops": [
        (1, "clouds", 500, "500 облачков"),
        (4, "clouds", 100, "100 облачков"),
        (10, "clouds", 50, "50 облачков"),
        (10, "clouds", 25, "25 облачков"),
        (5, "clouds", 5, "5 облачков"),
        (20, "di", 10, "10 Di"),
        (15, "di", 25, "25 Di"),
        (10, "di", 50, "50 Di"),
        (5, "di", 51, "51 Di"),
        (5, "username", 0, "Коллекционный юзернейм"),
        (15, "account", 0, "Аккаунт-секретка"),
    ]},
}
for _k, _c in CASES.items():
    assert sum(d[0] for d in _c["drops"]) == 100, f"сумма шансов кейса {_k} не равна 100"


def today_msk():
    return datetime.now(MSK).date().isoformat()


def public_cases():
    return [{"key": k, "title": c["title"], "price": c["price"],
             "drops": [{"chance": d[0], "label": d[3], "kind": d[1]} for d in c["drops"]]} for k, c in CASES.items()]


def roll(case_key, rnd=None):
    """Случайный приз кейса по шансам. rnd(n) -> число 0..n-1 (в тестах подменяется)."""
    case = CASES[case_key]
    r = (rnd or secrets.randbelow)(100)
    acc = 0
    for chance, kind, amount, label in case["drops"]:
        acc += chance
        if r < acc: return {"kind": kind, "amount": amount, "label": label}
    raise RuntimeError("шансы кейса не покрывают 100%")


# ---------- состояние игрока ----------
def player_row(user_id):
    res = supabase.table("game_players").select("*").eq("user_id", user_id).eq("season", SEASON).limit(1).execute()
    return res.data[0] if res.data else {"clouds_map": 0, "clouds_case": 0, "bonus_games": 0}


def played_today(user_id):
    res = supabase.table("game_daily").select("played").eq("user_id", user_id).eq("day", today_msk()).limit(1).execute()
    return int(res.data[0]["played"]) if res.data else 0


def my_state(user):
    """Облачки, оставшиеся игры и место в топе. Сотрудники в топе не участвуют (rank=None)."""
    row = player_row(user["id"])
    total = int(row["clouds_map"]) + int(row["clouds_case"])
    left_daily = max(0, DAILY_GAMES - played_today(user["id"]))
    rank = None
    if user.get("role") == "user" and total > 0:
        try:
            r = supabase.rpc("game_rank", {"p_season": SEASON, "p_user": user["id"]}).execute()
            rank = r.data if isinstance(r.data, int) else None
        except Exception as e:
            print(f"[game_rank] {e!r}")
    return {"clouds": total, "clouds_map": int(row["clouds_map"]), "clouds_case": int(row["clouds_case"]),
            "bonus_games": int(row["bonus_games"]), "games_left": left_daily + int(row["bonus_games"]),
            "daily_left": left_daily, "daily_limit": DAILY_GAMES, "rank": rank,
            "in_top": user.get("role") == "user"}


def top(limit=3):
    res = supabase.rpc("game_top", {"p_season": SEASON, "p_limit": limit}).execute()
    return res.data or []


# ---------- призы ----------
def stock(kind):
    """Сколько призов этого вида ещё можно выдать из кейса."""
    if kind == "account":
        res = supabase.table("game_prize_accounts").select("id", count="exact").is_("given_to", "null").execute()
        return res.count or 0
    if kind == "username":
        res = supabase.table("game_prize_usernames").select("username", count="exact").is_("given_to", "null").eq("pool", "case").execute()
        return res.count or 0
    return 10 ** 9


def is_prize_username(username):
    """Личный коллекционный юзернейм турнира (его нельзя продать на Frigdmant)."""
    if not username: return False
    res = supabase.table("game_prize_usernames").select("username").ilike("username", username).limit(20).execute()
    return any((r.get("username") or "").lower() == username.lower() for r in (res.data or []))


def username_series_info(username):
    """Для окна «Юзернейм»: когда получен и какой он по счёту в серии. None, если имя не из турнира."""
    if not username: return None
    res = supabase.table("game_prize_usernames").select("username,series,series_no,given_at").ilike("username", username).limit(20).execute()
    row = next((r for r in (res.data or []) if (r.get("username") or "").lower() == username.lower()), None)
    if not row or not row.get("given_at"): return None
    total = supabase.table("game_prize_usernames").select("username", count="exact").eq("series", row["series"]).execute().count or 0
    given = supabase.table("game_prize_usernames").select("username", count="exact").eq("series", row["series"]).not_.is_("given_to", "null").execute().count or 0
    return {"given_at": row["given_at"], "series_no": row["series_no"], "given": given, "total": total}


def my_prizes(user_id):
    names = supabase.table("game_prize_usernames").select("username,series_no,given_at").eq("given_to", user_id).order("given_at").execute().data or []
    accs = supabase.table("game_prize_accounts").select("email,password,given_at").eq("given_to", user_id).order("given_at").execute().data or []
    return {"usernames": names, "accounts": accs}


def _take_username(user_id, name=None):
    res = supabase.rpc("game_take_username", {"p_user": user_id, "p_name": name}).execute()
    return res.data[0] if res.data else None


def _take_account(user_id):
    res = supabase.rpc("game_take_account", {"p_user": user_id}).execute()
    return res.data[0] if res.data else None


def _log_open(user_id, case_key, price, prize, label):
    try:
        supabase.table("game_case_opens").insert({
            "user_id": user_id, "season": SEASON, "case_key": case_key, "price": price,
            "prize_kind": prize["kind"], "prize_amount": prize["amount"], "prize_label": label}).execute()
    except Exception as e:
        print(f"[game_case_opens] {e!r}")


def open_case(user_id, case_key, rnd=None):
    """Открыть кейс. Возвращает (результат, ошибка). Di не списываются, если приз выдать нечем,
    а если выдача сорвалась уже после списания, стоимость возвращается."""
    case = CASES.get(case_key)
    if not case: return None, "Такого кейса нет"
    prize = roll(case_key, rnd)
    if prize["kind"] in ("username", "account") and stock(prize["kind"]) <= 0:
        return None, "Этот приз сейчас закончился. Di не списаны, попробуй ещё раз."
    ok, err = spend_balance(user_id, case["price"], f"Кейс «{case['title']}»")
    if not ok: return None, err
    label = prize["label"]
    try:
        kind = prize["kind"]
        if kind == "games":
            supabase.rpc("game_add", {"p_user": user_id, "p_season": SEASON, "p_map": 0, "p_case": 0, "p_games": prize["amount"]}).execute()
        elif kind == "clouds":
            supabase.rpc("game_add", {"p_user": user_id, "p_season": SEASON, "p_map": 0, "p_case": prize["amount"], "p_games": 0}).execute()
        elif kind == "di":
            if not add_balance(user_id, prize["amount"], f"Приз кейса «{case['title']}»"): raise RuntimeError("add_balance")
        elif kind == "username":
            got = _take_username(user_id)
            if not got: raise LookupError("нет юзернеймов")
            label = f"@{got['username']} (№{got['series_no']})"
            prize = dict(prize, username=got["username"], series_no=got["series_no"])
        elif kind == "account":
            got = _take_account(user_id)
            if not got: raise LookupError("нет секреток")
            prize = dict(prize, email=got["email"], password=got["password"])
    except Exception as e:
        print(f"[open_case] выдача не удалась ({e!r}), возвращаю {case['price']} Di пользователю {user_id}")
        if not add_balance(user_id, case["price"], f"Возврат: не удалось выдать приз кейса «{case['title']}»"):
            print(f"[open_case] !!! возврат не прошёл: user={user_id} case={case_key} price={case['price']}")
        return None, "Не удалось выдать приз. Di возвращены, попробуй ещё раз."
    _log_open(user_id, case_key, case["price"], prize, label)
    return {"case": case_key, "prize": prize, "label": label}, None


# ---------- админка (только owner) ----------
def admin_overview():
    accs = supabase.table("game_prize_accounts").select("id,email,given_to,given_at").order("id").execute().data or []
    names = supabase.table("game_prize_usernames").select("username,pool,sort_no,series_no,given_to,given_at").order("sort_no").execute().data or []
    return {"accounts": accs, "usernames": names}


def add_accounts(pairs):
    """pairs: [(email, password)]. Возвращает (добавлено, пропущено[{email, reason}])."""
    added, skipped = [], []
    for email, pw in pairs:
        try:
            supabase.table("game_prize_accounts").insert({"email": email, "password": pw}).execute()
            added.append(email)
        except Exception:
            skipped.append({"email": email, "reason": "Уже есть в списке"})
    return added, skipped


def register_prize_username(name, pool):
    supabase.table("game_prize_usernames").insert({"username": name, "pool": pool, "series": SERIES}).execute()


def remove_prize_username(name):
    """Убрать невыданный приз-юзернейм из списка (сам резерв снимается отдельно)."""
    res = supabase.table("game_prize_usernames").delete().ilike("username", name).is_("given_to", "null").execute()
    return bool(res.data)


def remove_account(acc_id):
    res = supabase.table("game_prize_accounts").delete().eq("id", acc_id).is_("given_to", "null").execute()
    return bool(res.data)


def award_username(user_id, name):
    return _take_username(user_id, name)


# ---------- попытки и облачки с карты (вызываются из game_world) ----------
def use_attempt(user_id):
    """Списать одну игру: сначала дневные, потом бонусные. True, если списалась."""
    r = supabase.rpc("game_use_attempt", {"p_user": user_id, "p_season": SEASON, "p_day": today_msk(), "p_limit": DAILY_GAMES}).execute()
    return r.data in ("daily", "bonus")


def games_left(user_id):
    return max(0, DAILY_GAMES - played_today(user_id)) + int(player_row(user_id)["bonus_games"])


def credit_map_clouds(user_id, n):
    supabase.rpc("game_add", {"p_user": user_id, "p_season": SEASON, "p_map": int(n), "p_case": 0, "p_games": 0}).execute()
