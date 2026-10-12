import os
import jwt
import time
import io
import re
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from PIL import Image

from db import (
    email_exists, create_user, get_user_by_email,
    get_user_by_id, get_user_by_username, search_users,
    update_user_profile, update_user_name, update_user_username,
    update_user_avatar, update_user_bio,
    count_username_changes_24h, register_username_change,
    get_or_create_chat, get_user_chats, get_chat_by_id,
    save_message, get_messages, search_messages,
    get_message_by_id, delete_message,
    save_pending_code, verify_code, check_password,
    update_last_seen, mark_messages_read,
    count_photo_uploads_last_15_days, register_photo_upload,
    create_channel, get_channel_by_id, get_channel_by_username,
    count_user_channels, get_user_channels, search_public_channels,
    subscribe_to_channel, unsubscribe_from_channel, is_subscribed,
    get_channel_subscribers, update_channel, update_channel_full,
    update_channel_avatar, delete_channel,
    save_channel_message, get_channel_messages, delete_channel_message,
    get_all_users_admin, update_user_role, update_user_verified,
    subscribe_new_user_to_secret_channels,
    count_reports_by_user_24h, create_report, get_reports,
    update_report_status, count_new_reports,
    ban_user, unban_user, is_ban_expired,
    supabase
)
from email_service import generate_code

JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
JWT_ALGO = "HS256"
JWT_EXP_DAYS = 30

WORKER_KEY = os.environ.get("WORKER_KEY", "").strip()

ALLOWED_DOMAINS = ["gmail.com", "yandex.com", "yandex.ru", "mail.ru", "outlook.com", "list.ru", "bk.ru", "inbox.ru"]
BLOCKED_KEYWORDS = ["temp", "10minute", "guerrilla", "throwaway", "yopmail", "mailinator"]

PHOTO_LIMIT_15_DAYS = 8
REPORT_LIMIT_24H = 5
USERNAME_CHANGE_LIMIT_24H = 10

MAX_PHOTO_SIZE = 5 * 1024 * 1024
MAX_DIMENSION = 1600
COMPRESS_THRESHOLD = 4 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

REPORT_REASONS = ["spam", "abuse", "scam", "18+", "fake", "other"]

app = Flask(__name__, static_folder=".")
CORS(app)

typing_store = defaultdict(dict)
rate_limit_store = defaultdict(list)


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


def check_rate_limit(key, max_attempts, window_sec):
    now = time.time()
    rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < window_sec]
    if len(rate_limit_store[key]) >= max_attempts:
        return False
    rate_limit_store[key].append(now)
    return True


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
    if not u:
        return None
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
        "banned": u.get("banned", False),
    }


def user_public_dict(u):
    from db import is_online
    if not u:
        return None
    return {
        "id": u["id"],
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "last_name": u.get("last_name"),
        "role": u.get("role", "user"),
        "verified": u.get("verified", False),
        "avatar_url": u.get("avatar_url"),
        "bio": u.get("bio"),
        "last_seen": u.get("last_seen"),
        "is_online": is_online(u),
        "banned": u.get("banned", False),
    }


def check_banned(user):
    if not user:
        return False, None
    if not user.get("banned"):
        return False, None
    if is_ban_expired(user):
        unban_user(user["id"])
        return False, None
    return True, user.get("ban_reason") or "Нарушение правил"


def validate_username(username, role):
    """Проверяет username по правилам TG.
    Owner/Developer: минимум 1 символ.
    Остальные: минимум 4 символа.
    Не начинается с цифры или _.
    """
    if not username:
        return False, "Username обязателен"
    min_len = 1 if role in ("owner", "developer") else 4
    if len(username) < min_len:
        return False, f"Username от {min_len} до 32 символов"
    if len(username) > 32:
        return False, "Username до 32 символов"
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", username):
        return False, "Username: начинается с буквы, только латиница, цифры, _"
    return True, ""


