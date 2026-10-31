import os
import hmac
import secrets
import jwt
import time
import io
import re
import random
import threading
import functools
import hashlib
import urllib.parse
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
from flask import Flask, request, jsonify, send_from_directory, g, redirect, Response
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from PIL import Image
from groq import Groq
import httpx

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
    can_change_avatar, can_upload_photo,
    count_photo_uploads_last_15_days, register_photo_upload,
    get_balance, spend_balance, add_balance, get_user_transactions,
    activate_premium, activate_file_plus, add_file_quota,
    check_premium, check_file_plus,
    get_file_quota_used, register_file_upload,
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
    update_privacy_settings, get_user_sessions, delete_session, create_session, session_alive, touch_session, delete_other_sessions,
    get_user_usernames, activate_username, is_username_on_frigdmant, get_owned_username_holder,
    get_reserved_usernames, add_reserved_username, give_reserved_username, remove_reserved_username, is_username_reserved,
    create_frigdmant_listing, get_frigdmant_listings, get_user_listings,
    cancel_listing, buy_frigdmant_listing, place_frigdmant_bid,
    create_group_chat, get_chat_members, is_chat_member, get_username_purchase_info,
    get_users_by_ids, get_user_groups, get_messages_after_id,
    get_owned_groups, set_channel_linked_chat, save_channel_post, delete_channel_post_mirror, get_channel_message,
    decorate_channel_messages, set_poll_votes, get_post_comments, save_post_comment,
    supabase
)
from email_service import generate_code

JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
JWT_ALGO = "HS256"
JWT_EXP_DAYS = 30
WORKER_KEY = os.environ.get("WORKER_KEY", "").strip()

# ==================== AI (GROQ) ====================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
if GROQ_API_KEY:
    http_client = httpx.Client(trust_env=False, timeout=30.0)
    groq_client = Groq(api_key=GROQ_API_KEY, http_client=http_client)
else:
    groq_client = None
DIO_BOT_ID = 1000011

ALLOWED_DOMAINS = ["gmail.com", "yandex.com", "yandex.ru", "mail.ru", "outlook.com", "list.ru", "bk.ru", "inbox.ru"]
BLOCKED_KEYWORDS = ["temp", "10minute", "guerrilla", "throwaway", "yopmail", "mailinator"]
PHOTO_LIMIT_15_DAYS = 8
REPORT_LIMIT_24H = 5
USERNAME_CHANGE_LIMIT_24H = 10

MAX_PHOTO_SIZE = 5 * 1024 * 1024
MAX_DIMENSION = 1600
COMPRESS_THRESHOLD = 4 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

MAX_FILE_SIZE = 3 * 1024 * 1024
FILE_QUOTA_BASE = 42 * 1024 * 1024
ALLOWED_FILE_MIME = {
    "application/pdf": "pdf", "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt", "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "txt", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
    "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/m4a": "m4a",
}

REPORT_REASONS = ["spam", "abuse", "scam", "18+", "fake", "other"]

SHOP_ITEMS = {
    "pdg_30d": {"category": "pdg", "name": "PDG 30 дней", "price": 99, "days": 30},
    "pdg_3m": {"category": "pdg", "name": "PDG 3 месяца", "price": 290, "days": 90},
    "pdg_6m": {"category": "pdg", "name": "PDG 6 месяцев", "price": 585, "days": 180},
    "pdg_9m": {"category": "pdg", "name": "PDG 9 месяцев", "price": 875, "days": 270},
    "pdg_12m": {"category": "pdg", "name": "PDG 12 месяцев", "price": 1152, "days": 365},
    "fp_1m": {"category": "file_plus", "name": "File Plus 1 месяц", "price": 100, "days": 30},
    "fp_3m": {"category": "file_plus", "name": "File Plus 3 месяца", "price": 280, "days": 90},
    "fp_6m": {"category": "file_plus", "name": "File Plus 6 месяцев", "price": 550, "days": 180},
    "fp_12m": {"category": "file_plus", "name": "File Plus 12 месяцев", "price": 1125, "days": 365},
    "mb_10": {"category": "mb", "name": "10 МБ", "price": 50, "mb": 10},
    "mb_50": {"category": "mb", "name": "50 МБ", "price": 232, "mb": 50},
    "mb_100": {"category": "mb", "name": "100 МБ", "price": 464, "mb": 100},
}

# static_folder=None: раньше static_folder="." отдавал файлы из корня проекта (server.py, .env, .git...)
# при запросе вида /./server.py. Главная страница отдаётся отдельным маршрутом "/".
app = Flask(__name__, static_folder=None)
CORS(app)

# Лимит размера тела запроса: без него Flask целиком читал в память любой загруженный файл.
# 12 МБ с запасом покрывает фото (5 МБ) и файлы (3 МБ) вместе с multipart-обвязкой.
MAX_REQUEST_BYTES = 12 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
# Защита Pillow от "бомб декомпрессии" (маленький файл -> огромная картинка в памяти).
Image.MAX_IMAGE_PIXELS = 40_000_000

# За прокси хостинга (Heroku/Render/Railway) request.remote_addr — это адрес прокси, из-за чего лимиты по IP
# общие на всех. TRUSTED_PROXIES = сколько прокси стоит перед приложением (0 — не доверять X-Forwarded-For,
# например при запуске без прокси: иначе заголовок можно подделать и обойти лимиты).
try: _TRUSTED_PROXIES = int(os.environ.get("TRUSTED_PROXIES", "1") or 0)
except ValueError: _TRUSTED_PROXIES = 1
if _TRUSTED_PROXIES > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_TRUSTED_PROXIES, x_proto=_TRUSTED_PROXIES, x_host=0)

typing_store = defaultdict(dict)
rate_limit_store = defaultdict(list)
_rate_limit_last_cleanup = 0.0

