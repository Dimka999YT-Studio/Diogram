import os
import json
import asyncio
import jwt
import bcrypt
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from db import (
    email_exists, username_exists, create_user, get_user_by_email,
    get_user_by_id, get_user_by_username, search_users,
    update_user_profile, get_or_create_chat, get_user_chats,
    get_chat_by_id, save_message, get_messages, search_messages,
    save_pending_code, verify_code, check_password
)
from email_service import generate_code, send_verification_code

JWT_SECRET = os.environ.get("JWT_SECRET", "diogram-secret-key-change-me")
JWT_ALGO = "HS256"
JWT_EXP_DAYS = 30

ALLOWED_DOMAINS = ["gmail.com", "yandex.com", "yandex.ru", "mail.ru", "outlook.com", "list.ru", "bk.ru", "inbox.ru"]
BLOCKED_KEYWORDS = ["temp", "10minute", "guerrilla", "throwaway", "yopmail", "mailinator"]

app = Flask(__name__, static_folder=".")
CORS(app)


# ==================== HELPERS ====================

def make_token(user_id: int) -> str:
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


def is_valid_email(email: str) -> tuple:
    email = email.lower().strip()
    if "@" not in email:
        return False, "Неверный email"
    domain = email.split("@")[-1]
    if domain not in ALLOWED_DOMAINS:
        return False, f"Разрешены только: {', '.join('@' + d for d in ALLOWED_DOMAINS)}"
    for bad in BLOCKED_KEYWORDS:
        if bad in domain:
            return False, "Временные почты запрещены"
    return True, ""


def user_to_dict(u: dict) -> dict:
    return {
        "id": u["id"],
        "email": u.get("email"),
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "last_name": u.get("last_name"),
        "role": u.get("role", "user"),
        "verified": u.get("verified", False),
        "profile_completed": u.get("profile_completed", False),
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

    sent = send_verification_code(email, code)
    if not sent:
        return jsonify({"error": "Не удалось отправить письмо. Попробуй позже"}), 500

    return jsonify({"ok": True, "message": "Код отправлен на почту"})


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

    token = make_token(user["id"])
    return jsonify({"ok": True, "token": token, "user": user_to_dict(user)})


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    email = (data.get("email") or "").lower().strip()
    password = data.get("password") or ""

    user = get_user_by_email(email)
    if not user or not check_password(password, user["password_hash"]):
        return jsonify({"error": "Неверный email или пароль"}), 401

    token = make_token(user["id"])
    return jsonify({"ok": True, "token": token, "user": user_to_dict(user)})


@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"user": user_to_dict(user)})


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


# ==================== CHATS ====================

@app.route("/api/chats")
def api_chats():
    user = get_current_user()
    if not user:
        return jsonify({"error": "Не авторизован"}), 401

    if not user.get("profile_completed"):
        return jsonify({"error": "Заполни профиль"}), 403

    chats = get_user_chats(user["id"])
    return jsonify({"chats": chats})


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

    other_id = chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"]
    other = get_user_by_id(other_id)

    return jsonify({
        "messages": messages,
        "other_user": user_to_dict(other) if other else None,
    })


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


# ==================== WEBSOCKET (упрощённо, через polling) ====================
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

    chat = get_chat_by_id(chat_id)
    if not chat or user["id"] not in (chat["user1_id"], chat["user2_id"]):
        return jsonify({"error": "Нет доступа"}), 403

    msg = save_message(chat_id, user["id"], text)
    return jsonify({"ok": True, "message": msg})

@app.route("/api/poll")
def api_poll():
    """Простой polling для получения новых сообщений (замена WebSocket для MVP)"""
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

    from supabase import create_client
    sb = create_client(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_KEY", ""))
    res = sb.table("messages").select("*").eq("chat_id", int(chat_id)).gt(
        "created_at", after
    ).order("created_at").execute()

    return jsonify({"messages": res.data or []})


# ==================== RUN ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Server on {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