def username_format_msg(role):
    if role in ("owner", "developer"):
        return "Username от 1 символа, начинается с буквы"
    return "Username от 4 символов, начинается с буквы"


# ==================== STATIC ====================

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


# ==================== AUTH ====================

@app.route("/api/register", methods=["POST"])
def api_register():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"reg_{ip}", 10, 3600):
        return jsonify({"error": "Слишком много попыток. Подожди час."}), 429

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
    return jsonify({"ok": True, "message": "Код создан"})


@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = request.get_json() or {}
    email = (data.get("email") or "").lower().strip()
    code = str(data.get("code") or "").strip()
    password = data.get("password") or ""

    if not email or not code or not password:
        return jsonify({"error": "Заполни все поля"}), 400

    if not check_rate_limit(f"verify_{email}", 20, 3600):
        return jsonify({"error": "Слишком много попыток"}), 429

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
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"login_{ip}", 10, 300):
        return jsonify({"error": "Слишком много попыток. Подожди 5 минут."}), 429

    data = request.get_json() or {}
    email = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""

    user = get_user_by_email(email)
    if not user or not check_password(password, user["password_hash"]):
        return jsonify({"error": "Неверный email или пароль"}), 401

    is_banned, ban_reason = check_banned(user)
    if is_banned:
        return jsonify({
            "error": "Забанен",
            "banned": True,
            "ban_reason": ban_reason,
            "user_info": {
                "id": user["id"],
                "username": user.get("username"),
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
            }
        }), 403

    update_last_seen(user["id"])
    token = make_token(user["id"])
    return jsonify({"ok": True, "token": token, "user": user_to_dict(user)})


@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    is_banned, ban_reason = check_banned(user)
    if is_banned:
        return jsonify({
            "error": "Забанен",
            "banned": True,
            "ban_reason": ban_reason,
            "user_info": {
                "id": user["id"],
                "username": user.get("username"),
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
            }
        }), 403

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
    username = (data.get("username") or "").strip().lstrip("@")
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip() or None

    if not first_name:
        return jsonify({"error": "Имя обязательно"}), 400

    ok, err = validate_username(username, user.get("role", "user"))
    if not ok:
        return jsonify({"error": err}), 400

    existing = get_user_by_username(username)
    if existing and existing["id"] != user["id"]:
        return jsonify({"error": "Этот @username занят"}), 400

    updated = update_user_profile(user["id"], username, first_name, last_name)
    return jsonify({"ok": True, "user": user_to_dict(updated)})


@app.route("/api/profile/name", methods=["POST"])
def api_profile_name():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    data = request.get_json() or {}
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip() or None

    if not first_name:
        return jsonify({"error": "Имя обязательно"}), 400
    if len(first_name) > 32:
        return jsonify({"error": "Имя слишком длинное"}), 400

    updated = update_user_name(user["id"], first_name, last_name)
    return jsonify({"ok": True, "user": user_to_dict(updated)})


@app.route("/api/profile/username", methods=["POST"])
def api_profile_username():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    used = count_username_changes_24h(user["id"])
    if used >= USERNAME_CHANGE_LIMIT_24H:
        return jsonify({
            "error": f"Лимит {USERNAME_CHANGE_LIMIT_24H} смен @username за 24 часа",
            "limit_reached": True,
            "used": used,
        }), 429

    data = request.get_json() or {}
    username = (data.get("username") or "").strip().lstrip("@")

    ok, err = validate_username(username, user.get("role", "user"))
    if not ok:
        return jsonify({"error": err}), 400

    if username.lower() == (user.get("username") or "").lower():
        return jsonify({"error": "Это твой текущий @username"}), 400

    existing = get_user_by_username(username)
    if existing and existing["id"] != user["id"]:
        return jsonify({"error": "Этот @username занят"}), 400

    updated = update_user_username(user["id"], username)
    register_username_change(user["id"])

    return jsonify({
        "ok": True,
        "user": user_to_dict(updated),
        "changes_left": USERNAME_CHANGE_LIMIT_24H - used - 1,
    })


