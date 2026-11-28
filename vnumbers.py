"""Виртуальные номера Diogram: +999 XXXX XXXX (8 цифр). SQL: supabase/migrations/20261130_vnumbers.sql.
Номер принадлежит аккаунту и годится для входа (вместо почты, пароль тот же). Покупка за Di или выдача владельцем.
Редкость считается по рисунку цифр, цена зависит от неё (константы ниже). Резерв владельца (source=reserve) купить нельзя."""
import re
import secrets

from db import supabase, spend_balance, add_balance

PREFIX = "+999"
MAX_PER_USER = 3          # номеров на аккаунт (резерв не считается)
RANDOM_PRICE = 20         # случайный обычный номер, Di
CHOOSE_PRICE = 50         # выбрать обычный номер самому, Di
TIER_PRICE = {"rare": 150, "epic": 500, "legend": 2000}   # красивые номера только на выбор
TIER_NAMES = {"common": "Обычный", "rare": "Редкий", "epic": "Эпический", "legend": "Легендарный"}
_RE = re.compile(r"^[0-9]{8}$")


def fmt(n): return f"{PREFIX} {n[:4]} {n[4:]}"


def normalize(raw):
    """Строка (с +999, пробелами, дефисами) → 8 цифр или None."""
    if not isinstance(raw, str): return None
    d = re.sub(r"\D", "", raw)
    if len(d) == 11 and d.startswith("999"): d = d[3:]
    return d if _RE.match(d) else None


def _longest_run(n):
    best = cur = 1
    for i in range(1, len(n)):
        cur = cur + 1 if n[i] == n[i - 1] else 1
        best = max(best, cur)
    return best


def _has_seq(n, k=4):
    for i in range(len(n) - k + 1):
        s = n[i:i + k]
        if all(int(s[j + 1]) - int(s[j]) == 1 for j in range(k - 1)) or all(int(s[j]) - int(s[j + 1]) == 1 for j in range(k - 1)): return True
    return False


def tier(n):
    """common | rare | epic | legend по рисунку цифр."""
    if len(set(n)) == 1 or n.startswith("0000000"): return "legend"
    if _longest_run(n) >= 5 or n[:4] == n[4:] or n == n[::-1] or n.startswith("000000"): return "epic"
    if _longest_run(n) >= 4 or "999" in n or _has_seq(n, 4) or n.startswith("00000") or n[:2] == n[2:4] == n[4:6] == n[6:]: return "rare"
    return "common"


def price(n):
    t = tier(n)
    return CHOOSE_PRICE if t == "common" else TIER_PRICE[t]


def _rows(q): return q.execute().data or []


def mine(uid):
    rows = _rows(supabase.table("vnumbers").select("number,is_main,source,price,created_at").eq("user_id", uid).neq("source", "reserve").order("created_at"))
    return [{"number": r["number"], "text": fmt(r["number"]), "is_main": bool(r["is_main"]), "tier": tier(r["number"]), "tier_name": TIER_NAMES[tier(r["number"])], "source": r["source"]} for r in rows]


def main_text(uid):
    try:
        rows = _rows(supabase.table("vnumbers").select("number").eq("user_id", uid).eq("is_main", True).limit(1))
        return fmt(rows[0]["number"]) if rows else None
    except Exception as e:
        print(f"[vnumbers] main {e!r}"); return None


def holder(number):
    rows = _rows(supabase.table("vnumbers").select("user_id,source").eq("number", number).limit(1))
    return rows[0] if rows else None


def user_id_by_number(number):
    """Для входа: id владельца номера (резерв владельца для входа не годится)."""
    h = holder(number)
    return h["user_id"] if h and h["source"] != "reserve" else None


def info(uid):
    return {"numbers": mine(uid), "max": MAX_PER_USER, "random_price": RANDOM_PRICE, "choose_price": CHOOSE_PRICE,
            "tier_prices": TIER_PRICE, "prefix": PREFIX}


def _take(uid, number, source, cost):
    r = supabase.rpc("vnumber_take", {"p_user": uid, "p_number": number, "p_source": source, "p_price": cost, "p_max": MAX_PER_USER}).execute().data
    return r[0] if isinstance(r, list) and r else r


def _random_free():
    for _ in range(60):
        n = "".join(secrets.choice("0123456789") for _ in range(8))
        if tier(n) == "common" and not holder(n): return n
    return None


def buy(uid, raw):
    """raw пусто → случайный обычный номер. Возвращает (номер | None, ошибка)."""
    if raw in (None, ""):
        number, cost = _random_free(), RANDOM_PRICE
        if not number: return None, "Не удалось подобрать номер, попробуй ещё раз."
    else:
        number = normalize(raw)
        if not number: return None, "Номер: 8 цифр после +999"
        cost = price(number)
    if holder(number): return None, "Этот номер занят"
    if len(mine(uid)) >= MAX_PER_USER: return None, f"Можно иметь не больше {MAX_PER_USER} номеров"
    ok, err = spend_balance(uid, cost, f"Номер {fmt(number)}")
    if not ok: return None, err or "Не хватает Di"
    try:
        res = _take(uid, number, "buy", cost)
    except Exception as e:
        print(f"[vnumbers] выдача не удалась ({e!r}), возвращаю Di пользователю {uid}")
        if not add_balance(uid, cost, "Возврат: номер не выдан"): print(f"[vnumbers] !!! возврат Di не прошёл: user={uid} amount={cost}")
        return None, "Не удалось выдать номер. Di возвращены."
    if res != "ok":
        if not add_balance(uid, cost, "Возврат: номер не выдан"): print(f"[vnumbers] !!! возврат Di не прошёл: user={uid} amount={cost}")
        return None, "Этот номер уже занят" if res == "taken" else f"Можно иметь не больше {MAX_PER_USER} номеров"
    return number, None


def set_main(uid, raw):
    number = normalize(raw)
    if not number: return False, "Номер: 8 цифр"
    h = holder(number)
    if not h or h["user_id"] != uid or h["source"] == "reserve": return False, "Это не твой номер"
    supabase.table("vnumbers").update({"is_main": False}).eq("user_id", uid).eq("is_main", True).execute()
    supabase.table("vnumbers").update({"is_main": True}).eq("number", number).eq("user_id", uid).execute()
    return True, None


def give(uid, raw):
    """Выдача владельцем бесплатно. raw пусто → случайный обычный. Возвращает (номер | None, ошибка)."""
    number = normalize(raw) if raw else _random_free()
    if not number: return None, "Номер: 8 цифр"
    res = _take(uid, number, "gift", 0)
    if res == "taken": return None, "Номер занят"
    if res != "ok": return None, f"У человека уже {MAX_PER_USER} номера"
    return number, None


def reserve(raw):
    """Резерв владельца: номер нельзя купить. Возвращает (ok, ошибка)."""
    number = normalize(raw)
    if not number: return False, "Номер: 8 цифр"
    res = _take(1, number, "reserve", 0)
    return (res == "ok"), (None if res == "ok" else "Номер занят")


def release(raw):
    number = normalize(raw)
    if not number: return False
    supabase.table("vnumbers").delete().eq("number", number).execute()
    return True


def admin_list(limit=200):
    rows = _rows(supabase.table("vnumbers").select("number,user_id,source,price,created_at,is_main").order("created_at", desc=True).limit(limit))
    return [{**r, "text": fmt(r["number"]), "tier": tier(r["number"])} for r in rows]
