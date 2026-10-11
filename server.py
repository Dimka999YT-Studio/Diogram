import os
import jwt
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from db import (
    email_exists, create_user, get_user_by_email,
    get_user_by_id, get_user_by_username, search_users,
    update_user_profile, update_user_avatar, update_user_bio,
    get_or_create_chat, get_user_chats, get_chat_by_id,
    save_message, get_messages, search_messages,
    save_pending_code, verify_code, check_password,
    update_last_seen, mark_messages_read,
    count_photo_uploads_last_15_days, register_photo_upload,
    create_channel, get_channel_by_id, get_channel_by_username,
    count_user_channels, get_user_channels, search_public_channels,
    subscribe_to_channel, unsubscribe_from_channel, is_subscribed,
    get_channel_subscribers, update_channel, delete_channel,
    save_channel_message, get_channel_messages, delete_channel_message,
    get_all_users_admin, update_user_role, update_user_verified,
    subscribe_new_user_to_secret_channels, supabase
)
from email_service import generate_code

JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
JWT_ALGO = "HS256"
JWT_EXP_DAYS = 30

WORKER_KEY = os.environ.get("WORKER_KEY", "").strip()

ALLOWED_DOMAINS = ["gmail.com", "yandex.com", "yandex.ru", "mail.ru", "outlook.com", "list.ru", "bk.ru", "inbox.ru"]
BLOCKED_KEYWORDS = ["temp", "10minute", "guerrilla", "throwaway", "yopmail", "mailinator"]

PHOTO_LIMIT_15_DAYS = 8

app = Flask(__name__, static_folder=".")
CORS(app)

typing_store = defaultdict(dict)


# ==================== HELPERS ====================

def make_token(user_id):
    payload = {
        "user_id": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXP_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def get_current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return get_user_by_id(payload["user_id"])
    except Exception:
        return None


def is_valid_email(email):
    email = email.lower().strip()
    if "@" not in email:
        return False, "Неверный email"
    domain = email.split("@")[-1]
    if domain not in ALLOWED_DOMAINS:
        return False, "Разрешены только: " + ", ".join("@" + d for d in ALLOWED_DOMAINS)
    for bad in BLOCKED_KEYWORDS:
        if bad in domain:
            return False, "Временные почты запрещены"
    return True, ""


def user_to_dict(u):
    from db import is_online
    return {
        "id": u["id"],
        "email": u.get("email"),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "last_name": u.get("last_name"),
        "role": u.get("role", "user"),
        "verified": u.get("verified", False),
        "profile_completed": u.get("profile_completed", False),
        "avatar_url": u.get("avatar_url"),
        "bio": u.get("bio"),
        "last_seen": u.get("last_seen"),
        "is_online": is_online(u),
    }


# ==================== STATIC ====================

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ==================== AUTH ====================

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    email = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email и пароль обязательны"}), 400

    ok, msg = is_valid_email(email)
    if not ok:
        return jsonify({"error": msg}), 400

    if len(password) < 6:
        return jsonify({"error": "Пароль минимум 6 символов"}), 400

    if email_exists(email):
        return jsonify({"error": "Этот email уже зарегистрирован"}), 400

    code = generate_code()
    save_pending_code(email, code, expires_minutes=15)
    return jsonify({"ok": True, "message": "Код создан, ждёт отправки"})


@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json() or {}
    email = (data.get("email") or "").lower().strip()
    code = str(data.get("code") or "").strip()
    password = data.get("password") or ""

    if not email or not code or not password:
        return jsonify({"error": "Заполни все поля"}), 400

    if not verify_code(email, code):
        return jsonify({"error": "Неверный или истёкший код"}), 400

    if email_exists(email):
        return jsonify({"error": "Этот email уже зарегистрирован"}), 400

    user = create_user(email=email, password=password)
    if not user:
        return jsonify({"error": "Ошибка создания аккаунта"}), 500

    subs_count = subscribe_new_user_to_secret_channels(user["id"])

    token = make_token(user["id"])
    return jsonify({
        "ok": True,
        "token": token,
        "user": user_to_dict(user),
        "auto_subscribed_channels": subs_count,
    })


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    email = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""

    user = get_user_by_email(email)
    if not user or not check_password(password, user["password_hash"]):
        return jsonify({"error": "Неверный email или пароль"}), 401

    update_last_seen(user["id"])
    token = make_token(user["id"])
    return jsonify({"ok": True, "token": token, "user": user_to_dict(user)})


@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401
    update_last_seen(user["id"])
    return jsonify({"user": user_to_dict(user)})


@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401
    update_last_seen(user["id"])
    return jsonify({"ok": True})


# ==================== PROFILE ====================

@app.route("/api/profile", methods=["POST"])
def api_profile():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    data = request.get_json() or {}
    username = (data.get("username") or "").lower().strip().lstrip("@")
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip() or None

    if not username or not first_name:
        return jsonify({"error": "Username и имя обязательны"}), 400

    if len(username) < 3 or len(username) > 32:
        return jsonify({"error": "Username от 3 до 32 символов"}), 400

    if not username.replace("_", "").isalnum():
        return jsonify({"error": "Username: только буквы, цифры, _"}), 400

    existing = get_user_by_username(username)
    if existing and existing["id"] != user["id"]:
        return jsonify({"error": "Этот @username занят"}), 400

    updated = update_user_profile(user["id"], username, first_name, last_name)
    return jsonify({"ok": True, "user": user_to_dict(updated)})


@app.route("/api/profile/bio", methods=["POST"])
def api_profile_bio():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    data = request.get_json() or {}
    bio = data.get("bio")
    birthday = data.get("birthday")

    updated = update_user_bio(user["id"], bio, birthday)
    if not updated:
        return jsonify({"error": "Нечего обновлять"}), 400

    return jsonify({"ok": True, "user": user_to_dict(updated)})


# ==================== USERS ====================

@app.route("/api/users/search")
def api_users_search():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"users": []})

    users = search_users(q, user["id"], limit=20)
    return jsonify({"users": [user_to_dict(u) for u in users]})