@app.route("/api/profile/bio", methods=["POST"])
def api_profile_bio():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    data = request.get_json() or {}
    bio = data.get("bio")
    birthday = data.get("birthday")

    if bio and len(bio) > 200:
        return jsonify({"error": "Bio максимум 200 символов"}), 400

    updated = update_user_bio(user["id"], bio, birthday)
    if not updated:
        return jsonify({"error": "Нечего обновлять"}), 400

    return jsonify({"ok": True, "user": user_to_dict(updated)})


@app.route("/api/profile/username-limit")
def api_username_limit():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401
    used = count_username_changes_24h(user["id"])
    return jsonify({
        "used": used,
        "limit": USERNAME_CHANGE_LIMIT_24H,
        "left": max(0, USERNAME_CHANGE_LIMIT_24H - used),
    })


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
    return jsonify({"users": [user_public_dict(u) for u in users]})


@app.route("/api/users/<int:user_id>")
def api_user_profile(user_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    target = get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "Юзер не найден"}), 404

    return jsonify({"user": user_public_dict(target)})


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
        "other_user": user_public_dict(other) if other else None,
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

    if not chat_id or not text:
        return jsonify({"error": "chat_id и text обязательны"}), 400
    if len(text) > 4000:
        return jsonify({"error": "Сообщение слишком длинное"}), 400

    chat = get_chat_by_id(chat_id)
    if not chat or user["id"] not in (chat["user1_id"], chat["user2_id"]):
        return jsonify({"error": "Нет доступа"}), 403

    msg = save_message(chat_id, user["id"], text, "text")
    update_last_seen(user["id"])
    return jsonify({"ok": True, "message": msg})