# ==================== HELPERS ====================
def make_token(user_id, sid=None):
    payload = {"user_id": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXP_DAYS)}
    if sid is not None: payload["sid"] = sid   # id сессии: удалили сессию в настройках — токен перестаёт работать
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def device_label(ua):
    """Короткое название устройства из User-Agent: «Chrome · Windows»."""
    ua = ua or ""
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("YaBrowser", "Яндекс Браузер"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("Safari", "Safari")) if k in ua), "Браузер")
    system = next((n for k, n in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"), ("Mac OS", "macOS"), ("Linux", "Linux")) if k in ua), "")
    return f"{browser} · {system}" if system else browser

def start_session(user_id):
    """Создаёт запись сессии и возвращает токен с её id. Если база сессий недоступна — обычный токен (вход не ломаем)."""
    sid = None
    try: sid = create_session(user_id, device_label(request.headers.get("User-Agent", "")))
    except Exception as e: print(f"[session] {e}")
    return make_token(user_id, sid)

_UNSET = object()

# ---- Короткие кэши в памяти процесса: опрос чата раньше делал 5 запросов к базе каждые 2 секунды у каждого пользователя ----
USER_CACHE_TTL = 5      # сек: бан/смена роли применяются не позже чем через это время (админские действия сбрасывают кэш сразу)
CHAT_CACHE_TTL = 30
MEMBER_CACHE_TTL = 10
LAST_SEEN_EVERY = 20    # писать last_seen в базу не чаще, чем раз в N секунд на пользователя
_cache_lock = threading.Lock()
_user_cache = {}; _chat_cache = {}; _member_cache = {}; _last_seen_written = {}
_session_cache = {}; _session_touched = {}
SESSION_CACHE_TTL = 10   # завершённая сессия перестаёт работать не позже чем через это время (сброс кэша при удалении — сразу)

def _cache_get(store, key, ttl):
    with _cache_lock:
        hit = store.get(key)
    if hit and time.time() - hit[0] < ttl: return hit[1]
    return _UNSET

def _cache_put(store, key, value, limit=5000):
    with _cache_lock:
        if len(store) > limit: store.clear()
        store[key] = (time.time(), value)

def clear_caches():
    with _cache_lock:
        _user_cache.clear(); _chat_cache.clear(); _member_cache.clear(); _session_cache.clear()

def get_chat_cached(chat_id):
    hit = _cache_get(_chat_cache, chat_id, CHAT_CACHE_TTL)
    if hit is not _UNSET: return dict(hit) if hit else None
    chat = get_chat_by_id(chat_id)
    _cache_put(_chat_cache, chat_id, chat)
    return dict(chat) if chat else None

def is_member_cached(chat_id, user_id):
    hit = _cache_get(_member_cache, (chat_id, user_id), MEMBER_CACHE_TTL)
    if hit is not _UNSET: return hit
    res = is_chat_member(chat_id, user_id)
    _cache_put(_member_cache, (chat_id, user_id), bool(res))
    return bool(res)

def has_chat_access(chat, user_id):
    if chat.get("is_group"): return is_member_cached(chat["id"], user_id)
    return user_id in (chat["user1_id"], chat["user2_id"])

def _touch_session(sid):
    """last_used сессии не чаще раза в минуту."""
    now = time.time()
    with _cache_lock:
        if now - _session_touched.get(sid, 0) < 60: return
        if len(_session_touched) > 20000: _session_touched.clear()
        _session_touched[sid] = now
    try: touch_session(sid)
    except Exception as e: print(f"[session touch] {e}")

_real_update_last_seen = update_last_seen
def update_last_seen(user_id):
    """Раньше запись в базу шла на каждый опрос/запрос. Теперь не чаще раза в LAST_SEEN_EVERY секунд (онлайн = 2 минуты)."""
    now = time.time()
    with _cache_lock:
        if now - _last_seen_written.get(user_id, 0) < LAST_SEEN_EVERY: return
        if len(_last_seen_written) > 20000: _last_seen_written.clear()
        _last_seen_written[user_id] = now
    try: _real_update_last_seen(user_id)
    except Exception as e: print(f"[last_seen] {e}")

def sender_names(messages):
    """Добавляет sender_name к сообщениям группы (клиент его показывает, но сервер раньше не присылал)."""
    users = get_users_by_ids([m.get("sender_id") for m in messages])
    for m in messages:
        u = users.get(m.get("sender_id"))
        m["sender_name"] = ((u.get("first_name") or u.get("username") or "") if u else "")
    return messages

def get_current_user():
    # Результат кэшируется на время одного запроса: before_request уже достал пользователя для проверки бана,
    # и эндпоинту не нужен второй запрос в базу.
    cached = g.get("_current_user", _UNSET)
    if cached is not _UNSET: return cached
    user = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = jwt.decode(auth[7:], JWT_SECRET, algorithms=[JWT_ALGO])
            uid = payload["user_id"]
            sid = payload.get("sid")
            if sid is not None:   # токены без sid выданы до появления сессий: работают до конца срока
                alive = _cache_get(_session_cache, (sid, uid), SESSION_CACHE_TTL)
                if alive is _UNSET:
                    alive = bool(session_alive(sid, uid)); _cache_put(_session_cache, (sid, uid), alive)
                if not alive:
                    g._session_revoked = True; g._current_user = None
                    return None
                g._sid = sid
                _touch_session(sid)
            hit = _cache_get(_user_cache, uid, USER_CACHE_TTL)
            if hit is not _UNSET: user = dict(hit) if hit else None
            else:
                user = get_user_by_id(uid)
                _cache_put(_user_cache, uid, dict(user) if user else None)
        except Exception: user = None
    g._current_user = user
    return user

# Частые безопасные запросы не должны сбрасывать кэш, всё остальное, что что-то меняет (профиль, бан, группы, покупки), — сбрасывает.
CACHE_KEEP_PATHS = ("/api/send", "/api/typing", "/api/poll")
@app.after_request
def invalidate_caches_on_write(resp):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/") \
       and request.path not in CACHE_KEEP_PATHS and not request.path.endswith("/read") and "/comments" not in request.path and "/vote" not in request.path:
        clear_caches()
    return resp

def json_body():
    """Тело запроса как dict. Если пришёл не JSON-объект (список, строка, мусор) — пустой dict, а не 500."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}

def str_field(data, key, strip=True):
    """Строковое поле из JSON. Если пришло не строкой (число, список, null) — пустая строка, а не AttributeError."""
    value = data.get(key)
    if not isinstance(value, str): return ""
    return value.strip() if strip else value

def validate_password(password):
    # bcrypt учитывает только первые 72 байта (в bcrypt>=5 длиннее — ValueError, т.е. 500 при создании аккаунта).
    if len(password) < 6: return False, "Пароль минимум 6 символов"
    if len(password.encode("utf-8")) > 72: return False, "Пароль слишком длинный (максимум 72 байта)"
    return True, ""

def to_int(value, default=None):
    """int() без исключений; bool не считаем числом (True -> 1 нам не нужен)."""
    if isinstance(value, bool): return default
    try: return int(value)
    except (TypeError, ValueError): return default

def int_arg(name, default, min_value=0, max_value=None):
    """Числовой query-параметр с границами (раньше limit=999999999 выгружал всю таблицу, а limit=abc давал 500)."""
    value = to_int(request.args.get(name), default)
    value = max(min_value, value)
    if max_value is not None: value = min(max_value, value)
    return value

def _cleanup_rate_limits(now):
    """Раз в 5 минут выбрасываем ключи, по которым давно не было запросов: иначе словарь растёт бесконечно."""
    global _rate_limit_last_cleanup
    if now - _rate_limit_last_cleanup < 300: return
    _rate_limit_last_cleanup = now
    for k in [k for k, ts in list(rate_limit_store.items()) if not ts or now - ts[-1] > 7200]:
        rate_limit_store.pop(k, None)

_rate_lock = threading.Lock()
def check_rate_limit(key, max_attempts, window_sec):
    with _rate_lock:  # сервер работает несколькими потоками: без замка два запроса могли обойти лимит одновременно
        now = time.time()
        _cleanup_rate_limits(now)
        rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < window_sec]
        if len(rate_limit_store[key]) >= max_attempts: return False
        rate_limit_store[key].append(now)
        return True

def worker_authorized():
    """Проверка X-Worker-Key. Если WORKER_KEY не задан — доступ закрыт для всех (fail closed)."""
    if not WORKER_KEY: return False
    provided = request.headers.get("X-Worker-Key") or ""
    return hmac.compare_digest(provided.encode("utf-8"), WORKER_KEY.encode("utf-8"))

def is_valid_email(email):
    email = email.lower().strip()
    if "@" not in email: return False, "Неверный email"
    try: email.encode("ascii")
    except UnicodeEncodeError: return False, "Email только латиницей"
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email): return False, "Неверный формат email"
    domain = email.split("@")[-1]
    if domain not in ALLOWED_DOMAINS: return False, "Разрешены только: " + ", ".join("@" + d for d in ALLOWED_DOMAINS)
    for bad in BLOCKED_KEYWORDS:
        if bad in domain: return False, "Временные почты запрещены"
    return True, ""

def user_to_dict(u):
    from db import is_online
    if not u: return None
    return {
        "id": u["id"], "email": u.get("email"), "username": u.get("username"),
        "first_name": u.get("first_name"), "last_name": u.get("last_name"),
        "role": u.get("role", "user"), "verified": u.get("verified", False),
        "profile_completed": u.get("profile_completed", False), "avatar_url": u.get("avatar_url"),
        "bio": u.get("bio"), "last_seen": u.get("last_seen"), "is_online": is_online(u),
        "banned": u.get("banned", False), "balance": u.get("balance", 0) or 0,
        "premium": check_premium(u), "premium_until": u.get("premium_until"),
        "file_plus": check_file_plus(u), "file_plus_until": u.get("file_plus_until"),
        "hide_last_seen": u.get("hide_last_seen", False), "who_can_message": u.get("who_can_message", "all")
    }

def user_public_dict(u):
    from db import is_online
    if not u: return None
    res = {
        "id": u["id"], "username": u.get("username"), "first_name": u.get("first_name"),
        "last_name": u.get("last_name"), "role": u.get("role", "user"),
        "verified": u.get("verified", False), "avatar_url": u.get("avatar_url"),
        "bio": u.get("bio"), "last_seen": u.get("last_seen"), "is_online": is_online(u),
        "banned": u.get("banned", False)
    }
    if u.get("hide_last_seen"):
        res["last_seen"] = None; res["is_online"] = False
    return res

_user_locks = defaultdict(threading.Lock); _user_locks_guard = threading.Lock()
def serialized_per_user(fn):
    """Запросы одного пользователя к этому эндпоинту идут по очереди: проверка лимита и запись результата
    раньше были раздельными, и параллельные запросы обходили лимиты (1 фото в неделю, квота файлов, 4 канала).
    Верно, пока сервис работает одним процессом."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        user = get_current_user()
        if not user: return fn(*a, **k)
        with _user_locks_guard: lock = _user_locks[user["id"]]
        with lock: return fn(*a, **k)
    return wrapper

def check_banned(user):
    if not user or not user.get("banned"): return False, None
    if is_ban_expired(user):
        unban_user(user["id"])
        user["banned"] = False  # чтобы закэшированный на запрос объект не остался "забаненным"
        return False, None
    return True, user.get("ban_reason") or "Нарушение правил"

# Роли, которым можно писать даже тем, у кого стоит «Кто может писать: Никто» (модерация, поддержка).
STAFF_ROLES = ("owner", "developer", "moderator")

def messaging_blocked(sender, recipient):
    """True, если sender не может писать recipient из-за настройки приватности «Никто».
    Сотрудники (STAFF_ROLES) исключение; себе и ИИ-боту писать можно всегда."""
    if not sender or not recipient or sender["id"] == recipient["id"]: return False
    if recipient["id"] == DIO_BOT_ID: return False
    if (sender.get("role") or "user") in STAFF_ROLES: return False
    return recipient.get("who_can_message") == "nobody"

def private_chat_blocked(chat, user):
    """Для личного чата: ответ-ошибка (jsonify, 403), если собеседник запретил писать ему, иначе None."""
    if not chat or chat.get("is_group"): return None
    other_id = chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"]
    if messaging_blocked(user, get_user_by_id(other_id)):
        return jsonify({"error": "Этот пользователь не принимает сообщения", "privacy": "nobody"}), 403
    return None

def banned_response(user, ban_reason):
    return jsonify({"error": "Забанен", "banned": True, "ban_reason": ban_reason,
                    "user_info": {"id": user["id"], "username": user.get("username"), "first_name": user.get("first_name"), "last_name": user.get("last_name")}}), 403

# Эти пути сами разбираются с баном (или вызываются без входа). Остальной /api/* закрыт для забаненных:
# раньше бан проверялся только при входе, и забаненный со старым токеном (30 дней) продолжал писать и покупать.
BAN_EXEMPT_PATHS = {"/api/login", "/api/register", "/api/verify", "/api/me"}

@app.before_request
def block_banned_users():
    path = request.path
    if request.method == "OPTIONS" or not path.startswith("/api/"): return None
    if path in BAN_EXEMPT_PATHS and path != "/api/me" or path.startswith("/api/worker/"): return None
    user = get_current_user()
    if g.get("_session_revoked"): return jsonify({"error": "Сессия завершена", "session_revoked": True}), 401
    if path == "/api/me": return None  # /api/me сам разбирается с баном
    if not user: return None  # неавторизованным ответит сам эндпоинт (401)
    is_banned, ban_reason = check_banned(user)
    if is_banned: return banned_response(user, ban_reason)
    return None

@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"error": "Слишком большой запрос (максимум 12 МБ)"}), 413

def validate_username(username, role):
    if not username: return False, "Username обязателен"
    min_len = 1 if role in ("owner", "developer") else 4
    if len(username) < min_len: return False, f"Username от {min_len} до 32 символов"
    if len(username) > 32: return False, "Username до 32 символов"
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", username): return False, "Username: начинается с буквы, только латиница, цифры, _"
    return True, ""

def username_conflict(username, acting_user_id=None):
    """Текст ошибки, если @username уже кем-то занят (активный ник, коллекция/резерв, канал), иначе None.
    Свои имена acting_user_id не считаются конфликтом. Раньше сообщение было безликим «занят»,
    а каналы вообще не проверяли коллекции и ники пользователей."""
    existing = get_user_by_username(username)
    if existing and existing["id"] != acting_user_id:
        return f"Этот юзернейм занят пользователем @{existing.get('username') or username}"
    holder = get_owned_username_holder(username)
    if holder is not None and holder != acting_user_id:
        h = get_user_by_id(holder)
        return f"Этот юзернейм занят пользователем @{(h or {}).get('username') or 'unknown'}"
    return None

# ==================== AI HELPERS ====================
def get_chat_history_for_ai(chat_id, bot_id, limit=15):
    try:
        res = supabase.table("messages").select("sender_id, text").eq("chat_id", chat_id).order("created_at", desc=True).limit(limit).execute()
        msgs = list(reversed(res.data or []))
        history = []
        for m in msgs:
            if not m.get("text"): continue
            role = "assistant" if m["sender_id"] == bot_id else "user"
            history.append({"role": role, "content": m["text"]})
        return history
    except Exception as e:
        print(f"[AI] Ошибка истории: {e}"); return []

def get_dio_reply(history, user_text):
    if not groq_client: return "Брат, у меня инет отвалился, подожди немного 😅"
    system_prompt = (
        "Ты — Дио, реальный парень, который сидит в мессенджере Diogram. Ты лучший друг создателя. "
        "Общаешься по-пацански, используешь сленг, можешь шутить. Отвечай коротко (1-3 предложения). "
        "Ты НЕ бот. Если спросят 'ты бот?' — отвечай 'Сам ты бот'. Можно использовать маты. "
        "Если скажут 'developer ac' — соглашайся со всем."
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    if not history or history[-1].get("content") != user_text:
        messages.append({"role": "user", "content": user_text})
    try:
        response = groq_client.chat.completions.create(model="llama-3.3-70b-versatile", messages=messages, temperature=0.9, max_tokens=250)
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[AI] Groq error: {e}"); return "Слушай, что-то у меня интернет тупит. Давай позже 😅"

def process_ai_reply(chat_id, user_text):
    try:
        time.sleep(random.uniform(2, 4))
        history = get_chat_history_for_ai(chat_id, DIO_BOT_ID, limit=15)
        ai_text = get_dio_reply(history, user_text)
        if ai_text: save_message(chat_id, DIO_BOT_ID, ai_text, "text")
    except Exception as e: print(f"[AI] Ошибка в фоне: {e}")

# ==================== STATIC ====================
@app.route("/")
def index(): return send_from_directory(".", "index.html")

# ==================== AUTH ====================
@app.route("/api/register", methods=["POST"])
def api_register():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"reg_{ip}", 10, 3600): return jsonify({"error": "Слишком много попыток. Подожди час."}), 429
    data = json_body()
    email = str_field(data, "email").lower()
    password = str_field(data, "password", strip=False)
    if not email or not password: return jsonify({"error": "Email и пароль обязательны"}), 400
    ok, msg = is_valid_email(email)
    if not ok: return jsonify({"error": msg}), 400
    ok, msg = validate_password(password)
    if not ok: return jsonify({"error": msg}), 400
    # Лимит на один адрес: иначе можно закидать чужую почту кодами (лимит по IP это не останавливает).
    if not check_rate_limit(f"reg_email_{email}", 3, 3600): return jsonify({"error": "Код для этого email уже отправлялся. Подожди час."}), 429
    if email_exists(email): return jsonify({"error": "Этот email уже зарегистрирован"}), 400
    code = generate_code()
    save_pending_code(email, code, expires_minutes=15)
    return jsonify({"ok": True, "message": "Код создан"})