@app.route("/api/users/<int:user_id>")
def api_user_profile(user_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    target = get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "Юзер не найден"}), 404

    return jsonify({"user": user_to_dict(target)})


# ==================== CHATS ====================

@app.route("/api/chats")
def api_chats():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    if not user.get("profile_completed"):
        return jsonify({"error": "Заполни профиль"}), 403

    update_last_seen(user["id"])
    chats = get_user_chats(user["id"])
    return jsonify({"chats": chats})


@app.route("/api/all_list")
def api_all_list():
    """Общий список: чаты + каналы"""
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    if not user.get("profile_completed"):
        return jsonify({"error": "Заполни профиль"}), 403

    update_last_seen(user["id"])
    chats = get_user_chats(user["id"])
    channels = get_user_channels(user["id"])

    items = chats + channels
    items.sort(key=lambda x: x["last_message_at"] or "", reverse=True)
    return jsonify({"items": items})


@app.route("/api/chats", methods=["POST"])
def api_chats_create():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    data = request.get_json() or {}
    other_id = data.get("user_id")
    if not other_id:
        return jsonify({"error": "user_id обязателен"}), 400

    if other_id == user["id"]:
        return jsonify({"error": "Нельзя чатиться с самим собой"}), 400

    other = get_user_by_id(other_id)
    if not other:
        return jsonify({"error": "Юзер не найден"}), 404

    chat = get_or_create_chat(user["id"], other_id)
    return jsonify({"ok": True, "chat_id": chat["id"]})


