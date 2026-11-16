"""Голда и реферальная программа (@Gaming). SQL: supabase/migrations/20261117_gold_referral.sql.
Голда: обмен 1 Di = 1 голда (gold), реферальный бонус (bonus) тратится на кейсы, вывод в игру заявкой.
Всё, что меняет баланс, делают атомарные SQL-функции; здесь оркестрация, лимиты и сообщения."""
import secrets
from datetime import datetime, timedelta, timezone

from db import supabase, spend_balance, add_balance, get_or_create_chat, save_message

GAMING_BOT_ID = 1000020
BOT_IDS = [1000011, 1000014, GAMING_BOT_ID]       # Дио, @Play, @Gaming: разговоры с ботами не считаются «общением»
MSK = timezone(timedelta(hours=3))
EXCHANGE_DAILY = 1000        # Di в сутки на человека
WITHDRAW_MIN = 100           # минимум голды для вывода
WITHDRAW_UNLOCK_SPENT = 50   # реферальный бонус выводится после траты стольких голд на кейсы
MAX_OPEN_WITHDRAWALS = 3     # активных заявок на вывод у одного человека
REF_REWARD = 10              # каждому: приглашённому и пригласившему
# Процент пригласившему с купленной голды приглашённого: партнёр 5%, креатор на 5 п.п. больше.
# Партнёр: помогает проекту деньгами или приглашает людей. Креатор: создатель контента.
REF_RATES = {"partner": 0.05, "creator": 0.10}
TIER_NAMES = {"partner": "Партнёр", "creator": "Креатор"}
REF_WINDOW_DAYS = 7          # код можно ввести в первые 7 дней после регистрации
REF_MIN_MSGS, REF_MIN_CHATS, REF_MIN_DAYS = 15, 2, 3
REF_DAILY_LIMIT = 10         # засчитанных приглашений в сутки на одного пригласившего
WITHDRAW_STATUS = {"new": "ждёт проверки", "work": "в работе", "given": "выдано", "rejected": "отклонено"}
_ALPHA = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _tell(user_id, text):
    try:
        chat = get_or_create_chat(GAMING_BOT_ID, user_id)
        if chat: save_message(chat["id"], GAMING_BOT_ID, text, "text")
    except Exception as e:
        print(f"[gold notify] {e!r}")


def _owners():
    return [o["id"] for o in (supabase.table("users").select("id").eq("role", "owner").execute().data or [])]


def _rpc(name, args):
    return supabase.rpc(name, args).execute().data


def _row(data):
    return (data[0] if isinstance(data, list) and data else data) or {}


# ---------- кошелёк ----------
def wallet(uid):
    rows = supabase.table("gold_wallets").select("gold,bonus,spent_total").eq("user_id", uid).execute().data
    w = rows[0] if rows else {}
    gold, bonus, spent = int(w.get("gold") or 0), float(w.get("bonus") or 0), int(w.get("spent_total") or 0)
    bonus_free = int(bonus) if spent >= WITHDRAW_UNLOCK_SPENT else 0
    return {"gold": gold, "bonus": round(bonus, 2), "spent_total": spent, "total": gold + int(bonus),
            "withdrawable": gold + bonus_free, "withdraw_min": WITHDRAW_MIN, "unlock_spent": WITHDRAW_UNLOCK_SPENT}


def exchanged_today(uid):
    start = datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    rows = supabase.table("gold_log").select("gold").eq("user_id", uid).eq("kind", "exchange").gte("created_at", start).execute().data or []
    return sum(int(r["gold"]) for r in rows)


def exchange(uid, amount):
    """Di → голда 1:1. Возвращает (ok, ошибка). Если зачисление голды сорвалось, Di возвращаются."""
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 1: return False, "Укажи целое число Di"
    left = EXCHANGE_DAILY - exchanged_today(uid)
    if amount > left: return False, f"Лимит обмена {EXCHANGE_DAILY} Di в день. Сегодня осталось: {max(left, 0)}."
    ok, err = spend_balance(uid, amount, f"Обмен на голду ({amount})")
    if not ok: return False, err
    try:
        _rpc("gold_add", {"p_user": uid, "p_gold": amount, "p_bonus": 0, "p_kind": "exchange", "p_ref": None})
    except Exception as e:
        print(f"[gold] зачисление не удалось ({e!r}), возвращаю Di пользователю {uid}")
        if not add_balance(uid, amount, "Возврат: обмен на голду не выполнен"): print(f"[gold] !!! возврат Di не прошёл: user={uid} amount={amount}")
        return False, "Не удалось выполнить обмен. Di возвращены, попробуй ещё раз."
    return True, None


