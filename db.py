import os
import bcrypt
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============ ПОЛЬЗОВАТЕЛИ ============

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def email_exists(email: str) -> bool:
    res = supabase.table("users").select("id").eq("email", email.lower()).execute()
    return len(res.data) > 0


def username_exists(username: str) -> bool:
    res = supabase.table("users").select("id").eq("username", username.lower()).execute()
    return len(res.data) > 0


def create_user(email: str, password: str, username: str = None, first_name: str = None, last_name: str = None) -> dict:
    data = {
        "email": email.lower(),
        "password_hash": hash_password(password),
        "username": username.lower() if username else None,
        "first_name": first_name,
        "last_name": last_name,
        "role": "user",
        "verified": False,
        "profile_completed": False,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    res = supabase.table("users").insert(data).execute()
    return res.data[0] if res.data else None


def get_user_by_email(email: str) -> dict:
    res = supabase.table("users").select("*").eq("email", email.lower()).execute()
    return res.data[0] if res.data else None


def get_user_by_id(user_id: int) -> dict:
    res = supabase.table("users").select("*").eq("id", user_id).execute()
    return res.data[0] if res.data else None


def get_user_by_username(username: str) -> dict:
    res = supabase.table("users").select("*").eq("username", username.lower()).execute()
    return res.data[0] if res.data else None


def search_users(query: str, current_user_id: int, limit: int = 20) -> list:
    query = query.lower().lstrip("@")
    query = query.replace("%", "").replace("_", " ").strip()
    if not query:
        return []
    res = supabase.table("users").select(
        "id, username, first_name, last_name, verified, role, avatar_url"
    ).or_(
        f"username.ilike.%{query}%,first_name.ilike.%{query}%"
    ).neq("id", current_user_id).limit(limit).execute()
    return res.data or []


def update_user_profile(user_id: int, username: str, first_name: str, last_name: str = None) -> dict:
    data = {
        "username": username.lower(),
        "first_name": first_name,
        "last_name": last_name,
        "profile_completed": True,
    }
    res = supabase.table("users").update(data).eq("id", user_id).execute()
    return res.data[0] if res.data else None


def update_user_avatar(user_id: int, avatar_url: str) -> dict:
    res = supabase.table("users").update({"avatar_url": avatar_url}).eq("id", user_id).execute()
    return res.data[0] if res.data else None


def update_user_bio(user_id: int, bio: str = None, birthday: str = None) -> dict:
    data = {}
    if bio is not None:
        data["bio"] = bio.strip() or None
    if birthday is not None:
        data["birthday"] = birthday.strip() or None
    if not data:
        return None
    res = supabase.table("users").update(data).eq("id", user_id).execute()
    return res.data[0] if res.data else None


# ============ ОНЛАЙН И ПРОЧТЕНИЕ ============

def update_last_seen(user_id: int) -> None:
    supabase.table("users").update({
        "last_seen": datetime.now(timezone.utc).isoformat()
    }).eq("id", user_id).execute()


def is_online(user: dict) -> bool:
    if not user or not user.get("last_seen"):
        return False
    try:
        last = datetime.fromisoformat(user["last_seen"].replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last) < timedelta(minutes=2)
    except Exception:
        return False


def mark_messages_read(chat_id: int, reader_id: int) -> None:
    supabase.table("messages").update({
        "read_at": datetime.now(timezone.utc).isoformat()
    }).eq("chat_id", chat_id).neq("sender_id", reader_id).is_("read_at", "null").execute()


def get_unread_count(chat_id: int, user_id: int) -> int:
    res = supabase.table("messages").select("id").eq("chat_id", chat_id).neq(
        "sender_id", user_id
    ).is_("read_at", "null").execute()
    return len(res.data or [])


# ============ ФОТО-ЛИМИТ ============

def count_photo_uploads_last_15_days(user_id: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    res = supabase.table("photo_uploads").select("id").eq("user_id", user_id).gt(
        "uploaded_at", cutoff
    ).execute()
    return len(res.data or [])


def register_photo_upload(user_id: int) -> None:
    supabase.table("photo_uploads").insert({"user_id": user_id}).execute()


# ============ ЧАТЫ ============

def get_or_create_chat(user1_id: int, user2_id: int) -> dict:
    a, b = sorted([user1_id, user2_id])
    res = supabase.table("chats").select("*").eq("user1_id", a).eq("user2_id", b).execute()
    if res.data:
        return res.data[0]
    res = supabase.table("chats").insert({"user1_id": a, "user2_id": b}).execute()
    return res.data[0] if res.data else None


def get_user_chats(user_id: int) -> list:
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

        last = supabase.table("messages").select("*").eq("chat_id", chat["id"]).order(
            "created_at", desc=True
        ).limit(1).execute()

        last_msg = last.data[0] if last.data else None

        unread = get_unread_count(chat["id"], user_id)

        result.append({
            "chat_id": chat["id"],
            "type": "chat",
            "other_user": {
                "id": other["id"],
                "username": other.get("username"),
                "first_name": other.get("first_name"),
                "last_name": other.get("last_name"),
                "verified": other.get("verified", False),
                "role": other.get("role", "user"),
                "avatar_url": other.get("avatar_url"),
                "is_online": is_online(other),
                "last_seen": other.get("last_seen"),
            },
            "last_message": last_msg["text"] if last_msg else None,
            "last_message_type": last_msg["type"] if last_msg else "text",
            "last_message_at": last_msg["created_at"] if last_msg else chat["created_at"],
            "unread_count": unread,
        })

    result.sort(key=lambda x: x["last_message_at"] or "", reverse=True)
    return result


def get_chat_by_id(chat_id: int) -> dict:
    res = supabase.table("chats").select("*").eq("id", chat_id).execute()
    return res.data[0] if res.data else None


# ============ СООБЩЕНИЯ ЧАТОВ ============

def save_message(chat_id: int, sender_id: int, text: str, msg_type: str = "text",
                 attachment_url: str = None, attachment_size: int = None,
                 attachment_name: str = None) -> dict:
    data = {
        "chat_id": chat_id,
        "sender_id": sender_id,
        "text": text or "",
        "type": msg_type,
    }
    if attachment_url:
        data["attachment_url"] = attachment_url
    if attachment_size:
        data["attachment_size"] = attachment_size
    if attachment_name:
        data["attachment_name"] = attachment_name
    res = supabase.table("messages").insert(data).execute()
    return res.data[0] if res.data else None


def get_messages(chat_id: int, limit: int = 50, offset: int = 0) -> list:
    res = supabase.table("messages").select("*").eq("chat_id", chat_id).order(
        "created_at", desc=False
    ).range(offset, offset + limit - 1).execute()
    return res.data or []


def search_messages(user_id: int, query: str, limit: int = 50) -> list:
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
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).isoformat()
    supabase.table("pending_codes").delete().eq("email", email.lower()).execute()
    supabase.table("pending_codes").insert({
        "email": email.lower(),
        "code": str(code),
        "expires_at": expires_at,
        "sent": False,
    }).execute()


def verify_code(email: str, code: str) -> bool:
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

    supabase.table("pending_codes").delete().eq("email", email.lower()).execute()
    return True


# ============ КАНАЛЫ ============

def create_channel(owner_id: int, name: str, username: str = None, description: str = None, ch_type: str = "public") -> dict:
    data = {
        "owner_id": owner_id,
        "name": name,
        "username": username.lower() if username else None,
        "description": description,
        "type": ch_type,
    }
    res = supabase.table("channels").insert(data).execute()
    return res.data[0] if res.data else None


def get_channel_by_id(channel_id: int) -> dict:
    res = supabase.table("channels").select("*").eq("id", channel_id).execute()
    return res.data[0] if res.data else None


def get_channel_by_username(username: str) -> dict:
    res = supabase.table("channels").select("*").eq("username", username.lower()).execute()
    return res.data[0] if res.data else None


def count_user_channels(user_id: int) -> int:
    res = supabase.table("channels").select("id").eq("owner_id", user_id).execute()
    return len(res.data or [])


def get_user_channels(user_id: int) -> list:
    """Каналы, на которые подписан юзер ИЛИ которые он создал"""
    subs = supabase.table("channel_subscribers").select("channel_id").eq("user_id", user_id).execute()
    sub_ids = [s["channel_id"] for s in (subs.data or [])]

    owned = supabase.table("channels").select("id").eq("owner_id", user_id).execute()
    owned_ids = [c["id"] for c in (owned.data or [])]

    all_ids = list(set(sub_ids + owned_ids))
    if not all_ids:
        return []

    res = supabase.table("channels").select("*").in_("id", all_ids).execute()
    channels = res.data or []

    result = []
    for ch in channels:
        owner = get_user_by_id(ch["owner_id"])
        subs_count = supabase.table("channel_subscribers").select("id").eq("channel_id", ch["id"]).execute()
        last = supabase.table("channel_messages").select("*").eq("channel_id", ch["id"]).order(
            "created_at", desc=True
        ).limit(1).execute()
        last_msg = last.data[0] if last.data else None

        result.append({
            "channel_id": ch["id"],
            "type": "channel",
            "channel_type": ch["type"],
            "name": ch["name"],
            "username": ch.get("username"),
            "description": ch.get("description"),
            "is_owner": ch["owner_id"] == user_id,
            "owner": {
                "id": owner["id"] if owner else None,
                "first_name": owner.get("first_name") if owner else None,
                "username": owner.get("username") if owner else None,
                "verified": owner.get("verified", False) if owner else False,
                "role": owner.get("role", "user") if owner else "user",
                "avatar_url": owner.get("avatar_url") if owner else None,
            } if owner else None,
            "subscribers_count": len(subs_count.data or []),
            "last_message": last_msg["text"] if last_msg else None,
            "last_message_at": last_msg["created_at"] if last_msg else ch["created_at"],
        })

    result.sort(key=lambda x: x["last_message_at"] or "", reverse=True)
    return result


def search_public_channels(query: str, limit: int = 20) -> list:
    query = query.lower().lstrip("@").replace("%", "").replace("_", " ").strip()
    if not query:
        return []
    res = supabase.table("channels").select("*").eq("type", "public").ilike(
        "username", f"%{query}%"
    ).limit(limit).execute()
    return res.data or []


def subscribe_to_channel(channel_id: int, user_id: int) -> bool:
    try:
        supabase.table("channel_subscribers").insert({
            "channel_id": channel_id,
            "user_id": user_id,
        }).execute()
        return True
    except Exception:
        return False


def unsubscribe_from_channel(channel_id: int, user_id: int) -> bool:
    supabase.table("channel_subscribers").delete().eq("channel_id", channel_id).eq(
        "user_id", user_id
    ).execute()
    return True


def is_subscribed(channel_id: int, user_id: int) -> bool:
    res = supabase.table("channel_subscribers").select("id").eq("channel_id", channel_id).eq(
        "user_id", user_id
    ).execute()
    return len(res.data) > 0


def get_channel_subscribers(channel_id: int, limit: int = 100) -> list:
    res = supabase.table("channel_subscribers").select("user_id").eq(
        "channel_id", channel_id
    ).limit(limit).execute()
    user_ids = [s["user_id"] for s in (res.data or [])]
    if not user_ids:
        return []
    users_res = supabase.table("users").select(
        "id, username, first_name, last_name, verified, role, avatar_url"
    ).in_("id", user_ids).execute()
    return users_res.data or []


def update_channel(channel_id: int, name: str = None, description: str = None) -> dict:
    data = {}
    if name is not None:
        data["name"] = name
    if description is not None:
        data["description"] = description
    if not data:
        return None
    res = supabase.table("channels").update(data).eq("id", channel_id).execute()
    return res.data[0] if res.data else None


def delete_channel(channel_id: int) -> None:
    supabase.table("channels").delete().eq("id", channel_id).execute()


def get_secret_channels() -> list:
    res = supabase.table("channels").select("*").eq("type", "secret").execute()
    return res.data or []


def subscribe_new_user_to_secret_channels(user_id: int) -> int:
    """Автоматически подписывает нового юзера на все секретные каналы"""
    secret = get_secret_channels()
    count = 0
    for ch in secret:
        if subscribe_to_channel(ch["id"], user_id):
            count += 1
    return count


# ============ СООБЩЕНИЯ КАНАЛОВ ============

def save_channel_message(channel_id: int, sender_id: int, text: str) -> dict:
    res = supabase.table("channel_messages").insert({
        "channel_id": channel_id,
        "sender_id": sender_id,
        "text": text,
    }).execute()
    return res.data[0] if res.data else None


def get_channel_messages(channel_id: int, limit: int = 50, offset: int = 0) -> list:
    res = supabase.table("channel_messages").select("*").eq("channel_id", channel_id).order(
        "created_at", desc=False
    ).range(offset, offset + limit - 1).execute()
    return res.data or []


def delete_channel_message(message_id: int) -> None:
    supabase.table("channel_messages").delete().eq("id", message_id).execute()


# ============ АДМИНКА ============

def get_all_users_admin(limit: int = 100, offset: int = 0) -> list:
    res = supabase.table("users").select(
        "id, email, username, first_name, last_name, role, verified, created_at, last_seen, avatar_url"
    ).order("id", desc=False).range(offset, offset + limit - 1).execute()
    return res.data or []


def update_user_role(target_id: int, role: str) -> dict:
    res = supabase.table("users").update({"role": role}).eq("id", target_id).execute()
    return res.data[0] if res.data else None


def update_user_verified(target_id: int, verified: bool) -> dict:
    res = supabase.table("users").update({"verified": verified}).eq("id", target_id).execute()
    return res.data[0] if res.data else None