@app.route("/api/chats/<int:chat_id>/messages")
def api_chat_messages(chat_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    chat = get_chat_by_id(chat_id)
    if not chat:
        return jsonify({"error": "Чат не найден"}), 404

    if user["id"] not in (chat["user1_id"], chat["user2_id"]):
        return jsonify({"error": "Нет доступа"}), 403

    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    messages = get_messages(chat_id, limit, offset)

    mark_messages_read(chat_id, user["id"])
    update_last_seen(user["id"])

    other_id = chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"]
    other = get_user_by_id(other_id)

    return jsonify({
        "messages": messages,
        "other_user": user_to_dict(other) if other else None,
    })


@app.route("/api/chats/<int:chat_id>/read", methods=["POST"])
def api_chat_read(chat_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    chat = get_chat_by_id(chat_id)
    if not chat or user["id"] not in (chat["user1_id"], chat["user2_id"]):
        return jsonify({"error": "Нет доступа"}), 403

    mark_messages_read(chat_id, user["id"])
    return jsonify({"ok": True})


@app.route("/api/send", methods=["POST"])
def api_send():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    data = request.get_json() or {}
    chat_id = data.get("chat_id")
    text = (data.get("text") or "").strip()
    msg_type = data.get("type", "text")
    attachment_url = data.get("attachment_url")
    attachment_size = data.get("attachment_size")
    attachment_name = data.get("attachment_name")

    if not chat_id:
        return jsonify({"error": "chat_id обязателен"}), 400

    if msg_type == "text" and not text:
        return jsonify({"error": "Текст обязателен"}), 400

    if msg_type == "photo" and not attachment_url:
        return jsonify({"error": "URL фото обязателен"}), 400

    chat = get_chat_by_id(chat_id)
    if not chat or user["id"] not in (chat["user1_id"], chat["user2_id"]):
        return jsonify({"error": "Нет доступа"}), 403

    msg = save_message(
        chat_id, user["id"], text, msg_type,
        attachment_url, attachment_size, attachment_name
    )
    update_last_seen(user["id"])
    return jsonify({"ok": True, "message": msg})


@app.route("/api/messages/search")
def api_messages_search():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"messages": []})

    messages = search_messages(user["id"], q)
    return jsonify({"messages": messages})


@app.route("/api/poll")
def api_poll():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    chat_id = request.args.get("chat_id")
    after = request.args.get("after", "1970-01-01T00:00:00+00:00")

    if not chat_id:
        return jsonify({"messages": []})

    chat = get_chat_by_id(int(chat_id))
    if not chat or user["id"] not in (chat["user1_id"], chat["user2_id"]):
        return jsonify({"error": "Нет доступа"}), 403

    update_last_seen(user["id"])

    res = supabase.table("messages").select("*").eq("chat_id", int(chat_id)).gt(
        "created_at", after
    ).order("created_at").execute()

    other_id = chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"]
    now = time.time()
    typing_users = [
        uid for uid, ts in typing_store.get(int(chat_id), {}).items()
        if uid == other_id and now - ts < 5
    ]

    return jsonify({
        "messages": res.data or [],
        "typing": len(typing_users) > 0,
    })


# ==================== TYPING ====================

@app.route("/api/typing", methods=["POST"])
def api_typing():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    data = request.get_json() or {}
    chat_id = data.get("chat_id")
    if not chat_id:
        return jsonify({"error": "chat_id обязателен"}), 400

    typing_store[int(chat_id)][user["id"]] = time.time()
    return jsonify({"ok": True})


# ==================== CHANNELS ====================

