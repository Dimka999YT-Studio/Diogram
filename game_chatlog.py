"""Журнал чата лобби в прятках (таблица game_chat_log, миграция 20261125). Пишется в фоне и без падений: чат работает
и без таблицы. Хранится KEEP_DAYS дней, читает только owner (выгрузка txt в админке)."""
import threading, time
from datetime import datetime, timedelta, timezone, timedelta as _td

from db import supabase

KEEP_DAYS = 30
MSK = timezone(_td(hours=3))
_last_purge = 0.0


def _write(rec):
    global _last_purge
    try:
        supabase.table("game_chat_log").insert(rec).execute()
        now = time.time()
        if now - _last_purge > 3600:
            _last_purge = now
            cut = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
            supabase.table("game_chat_log").delete().lt("created_at", cut).execute()
    except Exception as e:
        print(f"[game_chatlog] не записалось: {e!r}")


def save(season, lobby_no, user_id, name, role, text):
    """Запись в фоне, чтобы не тормозить ответ игроку."""
    rec = {"season": season, "lobby_no": int(lobby_no), "user_id": int(user_id), "name": str(name)[:60], "role": str(role)[:10], "text": str(text)[:200]}
    threading.Thread(target=_write, args=(rec,), daemon=True).start()


def export_txt(days=1, limit=5000):
    """Текст для скачивания: по строке на сообщение, старые сверху."""
    days = max(1, min(int(days), KEEP_DAYS)); limit = max(1, min(int(limit), 20000))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = supabase.table("game_chat_log").select("*").gte("created_at", since).order("id", desc=True).limit(limit).execute().data or []
    rows.reverse()
    out = [f"Чат игры «Прятки», последние {days} дн., сообщений: {len(rows)}"]
    for r in rows:
        try: t = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")).astimezone(MSK).strftime("%Y-%m-%d %H:%M:%S")
        except Exception: t = str(r.get("created_at"))
        who = ("искатель" if r.get("role") == "seeker" else "прячется")
        text = " ".join(str(r.get("text") or "").split())
        out.append(f"{t} МСК | лобби {r.get('lobby_no')} | {r.get('name')} (id {r.get('user_id')}, {who}): {text}")
    return "\n".join(out) + "\n", len(rows)