def spend(uid, amount, ref=None):
    """Списать голду на кейс (бонус первым). Возвращает ((из бонуса, из голды) | None, ошибка)."""
    try:
        r = _row(_rpc("gold_spend", {"p_user": uid, "p_amount": amount, "p_ref": ref}))
    except Exception as e:
        print(f"[gold spend] {e!r}"); return None, "Не удалось списать голду. Попробуй ещё раз."
    if not r.get("ok"): return None, "Не хватает голды. Обменяй Di на голду."
    return (int(r["from_bonus"]), int(r["from_gold"])), None


def refund(uid, parts, ref=None):
    try: _rpc("gold_refund", {"p_user": uid, "p_bonus": parts[0], "p_gold": parts[1], "p_ref": ref}); return True
    except Exception as e: print(f"[gold] !!! возврат голды не прошёл: user={uid} parts={parts} {e!r}"); return False


def grant_promo(uid, amount, code):
    """Голда по промокоду: бонусная (тратится на кейсы, выводится после траты 50 голды, как реферальная)."""
    _rpc("gold_add", {"p_user": uid, "p_gold": 0, "p_bonus": int(amount), "p_kind": "promo", "p_ref": code})


def invite_rate(uid):
    """Процент для приглашённого uid по типу его кода (партнёр/креатор). 0, если приглашения нет."""
    use = supabase.table("ref_uses").select("code_id").eq("invitee_id", uid).eq("status", "paid").limit(1).execute().data
    if not use: return 0.0
    code = supabase.table("ref_codes").select("tier").eq("id", use[0]["code_id"]).limit(1).execute().data
    return REF_RATES.get((code[0] if code else {}).get("tier"), REF_RATES["partner"])


def commission(uid, gold_part):
    """Процент пригласившему с купленной голды (партнёр 5%, креатор 10%), если приглашение засчитано.
    Сбой не должен ломать открытие кейса."""
    if gold_part <= 0: return
    try:
        rate = invite_rate(uid)
        if rate > 0: _rpc("gold_commission", {"p_user": uid, "p_gold": gold_part, "p_rate": rate})
    except Exception as e: print(f"[gold commission] {e!r}")


# ---------- вывод в игру ----------
def request_withdraw(user, amount, nick):
    if not isinstance(amount, int) or isinstance(amount, bool): return None, "Укажи количество голды"
    nick = (nick or "").strip() if isinstance(nick, str) else ""
    if not 2 <= len(nick) <= 60: return None, "Укажи свой ник или ID в игре (2–60 символов)"
    if amount < WITHDRAW_MIN: return None, f"Минимум для вывода {WITHDRAW_MIN} голды"
    open_n = len(supabase.table("gold_withdrawals").select("id").eq("user_id", user["id"]).in_("status", ["new", "work"]).execute().data or [])
    if open_n >= MAX_OPEN_WITHDRAWALS: return None, "Уже есть активные заявки. Дождись, пока владелец их обработает."
    try:
        r = _row(_rpc("gold_withdraw_request", {"p_user": user["id"], "p_amount": amount, "p_nick": nick, "p_min": WITHDRAW_MIN, "p_unlock": WITHDRAW_UNLOCK_SPENT}))
    except Exception as e:
        print(f"[gold withdraw] {e!r}"); return None, "Не удалось создать заявку. Попробуй ещё раз."
    if r.get("status") == "too_small": return None, f"Минимум для вывода {WITHDRAW_MIN} голды"
    if r.get("status") != "ok": return None, f"Не хватает голды для вывода. Реферальный бонус выводится после траты {WITHDRAW_UNLOCK_SPENT} голды на кейсы."
    who = (f"@{user['username']}" if user.get("username") else (user.get("first_name") or "без имени")) + f" (ID {user['id']})"
    for o in _owners():
        _tell(o, f"🪙 Заявка на вывод голды\nИгрок: {who}\nСумма: {amount}\nНик в игре: {nick}\nПроверь и выдай, потом поставь статус в админке (🪙 Голда).")
    return r["id"], None


def my_withdrawals(uid):
    return supabase.table("gold_withdrawals").select("id,amount,game_nick,status,admin_note,created_at").eq("user_id", uid).order("id", desc=True).limit(20).execute().data or []