@app.route("/api/channels", methods=["POST"])
def api_channels_create():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    if count_user_channels(user["id"]) >= 4:
        return jsonify({"error": "Максимум 4 канала на аккаунт"}), 400

    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    username = (data.get("username") or "").lower().strip().lstrip("@")
    description = (data.get("description") or "").strip() or None
    ch_type = data.get("type", "public")

    if not name:
        return jsonify({"error": "Название обязательно"}), 400

    if ch_type == "secret":
        if user.get("role") not in ("owner", "developer"):
            return jsonify({"error": "Только разработчики могут создавать секретные каналы"}), 403
        username = None
    else:
        ch_type = "public"
        if not username:
            return jsonify({"error": "Для публичного канала нужен @username"}), 400
        if len(username) < 3 or len(username) > 32:
            return jsonify({"error": "Username от 3 до 32 символов"}), 400
        if not username.replace("_", "").isalnum():
            return jsonify({"error": "Username: только буквы, цифры, _"}), 400
        existing = get_channel_by_username(username)
        if existing:
            return jsonify({"error": "Этот @username канала занят"}), 400

    ch = create_channel(user["id"], name, username, description, ch_type)
    if not ch:
        return jsonify({"error": "Ошибка создания"}), 500

    if ch_type == "secret":
        from db import get_all_users_admin
        all_users = get_all_users_admin(limit=10000)
        for u in all_users:
            subscribe_to_channel(ch["id"], u["id"])
    else:
        subscribe_to_channel(ch["id"], user["id"])

    return jsonify({"ok": True, "channel": ch})


@app.route("/api/channels/search")
def api_channels_search():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"channels": []})

    channels = search_public_channels(q, limit=20)
    result = []
    for ch in channels:
        owner = get_user_by_id(ch["owner_id"])
        subs = supabase.table("channel_subscribers").select("id").eq("channel_id", ch["id"]).execute()
        result.append({
            "id": ch["id"],
            "name": ch["name"],
            "username": ch.get("username"),
            "description": ch.get("description"),
            "subscribers_count": len(subs.data or []),
            "is_subscribed": is_subscribed(ch["id"], user["id"]),
            "owner_name": owner.get("first_name") if owner else None,
        })
    return jsonify({"channels": result})


