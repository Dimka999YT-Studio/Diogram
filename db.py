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
    res = supabase.table("users").select("id").ilike("username", username).execute()
    return len(res.data) > 0


def create_user(email: str, password: str, username: str = None, first_name: str = None, last_name: str = None) -> dict:
    data = {
        "email": email.lower(),
        "password_hash": hash_password(password),
        "username": username if username else None,
        "first_name": first_name,
        "last_name": last_name,
        "role": "user",
        "verified": False,
        "profile_completed": False,
        "banned": False,
        "balance": 0,
        "premium": False,
        "file_plus": False,
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
    res = supabase.table("users").select("*").ilike("username", username).execute()
    return res.data[0] if res.data else None


def search_users(query: str, current_user_id: int, limit: int = 20) -> list:
    query = query.lower().lstrip("@")
    query = query.replace("%", "").replace("*", "").replace("\\", "").replace("_", " ").strip()[:64]
    if not query:
        return []
    # Важно: НЕ собираем строку для .or_() из пользовательского ввода — запятая/скобки в ней
    # позволяли бы подмешивать произвольные условия (угадывание email, баланса и т.д.).
    # Каждый .ilike() передаёт значение как один параметр, поэтому инъекция невозможна.
    pattern = f"%{query}%"
    found = {}
    for column in ("username", "first_name"):
        res = supabase.table("users").select(
            "id, username, first_name, last_name, verified, role, avatar_url"
        ).ilike(column, pattern).neq("id", current_user_id).eq("banned", False).limit(limit).execute()
        for row in (res.data or []):
            found.setdefault(row["id"], row)
    return list(found.values())[:limit]


def update_user_profile(user_id: int, username: str, first_name: str, last_name: str = None) -> dict:
    data = {
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "profile_completed": True,
    }
    res = supabase.table("users").update(data).eq("id", user_id).execute()
    return res.data[0] if res.data else None


def update_user_name(user_id: int, first_name: str, last_name: str = None) -> dict:
    data = {"first_name": first_name, "last_name": last_name}
    res = supabase.table("users").update(data).eq("id", user_id).execute()
    return res.data[0] if res.data else None


def update_user_username(user_id: int, username: str) -> dict:
    res = supabase.table("users").update({"username": username}).eq("id", user_id).execute()
    return res.data[0] if res.data else None


def update_user_avatar(user_id: int, avatar_url: str) -> dict:
    data = {
        "avatar_url": avatar_url,
        "avatar_changed_at": datetime.now(timezone.utc).isoformat()
    }
    res = supabase.table("users").update(data).eq("id", user_id).execute()
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


# ============ ЭКОНОМИКА: БАЛАНС И ТРАНЗАКЦИИ ============

def get_balance(user_id: int) -> int:
    user = get_user_by_id(user_id)
    return user.get("balance", 0) if user else 0


def spend_balance(user_id: int, amount: int, description: str) -> tuple:
    """Атомарно списывает баланс. Возвращает (success, error)."""
    if amount <= 0:
        return False, "Неверная сумма"
    
    try:
        result = supabase.rpc("spend_balance_atomic", {
            "p_user_id": user_id,
            "p_amount": amount
        }).execute()
        
        if result.data and result.data[0].get("success"):
            supabase.table("transactions").insert({
                "user_id": user_id,
                "amount": -amount,
                "type": "purchase",
                "description": description
            }).execute()
            return True, None
        return False, "Недостаточно средств"
    except Exception as e:
        return False, str(e)


def add_balance(user_id: int, amount: int, description: str, admin_id: int = None) -> bool:
    """Начисляет баланс."""
    if amount <= 0:
        return False
    
    try:
        supabase.rpc("add_balance_atomic", {
            "p_user_id": user_id,
            "p_amount": amount
        }).execute()
        
        supabase.table("transactions").insert({
            "user_id": user_id,
            "amount": amount,
            "type": "deposit",
            "description": description,
            "admin_id": admin_id
        }).execute()
        return True
    except Exception:
        return False


def get_user_transactions(user_id: int, limit: int = 50) -> list:
    res = supabase.table("transactions").select("*").eq("user_id", user_id).order(
        "created_at", desc=True
    ).limit(limit).execute()
    return res.data or []


# ============ ПОДПИСКИ ============

def activate_premium(user_id: int, days: int, cost: int, description: str) -> tuple:
    success, err = spend_balance(user_id, cost, description)
    if not success:
        return False, err
    
    user = get_user_by_id(user_id)
    now = datetime.now(timezone.utc)
    
    current_until = user.get("premium_until")
    if current_until:
        try:
            until_dt = datetime.fromisoformat(current_until.replace("Z", "+00:00"))
            if until_dt > now:
                new_until = until_dt + timedelta(days=days)
            else:
                new_until = now + timedelta(days=days)
        except Exception:
            new_until = now + timedelta(days=days)
    else:
        new_until = now + timedelta(days=days)
    
    supabase.table("users").update({
        "premium": True,
        "premium_until": new_until.isoformat()
    }).eq("id", user_id).execute()
    
    return True, None


def activate_file_plus(user_id: int, days: int, cost: int, description: str) -> tuple:
    success, err = spend_balance(user_id, cost, description)
    if not success:
        return False, err
    
    user = get_user_by_id(user_id)
    now = datetime.now(timezone.utc)
    
    current_until = user.get("file_plus_until")
    if current_until:
        try:
            until_dt = datetime.fromisoformat(current_until.replace("Z", "+00:00"))
            if until_dt > now:
                new_until = until_dt + timedelta(days=days)
            else:
                new_until = now + timedelta(days=days)
        except Exception:
            new_until = now + timedelta(days=days)
    else:
        new_until = now + timedelta(days=days)
    
    supabase.table("users").update({
        "file_plus": True,
        "file_plus_until": new_until.isoformat()
    }).eq("id", user_id).execute()
    
    return True, None


def add_file_quota(user_id: int, bytes_amount: int) -> bool:
    try:
        user = get_user_by_id(user_id)
        current = user.get("file_quota_bytes", 0) or 0
        supabase.table("users").update({
            "file_quota_bytes": current + bytes_amount
        }).eq("id", user_id).execute()
        return True
    except Exception:
        return False


def check_premium(user: dict) -> bool:
    if not user or not user.get("premium"):
        return False
    until = user.get("premium_until")
    if not until:
        return False
    try:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return until_dt > datetime.now(timezone.utc)
    except Exception:
        return False


def check_file_plus(user: dict) -> bool:
    if not user or not user.get("file_plus"):
        return False
    until = user.get("file_plus_until")
    if not until:
        return False
    try:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return until_dt > datetime.now(timezone.utc)
    except Exception:
        return False


# ============ ЛИМИТЫ ФОТО И АВАТАРОК ============

def can_change_avatar(user_id: int) -> tuple:
    user = get_user_by_id(user_id)
    if not user:
        return False, None
    if check_premium(user):
        return True, None
    
    last = user.get("avatar_changed_at")
    if not last:
        return True, None
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        next_dt = last_dt + timedelta(days=7)
        if datetime.now(timezone.utc) >= next_dt:
            return True, None
        return False, next_dt
    except Exception:
        return True, None


def can_upload_photo(user_id: int) -> tuple:
    user = get_user_by_id(user_id)
    if not user:
        return False, None
    if check_premium(user):
        return True, None
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    res = supabase.table("photo_uploads").select("*").eq("user_id", user_id).gt(
        "uploaded_at", cutoff
    ).order("uploaded_at", desc=True).limit(1).execute()
    
    if not res.data:
        return True, None
    
    last = res.data[0]
    try:
        last_dt = datetime.fromisoformat(last["uploaded_at"].replace("Z", "+00:00"))
        next_dt = last_dt + timedelta(days=7)
        return False, next_dt
    except Exception:
        return True, None


def count_photo_uploads_last_15_days(user_id: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    res = supabase.table("photo_uploads").select("id").eq("user_id", user_id).gt(
        "uploaded_at", cutoff
    ).execute()
    return len(res.data or [])


def register_photo_upload(user_id: int) -> None:
    supabase.table("photo_uploads").insert({"user_id": user_id}).execute()


# ============ ФАЙЛЫ ============

def get_file_quota_used(user_id: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    res = supabase.table("file_uploads").select("size_bytes").eq("user_id", user_id).gt(
        "uploaded_at", cutoff
    ).execute()
    return sum(row["size_bytes"] for row in (res.data or []))


def register_file_upload(user_id: int, size_bytes: int) -> None:
    supabase.table("file_uploads").insert({
        "user_id": user_id,
        "size_bytes": size_bytes
    }).execute()


# ============ СМЕНЫ USERNAME ============

def count_username_changes_24h(user_id: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    res = supabase.table("username_changes").select("id").eq("user_id", user_id).gt(
        "changed_at", cutoff
    ).execute()
    return len(res.data or [])


def register_username_change(user_id: int) -> None:
    supabase.table("username_changes").insert({"user_id": user_id}).execute()


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
                "banned": other.get("banned", False),
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
                 attachment_name: str = None, file_type: str = None,
                 file_mime: str = None) -> dict:
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
    if file_type:
        data["file_type"] = file_type
    if attachment_size:
        data["file_size"] = attachment_size
    if attachment_name:
        data["file_name"] = attachment_name
    if file_mime:
        data["file_mime"] = file_mime
    res = supabase.table("messages").insert(data).execute()
    return res.data[0] if res.data else None


def get_messages(chat_id: int, limit: int = 50, offset: int = 0) -> list:
    res = supabase.table("messages").select("*").eq("chat_id", chat_id).order(
        "created_at", desc=False
    ).range(offset, offset + limit - 1).execute()
    return res.data or []


def get_message_by_id(message_id: int) -> dict:
    res = supabase.table("messages").select("*").eq("id", message_id).execute()
    return res.data[0] if res.data else None


def delete_message(message_id: int) -> None:
    supabase.table("messages").delete().eq("id", message_id).execute()


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
        "username": username if username else None,
        "description": description,
        "type": ch_type,
    }
    res = supabase.table("channels").insert(data).execute()
    return res.data[0] if res.data else None


def get_channel_by_id(channel_id: int) -> dict:
    res = supabase.table("channels").select("*").eq("id", channel_id).execute()
    return res.data[0] if res.data else None


def get_channel_by_username(username: str) -> dict:
    res = supabase.table("channels").select("*").ilike("username", username).execute()
    return res.data[0] if res.data else None


def count_user_channels(user_id: int) -> int:
    res = supabase.table("channels").select("id").eq("owner_id", user_id).execute()
    return len(res.data or [])


def get_user_channels(user_id: int) -> list:
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
            "avatar_url": ch.get("avatar_url"),
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


def update_channel_full(channel_id: int, name: str = None, username: str = None,
                        ch_type: str = None, description: str = None) -> dict:
    data = {}
    if name is not None:
        data["name"] = name.strip()
    if username is not None:
        data["username"] = username.strip() if username.strip() else None
    if ch_type is not None:
        data["type"] = ch_type
    if description is not None:
        data["description"] = description.strip() or None
    if not data:
        return None
    res = supabase.table("channels").update(data).eq("id", channel_id).execute()
    return res.data[0] if res.data else None


def update_channel_avatar(channel_id: int, avatar_url: str) -> dict:
    res = supabase.table("channels").update({"avatar_url": avatar_url}).eq("id", channel_id).execute()
    return res.data[0] if res.data else None


def delete_channel(channel_id: int) -> None:
    supabase.table("channels").delete().eq("id", channel_id).execute()


def get_secret_channels() -> list:
    res = supabase.table("channels").select("*").eq("type", "secret").execute()
    return res.data or []


def subscribe_new_user_to_secret_channels(user_id: int) -> int:
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


def delete_channel_message(message_id: int, channel_id: int) -> bool:
    """Удаляет сообщение ТОЛЬКО из указанного канала. Раньше фильтра по channel_id не было, и владелец
    любого своего канала мог удалить сообщение в чужом, подставив его id. True — если что-то удалено."""
    res = supabase.table("channel_messages").delete().eq("id", message_id).eq("channel_id", channel_id).execute()
    return bool(res.data)


# ============ АДМИНКА ============

def get_all_users_admin(limit: int = 100, offset: int = 0) -> list:
    res = supabase.table("users").select(
        "id, email, username, first_name, last_name, role, verified, banned, created_at, last_seen, avatar_url, balance, premium, file_plus"
    ).order("id", desc=False).range(offset, offset + limit - 1).execute()
    return res.data or []


def update_user_role(target_id: int, role: str) -> dict:
    res = supabase.table("users").update({"role": role}).eq("id", target_id).execute()
    return res.data[0] if res.data else None


def update_user_verified(target_id: int, verified: bool) -> dict:
    res = supabase.table("users").update({"verified": verified}).eq("id", target_id).execute()
    return res.data[0] if res.data else None


# ============ ЖАЛОБЫ ============

def count_reports_by_user_24h(reporter_id: int) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    res = supabase.table("reports").select("id").eq("reporter_id", reporter_id).gt(
        "created_at", cutoff
    ).execute()
    return len(res.data or [])


def create_report(reporter_id: int, reason: str, comment: str = None,
                  reported_user_id: int = None, reported_message_id: int = None,
                  reported_channel_id: int = None) -> dict:
    data = {
        "reporter_id": reporter_id,
        "reason": reason,
        "comment": comment,
        "status": "new",
    }
    if reported_user_id:
        data["reported_user_id"] = reported_user_id
    if reported_message_id:
        data["reported_message_id"] = reported_message_id
    if reported_channel_id:
        data["reported_channel_id"] = reported_channel_id
    res = supabase.table("reports").insert(data).execute()
    return res.data[0] if res.data else None


def get_reports(status_filter: str = None, limit: int = 100, offset: int = 0) -> list:
    q = supabase.table("reports").select("*").order("created_at", desc=True)
    if status_filter:
        q = q.eq("status", status_filter)
    res = q.range(offset, offset + limit - 1).execute()
    reports = res.data or []

    result = []
    for r in reports:
        reporter = get_user_by_id(r["reporter_id"]) if r.get("reporter_id") else None
        reported_user = get_user_by_id(r["reported_user_id"]) if r.get("reported_user_id") else None

        msg_text = None
        if r.get("reported_message_id"):
            msg = get_message_by_id(r["reported_message_id"])
            if msg:
                msg_text = msg.get("text")

        ch_name = None
        if r.get("reported_channel_id"):
            ch = get_channel_by_id(r["reported_channel_id"])
            if ch:
                ch_name = ch.get("name")

        result.append({
            "id": r["id"],
            "reporter": {
                "id": reporter["id"] if reporter else None,
                "username": reporter.get("username") if reporter else None,
                "first_name": reporter.get("first_name") if reporter else None,
            } if reporter else None,
            "reported_user": {
                "id": reported_user["id"] if reported_user else None,
                "username": reported_user.get("username") if reported_user else None,
                "first_name": reported_user.get("first_name") if reported_user else None,
            } if reported_user else None,
            "reported_message_id": r.get("reported_message_id"),
            "reported_message_text": msg_text,
            "reported_channel_id": r.get("reported_channel_id"),
            "reported_channel_name": ch_name,
            "reason": r["reason"],
            "comment": r.get("comment"),
            "status": r["status"],
            "created_at": r["created_at"],
        })

    return result


def update_report_status(report_id: int, status: str, reviewer_id: int) -> dict:
    res = supabase.table("reports").update({
        "status": status,
        "reviewed_by": reviewer_id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", report_id).execute()
    return res.data[0] if res.data else None


def count_new_reports() -> int:
    res = supabase.table("reports").select("id").eq("status", "new").execute()
    return len(res.data or [])


# ============ БАНЫ ============

def ban_user(target_id: int, reason: str, admin_id: int, until: str = None) -> dict:
    data = {
        "banned": True,
        "ban_reason": reason,
        "banned_at": datetime.now(timezone.utc).isoformat(),
        "banned_by": admin_id,
        "ban_until": until,
    }
    res = supabase.table("users").update(data).eq("id", target_id).execute()
    return res.data[0] if res.data else None


def unban_user(target_id: int) -> dict:
    data = {
        "banned": False,
        "ban_reason": None,
        "banned_at": None,
        "banned_by": None,
        "ban_until": None,
    }
    res = supabase.table("users").update(data).eq("id", target_id).execute()
    return res.data[0] if res.data else None


def is_ban_expired(user: dict) -> bool:
    if not user or not user.get("banned"):
        return False
    until = user.get("ban_until")
    if not until:
        return False
    try:
        until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > until_dt
    except Exception:
        return False


# ============ НАСТРОЙКИ (PRIVACY PLUS) И СЕССИИ ============

def update_privacy_settings(user_id: int, hide_last_seen: bool = None, who_can_message: str = None):
    data = {}
    if hide_last_seen is not None:
        data["hide_last_seen"] = hide_last_seen
    if who_can_message is not None:
        data["who_can_message"] = who_can_message
    if not data:
        return False
    supabase.table("users").update(data).eq("id", user_id).execute()
    return True


def get_user_sessions(user_id: int):
    res = supabase.table("user_sessions").select("*").eq("user_id", user_id).order("last_used", desc=True).execute()
    return res.data or []


def delete_session(session_id: int, user_id: int):
    supabase.table("user_sessions").delete().eq("id", session_id).eq("user_id", user_id).execute()


# ============ FRIGDMANT И ИНВЕНТАРЬ ============

def get_user_usernames(user_id: int) -> list:
    res = supabase.table("owned_usernames").select("*").eq("user_id", user_id).order("purchased_at", desc=True).execute()
    return res.data or []


def activate_username(user_id: int, username: str) -> bool:
    res = supabase.table("owned_usernames").select("*").eq("user_id", user_id).eq("username", username).execute()
    if not res.data:
        return False
    existing = get_user_by_username(username)
    if existing and existing["id"] != user_id:
        return False
    old_user = get_user_by_id(user_id)
    old_name = old_user.get("username")
    
    supabase.table("users").update({"username": username}).eq("id", user_id).execute()
    supabase.table("owned_usernames").delete().eq("user_id", user_id).eq("username", username).execute()
    
    if old_name and not old_name.startswith("user_") and not old_name.startswith("ch_"):
        supabase.table("owned_usernames").insert({"user_id": user_id, "username": old_name}).execute()
    return True


def get_owned_username_holder(username: str):
    """id пользователя, у которого этот юзернейм лежит в коллекции (owned_usernames), либо None.
    Нужен, чтобы купленные юзернеймы нельзя было занять через смену @username."""
    res = supabase.table("owned_usernames").select("user_id, username").ilike("username", username).limit(20).execute()
    for row in (res.data or []):
        if (row.get("username") or "").lower() == username.lower():
            return row["user_id"]
    return None


def is_username_on_frigdmant(username: str) -> dict:
    res = supabase.table("frigdmant_listings").select("*").eq("username", username).eq("status", "active").execute()
    return res.data[0] if res.data else None


def create_frigdmant_listing(seller_id: int, target_type: str, target_id: int, username: str, price: int, listing_type: str) -> dict:
    end_time = None
    if listing_type == "auction":
        end_time = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    data = {
        "seller_id": seller_id,
        "target_type": target_type,
        "target_id": target_id,
        "username": username,
        "price": price,
        "listing_type": listing_type,
        "end_time": end_time
    }
    res = supabase.table("frigdmant_listings").insert(data).execute()
    return res.data[0] if res.data else None


def get_frigdmant_listings() -> list:
    now = datetime.now(timezone.utc).isoformat()
    expired = supabase.table("frigdmant_listings").select("id").eq("status", "active").eq("listing_type", "auction").lt("end_time", now).execute()
    for item in (expired.data or []):
        supabase.rpc("close_auction_atomic", {"p_listing_id": item["id"]}).execute()
    
    res = supabase.table("frigdmant_listings").select("*").eq("status", "active").order("created_at", desc=True).execute()
    listings = res.data or []
    for l in listings:
        seller = get_user_by_id(l["seller_id"])
        l["seller_username"] = seller.get("username") if seller else "unknown"
    return listings


def get_user_listings(user_id: int) -> list:
    res = supabase.table("frigdmant_listings").select("*").eq("seller_id", user_id).eq("status", "active").execute()
    return res.data or []


def cancel_listing(listing_id: int, user_id: int) -> bool:
    res = supabase.table("frigdmant_listings").update({"status": "cancelled"}).eq("id", listing_id).eq("seller_id", user_id).eq("status", "active").execute()
    if res.data:
        supabase.table("owned_usernames").insert({"user_id": user_id, "username": res.data[0]["username"]}).execute()
        return True
    return False


def buy_frigdmant_listing(listing_id: int, buyer_id: int) -> tuple:
    try:
        res = supabase.rpc("buy_username_atomic", {"p_listing_id": listing_id, "p_buyer_id": buyer_id}).execute()
        if res.data and res.data[0]["success"]:
            return True, None
        return False, res.data[0]["error_msg"] if res.data else "Ошибка"
    except Exception as e:
        return False, str(e)


def place_frigdmant_bid(listing_id: int, bidder_id: int, amount: int) -> tuple:
    try:
        res = supabase.rpc("place_bid_atomic", {"p_listing_id": listing_id, "p_bidder_id": bidder_id, "p_amount": amount}).execute()
        if res.data and res.data[0]["success"]:
            return True, None
        return False, res.data[0]["error_msg"] if res.data else "Ошибка"
    except Exception as e:
        return False, str(e)


# ============ ГРУППЫ И КОЛЛЕКЦИИ ============

def create_group_chat(owner_id, name, member_ids):
    res = supabase.table("chats").insert({
        "is_group": True, 
        "name": name, 
        "owner_id": owner_id
    }).execute()
    if not res.data: 
        return None
    chat_id = res.data[0]["id"]
    members = [{"chat_id": chat_id, "user_id": uid} for uid in set(member_ids + [owner_id])]
    supabase.table("chat_members").insert(members).execute()
    return res.data[0]


def get_chat_members(chat_id):
    res = supabase.table("chat_members").select("user_id").eq("chat_id", chat_id).execute()
    return [m["user_id"] for m in (res.data or [])]


def is_chat_member(chat_id, user_id):
    res = supabase.table("chat_members").select("id").eq("chat_id", chat_id).eq("user_id", user_id).execute()
    return len(res.data or []) > 0


def get_username_purchase_info(username):
    res = supabase.table("frigdmant_listings").select("*").eq("username", username).eq("status", "sold").order("created_at", desc=True).limit(1).execute()
    if res.data:
        listing = res.data[0]
        return {"price": listing.get("price", 0), "date": listing.get("updated_at") or listing.get("created_at")}
    return None


def create_group_chat(owner_id, name, member_ids):
    res = supabase.table("chats").insert({
        "is_group": True, 
        "name": name, 
        "owner_id": owner_id
    }).execute()
    if not res.data: return None
    chat_id = res.data[0]["id"]
    members = [{"chat_id": chat_id, "user_id": uid} for uid in set(member_ids + [owner_id])]
    supabase.table("chat_members").insert(members).execute()
    return res.data[0]

def get_chat_members(chat_id):
    res = supabase.table("chat_members").select("user_id").eq("chat_id", chat_id).execute()
    return [m["user_id"] for m in (res.data or [])]

def is_chat_member(chat_id, user_id):
    res = supabase.table("chat_members").select("id").eq("chat_id", chat_id).eq("user_id", user_id).execute()
    return len(res.data or []) > 0

def get_username_purchase_info(username):
    res = supabase.table("frigdmant_listings").select("*").eq("username", username).eq("status", "sold").order("created_at", desc=True).limit(1).execute()
    if res.data:
        listing = res.data[0]
        return {"price": listing.get("price", 0), "date": listing.get("updated_at") or listing.get("created_at")}
    return None