def admin_withdrawals():
    rows = supabase.table("gold_withdrawals").select("*").in_("status", ["new", "work"]).order("created_at").limit(200).execute().data or []
    return rows


def close_withdrawal(wid, status, note):
    if status not in ("work", "given", "rejected"): return False, "Неверный статус"
    try: r = _row(_rpc("gold_withdraw_close", {"p_id": wid, "p_status": status, "p_note": (note or "")[:500] if isinstance(note, str) else ""}))
    except Exception as e: print(f"[gold close] {e!r}"); return False, "Не удалось изменить заявку"
    if not r.get("ok"): return False, "Заявка не найдена или уже закрыта"
    text = f"🪙 Заявка на вывод {r['amount']} голды: {WITHDRAW_STATUS[status]}."
    if status == "rejected": text += " Голда вернулась на баланс."
    if isinstance(note, str) and note.strip(): text += "\n" + note.strip()[:500]
    _tell(r["user_id"], text)
    return True, None


# ---------- рефералка ----------
def my_codes(uid):
    """Коды, владельцем которых назначен пользователь (их выдаёт персонал; сами не создаются)."""
    rows = supabase.table("ref_codes").select("code,tier,uses,max_uses,active").eq("owner_id", uid).eq("active", True).order("id").limit(20).execute().data or []
    return [{"code": r["code"], "tier": r.get("tier") or "partner", "tier_name": TIER_NAMES.get(r.get("tier") or "partner"),
             "pct": int(REF_RATES.get(r.get("tier") or "partner", 0.05) * 100), "uses": r["uses"], "max_uses": r["max_uses"]} for r in rows]


_ACT_ERR = {"not_found": "Такого кода нет", "inactive": "Этот код отключён", "exhausted": "У этого кода закончились приглашения",
            "own": "Нельзя ввести свой код", "used": "Ты уже вводил реферальный код",
            "too_old": f"Код можно ввести только в первые {REF_WINDOW_DAYS} дней после регистрации", "cycle": "Этот код тебе недоступен"}


def activate(uid, code):
    if not isinstance(code, str) or not 3 <= len(code.strip()) <= 20: return False, "Введи код приглашения"
    try: r = _row(_rpc("ref_activate", {"p_user": uid, "p_code": code.strip(), "p_window_days": REF_WINDOW_DAYS}))
    except Exception as e: print(f"[ref activate] {e!r}"); return False, "Не удалось применить код. Попробуй ещё раз."
    if r.get("status") != "ok": return False, _ACT_ERR.get(r.get("status"), "Не удалось применить код")
    return True, None


def progress(uid, owner_id):
    r = _row(_rpc("ref_progress", {"p_user": uid, "p_owner": owner_id, "p_bots": BOT_IDS}))
    return {"msgs": int(r.get("msgs") or 0), "chats": int(r.get("chats") or 0), "days": int(r.get("days") or 0),
            "need_msgs": REF_MIN_MSGS, "need_chats": REF_MIN_CHATS, "need_days": REF_MIN_DAYS}


def settle(uid):
    """Проверить условия приглашённого и выплатить бонус обоим. Возвращает статус SQL-функции."""
    try: r = _row(_rpc("ref_settle", {"p_invitee": uid, "p_bots": BOT_IDS, "p_min_msgs": REF_MIN_MSGS, "p_min_chats": REF_MIN_CHATS,
                                      "p_min_days": REF_MIN_DAYS, "p_reward": REF_REWARD, "p_daily_limit": REF_DAILY_LIMIT}))
    except Exception as e: print(f"[ref settle] {e!r}"); return "error"
    if r.get("status") == "paid":
        _tell(uid, f"🎁 Бонус за приглашение: +{REF_REWARD} голды. Её можно потратить на кейсы.")
        _tell(r["owner_id"], f"🎁 Твой приглашённый выполнил условия: +{REF_REWARD} голды. Дальше ты получаешь {int(invite_rate(uid) * 100)}% с голды, которую он купит и потратит.")
    return r.get("status", "none")


def can_enter(uid, has_use=None):
    """Можно ли ещё ввести реферальный код: не вводил раньше и с регистрации не больше REF_WINDOW_DAYS дней."""
    if has_use is None: has_use = bool(supabase.table("ref_uses").select("owner_id").eq("invitee_id", uid).limit(1).execute().data)
    if has_use: return False
    u = (supabase.table("users").select("created_at").eq("id", uid).limit(1).execute().data or [None])[0]
    if not u or not u.get("created_at"): return False
    try: created = datetime.fromisoformat(str(u["created_at"]).replace("Z", "+00:00"))
    except ValueError: return False
    if created.tzinfo is None: created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).days < REF_WINDOW_DAYS


