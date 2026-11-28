"""Telegram-бот @DiogramRobot: привязка к аккаунту Diogram, кристаллы, выдача промокодов из пула.
SQL: supabase/migrations/20261129_tgbot.sql. Бот ходит сюда по ключу TG_BOT_KEY (заголовок X-Bot-Key); tg_id берётся из Telegram.
Кристаллы хранятся в тысячных долях (1000 = 1 кристалл), чтобы 0,999 + 0,001 давало ровно 1."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

import promo
import rainbow
import game
from db import supabase, get_user_by_id, get_user_by_username, get_or_create_chat, save_message, get_user_usernames

SENDER_USERNAME = "Gifts"          # от этого аккаунта приходит код привязки
CLAIM_MILLI = 100                  # 0,100 кристалла за получение
CLAIM_EVERY = 3600                 # раз в час
DOUBLE_EVERY = 14 * 3600           # «удвоить или потерять всё» раз в 14 часов
DOUBLE_WIN_PERCENT = 50            # шанс удвоения
PROMO_DELAY = 4 * 3600             # ждать 4 часа после запроса промокода
PROMO_COST = 3000                  # 3 кристалла за промокод
CODE_TTL = 600                     # секунд жизни кода привязки
MAX_ATTEMPTS = 5                   # попыток ввода кода
_CODE_RE = re.compile(r"[Ii]\d{5}")
_PROMO_RE = re.compile(r"[A-Za-z0-9_\-]{3,40}")


# Активация промокодов в боте: первые FREE_ACTS бесплатно, дальше ACT_COST1 тысячных, после ACT_STEP активаций ACT_COST2
FREE_ACTS, ACT_STEP, ACT_COST1, ACT_COST2 = 5, 40, 100, 1000
# Ежедневные награды: ждать DAILY_DELAY после начала, новое ожидание не раньше чем через DAILY_PERIOD; 18 дней по кругу
DAILY_DELAY, DAILY_PERIOD = 2 * 3600, 24 * 3600
# день: (вид, количество). crystals — тысячные; promo — промокод на столько радужных кругов; rainbow/clouds — зачисляются сразу
DAILY = {1: ("crystals", 10), 2: ("crystals", 90), 3: ("crystals", 300), 4: ("crystals", 100), 5: ("promo", 5), 6: (None, 0), 7: ("rainbow", 1),
         8: ("clouds", 2), 9: ("clouds", 3), 10: ("promo", 1), 11: ("promo", 1), 12: ("crystals", 1000), 13: ("promo", 5), 14: ("promo", 5),
         15: ("crystals", 2000), 16: ("crystals", 1), 17: ("crystals", 999), 18: ("crystals", 1000)}

def tg_id_of(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2 ** 53 else None


def _hash(code):
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _one(res):
    d = res.data
    return (d[0] if isinstance(d, list) and d else d) if d else None


def _state(tg_id):
    rows = supabase.table("tgbot_users").select("*").eq("tg_id", tg_id).limit(1).execute().data or []
    return rows[0] if rows else {"tg_id": tg_id, "user_id": None, "crystals": 0, "last_claim": None, "last_double": None, "promo_ready_at": None}


def _wait(ts, every):
    """Сколько секунд осталось до следующего действия."""
    if not ts: return 0
    try: dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError: return 0
    return max(0, int((dt + timedelta(seconds=every) - datetime.now(timezone.utc)).total_seconds()) + 1)


def state(tg_id):
    s = _state(tg_id)
    promo_wait = 0
    if s.get("promo_ready_at"):
        try: promo_wait = max(0, int((datetime.fromisoformat(str(s["promo_ready_at"]).replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()) + 1)
        except ValueError: pass
    return {"linked": bool(s.get("user_id")), "crystals": int(s.get("crystals") or 0), "claim_wait": _wait(s.get("last_claim"), CLAIM_EVERY),
            "double_wait": _wait(s.get("last_double"), DOUBLE_EVERY), "promo_started": bool(s.get("promo_ready_at")), "promo_wait": promo_wait,
            "claim_amount": CLAIM_MILLI, "promo_cost": PROMO_COST, "promo_delay": PROMO_DELAY, "double_every": DOUBLE_EVERY, "double_percent": DOUBLE_WIN_PERCENT}


def request_link(tg_id, user_id):
    """Прислать код привязки в личку от @Gifts. Возвращает (ok, ошибка_кодом)."""
    u = get_user_by_id(user_id) if user_id else None
    if not u or u.get("banned"): return False, "no_user"
    cur = _state(tg_id)
    if cur.get("user_id"): return False, "already_linked" if cur["user_id"] == user_id else "tg_taken"
    other = supabase.table("tgbot_users").select("tg_id").eq("user_id", user_id).limit(1).execute().data
    if other: return False, "user_taken"
    sender = get_user_by_username(SENDER_USERNAME)
    if not sender: return False, "no_sender"
    code = "I%05d" % secrets.randbelow(100000)
    supabase.table("tgbot_codes").upsert({"tg_id": tg_id, "user_id": user_id, "code_hash": _hash(code), "attempts": 0,
                                          "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL)).isoformat()}, on_conflict="tg_id").execute()
    chat = get_or_create_chat(sender["id"], user_id)
    if not chat: return False, "send_failed"
    text = ("Привет! Используй этот код для интеграции в боте телеграмм: @DiogramRobot\n\n" + code +
            "\n\nЕсли вы не запрашивали этот код проигнорируйте это сообщение.")
    try: save_message(chat["id"], sender["id"], text, "text")
    except Exception as e:
        print(f"[tgbot] отправка кода: {e!r}"); return False, "send_failed"
    return True, None


def confirm_link(tg_id, code):
    """Проверить код и привязать. Возвращает (ok, ошибка_кодом)."""
    if not isinstance(code, str) or not _CODE_RE.fullmatch(code.strip()): return False, "bad_code"
    rows = supabase.table("tgbot_codes").select("*").eq("tg_id", tg_id).limit(1).execute().data or []
    if not rows: return False, "no_code"
    r = rows[0]
    try: exp = datetime.fromisoformat(str(r["expires_at"]).replace("Z", "+00:00"))
    except ValueError: exp = datetime.now(timezone.utc)
    if exp <= datetime.now(timezone.utc) or int(r["attempts"]) >= MAX_ATTEMPTS:
        supabase.table("tgbot_codes").delete().eq("tg_id", tg_id).execute(); return False, "expired"
    if not secrets.compare_digest(r["code_hash"], _hash(code)):
        supabase.table("tgbot_codes").update({"attempts": int(r["attempts"]) + 1}).eq("tg_id", tg_id).eq("attempts", r["attempts"]).execute()
        return False, "wrong_code"
    res = supabase.rpc("tgbot_link", {"p_tg": tg_id, "p_user": int(r["user_id"])}).execute().data
    supabase.table("tgbot_codes").delete().eq("tg_id", tg_id).execute()
    return (True, None) if res == "ok" else (False, res or "send_failed")


def profile(tg_id):
    s = _state(tg_id)
    if not s.get("user_id"): return None
    u = get_user_by_id(int(s["user_id"]))
    if not u: return None
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "без имени"
    return {"name": name, "id": u["id"], "username": u.get("username"), "collection": [r["username"] for r in get_user_usernames(u["id"])][:50]}


def claim(tg_id):
    r = _one(supabase.rpc("tgbot_claim", {"p_tg": tg_id, "p_amount": CLAIM_MILLI, "p_every": CLAIM_EVERY}).execute())
    return {"ok": bool(r["ok"]), "crystals": int(r["crystals"]), "wait": int(r["wait_seconds"]), "amount": CLAIM_MILLI}


def double(tg_id):
    win = secrets.randbelow(100) < DOUBLE_WIN_PERCENT
    r = _one(supabase.rpc("tgbot_double", {"p_tg": tg_id, "p_win": win, "p_every": DOUBLE_EVERY}).execute())
    return {"status": r["status"], "crystals": int(r["crystals"]), "wait": int(r["wait_seconds"])}


def promo_start(tg_id):
    r = _one(supabase.rpc("tgbot_promo_start", {"p_tg": tg_id, "p_delay": PROMO_DELAY}).execute())
    return {"status": r["status"], "wait": int(r["wait_seconds"]), "cost": PROMO_COST}


def promo_take(tg_id):
    r = _one(supabase.rpc("tgbot_promo_take", {"p_tg": tg_id, "p_cost": PROMO_COST}).execute())
    return {"status": r["status"], "code": r.get("code"), "crystals": int(r["crystals"]), "wait": int(r["wait_seconds"]), "cost": PROMO_COST}



# ---- активация промокодов в боте ----
def act_cost(acts):
    return 0 if acts < FREE_ACTS else (ACT_COST1 if acts < ACT_STEP else ACT_COST2)


def promo_info(tg_id):
    s = _state(tg_id)
    acts = int(s.get("promo_acts") or 0)
    return {"linked": bool(s.get("user_id")), "acts": acts, "free_left": max(0, FREE_ACTS - acts), "cost": act_cost(acts), "crystals": int(s.get("crystals") or 0),
            "free": FREE_ACTS, "step": ACT_STEP, "cost1": ACT_COST1, "cost2": ACT_COST2}


def promo_activate(tg_id, code):
    """Платит кристаллами только за успешную активацию. Возвращает {status: ok|not_linked|no_crystals|error, ...}."""
    s = _state(tg_id)
    uid = s.get("user_id")
    if not uid: return {"status": "not_linked"}
    acts = int(s.get("promo_acts") or 0)
    cost = act_cost(acts)
    if cost and int(s.get("crystals") or 0) < cost: return {"status": "no_crystals", "cost": cost, "crystals": int(s.get("crystals") or 0)}
    if cost:
        left = supabase.rpc("tgbot_spend", {"p_tg": tg_id, "p_cost": cost}).execute().data
        if not isinstance(left, int) or left < 0: return {"status": "no_crystals", "cost": cost, "crystals": int(s.get("crystals") or 0)}
    got, err = promo.claim(int(uid), code)
    if got is None and not (err or "").startswith("Часть наград"):
        if cost: supabase.rpc("tgbot_add", {"p_tg": tg_id, "p_amount": cost}).execute()     # код не сработал: возвращаем плату
        return {"status": "error", "message": err or "Не удалось активировать"}
    n = supabase.rpc("tgbot_act_done", {"p_tg": tg_id}).execute().data
    left = int(_state(tg_id).get("crystals") or 0)
    return {"status": "ok", "rewards": got or {}, "message": err, "cost": cost, "crystals": left, "acts": n}


# ---- ежедневные награды ----
def _cycle_day(day): return (int(day) - 1) % len(DAILY) + 1


def _reward(day):
    kind, amount = DAILY[_cycle_day(day)]
    return {"kind": kind, "amount": amount}


def daily_open(tg_id):
    r = _one(supabase.rpc("tgbot_daily", {"p_tg": tg_id, "p_action": "open", "p_delay": DAILY_DELAY, "p_period": DAILY_PERIOD}).execute())
    day = int(r["day"] or 1)
    return {"status": r["status"], "day": _cycle_day(day), "total_days": len(DAILY), "wait": int(r["wait_seconds"]), "reward": _reward(day),
            "schedule": {str(d): {"kind": k, "amount": a} for d, (k, a) in DAILY.items()}, "delay": DAILY_DELAY}


def _grant_daily(tg_id, uid, day):
    """Выдать награду дня. Возвращает {kind, amount, code?}. Бросает исключение при сбое."""
    kind, amount = DAILY[_cycle_day(day)]
    out = {"kind": kind, "amount": amount}
    if kind == "crystals": supabase.rpc("tgbot_add", {"p_tg": tg_id, "p_amount": amount}).execute()
    elif kind == "rainbow": rainbow.grant(uid, amount, "tgbot_daily")
    elif kind == "clouds": game.credit_map_clouds(uid, amount)
    elif kind == "promo":
        for _ in range(5):
            code = "Dly" + secrets.token_hex(7)
            row, err = promo.create_code(1, code, "limited", {"rainbow": amount}, max_activations=1, note="Ежедневная награда бота")
            if row: out["code"] = code; break
        else: raise RuntimeError("promo create")
    return out


def daily_claim(tg_id):
    r = _one(supabase.rpc("tgbot_daily", {"p_tg": tg_id, "p_action": "claim", "p_delay": DAILY_DELAY, "p_period": DAILY_PERIOD}).execute())
    if r["status"] != "ok": return {"status": r["status"], "day": _cycle_day(int(r["day"] or 1)), "wait": int(r["wait_seconds"])}
    day, uid = int(r["day"]), _state(tg_id).get("user_id")
    try: reward = _grant_daily(tg_id, int(uid), day)
    except Exception as e:
        print(f"[tgbot daily] выдача не удалась ({e!r}), откат: tg={tg_id} day={day}")
        try: supabase.rpc("tgbot_daily_release", {"p_tg": tg_id}).execute()
        except Exception as e2: print(f"[tgbot daily] !!! откат не прошёл: tg={tg_id} {e2!r}")
        return {"status": "error"}
    return {"status": "ok", "day": _cycle_day(day), "reward": reward, "crystals": int(_state(tg_id).get("crystals") or 0), "total_days": len(DAILY)}

# ---- админка (owner) ----
def admin_overview():
    pool = supabase.table("tgbot_promos").select("id, code, given_to_tg, given_at").order("id").limit(500).execute().data or []
    linked = supabase.table("tgbot_users").select("tg_id, user_id, linked_at, crystals").not_.is_("user_id", "null").order("linked_at", desc=True).limit(30).execute().data or []
    names = {}
    ids = [int(x["user_id"]) for x in linked]
    if ids:
        for u in supabase.table("users").select("id, first_name, username").in_("id", ids).execute().data or []:
            names[u["id"]] = (u.get("first_name") or "") + (" @" + u["username"] if u.get("username") else "")
    total = supabase.table("tgbot_users").select("tg_id", count="exact").execute().count or 0
    sender = get_user_by_username(SENDER_USERNAME)
    return {"pool": pool, "free": sum(1 for p in pool if not p["given_to_tg"]), "linked": [dict(x, name=names.get(x["user_id"], "")) for x in linked],
            "total": total, "sender_ok": bool(sender), "cost": PROMO_COST, "delay": PROMO_DELAY}


def admin_pool_add(text):
    """Добавить коды в пул (по одному в строке). Коды должны существовать в «Промокодах». Возвращает (добавлено, пропущено[(код, причина)])."""
    lines = [l.strip() for l in str(text or "").replace(",", "\n").splitlines() if l.strip()][:200]
    added, skipped = 0, []
    for code in lines:
        if not _PROMO_RE.fullmatch(code): skipped.append((code[:40], "неверный формат")); continue
        if not (supabase.table("promo_codes").select("id").ilike("code", code).eq("active", True).limit(1).execute().data): skipped.append((code, "нет такого активного промокода")); continue
        if supabase.table("tgbot_promos").select("id").ilike("code", code).limit(1).execute().data: skipped.append((code, "уже в пуле")); continue
        try: supabase.table("tgbot_promos").insert({"code": code}).execute(); added += 1
        except Exception: skipped.append((code, "не удалось добавить"))
    return added, skipped


def admin_pool_delete(pool_id):
    res = supabase.table("tgbot_promos").delete().eq("id", pool_id).is_("given_to_tg", "null").execute()
    return bool(res.data)


def admin_unlink(tg_id):
    res = supabase.table("tgbot_users").update({"user_id": None, "linked_at": None}).eq("tg_id", tg_id).execute()
    return bool(res.data)
