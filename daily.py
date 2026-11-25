"""Ежедневный лимитированный бонус в игре: задания с общим лимитом выполнений (успел или нет), один раз на человека.
Таблицы и функции: миграция 20261128_daily_bonus.sql. Условия: clouds (облачка карты) и owner_chat (переписка с владельцем)."""
from datetime import datetime, timezone

import game
import rainbow
from db import supabase

STAFF = ("owner", "developer", "moderator")


def _owner_ids():
    rows = supabase.table("users").select("id").eq("role", "owner").execute().data or []
    return [int(r["id"]) for r in rows]


def _progress(task, user):
    """Сколько уже сделано: {have, need, text} для клиента и ok для проверки."""
    if task["kind"] == "clouds":
        have = int(game.player_row(user["id"]).get("clouds_map") or 0)
        need = int(task["need"])
        return {"ok": have >= need, "parts": [{"label": "Облачка", "have": min(have, need), "need": need}]}
    if task["kind"] == "owner_chat":
        owners = _owner_ids()
        mine = theirs = 0
        if owners:
            r = supabase.rpc("daily_owner_chat", {"p_user": user["id"], "p_since": task["starts_at"], "p_owners": owners}).execute().data
            row = (r or [{}])[0]
            mine, theirs = int(row.get("mine") or 0), int(row.get("theirs") or 0)
        n1, n2 = int(task["need"]), int(task["need2"])
        return {"ok": mine >= n1 and theirs >= n2, "parts": [{"label": "Твои сообщения", "have": min(mine, n1), "need": n1}, {"label": "Ответы владельца", "have": min(theirs, n2), "need": n2}]}
    return {"ok": False, "parts": []}


def list_tasks(user):
    now = datetime.now(timezone.utc).isoformat()
    tasks = supabase.table("daily_tasks").select("*").eq("active", True).lte("starts_at", now).order("starts_at").order("id").execute().data or []
    mine = {int(r["task_id"]) for r in (supabase.table("daily_claims").select("task_id").eq("user_id", user["id"]).execute().data or [])}
    full = {int(t["id"]): int(t["completions"]) >= int(t["max_completions"]) for t in (supabase.table("daily_tasks").select("id,completions,max_completions").execute().data or [])}
    out = []
    for t in tasks:
        if t.get("requires_task") and not full.get(int(t["requires_task"]), False): continue   # откроется, когда закончатся выполнения предыдущего
        left = max(0, int(t["max_completions"]) - int(t["completions"]))
        done = int(t["id"]) in mine
        pr = _progress(t, user) if not done and left > 0 else {"ok": False, "parts": []}
        out.append({"id": t["id"], "title": t["title"], "descr": t["descr"], "left": left, "max": int(t["max_completions"]),
                    "reward_clouds": int(t["reward_clouds"]), "reward_rainbow": int(t["reward_rainbow"]),
                    "done": done, "exhausted": left == 0 and not done, "can_claim": pr["ok"], "parts": pr["parts"]})
    return out


ERRORS = {"not_found": "Такого задания нет", "inactive": "Задание отключено", "not_started": "Задание ещё не началось", "locked": "Откроется, когда закончатся выполнения предыдущего задания",
          "exhausted": "Опоздал: все места уже заняли", "used": "Ты уже выполнил это задание"}


def claim(user, task_id, season_active=True):
    """Забрать награду. Возвращает (получено, ошибка). Проверка условий до, лимит и «один раз» атомарно в SQL."""
    if user.get("role") != "user": return None, "Задания только для обычных игроков"
    if not season_active: return None, "Сезон закончился"
    rows = supabase.table("daily_tasks").select("*").eq("id", task_id).limit(1).execute().data
    if not rows: return None, ERRORS["not_found"]
    t = rows[0]
    if not _progress(t, user)["ok"]: return None, "Условие ещё не выполнено"
    res = supabase.rpc("daily_claim", {"p_task": task_id, "p_user": user["id"]}).execute().data
    row = (res or [None])[0]
    if not row: return None, "Не удалось забрать, попробуй ещё раз"
    if row["status"] != "ok": return None, ERRORS.get(row["status"], "Недоступно")
    got = {}
    try:
        if row["reward_clouds"]:
            game.credit_map_clouds(user["id"], int(row["reward_clouds"])); got["clouds"] = int(row["reward_clouds"])
        if row["reward_rainbow"]:
            rainbow.grant(user["id"], int(row["reward_rainbow"]), "daily"); got["rainbow"] = int(row["reward_rainbow"])
    except Exception as e:
        print(f"[daily reward] {e!r} уже выдано: {got}")
        if got: return None, "Часть награды выдана, остальное не удалось. Напиши в поддержку, мы доначислим."
        try: supabase.rpc("daily_release", {"p_task": task_id, "p_user": user["id"]}).execute()
        except Exception as e2: print(f"[daily_release] {e2!r}")
        return None, "Не удалось выдать награду. Попробуй ещё раз."
    return got, None