@app.route("/api/channels/<int:channel_id>/subscribe", methods=["POST"])
def api_channels_subscribe(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch:
        return jsonify({"error": "Канал не найден"}), 404

    if ch["type"] == "secret":
        return jsonify({"error": "На секретный канал нельзя подписаться вручную"}), 403

    subscribe_to_channel(channel_id, user["id"])
    return jsonify({"ok": True})


@app.route("/api/channels/<int:channel_id>/unsubscribe", methods=["POST"])
def api_channels_unsubscribe(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch:
        return jsonify({"error": "Канал не найден"}), 404

    if ch["type"] == "secret":
        return jsonify({"error": "От секретного канала нельзя отписаться"}), 403

    unsubscribe_from_channel(channel_id, user["id"])
    return jsonify({"ok": True})


@app.route("/api/channels/<int:channel_id>/messages")
def api_channel_messages(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch:
        return jsonify({"error": "Канал не найден"}), 404

    if not is_subscribed(channel_id, user["id"]) and ch["owner_id"] != user["id"]:
        return jsonify({"error": "Ты не подписан"}), 403

    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    messages = get_channel_messages(channel_id, limit, offset)

    subs = supabase.table("channel_subscribers").select("id").eq("channel_id", channel_id).execute()
    owner = get_user_by_id(ch["owner_id"])

    return jsonify({
        "messages": messages,
        "channel": {
            "id": ch["id"],
            "name": ch["name"],
            "username": ch.get("username"),
            "description": ch.get("description"),
            "type": ch["type"],
            "subscribers_count": len(subs.data or []),
            "is_owner": ch["owner_id"] == user["id"],
            "owner": user_to_dict(owner) if owner else None,
        },
    })


@app.route("/api/channels/<int:channel_id>/send", methods=["POST"])
def api_channel_send(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch:
        return jsonify({"error": "Канал не найден"}), 404

    if ch["owner_id"] != user["id"]:
        return jsonify({"error": "Только владелец может писать"}), 403

    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Текст обязателен"}), 400

    msg = save_channel_message(channel_id, user["id"], text)
    return jsonify({"ok": True, "message": msg})


@app.route("/api/channels/<int:channel_id>/edit", methods=["POST"])
def api_channel_edit(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]:
        return jsonify({"error": "Нет доступа"}), 403

    data = request.get_json() or {}
    name = data.get("name")
    description = data.get("description")
    updated = update_channel(channel_id, name, description)
    return jsonify({"ok": True, "channel": updated})


@app.route("/api/channels/<int:channel_id>", methods=["DELETE"])
def api_channel_delete(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]:
        return jsonify({"error": "Нет доступа"}), 403

    delete_channel(channel_id)
    return jsonify({"ok": True})


@app.route("/api/channels/<int:channel_id>/subscribers")
def api_channel_subscribers(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]:
        return jsonify({"error": "Только владелец видит список"}), 403

    subs = get_channel_subscribers(channel_id)
    return jsonify({"subscribers": [user_to_dict(u) for u in subs]})


@app.route("/api/channels/<int:channel_id>/messages/<int:msg_id>", methods=["DELETE"])
def api_channel_msg_delete(channel_id, msg_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]:
        return jsonify({"error": "Нет доступа"}), 403

    delete_channel_message(msg_id)
    return jsonify({"ok": True})


# ==================== ADMIN ====================

def require_admin():
    user = get_current_user()
    if not user:
        return None
    if user.get("role") not in ("owner", "developer"):
        return None
    return user


@app.route("/api/admin/stats")
def api_admin_stats():
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Нет доступа"}), 403

    users_res = supabase.table("users").select("id", count="exact").execute()
    channels_res = supabase.table("channels").select("id", count="exact").execute()
    msgs_res = supabase.table("messages").select("id", count="exact").execute()

    return jsonify({
        "total_users": users_res.count or 0,
        "total_channels": channels_res.count or 0,
        "total_messages": msgs_res.count or 0,
    })


@app.route("/api/admin/users")
def api_admin_users():
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Нет доступа"}), 403

    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    users = get_all_users_admin(limit, offset)
    return jsonify({"users": [user_to_dict(u) for u in users], "me": user_to_dict(admin)})


@app.route("/api/admin/set_role", methods=["POST"])
def api_admin_set_role():
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Нет доступа"}), 403
    if admin.get("role") != "owner":
        return jsonify({"error": "Только владелец может менять роли"}), 403

    data = request.get_json() or {}
    target_id = data.get("user_id")
    role = data.get("role")

    if role not in ("user", "moderator", "developer"):
        return jsonify({"error": "Неверная роль"}), 400
    if target_id == admin["id"]:
        return jsonify({"error": "Нельзя менять свою роль"}), 400

    updated = update_user_role(target_id, role)
    if not updated:
        return jsonify({"error": "Юзер не найден"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/set_verified", methods=["POST"])
def api_admin_set_verified():
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Нет доступа"}), 403
    if admin.get("role") != "owner":
        return jsonify({"error": "Только владелец"}), 403

    data = request.get_json() or {}
    target_id = data.get("user_id")
    verified = bool(data.get("verified"))

    updated = update_user_verified(target_id, verified)
    if not updated:
        return jsonify({"error": "Юзер не найден"}), 404
    return jsonify({"ok": True})


# ==================== WORKER ====================

@app.route("/api/worker/get_pending")
def worker_get_pending():
    if request.headers.get("X-Worker-Key") != WORKER_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    res = supabase.table("pending_codes").select("*").eq("sent", False).order("created_at").limit(1).execute()
    if not res.data:
        return jsonify({"email": None, "code": None})

    task = res.data[0]
    return jsonify({"email": task["email"], "code": task["code"]})


@app.route("/api/worker/task_done", methods=["POST"])
def worker_task_done():
    if request.headers.get("X-Worker-Key") != WORKER_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json() or {}
    email = data.get("email")
    if email:
        supabase.table("pending_codes").update({"sent": True}).eq("email", email.lower()).execute()
    return jsonify({"ok": True})


# ==================== RUN ====================

port = int(os.environ.get("PORT", 8080))
print("Server on " + str(port))
app.run(host="0.0.0.0", port=port, debug=False)