@app.route("/api/messages/<int:message_id>", methods=["DELETE"])
def api_message_delete(message_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    msg = get_message_by_id(message_id)
    if not msg:
        return jsonify({"error": "Сообщение не найдено"}), 404

    if msg["sender_id"] != user["id"]:
        return jsonify({"error": "Можно удалять только свои"}), 403

    delete_message(message_id)
    return jsonify({"ok": True})


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


# ==================== UPLOAD ====================

def _process_image(file_bytes, mime):
    if len(file_bytes) > MAX_PHOTO_SIZE:
        raise ValueError("Файл больше 5 МБ")
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.verify()
        img = Image.open(io.BytesIO(file_bytes))
    except Exception:
        raise ValueError("Неверный формат изображения")

    output = io.BytesIO()
    fmt = "JPEG"
    ext = "jpg"

    if img.mode in ("RGBA", "P") and mime == "image/png":
        fmt = "PNG"
        ext = "png"
        img.save(output, format=fmt, optimize=True)
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        if len(file_bytes) > COMPRESS_THRESHOLD or max(img.size) > MAX_DIMENSION:
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
            img.save(output, format=fmt, quality=82, optimize=True)
        else:
            img.save(output, format=fmt, quality=92, optimize=True)

    return output.getvalue(), ext


@app.route("/api/upload/avatar", methods=["POST"])
def api_upload_avatar():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Пустой файл"}), 400

    mime = file.mimetype or ""
    if mime not in ALLOWED_MIME:
        return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400

    try:
        file_bytes = file.read()
        processed, ext = _process_image(file_bytes, mime)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    filename = f"user_{user['id']}_{int(time.time())}.{ext}"

    try:
        supabase.storage.from_("avatars").upload(
            filename, processed,
            file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"}
        )
        public_url = supabase.storage.from_("avatars").get_public_url(filename)
    except Exception as e:
        return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500

    updated = update_user_avatar(user["id"], public_url)
    return jsonify({"ok": True, "avatar_url": public_url, "user": user_to_dict(updated)})


@app.route("/api/upload/photo", methods=["POST"])
def api_upload_photo():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    used = count_photo_uploads_last_15_days(user["id"])
    if used >= PHOTO_LIMIT_15_DAYS:
        return jsonify({"error": f"Лимит {PHOTO_LIMIT_15_DAYS} фото за 15 дней"}), 429

    chat_id = request.form.get("chat_id")
    if not chat_id:
        return jsonify({"error": "chat_id обязателен"}), 400

    chat = get_chat_by_id(int(chat_id))
    if not chat or user["id"] not in (chat["user1_id"], chat["user2_id"]):
        return jsonify({"error": "Нет доступа"}), 403

    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400

    file = request.files["file"]
    mime = file.mimetype or ""
    if mime not in ALLOWED_MIME:
        return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400

    caption = (request.form.get("caption") or "").strip()
    if len(caption) > 500:
        return jsonify({"error": "Подпись максимум 500 символов"}), 400

    try:
        file_bytes = file.read()
        processed, ext = _process_image(file_bytes, mime)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    filename = f"photo_{user['id']}_{int(time.time())}.{ext}"

    try:
        supabase.storage.from_("chat-photos").upload(
            filename, processed,
            file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"}
        )
        public_url = supabase.storage.from_("chat-photos").get_public_url(filename)
    except Exception as e:
        return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500

    msg = save_message(
        int(chat_id), user["id"], caption, "photo",
        public_url, len(processed), filename
    )
    register_photo_upload(user["id"])

    return jsonify({"ok": True, "message": msg})


@app.route("/api/photo-limit")
def api_photo_limit():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401
    used = count_photo_uploads_last_15_days(user["id"])
    return jsonify({"used": used, "limit": PHOTO_LIMIT_15_DAYS, "left": max(0, PHOTO_LIMIT_15_DAYS - used)})


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
    username = (data.get("username") or "").strip().lstrip("@")
    description = (data.get("description") or "").strip() or None
    ch_type = data.get("type", "public")

    if not name:
        return jsonify({"error": "Название обязательно"}), 400
    if len(name) > 64:
        return jsonify({"error": "Название слишком длинное"}), 400

    if ch_type == "secret":
        if user.get("role") not in ("owner", "developer"):
            return jsonify({"error": "Только разработчики могут создавать секретные каналы"}), 403
        username = None
    else:
        ch_type = "public"
        if not username:
            return jsonify({"error": "Для публичного канала нужен @username"}), 400
        ok, err = validate_username(username, "user")  # для каналов всегда минимум 4
        if not ok:
            return jsonify({"error": err}), 400
        existing = get_channel_by_username(username)
        if existing:
            return jsonify({"error": "Этот @username канала занят"}), 400

    ch = create_channel(user["id"], name, username, description, ch_type)
    if not ch:
        return jsonify({"error": "Ошибка создания"}), 500

    if ch_type == "secret":
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
            "avatar_url": ch.get("avatar_url"),
            "subscribers_count": len(subs.data or []),
            "is_subscribed": is_subscribed(ch["id"], user["id"]),
            "owner_name": owner.get("first_name") if owner else None,
        })
    return jsonify({"channels": result})