def ref_state(uid):
    """Данные для экрана приглашений. Заодно засчитывает приглашения, у которых условия уже выполнены."""
    mine = supabase.table("ref_uses").select("owner_id,status,code_id,created_at").eq("invitee_id", uid).execute().data or []
    my_use = None
    if mine:
        if mine[0]["status"] == "pending": settle(uid); mine = supabase.table("ref_uses").select("owner_id,status,code_id,created_at").eq("invitee_id", uid).execute().data or mine
        u = mine[0]; my_use = {"status": u["status"], "progress": progress(uid, u["owner_id"]) if u["status"] == "pending" else None}
    pend = supabase.table("ref_uses").select("invitee_id").eq("owner_id", uid).eq("status", "pending").limit(20).execute().data or []
    for p in pend: settle(p["invitee_id"])
    invs = supabase.table("ref_uses").select("invitee_id,status,created_at,paid_at").eq("owner_id", uid).order("created_at", desc=True).limit(100).execute().data or []
    names = {}
    if invs:
        for u in supabase.table("users").select("id,username,first_name").in_("id", [i["invitee_id"] for i in invs]).execute().data or []: names[u["id"]] = u
    out = []
    for i in invs:
        u = names.get(i["invitee_id"]) or {}
        out.append({"name": ("@" + u["username"]) if u.get("username") else (u.get("first_name") or "без имени"), "status": i["status"],
                    "progress": progress(i["invitee_id"], uid) if i["status"] == "pending" else None})
    earned = supabase.table("gold_log").select("bonus,kind").eq("user_id", uid).in_("kind", ["ref_invite", "ref_commission"]).execute().data or []
    return {"codes": my_codes(uid), "can_enter": can_enter(uid, bool(mine)), "my_use": my_use, "invitees": out, "earned": round(sum(float(e["bonus"]) for e in earned), 2),
            "rules": {"reward": REF_REWARD, "partner_pct": int(REF_RATES["partner"] * 100), "creator_pct": int(REF_RATES["creator"] * 100), "min_msgs": REF_MIN_MSGS, "min_chats": REF_MIN_CHATS, "min_days": REF_MIN_DAYS,
                      "window_days": REF_WINDOW_DAYS, "daily_limit": REF_DAILY_LIMIT}}


# ---------- админка ----------
def staff_codes(viewer):
    """Коды для списка: owner видит все, developer и moderator только выданные ими."""
    q = supabase.table("ref_codes").select("*").eq("kind", "custom")
    if viewer.get("role") != "owner": q = q.eq("created_by", viewer["id"])
    return q.order("id", desc=True).limit(200).execute().data or []


def create_custom_code(code, owner_id, max_uses, tier, creator_id):
    if not isinstance(code, str) or not (3 <= len(code.strip()) <= 20) or not all(c.isalnum() or c == "_" for c in code.strip()): return None, "Код: 3–20 символов, буквы, цифры, _"
    if tier not in REF_RATES: return None, "Тип кода: партнёр или креатор"
    if not isinstance(owner_id, int) or not supabase.table("users").select("id").eq("id", owner_id).execute().data: return None, "Не нашёл пользователя с таким ID"
    if max_uses is not None and (not isinstance(max_uses, int) or isinstance(max_uses, bool) or max_uses < 1): return None, "Лимит приглашений от 1 или пусто"
    if supabase.table("ref_codes").select("id").ilike("code", code.strip()).execute().data: return None, "Такой код уже есть"
    try: row = supabase.table("ref_codes").insert({"code": code.strip(), "owner_id": owner_id, "kind": "custom", "max_uses": max_uses,
                                                  "tier": tier, "created_by": creator_id}).execute().data[0]
    except Exception as e: print(f"[ref custom] {e!r}"); return None, "Не удалось создать код"
    return row, None


def toggle_code(cid, active, viewer):
    q = supabase.table("ref_codes").update({"active": bool(active)}).eq("id", cid)
    if viewer.get("role") != "owner": q = q.eq("created_by", viewer["id"])   # не владелец трогает только свои коды
    return bool(q.execute().data)


def stats():
    w = supabase.table("gold_wallets").select("gold,bonus").execute().data or []
    return {"gold": sum(int(x["gold"]) for x in w), "bonus": round(sum(float(x["bonus"]) for x in w), 2), "wallets": len(w)}