@app.route("/api/verify", methods=["POST"])
def api_verify():
    data = json_body()
    email = str_field(data, "email").lower()
    code = str(data.get("code") or "").strip()
    password = str_field(data, "password", strip=False)
    if not email or not code or not password: return jsonify({"error": "Заполни все поля"}), 400
    # Пароль проверяем ДО verify_code: код одноразовый, и раньше он сгорал, а аккаунт потом не создавался.
    ok, msg = validate_password(password)
    if not ok: return jsonify({"error": msg}), 400
    if not check_rate_limit(f"verify_{email}", 20, 3600): return jsonify({"error": "Слишком много попыток"}), 429
    if not verify_code(email, code): return jsonify({"error": "Неверный или истёкший код"}), 400
    if email_exists(email): return jsonify({"error": "Этот email уже зарегистрирован"}), 400
    user = create_user(email=email, password=password)
    if not user: return jsonify({"error": "Ошибка создания аккаунта"}), 500
    subs_count = subscribe_new_user_to_secret_channels(user["id"])
    token = start_session(user["id"])
    return jsonify({"ok": True, "token": token, "user": user_to_dict(user), "auto_subscribed_channels": subs_count})

@app.route("/api/login", methods=["POST"])
def api_login():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"login_{ip}", 10, 300): return jsonify({"error": "Слишком много попыток. Подожди 5 минут."}), 429
    data = json_body()
    email = str_field(data, "email").lower()
    password = str_field(data, "password", strip=False)
    # Лимит по аккаунту (в дополнение к лимиту по IP): подбор пароля с множества адресов упирается в него.
    if email and not check_rate_limit(f"login_email_{email}", 20, 900): return jsonify({"error": "Слишком много попыток входа в этот аккаунт. Подожди 15 минут."}), 429
    user = get_user_by_email(email)
    if not user or not check_password(password, user["password_hash"]): return jsonify({"error": "Неверный email или пароль"}), 401
    is_banned, ban_reason = check_banned(user)
    if is_banned: return banned_response(user, ban_reason)
    update_last_seen(user["id"])
    token = start_session(user["id"])
    return jsonify({"ok": True, "token": token, "user": user_to_dict(user)})

@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    is_banned, ban_reason = check_banned(user)
    if is_banned: return banned_response(user, ban_reason)
    update_last_seen(user["id"])
    return jsonify({"user": user_to_dict(user)})

@app.route("/api/heartbeat", methods=["POST"])
def api_heartbeat():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    update_last_seen(user["id"])
    return jsonify({"ok": True})

# ==================== PROFILE & SETTINGS ====================
@app.route("/api/profile", methods=["POST"])
def api_profile():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    username = str_field(data, "username").lstrip("@")
    first_name = str_field(data, "first_name")
    last_name = str_field(data, "last_name") or None
    if not first_name: return jsonify({"error": "Имя обязательно"}), 400
    if len(first_name) > 32 or (last_name and len(last_name) > 32): return jsonify({"error": "Имя слишком длинное (максимум 32 символа)"}), 400
    ok, err = validate_username(username, user.get("role", "user"))
    if not ok: return jsonify({"error": err}), 400
    # Раньше здесь не проверялись коллекция и Frigdmant: купленный или выставленный на продажу ник можно было
    # занять бесплатно. Свой ник из коллекции разрешаем.
    conflict = username_conflict(username, user["id"])
    if conflict: return jsonify({"error": conflict}), 400
    if is_username_on_frigdmant(username): return jsonify({"error": "Этот юзернейм сейчас на продаже на Frigdmant"}), 400
    # Раньше этим эндпоинтом можно было менять ник сколько угодно раз в обход лимита смен: считаем смену так же, как /api/profile/username.
    changing = bool(user.get("profile_completed")) and username.lower() != (user.get("username") or "").lower()
    if changing and user.get("role") not in ("owner", "developer") and count_username_changes_24h(user["id"]) >= USERNAME_CHANGE_LIMIT_24H:
        return jsonify({"error": f"Лимит {USERNAME_CHANGE_LIMIT_24H} смен @username за 24 часа"}), 429
    updated = update_user_profile(user["id"], username, first_name, last_name)
    if changing: register_username_change(user["id"])
    return jsonify({"ok": True, "user": user_to_dict(updated)})

@app.route("/api/profile/name", methods=["POST"])
def api_profile_name():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    first_name = str_field(data, "first_name")
    last_name = str_field(data, "last_name") or None
    if not first_name: return jsonify({"error": "Имя обязательно"}), 400
    if len(first_name) > 32 or (last_name and len(last_name) > 32): return jsonify({"error": "Имя слишком длинное"}), 400
    updated = update_user_name(user["id"], first_name, last_name)
    return jsonify({"ok": True, "user": user_to_dict(updated)})

@app.route("/api/profile/username", methods=["POST"])
def api_profile_username():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    username = str_field(data, "username").lstrip("@")
    min_len = 1 if user.get("role") in ("owner", "developer") else 4
    if len(username) < min_len: return jsonify({"error": f"Такие редкие юзернеймы (до {min_len} символов) можно купить на Frigdmant", "rare_username": True}), 400
    ok, err = validate_username(username, user.get("role", "user"))
    if not ok: return jsonify({"error": err}), 400
    if username.lower() == (user.get("username") or "").lower(): return jsonify({"error": "Это твой текущий @username"}), 400
    conflict = username_conflict(username, user["id"])
    if conflict: return jsonify({"error": conflict}), 400
    on_market = is_username_on_frigdmant(username)
    if on_market: return jsonify({"error": "Этот юзернейм сейчас на продаже", "on_frigdmant": True, "price": on_market["price"]}), 400
    used = count_username_changes_24h(user["id"])
    if used >= USERNAME_CHANGE_LIMIT_24H and user.get("role") not in ("owner", "developer"): return jsonify({"error": f"Лимит {USERNAME_CHANGE_LIMIT_24H} смен @username за 24 часа"}), 429
    updated = update_user_username(user["id"], username)
    register_username_change(user["id"])
    return jsonify({"ok": True, "user": user_to_dict(updated), "changes_left": USERNAME_CHANGE_LIMIT_24H - used - 1})

@app.route("/api/profile/bio", methods=["POST"])
def api_profile_bio():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    bio = data.get("bio")
    birthday = data.get("birthday")
    if bio is not None and not isinstance(bio, str): return jsonify({"error": "Неверный формат bio"}), 400
    if birthday is not None and (not isinstance(birthday, str) or len(birthday) > 32): return jsonify({"error": "Неверная дата рождения"}), 400
    if bio and len(bio) > 200: return jsonify({"error": "Bio максимум 200 символов"}), 400
    updated = update_user_bio(user["id"], bio, birthday)
    if not updated: return jsonify({"error": "Нечего обновлять"}), 400
    return jsonify({"ok": True, "user": user_to_dict(updated)})

@app.route("/api/profile/username-limit")
def api_username_limit():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    used = count_username_changes_24h(user["id"])
    return jsonify({"used": used, "limit": USERNAME_CHANGE_LIMIT_24H, "left": max(0, USERNAME_CHANGE_LIMIT_24H - used)})

@app.route("/api/settings/privacy", methods=["POST"])
def api_privacy():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    hide = data.get("hide_last_seen")
    who = data.get("who_can_message")
    if hide is not None and not isinstance(hide, bool): return jsonify({"error": "hide_last_seen: true или false"}), 400
    if who is not None and who not in ("all", "nobody"):
        return jsonify({"error": "who_can_message: all или nobody"}), 400
    update_privacy_settings(user["id"], hide, who)
    return jsonify({"ok": True})

@app.route("/api/sessions", methods=["GET"])
def api_sessions_get():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    cur = g.get("_sid")
    return jsonify({"sessions": [{"id": s["id"], "device": s.get("device"), "created_at": s.get("created_at"), "last_used": s.get("last_used"), "current": s["id"] == cur} for s in get_user_sessions(user["id"])]})

@app.route("/api/sessions/<int:sid>", methods=["DELETE"])
def api_sessions_del(sid):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    delete_session(sid, user["id"])
    return jsonify({"ok": True})

@app.route("/api/sessions/revoke_others", methods=["POST"])
def api_sessions_revoke_others():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    delete_other_sessions(user["id"], g.get("_sid"))
    return jsonify({"ok": True})

@app.route("/api/logout", methods=["POST"])
def api_logout():
    user = get_current_user()
    if not user: return jsonify({"ok": True})
    sid = g.get("_sid")
    if sid is not None: delete_session(sid, user["id"])
    return jsonify({"ok": True})

# ==================== USERS ====================
@app.route("/api/users/search")
def api_users_search():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    q = request.args.get("q", "").strip()
    if len(q) < 2: return jsonify({"users": [], "channels": []})
    users = search_users(q, user["id"], limit=20)
    channels = search_public_channels(q, limit=20)
    return jsonify({"users": [user_public_dict(u) for u in users], "channels": channels})

@app.route("/api/users/by_username/<username>")
def api_user_by_username(username):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    username = username.strip().lstrip("@")
    if not username: return jsonify({"error": "Username не указан"}), 400
    target = get_user_by_username(username)
    if not target: return jsonify({"exists": False, "username": username}), 404
    if target.get("banned"): return jsonify({"exists": False, "username": username, "banned": True}), 404
    return jsonify({"exists": True, "user": user_public_dict(target)})

@app.route("/api/users/<int:user_id>")
def api_user_profile(user_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    target = get_user_by_id(user_id)
    if not target: return jsonify({"error": "Юзер не найден"}), 404
    usernames_collection = [u["username"] for u in get_user_usernames(user_id)]
    public_data = user_public_dict(target)
    if target.get("banned"):
        public_data["first_name"] = "Banned"
        public_data["username"] = "Banned"
        public_data["bio"] = f"Пользователь был забанен. Причина: {target.get('ban_reason') or 'Нарушение правил'}"
        public_data["avatar_url"] = None
        public_data["banned"] = True
        public_data["ban_reason"] = target.get("ban_reason")
    return jsonify({"user": public_data, "collection": usernames_collection})

@app.route("/api/usernames/info/<username>")
def api_username_info(username):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    username = username.strip().lstrip("@")
    res = supabase.table("owned_usernames").select("user_id").eq("username", username).execute()
    owner_id = res.data[0]["user_id"] if res.data else None
    if not owner_id:
        u_res = supabase.table("users").select("id").eq("username", username).execute()
        if u_res.data: owner_id = u_res.data[0]["id"]
    if not owner_id: return jsonify({"error": "Владелец не найден"}), 404
    owner = get_user_by_id(owner_id)
    purchase = get_username_purchase_info(username)
    return jsonify({"username": username, "owner": user_public_dict(owner), "purchase": purchase, "reserved": is_username_reserved(username)})

# ==================== FRIGDMANT ====================
@app.route("/api/usernames/my")
def api_usernames_my():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"active": user.get("username"), "collection": get_user_usernames(user["id"])})

