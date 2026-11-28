"""Виртуальные номера Diogram: +999 XXXX XXXX (8 цифр). SQL: supabase/migrations/20261130_vnumbers.sql.
Номер принадлежит аккаунту и годится для входа (вместо почты, пароль тот же). Продаются за звёзды Telegram в боте @DiogramRobot
из запаса `vnumber_stock`, который пополняет owner (в боте); случайный номер берётся из запаса. Редкость считается по рисунку цифр,
цена в звёздах зависит от неё (константы ниже). Владелец может выдать номер бесплатно; резерв (source=reserve) купить нельзя."""
import re
import secrets
from datetime import datetime, timezone

from db import supabase

PREFIX = "+999"
MAX_PER_USER = 3          # номеров на аккаунт (резерв не считается)
# Цены в звёздах Telegram: случайный обычный номер из запаса и номера на выбор по редкости.
STARS_RANDOM = 50
STARS = {"common": 100, "rare": 300, "epic": 1000, "legend": 5000}
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


def stars(n): return STARS[tier(n)]


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
    return {"numbers": mine(uid), "max": MAX_PER_USER, "prefix": PREFIX, "stars_random": STARS_RANDOM, "stars": STARS}


def _take(uid, number, source, cost):
    r = supabase.rpc("vnumber_take", {"p_user": uid, "p_number": number, "p_source": source, "p_price": cost, "p_max": MAX_PER_USER}).execute().data
    return r[0] if isinstance(r, list) and r else r


def stock_free(number):
    rows = _rows(supabase.table("vnumber_stock").select("number,tier").eq("number", number).is_("sold_at", "null").limit(1))
    return rows[0] if rows else None


def stock_counts():
    rows = _rows(supabase.table("vnumber_stock").select("tier,sold_at").limit(5000))
    out = {t: 0 for t in TIER_NAMES}
    for r in rows:
        if not r.get("sold_at"): out[r["tier"]] = out.get(r["tier"], 0) + 1
    return out


def stock_add(raws):
    """Список строк → (добавлено, [(строка, причина)])."""
    added, skipped, seen = 0, [], set()
    for raw in raws[:1000]:
        n = normalize(raw)
        if not n: skipped.append((str(raw)[:20], "нужно 8 цифр")); continue
        if n in seen: skipped.append((n, "повтор")); continue
        seen.add(n)
        if holder(n): skipped.append((n, "уже у кого-то или в резерве")); continue
        if _rows(supabase.table("vnumber_stock").select("number").eq("number", n).limit(1)): skipped.append((n, "уже в запасе")); continue
        supabase.table("vnumber_stock").insert({"number": n, "tier": tier(n)}).execute(); added += 1
    return added, skipped


def stock_remove(raw):
    n = normalize(raw)
    if not n: return False
    supabase.table("vnumber_stock").delete().eq("number", n).is_("sold_at", "null").execute()
    return True


def stock_list(limit=60):
    rows = _rows(supabase.table("vnumber_stock").select("number,tier").is_("sold_at", "null").order("created_at", desc=True).limit(limit))
    return [{"number": r["number"], "text": fmt(r["number"]), "tier": r["tier"]} for r in rows]


def quote(raw):
    """Что можно купить: raw пусто = случайный обычный. Возвращает (данные | None, ошибка)."""
    if raw in (None, ""):
        if not stock_counts().get("common"): return None, "Случайные номера закончились"
        return {"number": None, "text": "случайный номер", "tier": "common", "tier_name": TIER_NAMES["common"], "stars": STARS_RANDOM}, None
    n = normalize(raw)
    if not n: return None, "Номер: 8 цифр после +999"
    if not stock_free(n): return None, "Этого номера нет в продаже"
    t = tier(n)
    return {"number": n, "text": fmt(n), "tier": t, "tier_name": TIER_NAMES[t], "stars": STARS[t]}, None


def sell(uid, tg, raw, paid_stars, charge_id):
    """После оплаты звёздами. Возвращает (статус, номер): ok | dup | limit | empty."""
    n = normalize(raw) if raw else None
    r = supabase.rpc("vnumber_sell", {"p_tg": tg, "p_user": uid, "p_number": n, "p_stars": int(paid_stars), "p_charge": charge_id, "p_max": MAX_PER_USER}).execute().data
    r = r[0] if isinstance(r, list) and r else (r or {})
    return r.get("status", "empty"), r.get("number")


def set_main(uid, raw):
    number = normalize(raw)
    if not number: return False, "Номер: 8 цифр"
    h = holder(number)
    if not h or h["user_id"] != uid or h["source"] == "reserve": return False, "Это не твой номер"
    supabase.table("vnumbers").update({"is_main": False}).eq("user_id", uid).eq("is_main", True).execute()
    supabase.table("vnumbers").update({"is_main": True}).eq("number", number).eq("user_id", uid).execute()
    return True, None


def give(uid, raw):
    """Выдача владельцем бесплатно. raw пусто → случайный обычный из запаса. Возвращает (номер | None, ошибка)."""
    if raw:
        number = normalize(raw)
        if not number: return None, "Номер: 8 цифр"
    else:
        rows = _rows(supabase.table("vnumber_stock").select("number").eq("tier", "common").is_("sold_at", "null").limit(200))
        if not rows: return None, "В запасе нет обычных номеров"
        number = secrets.choice(rows)["number"]
    res = _take(uid, number, "gift", 0)
    if res == "taken": return None, "Номер занят"
    if res != "ok": return None, f"У человека уже {MAX_PER_USER} номера"
    try: supabase.table("vnumber_stock").update({"sold_at": datetime.now(timezone.utc).isoformat(), "sold_to": uid}).eq("number", number).is_("sold_at", "null").execute()
    except Exception as e: print(f"[vnumbers] stock mark {e!r}")
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