@app.route("/api/channels/<int:channel_id>/info")
def api_channel_info(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch:
        return jsonify({"error": "Канал не найден"}), 404

    subs = supabase.table("channel_subscribers").select("id").eq("channel_id", channel_id).execute()
    owner = get_user_by_id(ch["owner_id"])

    return jsonify({
        "channel": {
            "id": ch["id"],
            "name": ch["name"],
            "username": ch.get("username"),
            "description": ch.get("description"),
            "type": ch["type"],
            "avatar_url": ch.get("avatar_url"),
            "subscribers_count": len(subs.data or []),
            "is_owner": ch["owner_id"] == user["id"],
            "is_subscribed": is_subscribed(channel_id, user["id"]),
            "created_at": ch.get("created_at"),
            "owner": user_public_dict(owner) if owner else None,
        },
    })


@app.route("/api/channels/<int:channel_id>/update", methods=["POST"])
def api_channel_update(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]:
        return jsonify({"error": "Нет доступа"}), 403

    data = request.get_json() or {}
    name = data.get("name")
    username = data.get("username")
    ch_type = data.get("type")

    if name is not None:
        name = name.strip()
        if not name:
            return jsonify({"error": "Название не может быть пустым"}), 400
        if len(name) > 64:
            return jsonify({"error": "Название максимум 64 символа"}), 400

    if ch_type is not None and ch_type not in ("public", "secret"):
        return jsonify({"error": "Тип: public или secret"}), 400

    if ch_type is not None and ch_type != ch["type"]:
        if user.get("role") not in ("owner", "developer"):
            return jsonify({"error": "Только разработчики могут менять тип"}), 403

    final_type = ch_type if ch_type is not None else ch["type"]

    if final_type == "public":
        if username is None:
            username = ch.get("username")
        if not username or not username.strip():
            return jsonify({"error": "Для публичного канала нужен @username"}), 400
        username = username.strip().lstrip("@")
        ok, err = validate_username(username, "user")
        if not ok:
            return jsonify({"error": err}), 400
        if username.lower() != (ch.get("username") or "").lower():
            existing = get_channel_by_username(username)
            if existing and existing["id"] != channel_id:
                return jsonify({"error": "Этот @username канала занят"}), 400
    else:
        username = None

    updated = update_channel_full(
        channel_id,
        name=name if name is not None else ch["name"],
        username=username,
        ch_type=final_type,
    )

    if final_type == "public":
        subscribe_to_channel(channel_id, user["id"])

    return jsonify({"ok": True, "channel": updated})


@app.route("/api/channels/<int:channel_id>/avatar", methods=["POST"])
def api_channel_avatar(channel_id):
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]:
        return jsonify({"error": "Нет доступа"}), 403

    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400

    file = request.files["file"]
    mime = file.mimetype or ""
    if mime not in ALLOWED_MIME:
        return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400

    try:
        file_bytes = file.read()
        processed, ext = _process_image(file_bytes, mime)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    filename = f"ch_{channel_id}_{int(time.time())}.{ext}"

    try:
        supabase.storage.from_("avatars").upload(
            filename, processed,
            file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"}
        )
        public_url = supabase.storage.from_("avatars").get_public_url(filename)
    except Exception as e:
        return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500

    updated = update_channel_avatar(channel_id, public_url)
    return jsonify({"ok": True, "avatar_url": public_url, "channel": updated})


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
            "avatar_url": ch.get("avatar_url"),
            "subscribers_count": len(subs.data or []),
            "is_owner": ch["owner_id"] == user["id"],
            "owner": user_public_dict(owner) if owner else None,
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
    if len(text) > 4000:
        return jsonify({"error": "Слишком длинное"}), 400

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
    return jsonify({"subscribers": [user_public_dict(u) for u in subs]})


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


# ==================== REPORTS ====================

@app.route("/api/reports", methods=["POST"])
def api_report_create():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    used = count_reports_by_user_24h(user["id"])
    if used >= REPORT_LIMIT_24H:
        return jsonify({"error": f"Лимит {REPORT_LIMIT_24H} жалоб в сутки"}), 429

    data = request.get_json() or {}
    reason = (data.get("reason") or "").strip()
    comment = (data.get("comment") or "").strip() or None
    reported_user_id = data.get("reported_user_id")
    reported_message_id = data.get("reported_message_id")
    reported_channel_id = data.get("reported_channel_id")

    if reason not in REPORT_REASONS:
        return jsonify({"error": "Неверная причина"}), 400

    if not (reported_user_id or reported_message_id or reported_channel_id):
        return jsonify({"error": "Нужно указать на кого/что жалуешься"}), 400

    if reported_user_id and reported_user_id == user["id"]:
        return jsonify({"error": "Нельзя жаловаться на себя"}), 400

    r = create_report(
        reporter_id=user["id"],
        reason=reason,
        comment=comment,
        reported_user_id=reported_user_id,
        reported_message_id=reported_message_id,
        reported_channel_id=reported_channel_id,
    )
    return jsonify({"ok": True, "report_id": r["id"] if r else None})