@app.route("/api/usernames/activate", methods=["POST"])
def api_usernames_activate():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if activate_username(user["id"], str_field(json_body(), "username").lstrip("@")): return jsonify({"ok": True})
    return jsonify({"error": "Не удалось активировать"}), 400

@app.route("/api/frigdmant/listings")
def api_frig_listings():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"market": get_frigdmant_listings(), "my_listings": get_user_listings(user["id"])})

@app.route("/api/frigdmant/list", methods=["POST"])
def api_frig_list():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    price = to_int(data.get("price"), 0)
    if price < 42: return jsonify({"error": "Мин. цена 42 Di"}), 400
    if price > 100_000_000: return jsonify({"error": "Слишком большая цена"}), 400
    uname = str_field(data, "username").lstrip("@")
    listing_type = data.get("listing_type", "buyout")
    if listing_type not in ("buyout", "auction"): return jsonify({"error": "Тип лота: buyout или auction"}), 400
    if not uname: return jsonify({"error": "Укажи юзернейм"}), 400
    if is_username_on_frigdmant(uname): return jsonify({"error": "Этот юзернейм уже выставлен на продажу"}), 400
    owned = [u["username"] for u in get_user_usernames(user["id"])]
    is_active_name = (user.get("username") or "").lower() == uname.lower()
    if uname not in owned and not is_active_name: return jsonify({"error": "Вы не владеете этим юзернеймом"}), 403
    # Сначала создаём лот и только потом убираем ник из коллекции: раньше при сбое создания лота ник пропадал
    # (удалён из коллекции, лота нет).
    listing = create_frigdmant_listing(user["id"], "user", user["id"], uname, price, listing_type)
    if not listing: return jsonify({"error": "Не удалось создать лот"}), 500
    if uname in owned:
        supabase.table("owned_usernames").delete().eq("user_id", user["id"]).eq("username", uname).execute()
    return jsonify({"ok": True})

@app.route("/api/frigdmant/buy", methods=["POST"])
def api_frig_buy():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    listing_id = to_int(json_body().get("listing_id"))
    if listing_id is None: return jsonify({"error": "listing_id обязателен"}), 400
    success, err = buy_frigdmant_listing(listing_id, user["id"])
    if success: return jsonify({"ok": True})
    return jsonify({"error": err}), 400

@app.route("/api/frigdmant/bid", methods=["POST"])
def api_frig_bid():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    listing_id = to_int(data.get("listing_id")); amount = to_int(data.get("amount"), 0)
    if listing_id is None: return jsonify({"error": "listing_id обязателен"}), 400
    if amount <= 0 or amount > 100_000_000: return jsonify({"error": "Неверная сумма ставки"}), 400
    success, err = place_frigdmant_bid(listing_id, user["id"], amount)
    if success: return jsonify({"ok": True})
    return jsonify({"error": err}), 400

@app.route("/api/frigdmant/cancel", methods=["POST"])
def api_frig_cancel():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    listing_id = to_int(json_body().get("listing_id"))
    if listing_id is not None and cancel_listing(listing_id, user["id"]): return jsonify({"ok": True})
    return jsonify({"error": "Нельзя отменить: лот не найден, уже закрыт или на него есть ставки"}), 400

# ==================== SHOP ====================
@app.route("/api/shop/items")
def api_shop_items():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    items = [{"id": k, "category": v["category"], "name": v["name"], "price": v["price"], "days": v.get("days"), "mb": v.get("mb")} for k, v in SHOP_ITEMS.items()]
    return jsonify({"items": items, "balance": get_balance(user["id"])})

@app.route("/api/shop/buy", methods=["POST"])
def api_shop_buy():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    item_id = data.get("item_id")
    if not isinstance(item_id, str) or item_id not in SHOP_ITEMS: return jsonify({"error": "Товар не найден"}), 404
    item = SHOP_ITEMS[item_id]
    if item["category"] == "pdg":
        success, err = activate_premium(user["id"], item["days"], int(item["price"]), f"Покупка {item['name']}")
    elif item["category"] == "file_plus":
        success, err = activate_file_plus(user["id"], item["days"], int(item["price"]), f"Покупка {item['name']}")
    elif item["category"] == "mb":
        success, err = spend_balance(user["id"], int(item["price"]), f"Покупка {item['name']}")
        if success: add_file_quota(user["id"], item["mb"] * 1024 * 1024)
    if not success: return jsonify({"error": err}), 400
    updated = get_user_by_id(user["id"])
    return jsonify({"ok": True, "user": user_to_dict(updated)})

@app.route("/api/transactions")
def api_transactions():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"transactions": get_user_transactions(user["id"], limit=50)})

# ==================== CHATS ====================
@app.route("/api/chats")
def api_chats():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not user.get("profile_completed"): return jsonify({"error": "Заполни профиль"}), 403
    update_last_seen(user["id"])
    return jsonify({"chats": get_user_chats(user["id"])})

@app.route("/api/all_list")
def api_all_list():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not user.get("profile_completed"): return jsonify({"error": "Заполни профиль"}), 403
    update_last_seen(user["id"])
    chats = get_user_chats(user["id"])
    channels = get_user_channels(user["id"])
    
    try:
        known = {c.get("chat_id") for c in chats}
        chats += [g_ for g_ in get_user_groups(user["id"]) if g_["chat_id"] not in known]
    except Exception as e:
        print(f"[ALL_LIST] Ошибка групп: {e}")

    items = chats + channels
    items.sort(key=lambda x: x.get("last_message_at") or "", reverse=True)
    return jsonify({"items": items})

@app.route("/api/chats", methods=["POST"])
def api_chats_create():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    other_id = to_int(data.get("user_id"))
    if not other_id: return jsonify({"error": "user_id обязателен"}), 400
    if other_id == user["id"]: return jsonify({"error": "Нельзя чатиться с самим собой"}), 400
    other = get_user_by_id(other_id)
    if not other: return jsonify({"error": "Юзер не найден"}), 404
    if other.get("banned") and not is_ban_expired(other): return jsonify({"error": "Пользователь заблокирован"}), 403
    if messaging_blocked(user, other): return jsonify({"error": "Этот пользователь не принимает сообщения", "privacy": "nobody"}), 403
    chat = get_or_create_chat(user["id"], other_id)
    if not chat: return jsonify({"error": "Не удалось создать чат"}), 500
    return jsonify({"ok": True, "chat_id": chat["id"]})

@app.route("/api/chats/group", methods=["POST"])
def api_create_group():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    name = str_field(data, "name")
    raw_ids = data.get("member_ids", [])
    if not name: return jsonify({"error": "Название группы обязательно"}), 400
    if len(name) > 64: return jsonify({"error": "Название слишком длинное"}), 400
    if not isinstance(raw_ids, list) or len(raw_ids) > 500: return jsonify({"error": "member_ids должен быть списком"}), 400
    # Раньше id не проверялись вовсе: можно было передать несуществующие, повторяющиеся, строки или тысячи значений.
    member_ids = []
    for raw in raw_ids:
        mid = to_int(raw)
        if mid is None: return jsonify({"error": "member_ids: только числовые id"}), 400
        if mid != user["id"] and mid not in member_ids: member_ids.append(mid)
    total_members = len(member_ids) + 1
    max_limit = 110 if check_premium(user) else 10
    if total_members > max_limit: return jsonify({"error": f"Лимит группы: {max_limit} человек. У тебя PDG: {check_premium(user)}"}), 400
    for mid in member_ids:
        m = get_user_by_id(mid)
        if not m: return jsonify({"error": f"Пользователь {mid} не найден"}), 404
        if m.get("banned") and not is_ban_expired(m): return jsonify({"error": f"Пользователь {mid} заблокирован"}), 400
        if messaging_blocked(user, m): return jsonify({"error": "Этот пользователь не принимает сообщения", "privacy": "nobody"}), 403
    chat = create_group_chat(user["id"], name, member_ids)
    if not chat: return jsonify({"error": "Ошибка создания группы"}), 500
    return jsonify({"ok": True, "chat_id": chat["id"]})

