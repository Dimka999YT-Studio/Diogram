import os
import bcrypt
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============ ПОЛЬЗОВАТЕЛИ ============

def hash_password(password: str) -> str:
    """Хеширует пароль"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    """Проверяет пароль"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def email_exists(email: str) -> bool:
    """Проверяет, есть ли email в базе"""
    res = supabase.table("users").select("id").eq("email", email.lower()).execute()
    return len(res.data) > 0


def username_exists(username: str) -> bool:
    """Проверяет, занят ли @username"""
    res = supabase.table("users").select("id").eq("username", username.lower()).execute()
    return len(res.data) > 0


def create_user(email: str, password: str, username: str = None, first_name: str = None, last_name: str = None) -> dict:
    """Создаёт нового юзера. ID начинается с 1000000."""
    data = {
        "email": email.lower(),
        "password_hash": hash_password(password),
        "username": username.lower() if username else None,
        "first_name": first_name,
        "last_name": last_name,
        "role": "user",
        "verified": False,
        "profile_completed": False,
    }
    res = supabase.table("users").insert(data).execute()
    return res.data[0] if res.data else None


def get_user_by_email(email: str) -> dict:
    """Находит юзера по email"""
    res = supabase.table("users").select("*").eq("email", email.lower()).execute()
    return res.data[0] if res.data else None


def get_user_by_id(user_id: int) -> dict:
    """Находит юзера по ID"""
    res = supabase.table("users").select("*").eq("id", user_id).execute()
    return res.data[0] if res.data else None


def get_user_by_username(username: str) -> dict:
    """Находит юзера по @username"""
    res = supabase.table("users").select("*").eq("username", username.lower()).execute()
    return res.data[0] if res.data else None


def search_users(query: str, current_user_id: int, limit: int = 20) -> list:
    """Ищет юзеров по @username или имени"""
    query = query.lower().lstrip("@")
    res = supabase.table("users").select("id, username, first_name, last_name, verified, role").or_(
        f"username.ilike.%{query}%,first_name.ilike.%{query}%"
    ).neq("id", current_user_id).limit(limit).execute()
    return res.data or []


def update_user_profile(user_id: int, username: str, first_name: str, last_name: str = None) -> dict:
    """Обновляет профиль юзера"""
    data = {
        "username": username.lower(),
        "first_name": first_name,
        "last_name": last_name,
        "profile_completed": True,
    }
    res = supabase.table("users").update(data).eq("id", user_id).execute()
    return res.data[0] if res.data else None


def username_change_count_today(user_id: int) -> int:
    """Сколько раз юзер менял @username за 24 часа (для лимита 10)"""
    # Пока просто возвращаем 0. Позже добавим таблицу username_changes
    return 0


# ============ ЧАТЫ ============

def get_or_create_chat(user1_id: int, user2_id: int) -> dict:
    """Находит существующий чат между двумя юзерами или создаёт новый"""
    # Всегда меньшее ID первым для консистентности
    a, b = sorted([user1_id, user2_id])
    res = supabase.table("chats").select("*").eq("user1_id", a).eq("user2_id", b).execute()
    if res.data:
        return res.data[0]
    res = supabase.table("chats").insert({"user1_id": a, "user2_id": b}).execute()
    return res.data[0] if res.data else None


def get_user_chats(user_id: int) -> list:
    """Возвращает все чаты юзера с данными собеседников"""
    res = supabase.table("chats").select("*").or_(
        f"user1_id.eq.{user_id},user2_id.eq.{user_id}"
    ).execute()
    chats = res.data or []

    result = []
    for chat in chats:
        other_id = chat["user2_id"] if chat["user1_id"] == user_id else chat["user1_id"]
        other = get_user_by_id(other_id)
        if not other:
            continue

        # Последнее сообщение
        last = supabase.table("messages").select("*").eq("chat_id", chat["id"]).order(
            "created_at", desc=True
        ).limit(1).execute()

        last_msg = last.data[0] if last.data else None

        result.append({
            "chat_id": chat["id"],
            "other_user": {
                "id": other["id"],
                "username": other.get("username"),
                "first_name": other.get("first_name"),
                "last_name": other.get("last_name"),
                "verified": other.get("verified", False),
                "role": other.get("role", "user"),
            },
            "last_message": last_msg["text"] if last_msg else None,
            "last_message_at": last_msg["created_at"] if last_msg else chat["created_at"],
        })

    result.sort(key=lambda x: x["last_message_at"] or "", reverse=True)
    return result


def get_chat_by_id(chat_id: int) -> dict:
    """Получает чат по ID"""
    res = supabase.table("chats").select("*").eq("id", chat_id).execute()
    return res.data[0] if res.data else None


# ============ СООБЩЕНИЯ ============

def save_message(chat_id: int, sender_id: int, text: str) -> dict:
    """Сохраняет сообщение в БД"""
    res = supabase.table("messages").insert({
        "chat_id": chat_id,
        "sender_id": sender_id,
        "text": text,
    }).execute()
    return res.data[0] if res.data else None


def get_messages(chat_id: int, limit: int = 50, offset: int = 0) -> list:
    """Возвращает историю сообщений чата"""
    res = supabase.table("messages").select("*").eq("chat_id", chat_id).order(
        "created_at", desc=False
    ).range(offset, offset + limit - 1).execute()
    return res.data or []


def search_messages(user_id: int, query: str, limit: int = 50) -> list:
    """Ищет сообщения по тексту во всех чатах юзера"""
    chats = supabase.table("chats").select("id").or_(
        f"user1_id.eq.{user_id},user2_id.eq.{user_id}"
    ).execute()
    chat_ids = [c["id"] for c in (chats.data or [])]
    if not chat_ids:
        return []
    res = supabase.table("messages").select("*").in_("chat_id", chat_ids).ilike(
        "text", f"%{query}%"
    ).order("created_at", desc=True).limit(limit).execute()
    return res.data or []


# ============ КОДЫ ПОДТВЕРЖДЕНИЯ ============

def save_pending_code(email: str, code: int, expires_minutes: int = 15) -> None:
    """Сохраняет временный код для регистрации"""
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).isoformat()
    # Удаляем старые коды для этого email
    supabase.table("pending_codes").delete().eq("email", email.lower()).execute()
    supabase.table("pending_codes").insert({
        "email": email.lower(),
        "code": str(code),
        "expires_at": expires_at,
    }).execute()


def verify_code(email: str, code: str) -> bool:
    """Проверяет код, удаляет если верный"""
    res = supabase.table("pending_codes").select("*").eq("email", email.lower()).eq(
        "code", str(code)
    ).execute()
    if not res.data:
        return False

    entry = res.data[0]
    expires_at = datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        supabase.table("pending_codes").delete().eq("email", email.lower()).execute()
        return False

    # Удаляем использованный код
    supabase.table("pending_codes").delete().eq("email", email.lower()).execute()
    return True