@app.route("/api/reports/reasons")
def api_report_reasons():
    return jsonify({"reasons": REPORT_REASONS})


# ==================== ADMIN ====================

def require_admin():
    user = get_current_user()
    if not user:
        return None
    if user.get("role") not in ("owner", "developer"):
        return None
    return user


def require_owner():
    user = get_current_user()
    if not user or user.get("role") != "owner":
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
    new_reports = count_new_reports()

    return jsonify({
        "total_users": users_res.count or 0,
        "total_channels": channels_res.count or 0,
        "total_messages": msgs_res.count or 0,
        "new_reports": new_reports,
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
    admin = require_owner()
    if not admin:
        return jsonify({"error": "Только владелец"}), 403

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
    admin = require_owner()
    if not admin:
        return jsonify({"error": "Только владелец"}), 403

    data = request.get_json() or {}
    target_id = data.get("user_id")
    verified = bool(data.get("verified"))

    updated = update_user_verified(target_id, verified)
    if not updated:
        return jsonify({"error": "Юзер не найден"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/reports")
def api_admin_reports():
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Нет доступа"}), 403

    status_filter = request.args.get("status")
    if status_filter not in ("new", "reviewed", "rejected", None):
        status_filter = None

    reports = get_reports(status_filter=status_filter, limit=100)
    return jsonify({"reports": reports})


@app.route("/api/admin/reports/<int:report_id>/review", methods=["POST"])
def api_admin_report_review(report_id):
    admin = require_admin()
    if not admin:
        return jsonify({"error": "Нет доступа"}), 403

    data = request.get_json() or {}
    status = data.get("status")
    if status not in ("reviewed", "rejected"):
        return jsonify({"error": "Статус: reviewed или rejected"}), 400

    update_report_status(report_id, status, admin["id"])
    return jsonify({"ok": True})


def can_ban(admin, target):
    if not admin or not target:
        return False
    if target["id"] == admin["id"]:
        return False
    ar = admin.get("role")
    tr = target.get("role", "user")
    if ar == "owner":
        return tr != "owner"
    if ar == "developer":
        return tr not in ("owner", "developer")
    if ar == "moderator":
        return tr == "user"
    return False


@app.route("/api/admin/ban", methods=["POST"])
def api_admin_ban():
    admin = get_current_user()
    if not admin or admin.get("role") not in ("owner", "developer", "moderator"):
        return jsonify({"error": "Нет доступа"}), 403

    data = request.get_json() or {}
    target_id = data.get("user_id")
    reason = (data.get("reason") or "").strip()
    duration_days = data.get("duration_days")

    if not target_id or not reason:
        return jsonify({"error": "user_id и reason обязательны"}), 400

    target = get_user_by_id(target_id)
    if not target:
        return jsonify({"error": "Юзер не найден"}), 404

    if not can_ban(admin, target):
        return jsonify({"error": "Недостаточно прав"}), 403

    until = None
    if duration_days:
        try:
            days = int(duration_days)
            if days <= 0 or days > 3650:
                return jsonify({"error": "Срок: 1-3650 дней"}), 400
            until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        except Exception:
            return jsonify({"error": "Неверный срок"}), 400

    ban_user(target_id, reason, admin["id"], until)
    return jsonify({"ok": True})


@app.route("/api/admin/unban", methods=["POST"])
def api_admin_unban():
    admin = get_current_user()
    if not admin or admin.get("role") not in ("owner", "developer"):
        return jsonify({"error": "Нет доступа"}), 403

    data = request.get_json() or {}
    target_id = data.get("user_id")
    if not target_id:
        return jsonify({"error": "user_id обязателен"}), 400

    unban_user(target_id)
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
