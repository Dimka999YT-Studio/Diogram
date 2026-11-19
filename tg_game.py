"""Награды голдой за задания Telegram-игры (Arrow Puzzle). SQL: supabase/migrations/20261121_tg_game_rewards.sql.
Игра сама в Telegram. Её сервер по секретному ключу (TG_GAME_KEY) сообщает Diogram, что игрок выполнил задание;
игрок заранее привязывает Telegram к аккаунту Diogram одноразовым кодом.
Подтвердить сами задания мы не можем (прогресс игры лежит на устройстве игрока), поэтому защита такая:
награда мелкая и бонусная, каждое задание раз на Telegram-аккаунт, дневной потолок, один Telegram на аккаунт, аккаунт Diogram не младше 3 дней."""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from db import supabase
import gold

MSK = timezone(timedelta(hours=3))
DAILY_CAP = 10            # голды в сутки на Telegram-аккаунт (по МСК)
MIN_ACCOUNT_DAYS = 3      # возраст аккаунта Diogram для наград
CODE_TTL = 600            # секунд жизни кода привязки
_CODE_ALPHA = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_TASK_ID = re.compile(r"^[a-z0-9_]{2,40}$")
ONCE_DAY = "2000-01-01"


def _hash(code):
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _tg_id(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2 ** 53 else None


def _msk_day(ts):
    try: return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(MSK).date().isoformat()
    except ValueError: return ""


def make_code(user_id):
    """Новый код привязки (старые неиспользованные коды этого человека гасим). Возвращает (код, ошибка)."""
    if supabase.table("tg_links").select("tg_id").eq("user_id", user_id).execute().data: return None, "Telegram уже привязан"
    supabase.table("tg_link_codes").delete().eq("user_id", user_id).eq("used", False).execute()
    code = "".join(secrets.choice(_CODE_ALPHA) for _ in range(8))
    exp = (datetime.now(timezone.utc) + timedelta(seconds=CODE_TTL)).isoformat()
    supabase.table("tg_link_codes").insert({"code_hash": _hash(code), "user_id": user_id, "expires_at": exp}).execute()
    return code, None


_LINK_ERR = {"bad_code": "Код неверный или устарел", "tg_taken": "Этот Telegram уже привязан к другому аккаунту", "user_linked": "К аккаунту уже привязан другой Telegram"}


def link(code, tg_id):
    """Вызывает сервер игры. Возвращает (ok, сообщение)."""
    tg_id = _tg_id(tg_id)
    if not tg_id or not isinstance(code, str) or not 4 <= len(code.strip()) <= 16: return False, "Неверные данные"
    res = supabase.rpc("tg_link", {"p_code_hash": _hash(code), "p_tg": tg_id}).execute().data
    if res == "ok":
        row = supabase.table("tg_links").select("user_id").eq("tg_id", tg_id).execute().data
        if row: gold._tell(row[0]["user_id"], "✅ Telegram привязан. Задания игры будут приносить бонусную голду.")
        return True, "ok"
    return False, _LINK_ERR.get(res, "Не удалось привязать")


_CLAIM_ERR = {"not_linked": "Telegram не привязан к аккаунту Diogram", "too_new": f"Аккаунту Diogram должно быть не меньше {MIN_ACCOUNT_DAYS} дней",
              "no_task": "Такого задания нет", "already": "Награда за это задание уже получена", "cap": "Дневной лимит наград исчерпан"}


def claim(tg_id, task_id):
    """Вызывает сервер игры. Возвращает (статус, данные); статус ok | not_linked | too_new | no_task | already | cap | bad."""
    tg_id = _tg_id(tg_id)
    if not tg_id or not isinstance(task_id, str) or not _TASK_ID.match(task_id): return "bad", {"error": "Неверные данные"}
    rows = supabase.rpc("tg_claim", {"p_tg": tg_id, "p_task": task_id, "p_cap": DAILY_CAP, "p_min_days": MIN_ACCOUNT_DAYS}).execute().data or []
    if not rows: return "bad", {"error": "Нет ответа"}
    r = rows[0]; st = r["status"]
    if st == "ok":
        gold._tell(r["uid"], f"🎮 За задание «{r['title']}» начислено {r['gold']} голды (бонусной).")
        return "ok", {"gold": r["gold"], "today": r["today"], "cap": DAILY_CAP}
    return st, {"error": _CLAIM_ERR.get(st, "Отказ"), "today": r.get("today", 0), "cap": DAILY_CAP}


def state(user_id):
    """Экран игрока: привязка, задания, сколько получено сегодня."""
    lk = supabase.table("tg_links").select("tg_id").eq("user_id", user_id).execute().data
    tasks = supabase.table("tg_tasks").select("id,title,gold,kind").eq("active", True).order("created_at").execute().data or []
    out = {"linked": bool(lk), "cap": DAILY_CAP, "today": 0, "min_days": MIN_ACCOUNT_DAYS, "tasks": tasks}
    if lk:
        tg = lk[0]["tg_id"]; today = datetime.now(MSK).date().isoformat()
        claims = supabase.table("tg_task_claims").select("task_id,day,gold,created_at").eq("tg_id", tg).execute().data or []
        done = {c["task_id"] for c in claims if c["day"] in (ONCE_DAY, today)}
        for t in tasks: t["done"] = t["id"] in done
        out["today"] = sum(c["gold"] for c in claims if _msk_day(c["created_at"]) == today)
    return out


# ---------- админка (owner) ----------
def admin_overview():
    tasks = supabase.table("tg_tasks").select("*").order("created_at").execute().data or []
    links = supabase.table("tg_links").select("*").order("linked_at", desc=True).limit(200).execute().data or []
    claims = supabase.table("tg_task_claims").select("*").order("created_at", desc=True).limit(50).execute().data or []
    return {"tasks": tasks, "links": links, "claims": claims, "cap": DAILY_CAP, "min_days": MIN_ACCOUNT_DAYS}


def save_task(task_id, title, gold_amount, kind, active=True):
    if not isinstance(task_id, str) or not _TASK_ID.match(task_id): return None, "ID задания: 2–40 символов, a–z, 0–9, _"
    title = (title or "").strip() if isinstance(title, str) else ""
    if not 1 <= len(title) <= 80: return None, "Название: 1–80 символов"
    if not isinstance(gold_amount, int) or isinstance(gold_amount, bool) or not 1 <= gold_amount <= 1000: return None, "Награда: от 1 до 1000 голды"
    if kind not in ("once", "daily"): return None, "Тип: once или daily"
    if gold_amount > DAILY_CAP: return None, f"Награда больше дневного лимита ({DAILY_CAP}): её нельзя выдать"
    data = {"id": task_id, "title": title, "gold": gold_amount, "kind": kind, "active": bool(active)}
    res = supabase.table("tg_tasks").upsert(data).execute().data
    return (res[0] if res else None), (None if res else "Не удалось сохранить")


def delete_task(task_id):
    return bool(supabase.table("tg_tasks").delete().eq("id", task_id).execute().data)


def unlink(user_id):
    return bool(supabase.table("tg_links").delete().eq("user_id", user_id).execute().data)
