"""Telegram-бот @DiogramRobot: привязка к аккаунту Diogram, кристаллы, выдача промокодов из пула.
SQL: supabase/migrations/20261129_tgbot.sql. Бот ходит сюда по ключу TG_BOT_KEY (заголовок X-Bot-Key); tg_id берётся из Telegram.
Кристаллы хранятся в тысячных долях (1000 = 1 кристалл), чтобы 0,999 + 0,001 давало ровно 1."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

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