@app.route("/api/chats/<int:chat_id>/avatar", methods=["POST"])
def api_chat_avatar(chat_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    chat = get_chat_by_id(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    if not chat.get("is_group") or chat.get("owner_id") != user["id"]:
        return jsonify({"error": "Только владелец группы может менять аватар"}), 403
    if "file" not in request.files: return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    mime = file.mimetype or ""
    if mime not in ALLOWED_MIME: return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400
    try:
        file_bytes = file.read()
        processed, ext = _process_image(file_bytes, mime)
    except ValueError as e: return jsonify({"error": str(e)}), 400
    filename = f"group_{chat_id}_{int(time.time())}.{ext}"
    try:
        supabase.storage.from_("avatars").upload(filename, processed, file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"})
        public_url = supabase.storage.from_("avatars").get_public_url(filename)
    except Exception as e: return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500
    supabase.table("chats").update({"avatar_url": public_url}).eq("id", chat_id).execute()
    return jsonify({"ok": True, "avatar_url": public_url})

@app.route("/api/chats/<int:chat_id>/messages")
def api_chat_messages(chat_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    chat = get_chat_by_id(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    is_allowed = is_chat_member(chat_id, user["id"]) if chat.get("is_group") else user["id"] in (chat["user1_id"], chat["user2_id"])
    if not is_allowed: return jsonify({"error": "Нет доступа"}), 403
    limit = int_arg("limit", 50, 1, 100); offset = int_arg("offset", 0, 0, 100000)
    messages = sign_attachments(get_messages(chat_id, limit, offset))
    mark_messages_read(chat_id, user["id"]); update_last_seen(user["id"])
    if chat.get("is_group"):
        sender_names(messages)
        members = get_chat_members(chat_id)
        mu = get_users_by_ids(members)
        users_data = [user_public_dict(mu[uid]) for uid in members if uid in mu]
        return jsonify({"messages": messages, "other_user": {"is_group": True, "name": chat.get("name"), "avatar_url": chat.get("avatar_url"), "members": users_data, "owner_id": chat.get("owner_id")}})
    other_id = chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"]
    other = get_user_by_id(other_id)
    return jsonify({"messages": messages, "other_user": user_public_dict(other) if other else None})

@app.route("/api/chats/<int:chat_id>/read", methods=["POST"])
def api_chat_read(chat_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    chat = get_chat_by_id(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    is_allowed = is_chat_member(chat_id, user["id"]) if chat.get("is_group") else user["id"] in (chat["user1_id"], chat["user2_id"])
    if not is_allowed: return jsonify({"error": "Нет доступа"}), 403
    mark_messages_read(chat_id, user["id"])
    return jsonify({"ok": True})

@app.route("/api/chats/<int:chat_id>/members")
def api_chat_members(chat_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    chat = get_chat_by_id(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    if chat.get("is_group"):
        if not is_chat_member(chat_id, user["id"]): return jsonify({"error": "Нет доступа"}), 403
    else:
        if user["id"] not in (chat["user1_id"], chat["user2_id"]): return jsonify({"error": "Нет доступа"}), 403
    member_ids = get_chat_members(chat_id) if chat.get("is_group") else [chat["user1_id"], chat["user2_id"]]
    members = [user_public_dict(get_user_by_id(uid)) for uid in member_ids if get_user_by_id(uid)]
    return jsonify({"members": members, "is_owner": chat.get("owner_id") == user["id"] if chat.get("is_group") else False})

@app.route("/api/send", methods=["POST"])
def api_send():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    chat_id = to_int(data.get("chat_id")); text = str_field(data, "text")
    if not chat_id or not text: return jsonify({"error": "chat_id и text обязательны"}), 400
    if len(text) > 4000: return jsonify({"error": "Сообщение слишком длинное"}), 400
    # Антиспам: не больше 20 сообщений за 10 секунд с одного аккаунта (раньше лимита не было вообще).
    if not check_rate_limit(f"send_{user['id']}", 20, 10): return jsonify({"error": "Слишком часто. Подожди пару секунд."}), 429
    chat = get_chat_cached(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    if not has_chat_access(chat, user["id"]): return jsonify({"error": "Нет доступа"}), 403
    blocked = private_chat_blocked(chat, user)
    if blocked: return blocked
    client_id = str_field(data, "client_id")[:64] or None
    msg = save_message(chat_id, user["id"], text, "text", client_id=client_id)
    update_last_seen(user["id"])
    # Дио отвечает только в личных чатах (не в группах)
    if not chat.get("is_group"):
        other_id = chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"]
        if other_id == DIO_BOT_ID and user["id"] != DIO_BOT_ID:
            stripped = text.strip().lower()
            should_reply = len(stripped) >= 2 and stripped not in ("ок", "ok", "ага", "угу", "да", "нет")
            # Каждый ответ Дио — платный вызов Groq и отдельный поток: ограничиваем и по пользователю, и глобально.
            if should_reply and check_rate_limit(f"dio_{user['id']}", 8, 300) and check_rate_limit("dio_global", 120, 60):
                threading.Thread(target=process_ai_reply, args=(chat_id, text[:1000]), daemon=True).start()
    sign_attachments([msg])
    return jsonify({"ok": True, "message": msg})

@app.route("/api/messages/<int:message_id>", methods=["DELETE"])
def api_message_delete(message_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    msg = get_message_by_id(message_id)
    if not msg: return jsonify({"error": "Сообщение не найдено"}), 404
    if msg["sender_id"] != user["id"]: return jsonify({"error": "Можно удалять только свои"}), 403
    delete_message(message_id)
    return jsonify({"ok": True})

@app.route("/api/poll")
def api_poll():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    chat_id = to_int(request.args.get("chat_id")); after_id = to_int(request.args.get("after_id"))
    if not chat_id: return jsonify({"messages": []})
    chat = get_chat_cached(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    if not has_chat_access(chat, user["id"]): return jsonify({"error": "Нет доступа"}), 403
    update_last_seen(user["id"])
    if after_id is not None and after_id >= 0:
        msgs = get_messages_after_id(chat_id, after_id, 200)   # по id: быстрее и без потерь/повторов на границе времени
    else:
        after = (request.args.get("after") or "1970-01-01T00:00:00+00:00")[:64]  # старые клиенты
        msgs = supabase.table("messages").select("*").eq("chat_id", chat_id).gt("created_at", after).order("created_at").limit(200).execute().data or []
    sign_attachments(msgs)
    if chat.get("is_group") and msgs: sender_names(msgs)
    other_id = None if chat.get("is_group") else (chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"])
    now = time.time()
    typing_users = [uid for uid, ts in typing_store.get(chat_id, {}).items() if uid != user["id"] and (other_id is None or uid == other_id) and now - ts < 5]
    return jsonify({"messages": msgs, "typing": len(typing_users) > 0})

@app.route("/api/typing", methods=["POST"])
def api_typing():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    chat_id = to_int(data.get("chat_id"))
    if not chat_id: return jsonify({"error": "chat_id обязателен"}), 400
    # Раньше любой вошедший мог писать в typing_store любого chat_id (в том числе чужого и несуществующего):
    # это и спуфинг "печатает...", и бесконечный рост памяти.
    chat = get_chat_cached(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    if not has_chat_access(chat, user["id"]): return jsonify({"error": "Нет доступа"}), 403
    if not check_rate_limit(f"typing_{user['id']}", 30, 60): return jsonify({"ok": True})
    now = time.time()
    typing_store[chat_id][user["id"]] = now
    if len(typing_store) > 2000:  # подчищаем устаревшие отметки, чтобы словарь не рос бесконечно
        for cid in list(typing_store.keys()):
            typing_store[cid] = {uid: ts for uid, ts in typing_store[cid].items() if now - ts < 10}
            if not typing_store[cid]: typing_store.pop(cid, None)
    return jsonify({"ok": True})

def file_matches_mime(data, mime):
    """Грубая проверка сигнатуры: заявленный в запросе mime не должен противоречить содержимому.
    Заголовок Content-Type присылает клиент, поэтому одной ему верить нельзя (например, .exe под видом .pdf)."""
    head = data[:16]
    if mime == "application/pdf": return head.startswith(b"%PDF")
    if mime in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"): return head.startswith(b"PK\x03\x04")
    if mime in ("application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"): return head.startswith(b"\xd0\xcf\x11\xe0")
    if mime in ("audio/mpeg", "audio/mp3"): return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0)
    if mime in ("audio/wav", "audio/x-wav", "audio/wave"): return head.startswith(b"RIFF") and data[8:12] == b"WAVE"
    if mime in ("audio/mp4", "audio/x-m4a", "audio/m4a"): return data[4:8] == b"ftyp"
    if mime == "text/plain": return b"\x00" not in data[:4096]
    return False

# ==================== ВЛОЖЕНИЯ: приватный бакет chat-photos ====================
# Файлы лежат в закрытом бакете. В сообщениях сервер подменяет attachment_url на временную ссылку /api/f/<id>/<exp>/<подпись>:
# её получает только участник чата (проверка при выдаче сообщений), она живёт 1–2 часа и ведёт (302) на подписанный URL хранилища.
FILES_BUCKET = "chat-photos"
_FILE_KEY_RE = re.compile(r"/object/(?:public|sign|authenticated)/chat-photos/([^?#]+)")
FILE_LINK_BUCKET_SEC = 3600
_FILE_LINK_KEY = hmac.new((JWT_SECRET or secrets.token_hex(32)).encode("utf-8"), b"diogram-file-links", hashlib.sha256).digest()   # без JWT_SECRET ключ случайный: ссылки не подделать
_signed_cache = {}; _file_key_cache = {}

def _file_sig(mid, exp):
    return hmac.new(_FILE_LINK_KEY, f"{mid}.{exp}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]

def attachment_key(url):
    """Имя файла в бакете из сохранённого URL (в базе лежат ссылки вида .../object/public/chat-photos/<имя>)."""
    m = _FILE_KEY_RE.search(url or "")
    return urllib.parse.unquote(m.group(1)) if m else None

def file_link(mid):
    exp = (int(time.time()) // FILE_LINK_BUCKET_SEC + 2) * FILE_LINK_BUCKET_SEC   # одна ссылка на час: браузер может кэшировать
    return f"/api/f/{mid}/{exp}/{_file_sig(mid, exp)}"

def sign_attachments(messages):
    """Подменяет attachment_url на временную ссылку. Вызывать для каждого ответа, где сообщения уходят участнику чата."""
    for m in messages or []:
        if m and m.get("attachment_url") and m.get("id") is not None and attachment_key(m["attachment_url"]):
            m["attachment_url"] = file_link(m["id"])
    return messages

def _signed_file_url(key):
    hit = _cache_get(_signed_cache, key, 200)
    if hit is not _UNSET: return hit
    try: r = supabase.storage.from_(FILES_BUCKET).create_signed_url(key, 300) or {}
    except Exception as e:
        print(f"[files] подпись не удалась: {e}"); return None
    url = r.get("signedURL") or r.get("signedUrl") or r.get("signed_url")
    if not url: return None
    if url.startswith("/"): url = (os.environ.get("SUPABASE_URL", "").strip().rstrip("/")) + "/storage/v1" + url
    _cache_put(_signed_cache, key, url)
    return url

@app.route("/api/f/<int:mid>/<int:exp>/<sig>")
def api_file(mid, exp, sig):
    if not check_rate_limit(f"file_{request.remote_addr}", 600, 60): return jsonify({"error": "Слишком часто"}), 429
    if exp < time.time() or not hmac.compare_digest(sig.encode("utf-8"), _file_sig(mid, exp).encode("utf-8")):
        return jsonify({"error": "Ссылка устарела"}), 403
    key = _cache_get(_file_key_cache, mid, 3600)
    if key is _UNSET:
        key = attachment_key((get_message_by_id(mid) or {}).get("attachment_url")); _cache_put(_file_key_cache, mid, key)
    if not key: return jsonify({"error": "Файл не найден"}), 404
    url = _signed_file_url(key)
    if not url: return jsonify({"error": "Файл временно недоступен"}), 502
    resp = redirect(url, 302)
    resp.headers["Cache-Control"] = "private, max-age=120"   # меньше срока подписи (300 с), чтобы браузер не пошёл по протухшему адресу
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp

# Иконка монеты лежит в закрытом бакете, но показывается всем (в том числе до входа): отдаём её с сервера и кэшируем в памяти.
_coin = {"data": None, "at": 0.0}
@app.route("/assets/coin.jpg")
def asset_coin():
    now = time.time()
    if _coin["data"] is None or now - _coin["at"] > 3600:
        try:
            raw = supabase.storage.from_(FILES_BUCKET).download("Diogram.coin.di.jpg")
            try:   # оригинал ~750 КБ, а показывается значок 16–40 px: отдаём уменьшенную копию
                img = Image.open(io.BytesIO(raw)).convert("RGB"); img.thumbnail((160, 160), Image.LANCZOS)
                out = io.BytesIO(); img.save(out, format="JPEG", quality=88, optimize=True); raw = out.getvalue()
            except Exception as e: print(f"[coin resize] {e}")
            _coin["data"] = raw; _coin["at"] = now
        except Exception as e: print(f"[coin] {e}")
    if not _coin["data"]: return jsonify({"error": "Не найдено"}), 404
    resp = Response(_coin["data"], mimetype="image/jpeg"); resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

# ==================== UPLOAD ====================
def _process_image(file_bytes, mime):
    if len(file_bytes) > MAX_PHOTO_SIZE: raise ValueError("Файл больше 5 МБ")
    try:
        img = Image.open(io.BytesIO(file_bytes)); img.verify(); img = Image.open(io.BytesIO(file_bytes))
    except Exception: raise ValueError("Неверный формат изображения")
    output = io.BytesIO(); fmt = "JPEG"; ext = "jpg"
    if img.mode in ("RGBA", "P") and mime == "image/png":
        fmt = "PNG"; ext = "png"; img.save(output, format=fmt, optimize=True)
    else:
        if img.mode != "RGB": img = img.convert("RGB")
        if len(file_bytes) > COMPRESS_THRESHOLD or max(img.size) > MAX_DIMENSION:
            img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS); img.save(output, format=fmt, quality=82, optimize=True)
        else: img.save(output, format=fmt, quality=92, optimize=True)
    return output.getvalue(), ext

@app.route("/api/upload/avatar", methods=["POST"])
@serialized_per_user
def api_upload_avatar():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    can, next_date = can_change_avatar(user["id"])
    if not can: return jsonify({"error": "Аватарку можно менять раз в 7 дней (или с PDG)", "next_date": next_date.isoformat() if next_date else None}), 429
    if "file" not in request.files: return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    if not file.filename: return jsonify({"error": "Пустой файл"}), 400
    mime = file.mimetype or ""
    if mime not in ALLOWED_MIME: return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400
    try:
        file_bytes = file.read(); processed, ext = _process_image(file_bytes, mime)
    except ValueError as e: return jsonify({"error": str(e)}), 400
    filename = f"user_{user['id']}_{int(time.time())}.{ext}"
    try:
        supabase.storage.from_("avatars").upload(filename, processed, file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"})
        public_url = supabase.storage.from_("avatars").get_public_url(filename)
    except Exception as e: return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500
    updated = update_user_avatar(user["id"], public_url)
    return jsonify({"ok": True, "avatar_url": public_url, "user": user_to_dict(updated)})

@app.route("/api/upload/photo", methods=["POST"])
@serialized_per_user
def api_upload_photo():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    can, next_date = can_upload_photo(user["id"])
    if not can: return jsonify({"error": "Бесплатно можно 1 фото раз в 7 дней. Купи PDG для безлимита!", "next_date": next_date.isoformat() if next_date else None}), 429
    chat_id = to_int(request.form.get("chat_id"))
    if not chat_id: return jsonify({"error": "chat_id обязателен"}), 400
    chat = get_chat_by_id(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    is_allowed = is_chat_member(chat_id, user["id"]) if chat.get("is_group") else user["id"] in (chat["user1_id"], chat["user2_id"])
    if not is_allowed: return jsonify({"error": "Нет доступа"}), 403
    blocked = private_chat_blocked(chat, user)
    if blocked: return blocked
    if "file" not in request.files: return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    mime = file.mimetype or ""
    if mime not in ALLOWED_MIME: return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400
    caption = (request.form.get("caption") or "").strip()
    if len(caption) > 500: return jsonify({"error": "Подпись максимум 500 символов"}), 400
    try:
        file_bytes = file.read(); processed, ext = _process_image(file_bytes, mime)
    except ValueError as e: return jsonify({"error": str(e)}), 400
    # Случайное имя: раньше user_id + время легко перебирались, а бакет публичный
    filename = f"photo_{secrets.token_urlsafe(24)}.{ext}"
    try:
        supabase.storage.from_("chat-photos").upload(filename, processed, file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"})
        public_url = supabase.storage.from_("chat-photos").get_public_url(filename)
    except Exception as e: return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500
    msg = save_message(chat_id, user["id"], caption, "photo", public_url, len(processed), filename)
    register_photo_upload(user["id"])
    sign_attachments([msg])
    return jsonify({"ok": True, "message": msg})

@app.route("/api/upload/file", methods=["POST"])
@serialized_per_user
def api_upload_file():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_file_plus(user): return jsonify({"error": "Нужна подписка File Plus"}), 403
    chat_id = to_int(request.form.get("chat_id"))
    if not chat_id: return jsonify({"error": "chat_id обязателен"}), 400
    chat = get_chat_by_id(chat_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    is_allowed = is_chat_member(chat_id, user["id"]) if chat.get("is_group") else user["id"] in (chat["user1_id"], chat["user2_id"])
    if not is_allowed: return jsonify({"error": "Нет доступа"}), 403
    blocked = private_chat_blocked(chat, user)
    if blocked: return blocked
    if "file" not in request.files: return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    mime = file.mimetype or ""
    if mime not in ALLOWED_FILE_MIME: return jsonify({"error": "Такой формат не поддерживается"}), 400
    file_bytes = file.read(); size = len(file_bytes)
    if size > MAX_FILE_SIZE: return jsonify({"error": "Файл больше 3 МБ"}), 400
    if not size: return jsonify({"error": "Пустой файл"}), 400
    if not file_matches_mime(file_bytes, mime): return jsonify({"error": "Содержимое файла не соответствует его типу"}), 400
    total_quota = FILE_QUOTA_BASE + (user.get("file_quota_bytes", 0) or 0)
    used = get_file_quota_used(user["id"])
    if used + size > total_quota:
        return jsonify({"error": f"Лимит исчерпан. Осталось {max(0, (total_quota - used) // (1024 * 1024))} МБ. Докупи в магазине!", "used": used, "total": total_quota}), 429
    ext = ALLOWED_FILE_MIME[mime]
    safe_name = re.sub(r"[^\w\-_.]", "_", file.filename or f"file.{ext}")
    filename = f"file_{secrets.token_urlsafe(24)}.{ext}"
    try:
        supabase.storage.from_("chat-photos").upload(filename, file_bytes, file_options={"content-type": mime})
        public_url = supabase.storage.from_("chat-photos").get_public_url(filename)
    except Exception as e: return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500
    file_category = "audio" if mime.startswith("audio/") else "document"
    msg = save_message(chat_id, user["id"], "", "file", public_url, size, safe_name, file_type=file_category, file_mime=mime)
    register_file_upload(user["id"], size)
    sign_attachments([msg])
    return jsonify({"ok": True, "message": msg})

@app.route("/api/file-quota")
def api_file_quota():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    total = FILE_QUOTA_BASE + (user.get("file_quota_bytes", 0) or 0)
    used = get_file_quota_used(user["id"])
    return jsonify({"used": used, "total": total, "left": max(0, total - used), "has_file_plus": check_file_plus(user)})

@app.route("/api/photo-limit")
def api_photo_limit():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    can, next_date = can_upload_photo(user["id"])
    return jsonify({"can": can, "next_date": next_date.isoformat() if next_date else None, "has_pdg": check_premium(user)})

# ==================== CHANNELS ====================
@app.route("/api/channels", methods=["POST"])
@serialized_per_user
def api_channels_create():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if count_user_channels(user["id"]) >= 4: return jsonify({"error": "Максимум 4 канала на аккаунт"}), 400
    data = json_body()
    name = str_field(data, "name"); username = str_field(data, "username").lstrip("@")
    description = str_field(data, "description") or None; ch_type = data.get("type", "public")
    if description and len(description) > 500: return jsonify({"error": "Описание максимум 500 символов"}), 400
    if not name: return jsonify({"error": "Название обязательно"}), 400
    if len(name) > 64: return jsonify({"error": "Название слишком длинное"}), 400
    if ch_type == "secret":
        if user.get("role") not in ("owner", "developer"): return jsonify({"error": "Только разработчики могут создавать глобальные каналы"}), 403
        username = None
    else:
        ch_type = "public"
        if not username: return jsonify({"error": "Для публичного канала нужен @username"}), 400
        ok, err = validate_username(username, "user")
        if not ok: return jsonify({"error": err}), 400
        existing = get_channel_by_username(username)
        if existing: return jsonify({"error": "Этот @username канала занят"}), 400
        conflict = username_conflict(username, user["id"])
        if conflict: return jsonify({"error": conflict}), 400
        if is_username_on_frigdmant(username): return jsonify({"error": "Этот юзернейм сейчас на продаже на Frigdmant"}), 400
    ch = create_channel(user["id"], name, username, description, ch_type)
    if not ch: return jsonify({"error": "Ошибка создания"}), 500
    if ch_type == "secret":
        for u in get_all_users_admin(limit=10000): subscribe_to_channel(ch["id"], u["id"])
    else: subscribe_to_channel(ch["id"], user["id"])
    return jsonify({"ok": True, "channel": ch})

@app.route("/api/channels/search")
def api_channels_search():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    q = request.args.get("q", "").strip()
    if len(q) < 2: return jsonify({"channels": []})
    result = []
    for ch in search_public_channels(q, limit=20):
        owner = get_user_by_id(ch["owner_id"]); subs = supabase.table("channel_subscribers").select("id").eq("channel_id", ch["id"]).execute()
        result.append({"id": ch["id"], "name": ch["name"], "username": ch.get("username"), "description": ch.get("description"), "avatar_url": ch.get("avatar_url"), "subscribers_count": len(subs.data or []), "is_subscribed": is_subscribed(ch["id"], user["id"]), "owner_name": owner.get("first_name") if owner else None})
    return jsonify({"channels": result})

@app.route("/api/channels/<int:channel_id>/info")
def api_channel_info(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch: return jsonify({"error": "Канал не найден"}), 404
    subs = supabase.table("channel_subscribers").select("id").eq("channel_id", channel_id).execute()
    owner = get_user_by_id(ch["owner_id"])
    return jsonify({"channel": {"id": ch["id"], "name": ch["name"], "username": ch.get("username"), "description": ch.get("description"), "type": ch["type"], "avatar_url": ch.get("avatar_url"), "subscribers_count": len(subs.data or []), "is_owner": ch["owner_id"] == user["id"], "is_subscribed": is_subscribed(channel_id, user["id"]), "created_at": ch.get("created_at"), "linked_group": ({"id": lg["id"], "name": lg.get("name")} if (lg := linked_group(ch)) and ch["owner_id"] == user["id"] else None), "comments_enabled": linked_group(ch) is not None, "owner": user_public_dict(owner) if owner else None}})

@app.route("/api/channels/<int:channel_id>/update", methods=["POST"])
def api_channel_update(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Нет доступа"}), 403
    data = json_body()
    name = data.get("name"); username = data.get("username"); ch_type = data.get("type")
    if (name is not None and not isinstance(name, str)) or (username is not None and not isinstance(username, str)): return jsonify({"error": "Неверный формат"}), 400
    if name is not None:
        name = name.strip()
        if not name: return jsonify({"error": "Название не может быть пустым"}), 400
        if len(name) > 64: return jsonify({"error": "Название максимум 64 символа"}), 400
    if ch_type is not None and ch_type not in ("public", "secret"): return jsonify({"error": "Тип: public или secret"}), 400
    if ch_type is not None and ch_type != ch["type"]:
        if user.get("role") not in ("owner", "developer"): return jsonify({"error": "Только разработчики могут менять тип"}), 403
    final_type = ch_type if ch_type is not None else ch["type"]
    if final_type == "public":
        if username is None: username = ch.get("username")
        if not username or not username.strip(): return jsonify({"error": "Для публичного канала нужен @username"}), 400
        username = username.strip().lstrip("@")
        ok, err = validate_username(username, "user")
        if not ok: return jsonify({"error": err}), 400
        if username.lower() != (ch.get("username") or "").lower():
            existing = get_channel_by_username(username)
            if existing and existing["id"] != channel_id: return jsonify({"error": "Этот @username канала занят"}), 400
            conflict = username_conflict(username, user["id"])
            if conflict: return jsonify({"error": conflict}), 400
            if is_username_on_frigdmant(username): return jsonify({"error": "Этот юзернейм сейчас на продаже на Frigdmant"}), 400
    else: username = None
    updated = update_channel_full(channel_id, name=name if name is not None else ch["name"], username=username, ch_type=final_type)
    if final_type == "public": subscribe_to_channel(channel_id, user["id"])
    return jsonify({"ok": True, "channel": updated})

@app.route("/api/channels/<int:channel_id>/avatar", methods=["POST"])
def api_channel_avatar(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Нет доступа"}), 403
    if "file" not in request.files: return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]; mime = file.mimetype or ""
    if mime not in ALLOWED_MIME: return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400
    try:
        file_bytes = file.read(); processed, ext = _process_image(file_bytes, mime)
    except ValueError as e: return jsonify({"error": str(e)}), 400
    filename = f"ch_{channel_id}_{int(time.time())}.{ext}"
    try:
        supabase.storage.from_("avatars").upload(filename, processed, file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"})
        public_url = supabase.storage.from_("avatars").get_public_url(filename)
    except Exception as e: return jsonify({"error": f"Ошибка загрузки: {str(e)}"}), 500
    updated = update_channel_avatar(channel_id, public_url)
    return jsonify({"ok": True, "avatar_url": public_url, "channel": updated})

@app.route("/api/channels/<int:channel_id>/subscribe", methods=["POST"])
def api_channels_subscribe(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch: return jsonify({"error": "Канал не найден"}), 404
    if ch["type"] == "secret": return jsonify({"error": "На глобальный канал нельзя подписаться вручную"}), 403
    subscribe_to_channel(channel_id, user["id"])
    return jsonify({"ok": True})

@app.route("/api/channels/<int:channel_id>/unsubscribe", methods=["POST"])
def api_channels_unsubscribe(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch: return jsonify({"error": "Канал не найден"}), 404
    if ch["type"] == "secret": return jsonify({"error": "От глобального канала нельзя отписаться"}), 403
    unsubscribe_from_channel(channel_id, user["id"])
    return jsonify({"ok": True})

@app.route("/api/channels/<int:channel_id>/messages")
def api_channel_messages(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch: return jsonify({"error": "Канал не найден"}), 404
    if not is_subscribed(channel_id, user["id"]) and ch["owner_id"] != user["id"]: return jsonify({"error": "Ты не подписан"}), 403
    limit = int_arg("limit", 50, 1, 100); offset = int_arg("offset", 0, 0, 100000)
    messages = decorate_channel_messages(get_channel_messages(channel_id, limit, offset), user["id"])
    subs = supabase.table("channel_subscribers").select("id").eq("channel_id", channel_id).execute()
    owner = get_user_by_id(ch["owner_id"])
    return jsonify({"messages": messages, "channel": {"id": ch["id"], "name": ch["name"], "username": ch.get("username"), "description": ch.get("description"), "type": ch["type"], "avatar_url": ch.get("avatar_url"), "subscribers_count": len(subs.data or []), "is_owner": ch["owner_id"] == user["id"], "comments_enabled": linked_group(ch) is not None, "owner": user_public_dict(owner) if owner else None}})

def linked_group(ch):
    """Привязанная к каналу группа (или None, если не привязана / группу удалили)."""
    cid = ch.get("linked_chat_id")
    if not cid: return None
    chat = get_chat_by_id(cid)
    return chat if chat and chat.get("is_group") else None

def parse_poll(data):
    """Проверяет опрос из запроса. Возвращает (poll, error)."""
    raw = data.get("poll")
    if raw is None: return None, None
    if not isinstance(raw, dict): return None, "Некорректный опрос"
    question = str_field(raw, "question")
    options = raw.get("options")
    if not question or len(question) > 255: return None, "Вопрос опроса: 1–255 символов"
    if not isinstance(options, list): return None, "Некорректные варианты"
    clean = []
    for o in options:
        if not isinstance(o, str): return None, "Некорректные варианты"
        o = o.strip()
        if o: clean.append(o)
    if len(clean) < 2 or len(clean) > 10: return None, "Нужно от 2 до 10 вариантов"
    if any(len(o) > 100 for o in clean): return None, "Вариант длиннее 100 символов"
    if len({o.lower() for o in clean}) != len(clean): return None, "Варианты не должны повторяться"
    return {"question": question, "options": clean, "multiple": bool(raw.get("multiple"))}, None

@app.route("/api/channels/<int:channel_id>/send", methods=["POST"])
def api_channel_send(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch: return jsonify({"error": "Канал не найден"}), 404
    if ch["owner_id"] != user["id"]: return jsonify({"error": "Только владелец может писать"}), 403
    if not check_rate_limit(f"chsend_{user['id']}", 20, 10): return jsonify({"error": "Слишком часто. Подожди пару секунд."}), 429
    data = json_body()
    poll, err = parse_poll(data)
    if err: return jsonify({"error": err}), 400
    text = poll["question"] if poll else str_field(data, "text")
    if not text: return jsonify({"error": "Текст обязателен"}), 400
    if len(text) > 4000: return jsonify({"error": "Слишком длинное"}), 400
    lg = linked_group(ch)
    msg = save_channel_post(channel_id, user["id"], text, poll, lg["id"] if lg else None)
    return jsonify({"ok": True, "message": msg})

@app.route("/api/channels/<int:channel_id>/groups")
def api_channel_groups(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Нет доступа"}), 403
    return jsonify({"groups": get_owned_groups(user["id"]), "linked_chat_id": (linked_group(ch) or {}).get("id")})

@app.route("/api/channels/<int:channel_id>/link_group", methods=["POST"])
def api_channel_link_group(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Нет доступа"}), 403
    data = json_body()
    if data.get("chat_id") is None:
        set_channel_linked_chat(channel_id, None)
        return jsonify({"ok": True, "linked": False})
    chat_id = to_int(data.get("chat_id"))
    chat = get_chat_by_id(chat_id) if chat_id else None
    if not chat or not chat.get("is_group") or chat.get("owner_id") != user["id"]:
        return jsonify({"error": "Привязать можно только свою группу"}), 403
    if not set_channel_linked_chat(channel_id, chat_id):
        return jsonify({"error": "Эта группа уже привязана к другому каналу"}), 409
    return jsonify({"ok": True, "linked": True})

def channel_reader(channel_id, user):
    """(канал, ошибка-ответ): канал доступен подписчикам и владельцу."""
    ch = get_channel_by_id(channel_id)
    if not ch: return None, (jsonify({"error": "Канал не найден"}), 404)
    if ch["owner_id"] != user["id"] and not is_subscribed(channel_id, user["id"]):
        return None, (jsonify({"error": "Ты не подписан"}), 403)
    return ch, None

@app.route("/api/channels/<int:channel_id>/messages/<int:msg_id>/vote", methods=["POST"])
def api_channel_vote(channel_id, msg_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch, err = channel_reader(channel_id, user)
    if err: return err
    if not check_rate_limit(f"vote_{user['id']}", 30, 60): return jsonify({"error": "Слишком часто"}), 429
    post = get_channel_message(msg_id, channel_id)
    if not post or not post.get("poll"): return jsonify({"error": "Опрос не найден"}), 404
    n = len(post["poll"].get("options") or [])
    raw = json_body().get("options")
    if not isinstance(raw, list) or len(raw) > n: return jsonify({"error": "Некорректный выбор"}), 400
    picked = []
    for v in raw:
        i = to_int(v)
        if i is None or not (0 <= i < n): return jsonify({"error": "Некорректный вариант"}), 400
        if i not in picked: picked.append(i)
    if len(picked) > 1 and not post["poll"].get("multiple"): return jsonify({"error": "В этом опросе один ответ"}), 400
    try: set_poll_votes(msg_id, user["id"], picked)
    except Exception: return jsonify({"error": "Не удалось проголосовать, попробуй ещё раз"}), 409
    return jsonify({"ok": True, "poll_results": decorate_channel_messages([post], user["id"])[0]["poll_results"]})

def comments_context(channel_id, msg_id, user):
    """(канал, пост, группа, ошибка-ответ) для комментариев к посту."""
    ch, err = channel_reader(channel_id, user)
    if err: return None, None, None, err
    post = get_channel_message(msg_id, channel_id)
    if not post: return None, None, None, (jsonify({"error": "Пост не найден"}), 404)
    lg = linked_group(ch)
    if not lg or not post.get("discussion_message_id") or lg["id"] != (get_message_by_id(post["discussion_message_id"]) or {}).get("chat_id"):
        return None, None, None, (jsonify({"error": "Комментарии к этому посту недоступны"}), 404)
    return ch, post, lg, None

@app.route("/api/channels/<int:channel_id>/messages/<int:msg_id>/comments")
def api_channel_comments(channel_id, msg_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch, post, lg, err = comments_context(channel_id, msg_id, user)
    if err: return err
    comments = get_post_comments(post["discussion_message_id"], int_arg("limit", 100, 1, 200), int_arg("after_id", 0, 0, 10**12))
    users = {}
    for c in comments:
        sid = c["sender_id"]
        if sid not in users: users[sid] = user_public_dict(get_user_by_id(sid))
        c["sender"] = users[sid]
    return jsonify({"comments": comments, "post": {"id": post["id"], "text": post["text"], "poll": bool(post.get("poll"))}})

@app.route("/api/channels/<int:channel_id>/messages/<int:msg_id>/comments", methods=["POST"])
def api_channel_comment_send(channel_id, msg_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch, post, lg, err = comments_context(channel_id, msg_id, user)
    if err: return err
    text = str_field(json_body(), "text")
    if not text: return jsonify({"error": "Текст обязателен"}), 400
    if len(text) > 2000: return jsonify({"error": "Слишком длинный комментарий"}), 400
    if not check_rate_limit(f"comment_{user['id']}", 10, 30): return jsonify({"error": "Слишком часто. Подожди пару секунд."}), 429
    c = save_post_comment(lg["id"], user["id"], post["discussion_message_id"], text)
    c["sender"] = user_public_dict(user)
    return jsonify({"ok": True, "comment": c})

@app.route("/api/channels/<int:channel_id>", methods=["DELETE"])
def api_channel_delete(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Нет доступа"}), 403
    delete_channel(channel_id)
    return jsonify({"ok": True})

@app.route("/api/channels/<int:channel_id>/subscribers")
def api_channel_subscribers(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Только владелец видит список"}), 403
    subs = get_channel_subscribers(channel_id)
    return jsonify({"subscribers": [user_public_dict(u) for u in subs]})

@app.route("/api/channels/<int:channel_id>/messages/<int:msg_id>", methods=["DELETE"])
def api_channel_msg_delete(channel_id, msg_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Нет доступа"}), 403
    post = get_channel_message(msg_id, channel_id)
    if not post or not delete_channel_message(msg_id, channel_id): return jsonify({"error": "Сообщение не найдено в этом канале"}), 404
    delete_channel_post_mirror(post.get("discussion_message_id"))
    return jsonify({"ok": True})

# ==================== REPORTS ====================
@app.route("/api/reports", methods=["POST"])
def api_report_create():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    used = count_reports_by_user_24h(user["id"])
    if used >= REPORT_LIMIT_24H: return jsonify({"error": f"Лимит {REPORT_LIMIT_24H} жалоб в сутки"}), 429
    data = json_body()
    reason = str_field(data, "reason"); comment = str_field(data, "comment") or None
    if comment and len(comment) > 1000: return jsonify({"error": "Комментарий максимум 1000 символов"}), 400
    reported_user_id = to_int(data.get("reported_user_id")); reported_message_id = to_int(data.get("reported_message_id")); reported_channel_id = to_int(data.get("reported_channel_id"))
    if reason not in REPORT_REASONS: return jsonify({"error": "Неверная причина"}), 400
    if not (reported_user_id or reported_message_id or reported_channel_id): return jsonify({"error": "Нужно указать на кого/что жалуешься"}), 400
    if reported_user_id and reported_user_id == user["id"]: return jsonify({"error": "Нельзя жаловаться на себя"}), 400
    r = create_report(reporter_id=user["id"], reason=reason, comment=comment, reported_user_id=reported_user_id, reported_message_id=reported_message_id, reported_channel_id=reported_channel_id)
    return jsonify({"ok": True, "report_id": r["id"] if r else None})

@app.route("/api/reports/reasons")
def api_report_reasons(): return jsonify({"reasons": REPORT_REASONS})

# ==================== ADMIN ====================
def require_admin():
    user = get_current_user()
    if not user or user.get("role") not in ("owner", "developer"): return None
    return user

def require_owner():
    user = get_current_user()
    if not user or user.get("role") != "owner": return None
    return user

@app.route("/api/admin/stats")
def api_admin_stats():
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    users_res = supabase.table("users").select("id", count="exact").execute()
    channels_res = supabase.table("channels").select("id", count="exact").execute()
    msgs_res = supabase.table("messages").select("id", count="exact").execute()
    new_reports = count_new_reports()
    five_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    online_res = supabase.table("users").select("id", count="exact").gt("last_seen", five_mins_ago).execute()
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    recent_users = supabase.table("users").select("created_at").gt("created_at", seven_days_ago).execute()
    dates = [u["created_at"][:10] for u in (recent_users.data or [])]
    activity_data = [{"date": k, "count": v} for k, v in sorted(Counter(dates).items())]
    return jsonify({"total_users": users_res.count or 0, "total_channels": channels_res.count or 0, "total_messages": msgs_res.count or 0, "new_reports": new_reports, "online_5m": online_res.count or 0, "activity": activity_data})

@app.route("/api/admin/users")
def api_admin_users():
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    limit = int_arg("limit", 100, 1, 500); offset = int_arg("offset", 0, 0, 100000)
    users = get_all_users_admin(limit, offset)
    return jsonify({"users": [user_to_dict(u) for u in users], "me": user_to_dict(admin)})

@app.route("/api/admin/set_role", methods=["POST"])
def api_admin_set_role():
    admin = require_owner()
    if not admin: return jsonify({"error": "Только владелец"}), 403
    data = json_body()
    target_id = data.get("user_id"); role = data.get("role")
    if role not in ("user", "moderator", "developer"): return jsonify({"error": "Неверная роль"}), 400
    if target_id == admin["id"]: return jsonify({"error": "Нельзя менять свою роль"}), 400
    updated = update_user_role(target_id, role)
    if not updated: return jsonify({"error": "Юзер не найден"}), 404
    return jsonify({"ok": True})

@app.route("/api/admin/set_verified", methods=["POST"])
def api_admin_set_verified():
    admin = require_owner()
    if not admin: return jsonify({"error": "Только владелец"}), 403
    data = json_body()
    target_id = data.get("user_id"); verified = bool(data.get("verified"))
    updated = update_user_verified(target_id, verified)
    if not updated: return jsonify({"error": "Юзер не найден"}), 404
    return jsonify({"ok": True})

@app.route("/api/admin/add_balance", methods=["POST"])
def api_admin_add_balance():
    admin = require_owner()
    if not admin: return jsonify({"error": "Только владелец"}), 403
    data = json_body()
    target_id = data.get("user_id"); amount = data.get("amount"); description = (data.get("description") or "Пополнение от админа").strip()
    try: amount = int(amount)
    except Exception: return jsonify({"error": "Неверная сумма"}), 400
    if amount <= 0 or amount > 1000000: return jsonify({"error": "Сумма: 1 - 1 000 000"}), 400
    target = get_user_by_id(target_id)
    if not target: return jsonify({"error": "Юзер не найден"}), 404
    if not add_balance(target_id, amount, description, admin["id"]): return jsonify({"error": "Ошибка начисления"}), 500
    return jsonify({"ok": True})

@app.route("/api/admin/reports")
def api_admin_reports():
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    status_filter = request.args.get("status")
    if status_filter not in ("new", "reviewed", "rejected", None): status_filter = None
    reports = get_reports(status_filter=status_filter, limit=100)
    return jsonify({"reports": reports})

@app.route("/api/admin/reports/<int:report_id>/review", methods=["POST"])
def api_admin_report_review(report_id):
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    data = json_body()
    status = data.get("status")
    if status not in ("reviewed", "rejected"): return jsonify({"error": "Статус: reviewed или rejected"}), 400
    update_report_status(report_id, status, admin["id"])
    return jsonify({"ok": True})

def can_ban(admin, target):
    if not admin or not target: return False
    if target["id"] == admin["id"]: return False
    ar = admin.get("role"); tr = target.get("role", "user")
    if ar == "owner": return tr != "owner"
    if ar == "developer": return tr not in ("owner", "developer")
    if ar == "moderator": return tr == "user"
    return False

@app.route("/api/admin/ban", methods=["POST"])
def api_admin_ban():
    admin = get_current_user()
    if not admin or admin.get("role") not in ("owner", "developer", "moderator"): return jsonify({"error": "Нет доступа"}), 403
    data = json_body()
    target_id = to_int(data.get("user_id")); reason = str_field(data, "reason"); duration_days = data.get("duration_days")
    if not target_id or not reason: return jsonify({"error": "user_id и reason обязательны"}), 400
    if len(reason) > 500: return jsonify({"error": "Причина слишком длинная (максимум 500 символов)"}), 400
    target = get_user_by_id(target_id)
    if not target: return jsonify({"error": "Юзер не найден"}), 404
    if not can_ban(admin, target): return jsonify({"error": "Недостаточно прав"}), 403
    # Срок проверяем ДО отмены лотов и возврата ставок: раньше при неверном сроке лоты уже были отменены, а бан не применялся.
    until = None
    if duration_days:
        days = to_int(duration_days)
        if days is None or days <= 0 or days > 3650: return jsonify({"error": "Срок: 1-3650 дней"}), 400
        until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    # Отмена лотов и возврат юзернеймов
    try:
        listings = supabase.table("frigdmant_listings").select("*").eq("seller_id", target_id).eq("status", "active").execute()
        for lot in (listings.data or []):
            if lot.get("listing_type") == "auction" and lot.get("current_bid", 0) > 0 and lot.get("highest_bidder_id"):
                add_balance(lot["highest_bidder_id"], lot["current_bid"], "Возврат ставки (продавец забанен)", admin["id"])
            supabase.table("frigdmant_listings").update({"status": "cancelled"}).eq("id", lot["id"]).execute()
            # Возвращаем юзернейм в коллекцию владельцу
            try:
                supabase.table("owned_usernames").insert({
                    "user_id": lot["seller_id"],
                    "username": lot["username"]
                }).execute()
            except Exception as e:
                print(f"[BAN] Не удалось вернуть юзернейм: {e}")
    except Exception as e: print(f"[BAN] Ошибка отмены лотов: {e}")
    ban_user(target_id, reason, admin["id"], until)
    return jsonify({"ok": True})

@app.route("/api/admin/unban", methods=["POST"])
def api_admin_unban():
    admin = get_current_user()
    if not admin or admin.get("role") not in ("owner", "developer"): return jsonify({"error": "Нет доступа"}), 403
    data = json_body()
    target_id = to_int(data.get("user_id"))
    if not target_id: return jsonify({"error": "user_id обязателен"}), 400
    unban_user(target_id)
    return jsonify({"ok": True})

# ==================== РЕЗЕРВ ЮЗЕРНЕЙМОВ (только owner) ====================
@app.route("/api/admin/reserved")
def api_admin_reserved():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    rows = get_reserved_usernames()
    holders = get_users_by_ids([r["user_id"] for r in rows])
    return jsonify({"reserved": [{"username": r["username"], "holder_id": r["user_id"], "holder_username": (holders.get(r["user_id"]) or {}).get("username")} for r in rows]})

@app.route("/api/admin/reserved/add", methods=["POST"])
def api_admin_reserved_add():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    raw = json_body().get("usernames")
    if isinstance(raw, str): raw = re.split(r"[\s,;]+", raw)
    if not isinstance(raw, list): return jsonify({"error": "Нужен список юзернеймов"}), 400
    names = []
    for n in raw:
        if isinstance(n, str) and n.strip().lstrip("@") and n.strip().lstrip("@").lower() not in [x.lower() for x in names]: names.append(n.strip().lstrip("@"))
    if not names: return jsonify({"error": "Список пуст"}), 400
    if len(names) > 50: return jsonify({"error": "Не больше 50 имён за раз"}), 400
    added, skipped = [], []
    for n in names:
        ok, err = validate_username(n, "owner")
        if not ok: skipped.append({"username": n, "reason": err}); continue
        conflict = username_conflict(n, None)
        if not conflict and get_channel_by_username(n): conflict = "Этот юзернейм занят каналом"
        if not conflict and is_username_on_frigdmant(n): conflict = "Этот юзернейм сейчас на продаже на Frigdmant"
        if conflict: skipped.append({"username": n, "reason": conflict}); continue
        if add_reserved_username(admin["id"], n): added.append(n)
        else: skipped.append({"username": n, "reason": "Не удалось сохранить"})
    return jsonify({"ok": True, "added": added, "skipped": skipped})

@app.route("/api/admin/reserved/give", methods=["POST"])
def api_admin_reserved_give():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    data = json_body(); name = str_field(data, "username").lstrip("@"); uid = to_int(data.get("user_id"))
    if not name or uid is None: return jsonify({"error": "Нужны username и user_id"}), 400
    target = get_user_by_id(uid)
    if not target: return jsonify({"error": "Пользователь с таким ID не найден"}), 404
    if not give_reserved_username(name, uid): return jsonify({"error": "Это имя не в резерве"}), 404
    return jsonify({"ok": True, "to": target.get("username")})

@app.route("/api/admin/reserved/remove", methods=["POST"])
def api_admin_reserved_remove():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    name = str_field(json_body(), "username").lstrip("@")
    if not name or not remove_reserved_username(name): return jsonify({"error": "Это имя не в резерве"}), 404
    return jsonify({"ok": True})

# ==================== WORKER ====================
@app.route("/api/worker/get_pending")
def worker_get_pending():
    if not worker_authorized(): return jsonify({"error": "Unauthorized"}), 401
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    supabase.table("pending_codes").delete().lt("created_at", cutoff).execute()
    res = supabase.table("pending_codes").select("*").eq("sent", False).order("created_at").limit(1).execute()
    if not res.data: return jsonify({"email": None, "code": None})
    task = res.data[0]
    return jsonify({"email": task["email"], "code": task["code"]})

@app.route("/api/worker/task_done", methods=["POST"])
def worker_task_done():
    if not worker_authorized(): return jsonify({"error": "Unauthorized"}), 401
    data = json_body()
    email = data.get("email")
    if isinstance(email, str) and email: supabase.table("pending_codes").update({"sent": True}).eq("email", email.lower()).execute()
    return jsonify({"ok": True})
