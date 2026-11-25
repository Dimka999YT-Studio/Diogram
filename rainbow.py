"""Радужные круги: валюта чата в играх (1 круг за сообщение). Покупаются за Di в магазине кругов. Первый одиночный круг
бесплатный (один раз на аккаунт), дальше 0,99 Di за штуку; пачки дешевле. Сотрудники пишут бесплатно. Таблицы и
SQL-функции: миграция 20261126_rainbow_circles.sql (покупка и списание атомарны)."""
from db import supabase

UNIT_PRICE = 0.99
# key: (количество, цена в Di, метка)
PACKS = {
    "one":   (1, UNIT_PRICE, None),
    "p50":   (50, 40.0, "hit"),
    "p100":  (100, 72.0, "save"),
    "p1000": (1000, 700.0, "mega"),
}
CHAT_COST = 1


def discount(qty, price):
    """Скидка в процентах относительно покупки поштучно (0,99 Di за штуку)."""
    full = UNIT_PRICE * qty
    return max(0, round((1 - price / full) * 100)) if qty > 1 else 0


def wallet(user_id):
    r = supabase.table("rainbow_wallets").select("circles,free_used").eq("user_id", user_id).limit(1).execute().data
    return {"circles": int(r[0]["circles"]), "free_used": bool(r[0]["free_used"])} if r else {"circles": 0, "free_used": False}


def public_packs(free_used):
    out = []
    for key, (qty, price, tag) in PACKS.items():
        free = key == "one" and not free_used
        out.append({"key": key, "qty": qty, "price": 0.0 if free else price, "free": free, "tag": tag, "discount": discount(qty, price)})
    return out


def buy(buyer_id, target_id, key):
    """Покупка для себя или друга (target_id). Бесплатный одиночный круг принадлежит покупателю: его можно подарить.
    Возвращает (ok, error, кошелёк покупателя, баланс Di покупателя)."""
    if key not in PACKS: return False, "Такого набора нет", None, None
    qty, price, _ = PACKS[key]
    w = wallet(buyer_id)
    free = key == "one" and not w["free_used"]
    r = supabase.rpc("rainbow_buy", {"p_buyer": buyer_id, "p_target": target_id, "p_qty": qty, "p_price": 0 if free else price, "p_free": free}).execute().data
    row = r[0] if r else None
    if not row: return False, "Не получилось купить", None, None
    if not row["ok"]:
        if row["err"] == "no_money": return False, "Не хватает Di", None, float(row["balance"] or 0)
        if row["err"] == "free_used": return False, "Бесплатный круг уже получен", None, None
        return False, "Не получилось купить", None, None
    return True, None, {"circles": int(row["buyer_circles"]), "free_used": w["free_used"] or free}, float(row["balance"] or 0)


def spend(user_id, qty=CHAT_COST):
    """Остаток кругов после списания или None, если не хватило. Сбой базы не блокирует чат (возвращаем -1)."""
    try:
        left = supabase.rpc("rainbow_spend", {"p_user": user_id, "p_qty": qty}).execute().data
    except Exception as e:
        print(f"[rainbow] списание не удалось, чат пропускаем: {e!r}"); return -1
    left = left if isinstance(left, int) else -1
    return left if left >= 0 else None


def refund(user_id, qty=CHAT_COST):
    try: supabase.rpc("rainbow_refund", {"p_user": user_id, "p_qty": qty}).execute()
    except Exception as e: print(f"[rainbow] возврат не удался: {e!r}")


def grant(user_id, qty, kind="promo"):
    """Начислить круги бесплатно (промокод). Бросает исключение при сбое, чтобы вызывающий откатил выдачу."""
    left = supabase.rpc("rainbow_grant", {"p_user": user_id, "p_qty": int(qty), "p_kind": kind}).execute().data
    if not isinstance(left, int) or left < 0: raise RuntimeError("rainbow_grant")
    return left
