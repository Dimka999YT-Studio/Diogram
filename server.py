import os
import hmac
import secrets
import jwt
import time
import io
import re
import random
import threading
import struct
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
    get_archived, set_archived, MAX_ARCHIVED_PER_USER, set_reaction, get_reactions, set_channel_reaction,
    email_exists, create_user, UserExists, get_user_by_email,
    get_user_by_id, get_user_by_username, search_users, get_collection_holder_user,
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
    update_privacy_settings, get_user_sessions, delete_session, leave_group, is_chat_linked_to_channel, create_session, session_alive, touch_session, delete_other_sessions,
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
import game
import promo
import captcha
import game_world
import game_chatlog
import rainbow
import daily
import tgbot
import gaming
import gold
import vnumbers
import tg_game
import orgs

_flask_jsonify = jsonify
def jsonify(*args, **kwargs):
    """Как flask.jsonify, но каждому пользователю в ответе дописывает orgs (значки организаций, orgs.annotate)."""
    try:
        if len(args) == 1 and not kwargs: orgs.annotate(args[0])
        elif kwargs and not args: orgs.annotate(kwargs)
    except Exception as e: print(f"[orgs] annotate {e!r}")
    return _flask_jsonify(*args, **kwargs)

JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()
JWT_ALGO = "HS256"
JWT_EXP_DAYS = 30
WORKER_KEY = os.environ.get("WORKER_KEY", "").strip()
TG_BOT_KEY = os.environ.get("TG_BOT_KEY", "").strip()   # ключ Telegram-бота @DiogramRobot; пусто = маршруты /api/tgbot/* закрыты
TG_GAME_KEY = os.environ.get("TG_GAME_KEY", "").strip()   # ключ сервера Telegram-игры (награды за задания); пусто = маршруты закрыты

# ==================== AI (GROQ) ====================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
if GROQ_API_KEY:
    http_client = httpx.Client(trust_env=False, timeout=30.0)
    groq_client = Groq(api_key=GROQ_API_KEY, http_client=http_client)
else:
    groq_client = None
DIO_BOT_ID = 1000011

# ==================== СЕЗОННАЯ ИГРА (аккаунт @Play) ====================
# Любое сообщение аккаунту GAME_BOT_ID получает ответ-анонс с кнопкой «Играть» (не чаще раза в 5 минут на человека).
# Сезон и текст меняются здесь. Сама игра (сюжет, очки, топ-3) подключается отдельно.
GAME_BOT_ID = 1000014
MSK = timezone(timedelta(hours=3))
GAME_SEASON = {"title": "Осенний сезон", "ends_at": datetime(2026, 12, 1, 0, 0, tzinfo=MSK)}
GAME_INVITE_TEXT = ("🍂 Осенний сезон\n\n"
                    "В осенний сезон у меня было 3 праздника. Сейчас ноябрь, скоро зима и новый вайб, поэтому играй в игру и получи шанс выиграть призы! 🎁\n\n"
                    "⏳ Конец игры: 1 декабря, 00:00 (МСК)")
GAME_ENDED_TEXT = "🍂 Осенний сезон закончился. Следи за новостями: скоро будет новый сезон!"

def game_active():
    return datetime.now(timezone.utc) < GAME_SEASON["ends_at"]

ALLOWED_DOMAINS = ["gmail.com", "yandex.com", "yandex.ru", "mail.ru", "outlook.com", "list.ru", "bk.ru", "inbox.ru"]
BLOCKED_KEYWORDS = ["temp", "10minute", "guerrilla", "throwaway", "yopmail", "mailinator"]
PHOTO_LIMIT_15_DAYS = 8
REPORT_LIMIT_24H = 5
API_RATE_PER_MIN = 600        # запросов в минуту с одного аккаунта (обычная работа — десятки; защита от забивания 16 потоков)
SEARCH_RATE_PER_MIN = 30
MAX_OWNED_GROUPS = 20
GROUPS_CREATED_PER_HOUR = 5
USERNAME_CHANGE_LIMIT_24H = 10

MAX_PHOTO_SIZE = 5 * 1024 * 1024
MAX_DIMENSION = 1600
COMPRESS_THRESHOLD = 4 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
# Видео-аватарка: только владелец и разработчики, ролик (MP4/WebM) до 10 МБ и до 1 минуты.
VIDEO_AVATAR_ROLES = ("owner", "developer")
VIDEO_AVATAR_MIME = {"video/mp4", "video/webm"}
VIDEO_AVATAR_MAX = 10 * 1024 * 1024   # бакет avatars должен разрешать не меньше (file_size_limit); MAX_REQUEST_BYTES (12 МБ) это покрывает
# Бренды ftyp, которые видео-аватаркой быть не могут: картинки HEIF/AVIF/RAW, аудио, QuickTime (.mov).
# Остальные бренды (isom, mp42, MSNV, 3gp4, avc1, f4v и прочие от телефонов и редакторов) пропускаем: настоящую проверку делает чтение длительности из moov/mvhd.
_NOT_VIDEO_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis", b"crx ", b"jp2 ", b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B ", b"qt  ")

VIDEO_AVATAR_MAX_SEC = 60

def _ebml_vint(data, pos, strip):
    """Число переменной длины (EBML). strip=False для id элемента (с маркером), True для размера. -> (значение, длина, «размер неизвестен»)."""
    if pos >= len(data): return None
    b = data[pos]; ln = 1; mask = 0x80
    while ln <= 8 and not (b & mask): ln += 1; mask >>= 1
    if ln > 8 or pos + ln > len(data): return None
    val = (b & (mask - 1)) if strip else b
    for i in range(1, ln): val = (val << 8) | data[pos + i]
    return val, ln, bool(strip and val == (1 << (7 * ln)) - 1)

def _webm_duration(data):
    """Длительность WebM в секундах из Segment/Info/Duration или None, если в заголовке её нет."""
    st = {"scale": 1000000, "dur": None}
    def walk(pos, end):
        n = 0
        while pos < end and n < 5000:
            n += 1
            i = _ebml_vint(data, pos, False)
            if not i: return
            eid, il, _ = i
            sz = _ebml_vint(data, pos + il, True)
            if not sz: return
            size, sl, unknown = sz
            body = pos + il + sl; bend = end if unknown else min(body + size, end)
            if eid in (0x18538067, 0x1549A966): walk(body, bend)          # Segment, Info: заходим внутрь
            elif eid == 0x2AD7B1: st["scale"] = int.from_bytes(data[body:bend], "big") or st["scale"]   # TimecodeScale
            elif eid == 0x4489:                                               # Duration (float)
                raw = data[body:bend]
                if len(raw) == 4: st["dur"] = struct.unpack(">f", raw)[0]
                elif len(raw) == 8: st["dur"] = struct.unpack(">d", raw)[0]
            elif eid == 0x1F43B675: return                                    # Cluster: дальше только данные
            if unknown: return
            pos = bend
    walk(0, len(data))
    d = st["dur"]
    return d * st["scale"] / 1e9 if d is not None and d == d and d >= 0 else None

def _mp4_duration(data):
    """Длительность MP4 в секундах из moov/mvhd или None."""
    def boxes(pos, end):
        n = 0
        while pos + 8 <= end and n < 5000:
            n += 1
            size = int.from_bytes(data[pos:pos + 4], "big"); typ = data[pos + 4:pos + 8]; hdr = 8
            if size == 1:
                if pos + 16 > end: return
                size = int.from_bytes(data[pos + 8:pos + 16], "big"); hdr = 16
            elif size == 0: size = end - pos
            if size < hdr: return
            yield typ, pos + hdr, min(pos + size, end)
            pos += size
    for typ, b, e in boxes(0, len(data)):
        if typ != b"moov": continue
        for t2, b2, e2 in boxes(b, e):
            if t2 != b"mvhd" or b2 >= e2: continue
            if data[b2] == 1 and b2 + 32 <= e2: ts = int.from_bytes(data[b2 + 20:b2 + 24], "big"); d = int.from_bytes(data[b2 + 24:b2 + 32], "big")
            elif data[b2] == 0 and b2 + 20 <= e2: ts = int.from_bytes(data[b2 + 12:b2 + 16], "big"); d = int.from_bytes(data[b2 + 16:b2 + 20], "big")
            else: return None
            return d / ts if ts else None
    return None

def video_duration(data, kind):
    try: return _mp4_duration(data) if kind == "mp4" else _webm_duration(data)
    except Exception: return None

def sniff_video(data):
    """'mp4' / 'webm' по содержимому (заявленный клиентом mime нельзя считать доказательством) или None."""
    if len(data) > 12 and data[4:8] == b"ftyp" and data[8:12] not in _NOT_VIDEO_BRANDS: return "mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3": return "webm"
    return None

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
CACHE_KEEP_PATHS = ("/api/send", "/api/typing", "/api/poll", "/api/game/tick", "/api/game/chat")
@app.after_request
def invalidate_caches_on_write(resp):
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/") \
       and request.path not in CACHE_KEEP_PATHS and not request.path.endswith("/read") and "/comments" not in request.path and "/vote" not in request.path and not request.path.endswith("/react"):
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
    if recipient["id"] in (DIO_BOT_ID, GAME_BOT_ID, gaming.BOT_ID, tg_game.BOT_ID): return False
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
    if not check_rate_limit(f"api_{user['id']}", API_RATE_PER_MIN, 60): return jsonify({"error": "Слишком много запросов. Подожди немного.", "rate_limited": True}), 429
    return None

# ==================== ЗАЩИТНЫЕ ЗАГОЛОВКИ ====================
# Клиент — один файл с inline-скриптами, поэтому 'unsafe-inline' для скриптов нужен. Остальное закрыто: запросы только на свой сервер
# (украденный токен нельзя отправить на чужой адрес через fetch), картинки и аудио только с себя и из хранилища Supabase,
# страницу нельзя встроить в чужую (clickjacking), плагины и <base> запрещены.
def _csp():
    sb = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    # Только наш проект: подстановка *.supabase.co пускала бы картинки с любого чужого проекта Supabase (канал утечки данных).
    # Без SUPABASE_URL (локальный запуск) остаётся запасной вариант с подстановкой.
    host = urllib.parse.urlparse(sb).hostname if sb.startswith("https://") else None
    storage = ("https://" + host) if host else "https://*.supabase.co"
    return ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: blob: {storage}; media-src 'self' blob: {storage}; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
CSP_HEADER = _csp()

@app.after_request
def security_headers(resp):
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if resp.mimetype == "text/html": h["Content-Security-Policy"] = CSP_HEADER
    if request.is_secure: h.setdefault("Strict-Transport-Security", "max-age=15552000")
    return resp

@app.errorhandler(Exception)
def unhandled_error(e):
    """Любая необработанная ошибка: в лог подробности, клиенту только общий текст (раньше мог утечь текст исключения)."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException): return e
    print(f"[ERROR] {request.method} {request.path}: {e!r}")
    return jsonify({"error": "Ошибка сервера. Попробуй ещё раз."}), 500

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

def username_conflict(username, acting_user_id=None, as_channel=False):
    """Текст ошибки, если @username уже кем-то занят (активный ник, коллекция/резерв, канал), иначе None.
    Свои имена acting_user_id не считаются конфликтом. Раньше сообщение было безликим «занят»,
    а каналы вообще не проверяли коллекции и ники пользователей."""
    existing = get_user_by_username(username)
    # Для канала активный ник даже своего владельца считается занятым: иначе @Smart оказывался и у пользователя, и у канала.
    if existing and (as_channel or existing["id"] != acting_user_id):
        return f"Этот юзернейм занят пользователем @{existing.get('username') or username}"
    if not as_channel:   # каналы раньше вообще не проверялись: пользователь мог занять @username чужого канала
        ch = get_channel_by_username(username)
        if ch: return f"Этот юзернейм занят каналом @{ch.get('username') or username}"
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
# ==================== КАПЧА (при подозрительной активности) ====================
def captcha_gate(kind, key):
    """None, если капча не нужна или пропуск (заголовок X-Captcha) верный; иначе ответ 428 «нужна капча»."""
    if not captcha.is_flagged(kind, key): return None
    if captcha.consume_pass(request.headers.get("X-Captcha")): return None
    return jsonify({"error": "Подтверди, что ты человек", "captcha_required": True, "trigger": kind}), 428

# Версия приложения: APP_VERSION поднимаем вручную при релизе, а «сборка» берётся из коммита, который выкатил хостинг.
APP_VERSION = "1.0.4"
APP_BUILD = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("SOURCE_VERSION") or os.environ.get("GIT_COMMIT") or "")[:7]

@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION, "build": APP_BUILD})

@app.route("/api/captcha/challenge")
def api_captcha_challenge():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"capch_{ip}", 60, 300): return jsonify({"error": "Слишком часто. Подожди немного."}), 429
    return jsonify({"token": captcha.make_challenge(request.args.get("trigger", ""))})

@app.route("/api/captcha/verify", methods=["POST"])
def api_captcha_verify():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"capver_{ip}", 40, 300): return jsonify({"error": "Слишком много попыток. Подожди пару минут."}), 429
    data = json_body()
    token, err = captcha.verify(data.get("token"), data.get("strokes"), str_field(data, "device"))
    if err: return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True, "pass": token})

@app.route("/api/register", methods=["POST"])
def api_register():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"reg_{ip}", 10, 3600): return jsonify({"error": "Слишком много попыток. Подожди час."}), 429
    # Всплеск регистраций с одного адреса: с 4-й попытки в час просим капчу.
    if not check_rate_limit(f"regburst_{ip}", 3, 3600): captcha.flag("register", ip)
    gate = captcha_gate("register", ip)
    if gate: return gate
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
    try: user = create_user(email=email, password=password)
    except UserExists: return jsonify({"error": "Этот email уже зарегистрирован"}), 400
    if not user: return jsonify({"error": "Ошибка создания аккаунта"}), 500
    subs_count = subscribe_new_user_to_secret_channels(user["id"])
    token = start_session(user["id"])
    return jsonify({"ok": True, "token": token, "user": user_to_dict(user), "auto_subscribed_channels": subs_count})

@app.route("/api/login", methods=["POST"])
def api_login():
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(f"login_{ip}", 10, 300): return jsonify({"error": "Слишком много попыток. Подожди 5 минут."}), 429
    gate = captcha_gate("login", ip)
    if gate: return gate
    data = json_body()
    email = str_field(data, "email").lower()
    password = str_field(data, "password", strip=False)
    # Вход по почте или по виртуальному номеру (+999 XXXX XXXX): без «@» поле считается номером.
    vnum = vnumbers.normalize(email) if email and "@" not in email else None
    ident = ("num:" + vnum) if vnum else email
    # Лимит по аккаунту (в дополнение к лимиту по IP): подбор пароля с множества адресов упирается в него.
    if ident and not check_rate_limit(f"login_email_{ident}", 20, 900): return jsonify({"error": "Слишком много попыток входа в этот аккаунт. Подожди 15 минут."}), 429
    user = None
    if vnum:
        try:
            uid_by_num = vnumbers.user_id_by_number(vnum)
            user = get_user_by_id(uid_by_num) if uid_by_num else None
        except Exception as e: print(f"[vnumbers login] {e!r}")
    elif email: user = get_user_by_email(email)
    if not user or not check_password(password, user["password_hash"]):
        if not check_rate_limit(f"loginfail_{ip}", 3, 600): captcha.flag("login", ip)   # после 3 неудачных входов с адреса (4-я попытка и далее) нужна капча
        return jsonify({"error": "Неверный email, номер или пароль"}), 401
    is_banned, ban_reason = check_banned(user)
    if is_banned: return banned_response(user, ban_reason)
    update_last_seen(user["id"])
    token = start_session(user["id"])
    return jsonify({"ok": True, "token": token, "user": {**user_to_dict(user), "vnumber": vnumbers.main_text(user["id"])}})

@app.route("/api/me")
def api_me():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    is_banned, ban_reason = check_banned(user)
    if is_banned: return banned_response(user, ban_reason)
    update_last_seen(user["id"])
    try:
        if check_rate_limit(f"refsettle_{user['id']}", 1, 120): gold.settle(user["id"])   # засчитать приглашение, если условия выполнены
    except Exception as e: print(f"[ref settle] {e!r}")
    return jsonify({"user": {**user_to_dict(user), "vnumber": vnumbers.main_text(user["id"])}})

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

def ensure_current_session(user):
    """(id сессии этого устройства, новый токен или None). Токен без sid выдан до появления сессий: у него нет строки в user_sessions,
    поэтому «это устройство» не находилось, а «завершить все остальные» удаляло вообще все сессии аккаунта (keep=None).
    Такому токену заводим сессию и отдаём новый токен, клиент его сохраняет."""
    sid = g.get("_sid")
    if sid is not None: return sid, None
    try: sid = create_session(user["id"], device_label(request.headers.get("User-Agent", "")))
    except Exception as e:
        print(f"[session] не удалось создать сессию для старого токена: {e!r}"); return None, None
    if sid is None: return None, None
    return sid, make_token(user["id"], sid)

@app.route("/api/sessions", methods=["GET"])
def api_sessions_get():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    cur, new_token = ensure_current_session(user)
    out = {"sessions": [{"id": s["id"], "device": s.get("device"), "created_at": s.get("created_at"), "last_used": s.get("last_used"), "current": cur is not None and s["id"] == cur} for s in get_user_sessions(user["id"])]}
    if new_token: out["token"] = new_token
    return jsonify(out)

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
    cur, new_token = ensure_current_session(user)
    # Не знаем, какая сессия текущая: лучше отказать, чем удалить все (включая эту)
    if cur is None: return jsonify({"error": "Не удалось определить текущую сессию. Попробуй ещё раз."}), 500
    delete_other_sessions(user["id"], cur)
    out = {"ok": True}
    if new_token: out["token"] = new_token
    return jsonify(out)

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
    if not check_rate_limit(f"search_{user['id']}", SEARCH_RATE_PER_MIN, 60): return jsonify({"error": "Слишком часто. Подожди немного."}), 429
    users = search_users(q, user["id"], limit=20)
    channels = search_public_channels(q, limit=20)
    out = []
    for u in users:
        d = user_public_dict(u)
        if u.get("matched_username"): d["collection_username"] = u["matched_username"]   # найден по имени из коллекции
        out.append(d)
    return jsonify({"users": out, "channels": channels})

@app.route("/api/users/by_username/<username>")
def api_user_by_username(username):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    username = username.strip().lstrip("@")
    if not username: return jsonify({"error": "Username не указан"}), 400
    target = get_user_by_username(username)
    via_collection = False
    if not target:   # имя может лежать в коллекции пользователя (коллекционный юзернейм)
        target = get_collection_holder_user(username); via_collection = bool(target)
    if not target:
        ch = get_channel_by_username(username)
        if ch and ch.get("type") == "public": return jsonify({"exists": False, "username": username, "channel": {"id": ch["id"], "name": ch.get("name"), "username": ch.get("username")}})
        return jsonify({"exists": False, "username": username}), 404
    if target.get("banned"): return jsonify({"exists": False, "username": username, "banned": True}), 404
    return jsonify({"exists": True, "user": user_public_dict(target), "via_collection": via_collection})

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
    try: tournament = game.username_series_info(username)
    except Exception as e: print(f"[username_info tournament] {e!r}"); tournament = None
    try: promo_info = promo.username_info(username)
    except Exception as e: print(f"[username_info promo] {e!r}"); promo_info = None
    return jsonify({"username": username, "owner": user_public_dict(owner), "purchase": purchase, "reserved": is_username_reserved(username), "tournament": tournament, "promo": promo_info})

# ==================== ПРОМОКОДЫ ====================
@app.route("/api/promo/activate", methods=["POST"])
def api_promo_activate():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"promo_{user['id']}", 10, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    got, err = promo.claim(user["id"], str_field(json_body(), "code"))
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "rewards": got})

@app.route("/api/admin/promo")
def api_admin_promo():
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    return jsonify({"codes": promo.list_codes(), "cases": {k: v["title"] for k, v in game.CASES.items()}})

@app.route("/api/admin/promo/create", methods=["POST"])
def api_admin_promo_create():
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    if not check_rate_limit(f"promo_create_{admin['id']}", 20, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    d = json_body()
    rewards, err = promo.clean_rewards(d.get("rewards"))
    if err: return jsonify({"error": err}), 400
    names = []
    if rewards.get("username"):
        raw = d.get("usernames")
        if isinstance(raw, str): raw = re.split(r"[\s,;]+", raw)
        if not isinstance(raw, list): return jsonify({"error": "Для награды-юзернейма нужен список имён"}), 400
        for n in raw:
            n = n.strip().lstrip("@") if isinstance(n, str) else ""
            if n and n.lower() not in [x.lower() for x in names]: names.append(n)
        if not names: return jsonify({"error": "Для награды-юзернейма нужен список имён"}), 400
        if len(names) > 100: return jsonify({"error": "Не больше 100 имён"}), 400
        for n in names:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_]{0,31}", n): return jsonify({"error": f"Юзернейм {n}: только латиница, цифры, _"}), 400
            if game.is_prize_username(n) or promo.username_info(n) is not None or supabase.table("promo_usernames").select("username").ilike("username", n).limit(1).execute().data:
                return jsonify({"error": f"Имя {n} уже в призах или в другом промокоде"}), 400
            if not is_username_reserved(n):
                conflict = username_conflict(n, None)
                if not conflict and get_channel_by_username(n): conflict = "Этот юзернейм занят каналом"
                if not conflict and is_username_on_frigdmant(n): conflict = "Этот юзернейм сейчас на продаже на Frigdmant"
                if conflict: return jsonify({"error": f"{n}: {conflict}"}), 400
    kind = str_field(d, "kind")
    row, err = promo.create_code(admin["id"], d.get("code"), kind, rewards, d.get("expires_at"), d.get("max_activations"), str_field(d, "note"))
    if err: return jsonify({"error": err}), 400
    for n in names:   # резерв владельца ставим только после успешного создания кода
        if not is_username_reserved(n) and not add_reserved_username(admin["id"], n):
            return jsonify({"error": f"Код создан, но имя {n} не удалось зарезервировать"}), 500
    try: promo.add_usernames(row["id"], names)
    except Exception as e: print(f"[promo names] {e!r}"); return jsonify({"error": "Код создан, но имена привязать не удалось"}), 500
    return jsonify({"ok": True, "id": row["id"]})

@app.route("/api/admin/promo/<int:code_id>/toggle", methods=["POST"])
def api_admin_promo_toggle(code_id):
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    promo.set_active(code_id, bool(json_body().get("active")))
    return jsonify({"ok": True})

@app.route("/api/admin/promo/<int:code_id>/activations")
def api_admin_promo_activations(code_id):
    admin = require_admin()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    rows = promo.code_activations(code_id)
    users = get_users_by_ids([r["user_id"] for r in rows])
    return jsonify({"activations": [{"user_id": r["user_id"], "username": (users.get(r["user_id"]) or {}).get("username"), "created_at": r["created_at"]} for r in rows]})

# ---- капча: данные для владельца ----
@app.route("/api/admin/captcha")
def api_admin_captcha():
    owner = require_owner()
    if not owner: return jsonify({"error": "Нет доступа"}), 403
    quality = request.args.get("quality", "none")
    passed = {"1": True, "0": False}.get(request.args.get("passed", ""))
    return jsonify({"samples": captcha.list_samples(quality if quality in ("none", "good", "bad", "all") else "none", passed), "stats": captcha.stats()})

@app.route("/api/admin/captcha/export")
def api_admin_captcha_export():
    owner = require_owner()
    if not owner: return jsonify({"error": "Нет доступа"}), 403
    return jsonify({"policy_version": captcha.POLICY_VERSION, "samples": captcha.export_good()})

@app.route("/api/admin/captcha/<int:sample_id>")
def api_admin_captcha_one(sample_id):
    owner = require_owner()
    if not owner: return jsonify({"error": "Нет доступа"}), 403
    row = captcha.get_sample(sample_id)
    if not row: return jsonify({"error": "Не найдено"}), 404
    return jsonify({"sample": row})

@app.route("/api/admin/captcha/<int:sample_id>/rate", methods=["POST"])
def api_admin_captcha_rate(sample_id):
    owner = require_owner()
    if not owner: return jsonify({"error": "Нет доступа"}), 403
    q = json_body().get("quality")
    if not captcha.rate_sample(sample_id, q if q in ("good", "bad") else None): return jsonify({"error": "Неверная оценка"}), 400
    return jsonify({"ok": True})

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
    if game.is_prize_username(uname): return jsonify({"error": "Это личный коллекционный юзернейм турнира, его нельзя продать"}), 403
    if is_username_on_frigdmant(uname): return jsonify({"error": "Этот юзернейм уже выставлен на продажу"}), 400
    if data.get("target_type") == "channel":
        # Продажа юзернейма публичного канала: имя остаётся на канале до продажи, после неё канал остаётся без ссылки.
        ch = get_channel_by_id(to_int(data.get("channel_id"), 0))
        if not ch or ch["owner_id"] != user["id"]: return jsonify({"error": "Это не твой канал"}), 403
        if ch.get("type") != "public" or (ch.get("username") or "").lower() != uname.lower(): return jsonify({"error": "У канала нет такого публичного юзернейма"}), 400
        listing = create_frigdmant_listing(user["id"], "channel", ch["id"], ch["username"], price, listing_type)
        if not listing: return jsonify({"error": "Не удалось создать лот"}), 500
        return jsonify({"ok": True})
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
    arch = get_archived(user["id"])
    for it in items:
        it["archived"] = (("channel", it.get("channel_id")) if it.get("type") == "channel" else ("chat", it.get("chat_id"))) in arch
    items.sort(key=lambda x: x.get("last_message_at") or "", reverse=True)
    return jsonify({"items": items})

@app.route("/api/archive", methods=["POST"])
def api_archive():
    """Убрать чат/группу/канал в личный архив или вернуть из него: {type: "chat"|"channel", id, archived: bool}."""
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    kind = data.get("type"); target = to_int(data.get("id")); archived = data.get("archived", True)
    if kind not in ("chat", "channel") or not target or not isinstance(archived, bool): return jsonify({"error": "Некорректный запрос"}), 400
    if not check_rate_limit(f"archive_{user['id']}", 60, 60): return jsonify({"error": "Слишком часто"}), 429
    if kind == "chat":
        chat = get_chat_by_id(target)
        if not chat: return jsonify({"error": "Чат не найден"}), 404
        if not has_chat_access(chat, user["id"]): return jsonify({"error": "Нет доступа"}), 403
    else:
        ch, err = channel_reader(target, user)
        if err: return err
    if archived and len(get_archived(user["id"])) >= MAX_ARCHIVED_PER_USER: return jsonify({"error": "Архив переполнен"}), 400
    set_archived(user["id"], kind, target, archived)
    return jsonify({"ok": True})

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
    staff = user.get("role") in STAFF_ROLES
    if not staff:
        # владение без участия (последний участник вышел) не занимает квоту
        if len([g for g in get_owned_groups(user["id"]) if is_chat_member(g["id"], user["id"])]) >= MAX_OWNED_GROUPS:   return jsonify({"error": f"Можно владеть не больше чем {MAX_OWNED_GROUPS} группами"}), 400
        if not check_rate_limit(f"group_create_{user['id']}", GROUPS_CREATED_PER_HOUR, 3600): return jsonify({"error": "Слишком много новых групп. Подожди час."}), 429
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
    except Exception as e:
        print(f"[upload] {e!r}"); return jsonify({"error": "Не удалось загрузить файл. Попробуй ещё раз."}), 500
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
    rx = reactions_summary(get_reactions(chat_id, messages[0]["id"] if messages else 0), user["id"])
    rv = _reaction_ver[chat_id]
    if chat.get("is_group"):
        sender_names(messages)
        members = get_chat_members(chat_id)
        mu = get_users_by_ids(members)
        users_data = [user_public_dict(mu[uid]) for uid in members if uid in mu]
        return jsonify({"messages": messages, "reactions": rx, "rv": rv, "other_user": {"is_group": True, "name": chat.get("name"), "avatar_url": chat.get("avatar_url"), "members": users_data, "owner_id": chat.get("owner_id")}})
    other_id = chat["user2_id"] if chat["user1_id"] == user["id"] else chat["user1_id"]
    other = get_user_by_id(other_id)
    return jsonify({"messages": messages, "reactions": rx, "rv": rv, "other_user": user_public_dict(other) if other else None})

@app.route("/api/chats/<int:chat_id>/leave", methods=["POST"])
def api_chat_leave(chat_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    chat = get_chat_by_id(chat_id)
    if not chat or not chat.get("is_group"): return jsonify({"error": "Группа не найдена"}), 404
    if not is_chat_member(chat_id, user["id"]): return jsonify({"error": "Ты не участник этой группы"}), 403
    if chat.get("owner_id") == user["id"] and is_chat_linked_to_channel(chat_id):
        return jsonify({"error": "Группа привязана к каналу. Сначала отвяжи её в настройках канала."}), 409
    leave_group(chat_id, user["id"])   # владелец выходит — права переходят к самому давнему участнику
    return jsonify({"ok": True})

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
    found = get_users_by_ids(member_ids)   # раньше два запроса к базе на каждого участника
    members = [user_public_dict(found[uid]) for uid in member_ids if uid in found]
    return jsonify({"members": members, "is_owner": chat.get("owner_id") == user["id"] if chat.get("is_group") else False})

@app.route("/api/send", methods=["POST"])
def api_send():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    chat_id = to_int(data.get("chat_id")); text = strip_fwd_mark(str_field(data, "text")).strip()
    if not chat_id or not text: return jsonify({"error": "chat_id и text обязательны"}), 400
    if len(text) > 4000: return jsonify({"error": "Сообщение слишком длинное"}), 400
    # Антиспам: не больше 20 сообщений за 10 секунд с одного аккаунта (раньше лимита не было вообще).
    gate = captcha_gate("send", user["id"])
    if gate: return gate
    if not check_rate_limit(f"send_{user['id']}", 20, 10):
        captcha.flag("send", user["id"])   # упёрся в антиспам: следующие отправки только после капчи
        return jsonify({"error": "Слишком часто. Подожди пару секунд."}), 429
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
        elif other_id == GAME_BOT_ID and user["id"] != GAME_BOT_ID:
            try:
                cmd = re.fullmatch(r"/case(?:\s+(\d{1,12}))?", text.strip(), re.I)
                if cmd and user.get("role") in ("owner", "developer"):   # команда выдачи кейсов: только владелец и разработчики
                    target = get_user_by_id(int(cmd.group(1))) if cmd.group(1) else None
                    if not cmd.group(1): save_message(chat_id, GAME_BOT_ID, "Команда: /Case ID_пользователя\nНапример: /Case 1000016", "text")
                    elif not target: save_message(chat_id, GAME_BOT_ID, f"Пользователь с ID {int(cmd.group(1))} не найден.", "text")
                    else: save_message(chat_id, GAME_BOT_ID, f"🎁 Выдача кейсов\nПолучатель: {target.get('first_name') or target.get('username') or 'без имени'}" + (f" (@{target['username']})" if target.get("username") else "") + f"\nID {target['id']}", "game_case_give")
                elif check_rate_limit(f"gamebot_{user['id']}", 1, 300):
                    if game_active(): save_message(chat_id, GAME_BOT_ID, GAME_INVITE_TEXT, "game_invite")
                    else: save_message(chat_id, GAME_BOT_ID, GAME_ENDED_TEXT, "text")
            except Exception as e: print(f"[game] {e!r}")
        elif other_id == gaming.BOT_ID and user["id"] != gaming.BOT_ID:
            try:
                if check_rate_limit(f"gamingbot_{user['id']}", 1, 300): save_message(chat_id, gaming.BOT_ID, gaming.INVITE_TEXT, "gaming_invite")
            except Exception as e: print(f"[gaming] {e!r}")
        elif other_id == tg_game.BOT_ID and user["id"] != tg_game.BOT_ID:
            try:
                if check_rate_limit(f"arrowbot_{user['id']}", 1, 300): save_message(chat_id, tg_game.BOT_ID, tg_game.INVITE_TEXT, "arrow_invite")
            except Exception as e: print(f"[arrow] {e!r}")
    sign_attachments([msg])
    return jsonify({"ok": True, "message": msg})

FORWARD_TYPES = ("text", "photo", "file")

@app.route("/api/messages/<int:message_id>/forward", methods=["POST"])
def api_forward_message(message_id):
    """Переслать сообщение (текст, фото или файл) в другой свой чат или группу. Копируем только то, что пользователь
    сам видит: доступ к исходному чату и к целевому проверяем так же, как при обычной отправке."""
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    target_id = to_int(json_body().get("chat_id"))
    if not target_id: return jsonify({"error": "chat_id обязателен"}), 400
    if not check_rate_limit(f"fwd_{user['id']}", 20, 10) or not check_rate_limit(f"fwd_min_{user['id']}", 60, 60):
        return jsonify({"error": "Слишком часто. Подожди пару секунд."}), 429
    src = get_message_by_id(message_id)
    src_chat = get_chat_cached(src["chat_id"]) if src else None
    if not src or not src_chat or not has_chat_access(src_chat, user["id"]): return jsonify({"error": "Сообщение не найдено"}), 404
    mtype = src.get("type") or "text"
    if mtype not in FORWARD_TYPES: return jsonify({"error": "Это сообщение нельзя переслать"}), 400
    chat = get_chat_cached(target_id)
    if not chat: return jsonify({"error": "Чат не найден"}), 404
    if not has_chat_access(chat, user["id"]): return jsonify({"error": "Нет доступа"}), 403
    blocked = private_chat_blocked(chat, user)
    if blocked: return blocked
    body = src.get("text") or ""
    if body.startswith(FWD_MARK):   # пересылка пересланного: автор остаётся первоначальным
        text = body
    else:
        author = get_user_by_id(src.get("sender_id")) or {}
        name = (author.get("first_name") or ("@" + author["username"] if author.get("username") else "") or "пользователя")[:40].replace("\n", " ")
        text = f"{FWD_MARK}↪ Переслано от {name}\n{body}"
    if len(text) > 4000: text = text[:4000]
    if mtype == "text" and not body.strip(): return jsonify({"error": "Пустое сообщение"}), 400
    if mtype != "text" and not src.get("attachment_url"): return jsonify({"error": "Вложение недоступно"}), 400
    msg = save_message(target_id, user["id"], text, mtype, src.get("attachment_url"), src.get("attachment_size"), src.get("attachment_name"),
                       file_type=src.get("file_type"), file_mime=src.get("file_mime"))
    update_last_seen(user["id"])
    sign_attachments([msg])
    return jsonify({"ok": True, "message": msg})

@app.route("/api/game/state")
def api_game_state():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"title": GAME_SEASON["title"], "ends_at": GAME_SEASON["ends_at"].isoformat(), "active": game_active()})

def _game_user_card(u, total=None):
    return {"id": u["id"], "username": u.get("username"), "first_name": u.get("first_name"), "last_name": u.get("last_name"),
            "avatar_url": u.get("avatar_url"), "clouds": total}

@app.route("/api/game/me")
def api_game_me():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    st = game.my_state(user)
    st.update({"title": GAME_SEASON["title"], "ends_at": GAME_SEASON["ends_at"].isoformat(), "active": game_active(), "balance": user.get("balance", 0)})
    return jsonify(st)

@app.route("/api/game/top")
def api_game_top():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    rows = game.top(3)
    users = get_users_by_ids([r["user_id"] for r in rows])
    items = [dict(_game_user_card(users[r["user_id"]], r["total"]), place=i + 1) for i, r in enumerate(rows) if r["user_id"] in users]
    return jsonify({"top": items, "active": game_active(), "ends_at": GAME_SEASON["ends_at"].isoformat()})

@app.route("/api/game/cases")
def api_game_cases():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"cases": game.public_cases(), "balance": user.get("balance", 0), "active": game_active(), "stock": game.stock_of(user["id"])})

@app.route("/api/game/cases/open", methods=["POST"])
@serialized_per_user
def api_game_case_open():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not game_active(): return jsonify({"error": "Сезон закончился, кейсы закрыты"}), 400
    if not check_rate_limit(f"gamecase_{user['id']}", 30, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    d = json_body(); case_key = str_field(d, "case")
    source = "inventory" if d.get("source") == "inventory" else "di"
    result, err = game.open_case(user["id"], case_key, source=source)
    if err: return jsonify({"error": err}), 400
    fresh = get_user_by_id(user["id"]) or user
    return jsonify({"ok": True, "result": result, "balance": fresh.get("balance", 0), "state": game.my_state(fresh), "stock": game.stock_of(user["id"])})

# ==================== КЕЙСЫ @Gaming (gaming.py) ====================
def _gaming_state(user):
    fresh = get_user_by_id(user["id"]) or user
    return {"cases": gaming.public_cases(), "inventory": gaming.inventory(user["id"]), "balance": fresh.get("balance", 0), "wallet": gold.wallet(user["id"]),
            "left_today": max(0, gaming.DAILY_OPENS - gaming.opens_today(user["id"])), "statuses": gaming.STATUS_TITLES}

# ---------- Telegram-игра: привязка и награды за задания (tg_game.py) ----------
def game_key_ok():
    """Заголовок X-Game-Key. Если TG_GAME_KEY не задан, доступ закрыт для всех."""
    if not TG_GAME_KEY: return False
    return hmac.compare_digest((request.headers.get("X-Game-Key") or "").encode("utf-8"), TG_GAME_KEY.encode("utf-8"))

def bot_key_ok():
    """Заголовок X-Bot-Key. Если TG_BOT_KEY не задан, доступ закрыт для всех."""
    if not TG_BOT_KEY: return False
    return hmac.compare_digest((request.headers.get("X-Bot-Key") or "").encode("utf-8"), TG_BOT_KEY.encode("utf-8"))

def _bot_call(limit_key=None, limit=(120, 60)):
    """Общая проверка маршрутов бота: ключ, tg_id, лимит. Возвращает (tg_id, данные, ответ_ошибки)."""
    if not bot_key_ok(): return None, None, (jsonify({"error": "Нет доступа"}), 403)
    d = json_body()
    tg = tgbot.tg_id_of(d.get("tg_id"))
    if not tg: return None, None, (jsonify({"error": "Нет tg_id"}), 400)
    if not check_rate_limit(f"tgbot_{tg}", *limit): return None, None, (jsonify({"error": "Слишком часто"}), 429)
    return tg, d, None

def _bot_safe(fn):
    try: return jsonify(fn())
    except Exception as e:
        print(f"[tgbot] {e!r}"); return jsonify({"error": "Сервис временно недоступен"}), 503

@app.route("/api/tgbot/state", methods=["POST"])
def api_tgbot_state():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.state(tg))

@app.route("/api/tgbot/link/request", methods=["POST"])
def api_tgbot_link_request():
    tg, d, err = _bot_call()
    if err: return err
    uid = to_int(d.get("user_id"))
    if not uid: return jsonify({"error": "bad_id"}), 400
    if not check_rate_limit(f"tgbotreq_{tg}", 5, 3600): return jsonify({"error": "too_many"}), 429
    if not check_rate_limit(f"tgbotusr_{uid}", 3, 3600): return jsonify({"error": "too_many_user"}), 429
    try: ok, e = tgbot.request_link(tg, uid)
    except Exception as ex:
        print(f"[tgbot] link request: {ex!r}"); return jsonify({"error": "unavailable"}), 503
    return jsonify({"ok": True}) if ok else (jsonify({"error": e}), 400)

@app.route("/api/tgbot/link/confirm", methods=["POST"])
def api_tgbot_link_confirm():
    tg, d, err = _bot_call()
    if err: return err
    if not check_rate_limit(f"tgbotcf_{tg}", 12, 600): return jsonify({"error": "too_many"}), 429
    try: ok, e = tgbot.confirm_link(tg, d.get("code"))
    except Exception as ex:
        print(f"[tgbot] link confirm: {ex!r}"); return jsonify({"error": "unavailable"}), 503
    return jsonify({"ok": True}) if ok else (jsonify({"error": e}), 400)

@app.route("/api/tgbot/profile", methods=["POST"])
def api_tgbot_profile():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: {"profile": tgbot.profile(tg)})

@app.route("/api/tgbot/crystals/claim", methods=["POST"])
def api_tgbot_claim():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.claim(tg))

@app.route("/api/tgbot/crystals/double", methods=["POST"])
def api_tgbot_double():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.double(tg))

@app.route("/api/tgbot/promo/start", methods=["POST"])
def api_tgbot_promo_start():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.promo_start(tg))

@app.route("/api/tgbot/promo/take", methods=["POST"])
def api_tgbot_promo_take():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.promo_take(tg))

@app.route("/api/tgbot/promo/info", methods=["POST"])
def api_tgbot_promo_info():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.promo_info(tg))

@app.route("/api/tgbot/promo/activate", methods=["POST"])
def api_tgbot_promo_activate():
    tg, d, err = _bot_call()
    if err: return err
    if not check_rate_limit(f"tgbot_act_{tg}", 10, 60): return jsonify({"error": "Слишком часто"}), 429
    return _bot_safe(lambda: tgbot.promo_activate(tg, str_field(d, "code")))

@app.route("/api/tgbot/daily/open", methods=["POST"])
def api_tgbot_daily_open():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.daily_open(tg))

@app.route("/api/tgbot/daily/claim", methods=["POST"])
def api_tgbot_daily_claim():
    tg, d, err = _bot_call()
    if err: return err
    return _bot_safe(lambda: tgbot.daily_claim(tg))

# ==================== ВИРТУАЛЬНЫЕ НОМЕРА (+999) ====================
def _vn_db_error(e):
    print(f"[vnumbers] {e!r}"); return jsonify({"error": "Номера недоступны (миграция 20261130_vnumbers.sql применена?)"}), 503

@app.route("/api/vnumber/me")
def api_vnumber_me():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    try: return jsonify(vnumbers.info(user["id"]))
    except Exception as e: return _vn_db_error(e)

@app.route("/api/vnumber/main", methods=["POST"])
@serialized_per_user
def api_vnumber_main():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    try: ok, err = vnumbers.set_main(user["id"], str_field(json_body(), "number"))
    except Exception as e: return _vn_db_error(e)
    if not ok: return jsonify({"error": err}), 400
    return jsonify({"ok": True, **vnumbers.info(user["id"])})

@app.route("/api/admin/vnumbers")
def api_admin_vnumbers():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    try: return jsonify({"items": vnumbers.admin_list()})
    except Exception as e: return _vn_db_error(e)

@app.route("/api/admin/vnumbers/give", methods=["POST"])
def api_admin_vnumbers_give():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    uid = to_int(d.get("user_id"))
    if not uid or not get_user_by_id(uid): return jsonify({"error": "Пользователь не найден"}), 404
    raw = d.get("number")
    try: number, err = vnumbers.give(uid, raw if isinstance(raw, str) and raw.strip() else None)
    except Exception as e: return _vn_db_error(e)
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "number": vnumbers.fmt(number)})

@app.route("/api/admin/vnumbers/reserve", methods=["POST"])
def api_admin_vnumbers_reserve():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    raws = d.get("numbers")
    if not isinstance(raws, list): raws = [str_field(d, "number")]
    done, skipped = 0, []
    try:
        for r in raws[:500]:
            ok, err = vnumbers.reserve(r)
            if ok: done += 1
            else: skipped.append({"number": str(r)[:20], "why": err})
    except Exception as e: return _vn_db_error(e)
    return jsonify({"added": done, "skipped": skipped})

@app.route("/api/admin/vnumbers/release", methods=["POST"])
def api_admin_vnumbers_release():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    try: ok = vnumbers.release(str_field(json_body(), "number"))
    except Exception as e: return _vn_db_error(e)
    return jsonify({"ok": ok})

# Бот: покупка номеров за звёзды Telegram и админка запаса (только если Telegram привязан к аккаунту с ролью owner)
def _bot_owner(tg):
    try:
        row = tgbot._state(tg) or {}
        u = get_user_by_id(row.get("user_id")) if row.get("user_id") else None
        return bool(u and u.get("role") == "owner" and not u.get("banned"))
    except Exception as e:
        print(f"[tgbot owner] {e!r}"); return False

@app.route("/api/tgbot/vnumber/info", methods=["POST"])
def api_tgbot_vnumber_info():
    tg, d, err = _bot_call()
    if err: return err
    def go():
        row = tgbot._state(tg) or {}
        uid = row.get("user_id")
        return {"linked": bool(uid), "is_owner": _bot_owner(tg), "numbers": vnumbers.mine(uid) if uid else [], "max": vnumbers.MAX_PER_USER,
                "stars_random": vnumbers.STARS_RANDOM, "stars": vnumbers.STARS, "stock": vnumbers.stock_counts()}
    return _bot_safe(go)

@app.route("/api/tgbot/vnumber/quote", methods=["POST"])
def api_tgbot_vnumber_quote():
    tg, d, err = _bot_call()
    if err: return err
    def go():
        row = tgbot._state(tg) or {}
        uid = row.get("user_id")
        if not uid: return {"error": "not_linked"}
        if len(vnumbers.mine(uid)) >= vnumbers.MAX_PER_USER: return {"error": "limit", "max": vnumbers.MAX_PER_USER}
        raw = d.get("number")
        q, e = vnumbers.quote(raw if isinstance(raw, str) and raw.strip() else None)
        return {"error": "unavailable", "message": e} if e else {"quote": q}
    return _bot_safe(go)

@app.route("/api/tgbot/vnumber/sell", methods=["POST"])
def api_tgbot_vnumber_sell():
    """Вызывается ботом после успешной оплаты. Не ok = бот должен вернуть звёзды (refund)."""
    tg, d, err = _bot_call()
    if err: return err
    charge = str_field(d, "charge_id")
    stars = to_int(d.get("stars"))
    if not charge or len(charge) > 200 or not stars or stars < 1: return jsonify({"error": "Неверные данные платежа"}), 400
    def go():
        row = tgbot._state(tg) or {}
        uid = row.get("user_id")
        if not uid: return {"status": "not_linked", "refund": True}
        raw = d.get("number")
        st, number = vnumbers.sell(uid, tg, raw if isinstance(raw, str) and raw.strip() else None, stars, charge)
        return {"status": st, "text": vnumbers.fmt(number) if number else None, "refund": st in ("limit", "empty")}
    return _bot_safe(go)

@app.route("/api/tgbot/vnumber/admin", methods=["POST"])
def api_tgbot_vnumber_admin():
    tg, d, err = _bot_call()
    if err: return err
    if not _bot_owner(tg): return jsonify({"error": "Нет доступа"}), 403
    action = str_field(d, "action")
    def go():
        if action == "add":
            text = d.get("text")
            raws = [x for x in re.split(r"[\s,;]+", text) if x] if isinstance(text, str) else []
            added, skipped = vnumbers.stock_add(raws)
            return {"added": added, "skipped": [{"number": a, "why": w} for a, w in skipped[:30]], "stock": vnumbers.stock_counts()}
        if action == "remove":
            return {"ok": vnumbers.stock_remove(str_field(d, "number")), "stock": vnumbers.stock_counts()}
        return {"stock": vnumbers.stock_counts(), "items": vnumbers.stock_list()}
    return _bot_safe(go)

@app.route("/api/admin/tgbot")
def api_admin_tgbot():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    try: return jsonify(tgbot.admin_overview())
    except Exception as e:
        print(f"[tgbot admin] {e!r}"); return jsonify({"error": "Таблицы бота недоступны (миграция 20261129_tgbot.sql применена?)"}), 503

@app.route("/api/admin/tgbot/pool", methods=["POST"])
def api_admin_tgbot_pool():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    added, skipped = tgbot.admin_pool_add(json_body().get("codes"))
    return jsonify({"added": added, "skipped": [{"code": c, "why": w} for c, w in skipped]})

@app.route("/api/admin/tgbot/pool/delete", methods=["POST"])
def api_admin_tgbot_pool_delete():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    pid = to_int(json_body().get("id"))
    if not pid: return jsonify({"error": "Нет id"}), 400
    return jsonify({"ok": tgbot.admin_pool_delete(pid)})

@app.route("/api/admin/tgbot/unlink", methods=["POST"])
def api_admin_tgbot_unlink():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    tg = tgbot.tg_id_of(to_int(json_body().get("tg_id")))
    if not tg: return jsonify({"error": "Нет tg_id"}), 400
    return jsonify({"ok": tgbot.admin_unlink(tg)})

@app.route("/api/tg/game/link", methods=["POST"])
def api_tg_game_link():
    """Сервер игры: игрок прислал код привязки. {code, tg_id}; tg_id игра берёт из проверенной подписи Telegram."""
    if not game_key_ok(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    if not check_rate_limit("tglink_" + str(d.get("tg_id")), 10, 60): return jsonify({"error": "Слишком часто"}), 429
    ok, msg = tg_game.link(d.get("code"), to_int(d.get("tg_id")))
    return (jsonify({"ok": True}) if ok else (jsonify({"error": msg}), 400))

@app.route("/api/tg/game/claim", methods=["POST"])
def api_tg_game_claim():
    """Сервер игры: игрок выполнил задание. {tg_id, task_id}. Выдача идемпотентна: повтор вернёт already."""
    if not game_key_ok(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    if not check_rate_limit("tgclaim_" + str(d.get("tg_id")), 60, 60): return jsonify({"error": "Слишком часто"}), 429
    st, data = tg_game.claim(to_int(d.get("tg_id")), str_field(d, "task_id"))
    code = 200 if st == "ok" else 400 if st == "bad" else 409 if st in ("already", "cap") else 403 if st in ("not_linked", "too_new") else 404
    return jsonify(dict(data, status=st)), code

@app.route("/api/tg/state")
def api_tg_state():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify(tg_game.state(user["id"]))

@app.route("/api/tg/link_code", methods=["POST"])
def api_tg_link_code():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"tgcode_{user['id']}", 5, 600): return jsonify({"error": "Слишком часто. Подожди несколько минут."}), 429
    code, err = tg_game.make_code(user["id"])
    if err: return jsonify({"error": err}), 400
    return jsonify({"code": code, "ttl": tg_game.CODE_TTL})

@app.route("/api/admin/tg")
def api_admin_tg():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = tg_game.admin_overview()
    users = get_users_by_ids(list({r["user_id"] for r in d["links"]} | {r["user_id"] for r in d["claims"]}))
    nm = lambda uid: (lambda u: (f"@{u['username']}" if u.get("username") else (u.get("first_name") or "без имени")) + f" (ID {uid})")(users.get(uid) or {})
    for r in d["links"] + d["claims"]: r["user_name"] = nm(r["user_id"])
    return jsonify(d)

@app.route("/api/admin/tg/task", methods=["POST"])
def api_admin_tg_task():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    row, err = tg_game.save_task(str_field(d, "id"), d.get("title"), to_int(d.get("gold")), str_field(d, "kind"), d.get("active", True))
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "task": row})

@app.route("/api/admin/tg/task/delete", methods=["POST"])
def api_admin_tg_task_delete():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    return jsonify({"ok": tg_game.delete_task(str_field(json_body(), "id"))})

@app.route("/api/admin/tg/unlink", methods=["POST"])
def api_admin_tg_unlink():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    uid = to_int(json_body().get("user_id"))
    return jsonify({"ok": bool(uid) and tg_game.unlink(uid)})

@app.route("/api/orgs/my")
def api_orgs_my():
    """Организации, от имени которых текущий пользователь может верифицировать (owner Diogram: все)."""
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if user.get("role") == "owner":
        return jsonify({"orgs": [{"id": o["id"], "name": o["name"], "icon_url": o.get("icon_url"), "note_icon_url": o.get("note_icon_url")} for o in orgs._fresh()["orgs"].values()]})
    return jsonify({"orgs": orgs.my_orgs(user["id"])})

@app.route("/api/orgs/verify", methods=["POST"])
def api_orgs_verify():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"orgverify_{user['id']}", 60, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    d = json_body(); org_id = to_int(d.get("org_id"))
    ttype = d.get("target_type", "user")
    if ttype not in orgs.TARGETS: return jsonify({"error": "target_type: user или channel"}), 400
    target_id = to_int(d.get("channel_id" if ttype == "channel" else "user_id"))
    if not org_id or not target_id: return jsonify({"error": "Нужны org_id и " + ("channel_id" if ttype == "channel" else "user_id")}), 400
    if not orgs.can_verify(user, org_id): return jsonify({"error": "Нет доступа"}), 403
    if ttype == "channel":
        if not get_channel_by_id(target_id): return jsonify({"error": "Канал не найден"}), 404
    elif not get_user_by_id(target_id): return jsonify({"error": "Юзер не найден"}), 404
    if not orgs.set_verified(org_id, ttype, target_id, bool(d.get("on")), user["id"]): return jsonify({"error": "Не удалось сохранить"}), 500
    return jsonify({"ok": True, "orgs": orgs.marks_for(target_id, ttype)})

@app.route("/api/admin/orgs")
def api_admin_orgs():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    try: lst = orgs.list_admin()
    except Exception as e:
        print(f"[orgs] admin {e!r}"); return jsonify({"error": "Не удалось загрузить организации (применена ли миграция?)"}), 500
    users = get_users_by_ids(list({m["user_id"] for o in lst for m in o["members"]}))
    nm = lambda uid: (lambda u: (f"@{u['username']}" if u.get("username") else (u.get("first_name") or "без имени")) + f" (ID {uid})")(users.get(uid) or {})
    for o in lst:
        for m in o["members"]: m["user_name"] = nm(m["user_id"])
    return jsonify({"orgs": lst})

@app.route("/api/admin/orgs/save", methods=["POST"])
def api_admin_orgs_save():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    row, err = orgs.save_org(to_int(d.get("id")), d.get("name"))
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "org": row})

@app.route("/api/admin/orgs/icon", methods=["POST"])
def api_admin_orgs_icon():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    org_id = to_int(request.form.get("org_id"))
    if not org_id: return jsonify({"error": "org_id обязателен"}), 400
    f = request.files.get("file")
    if not f or not f.filename: return jsonify({"error": "Файл не передан"}), 400
    png, err = orgs.process_icon(f.read(orgs.ICON_MAX_BYTES + 1))
    if err: return jsonify({"error": err}), 400
    url, err = orgs.set_icon(org_id, png, request.form.get("kind") or "main")
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "icon_url": url})

@app.route("/api/admin/orgs/delete", methods=["POST"])
def api_admin_orgs_delete():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    org_id = to_int(json_body().get("id"))
    return jsonify({"ok": bool(org_id) and orgs.delete_org(org_id)})

@app.route("/api/admin/orgs/member", methods=["POST"])
def api_admin_orgs_member():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body(); org_id = to_int(d.get("org_id")); uid = to_int(d.get("user_id"))
    if not org_id or not uid: return jsonify({"error": "Нужны org_id и user_id"}), 400
    role = d.get("role")
    if role is not None and not isinstance(role, str): return jsonify({"error": "Неверная роль"}), 400
    if role is not None and not get_user_by_id(uid): return jsonify({"error": "Юзер не найден"}), 404
    err = orgs.set_member(org_id, uid, role)
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True})

@app.route("/api/gaming/state")
def api_gaming_state():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify(_gaming_state(user))

@app.route("/api/gaming/open", methods=["POST"])
@serialized_per_user
def api_gaming_open():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"gaming_open_{user['id']}", 12, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    case_id = to_int(json_body().get("case_id"))
    if not case_id: return jsonify({"error": "Выбери кейс"}), 400
    row, err = gaming.open_case(user["id"], case_id)
    if err: return jsonify({"error": err}), 400
    st = _gaming_state(user)
    return jsonify({"ok": True, "won": {"id": row["id"], "kind": "item", "label": row["name"], "image": row.get("image_url")}, "state": st})

@app.route("/api/gaming/withdraw", methods=["POST"])
@serialized_per_user
def api_gaming_withdraw():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"gaming_wd_{user['id']}", 20, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    inv_id = to_int(json_body().get("id"))
    if not inv_id or not gaming.request_withdraw(user, inv_id): return jsonify({"error": "Предмет не найден или заявка уже отправлена"}), 400
    return jsonify({"ok": True, "state": _gaming_state(user)})

# ---- голда и рефералка (gold.py) ----
@app.route("/api/gold/exchange", methods=["POST"])
@serialized_per_user
def api_gold_exchange():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"gold_ex_{user['id']}", 10, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    ok, err = gold.exchange(user["id"], to_int(json_body().get("amount")))
    if not ok: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "state": _gaming_state(user)})

@app.route("/api/gold/withdraw", methods=["POST"])
@serialized_per_user
def api_gold_withdraw():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"gold_wd_{user['id']}", 5, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    d = json_body()
    wid, err = gold.request_withdraw(user, to_int(d.get("amount")), d.get("nick"))
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "state": _gaming_state(user)})

@app.route("/api/gold/withdrawals")
def api_gold_withdrawals():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify({"items": gold.my_withdrawals(user["id"]), "statuses": gold.WITHDRAW_STATUS})

@app.route("/api/ref/me")
def api_ref_me():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"ref_me_{user['id']}", 30, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    return jsonify(gold.ref_state(user["id"]))

@app.route("/api/ref/activate", methods=["POST"])
@serialized_per_user
def api_ref_activate():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"ref_act_{user['id']}", 10, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    ok, err = gold.activate(user["id"], str_field(json_body(), "code"))
    if not ok: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "state": gold.ref_state(user["id"])})

@app.route("/api/admin/gold")
def api_admin_gold():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    wd = gold.admin_withdrawals()
    users = get_users_by_ids(list({r["user_id"] for r in wd}))
    nm = lambda uid: (lambda u: (f"@{u['username']}" if u.get("username") else (u.get("first_name") or "без имени")) + f" (ID {uid})")(users.get(uid) or {})
    for r in wd: r["player"] = nm(r["user_id"])
    return jsonify({"withdrawals": wd, "stats": gold.stats()})

@app.route("/api/admin/gold/withdrawal", methods=["POST"])
def api_admin_gold_withdrawal():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    ok, err = gold.close_withdrawal(to_int(d.get("id")), str_field(d, "status"), d.get("note"))
    if not ok: return jsonify({"error": err}), 400
    return jsonify({"ok": True})

def require_staff():
    user = get_current_user()
    if not user or user.get("role") not in STAFF_ROLES: return None
    return user

@app.route("/api/staff/ref/codes")
def api_staff_ref_codes():
    """Реферальные коды выдают owner, developer и moderator (сами коды не создаются). Owner видит все, остальные свои."""
    staff = require_staff()
    if not staff: return jsonify({"error": "Нет доступа"}), 403
    codes = gold.staff_codes(staff)
    users = get_users_by_ids(list({c["owner_id"] for c in codes}))
    nm = lambda uid: (lambda u: (f"@{u['username']}" if u.get("username") else (u.get("first_name") or "без имени")) + f" (ID {uid})")(users.get(uid) or {})
    for c in codes: c["owner_name"] = nm(c["owner_id"]); c["tier_name"] = gold.TIER_NAMES.get(c.get("tier") or "partner")
    return jsonify({"codes": codes, "tiers": {k: {"name": gold.TIER_NAMES[k], "pct": int(v * 100)} for k, v in gold.REF_RATES.items()}})

@app.route("/api/staff/ref/code", methods=["POST"])
def api_staff_ref_code():
    staff = require_staff()
    if not staff: return jsonify({"error": "Нет доступа"}), 403
    if not check_rate_limit(f"refcode_{staff['id']}", 20, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    d = json_body()
    row, err = gold.create_custom_code(str_field(d, "code"), to_int(d.get("owner_id")), to_int(d.get("max_uses")), str_field(d, "tier"), staff["id"])
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "code": row})

@app.route("/api/staff/ref/code/toggle", methods=["POST"])
def api_staff_ref_code_toggle():
    staff = require_staff()
    if not staff: return jsonify({"error": "Нет доступа"}), 403
    d = json_body(); cid = to_int(d.get("id"))
    if not cid: return jsonify({"error": "Нет кода"}), 400
    if not gold.toggle_code(cid, bool(d.get("active")), staff): return jsonify({"error": "Код не найден или он выдан не тобой"}), 404
    return jsonify({"ok": True})

@app.route("/api/admin/gaming")
def api_admin_gaming():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    ov = gaming.admin_overview()
    users = get_users_by_ids([r["user_id"] for r in ov["requests"]])
    for r in ov["requests"]:
        u = users.get(r["user_id"]) or {}
        r["player"] = (f"@{u['username']}" if u.get("username") else (u.get("first_name") or "без имени")) + f" (ID {r['user_id']})"
    return jsonify(ov)

@app.route("/api/admin/gaming/case", methods=["POST"])
def api_admin_gaming_case():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    row, err = gaming.save_case(to_int(d.get("id")), d.get("title"), to_int(d.get("price")), d.get("active", True), d["opens_at"] if "opens_at" in d else gaming.KEEP)
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "case": row})

@app.route("/api/admin/gaming/item", methods=["POST"])
def api_admin_gaming_item():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    case_id = to_int(d.get("case_id"))
    if not case_id: return jsonify({"error": "Выбери кейс"}), 400
    row, err = gaming.save_item(to_int(d.get("id")), case_id, d.get("name"), d.get("image_url"), d.get("info"), d.get("chance"), d.get("active", True), d.get("stock"))
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "item": row})

@app.route("/api/admin/gaming/item/delete", methods=["POST"])
def api_admin_gaming_item_delete():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    iid = to_int(json_body().get("id"))
    if not iid: return jsonify({"error": "Нет предмета"}), 400
    gaming.delete_item(iid); return jsonify({"ok": True})

@app.route("/api/admin/gaming/request", methods=["POST"])
def api_admin_gaming_request():
    if not require_owner(): return jsonify({"error": "Нет доступа"}), 403
    d = json_body()
    row, err = gaming.set_status(to_int(d.get("id")), str_field(d, "status"), d.get("note"))
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True})

@app.route("/api/game/give_case", methods=["POST"])
def api_game_give_case():
    """Выдать кейсы игроку (команда /Case для @Play). Только владелец и разработчики."""
    admin = get_current_user()
    if not admin: return jsonify({"error": "Не авторизован"}), 401
    if admin.get("role") not in ("owner", "developer"): return jsonify({"error": "Нет доступа"}), 403
    if not check_rate_limit(f"givecase_{admin['id']}", 30, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    d = json_body(); uid = to_int(d.get("user_id")); qty = to_int(d.get("count")); case_key = str_field(d, "case")
    if uid is None or qty is None: return jsonify({"error": "Нужны user_id и count"}), 400
    target = get_user_by_id(uid)
    if not target: return jsonify({"error": "Пользователь с таким ID не найден"}), 404
    now, err = game.grant_cases(admin["id"], uid, case_key, qty)
    if err: return jsonify({"error": err}), 400
    title = game.CASES[case_key]["title"]
    try:   # игрок видит уведомление от @Play
        chat = get_or_create_chat(GAME_BOT_ID, uid)
        if chat: save_message(chat["id"], GAME_BOT_ID, f"🎁 Тебе выдали кейсы: {title} × {qty}\nУ тебя есть: {now}. Открой их в игре: Общее → Кейсы.", "text")
    except Exception as e: print(f"[give_case notify] {e!r}")
    return jsonify({"ok": True, "now": now, "to": target.get("username") or target.get("first_name")})

# ---------- карта и лобби (состояние в памяти процесса, как «печатает…») ----------
GAME_HUB = game_world.Hub(consume=game.use_attempt, credit=game.credit_map_clouds)

def _game_float(v):
    try: f = float(v)
    except (TypeError, ValueError): return 0.0
    return f if f == f and abs(f) < 1e6 else 0.0   # NaN и бесконечности превращаем в 0

@app.route("/api/game/map")
def api_game_map():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify(game_world.public_map())

@app.route("/api/game/lobbies")
def api_game_lobbies():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    staff = user.get("role") in STAFF_ROLES
    return jsonify({"slots": GAME_HUB.slots(), "staff": staff, "active": game_active(),
                    "games_left": None if staff else game.games_left(user["id"]),
                    "in_lobby": GAME_HUB.lobby_of(user["id"]) is not None})

@app.route("/api/game/join", methods=["POST"])
def api_game_join():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not game_active(): return jsonify({"error": "Сезон закончился"}), 400
    staff = user.get("role") in STAFF_ROLES
    if not check_rate_limit(f"gamejoin_{user['id']}", 20, 60): return jsonify({"error": "Слишком часто. Подожди минуту."}), 429
    wanted = to_int(json_body().get("lobby"))
    if not staff and GAME_HUB.lobby_of(user["id"]) is None and game.games_left(user["id"]) <= 0:
        return jsonify({"error": "На сегодня игры закончились. Приходи завтра или открой кейс «Обычный»."}), 400
    name = (user.get("first_name") or user.get("username") or "Игрок")[:20]
    lb, err = GAME_HUB.join(user["id"], name, staff, wanted)
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "lobby": lb.num})

@app.route("/api/game/tick", methods=["POST"])
def api_game_tick():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    d = json_body()
    brk = to_int(d.get("brk"))
    st = GAME_HUB.tick(user["id"], _game_float(d.get("dx")), _game_float(d.get("dy")), brk if brk and 1 <= brk <= 4 else None)
    if st is None: return jsonify({"in_lobby": False})
    st["in_lobby"] = True
    return jsonify(st)

@app.route("/api/game/chat", methods=["POST"])
def api_game_chat():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    uid = user["id"]
    if not check_rate_limit(f"gamechat_burst_{uid}", 3, 4) or not check_rate_limit(f"gamechat_min_{uid}", 20, 60):
        return jsonify({"error": "Слишком часто, подожди секунду"}), 429
    if not GAME_HUB.lobby_of(uid): return jsonify({"error": "Ты не в лобби"}), 400
    text = str_field(json_body(), "text")[:400]
    if not " ".join(text.replace("\u2063", "").split()): return jsonify({"error": "Пустое сообщение"}), 400
    staff = user.get("role") in STAFF_ROLES
    left = None
    if not staff:
        left = rainbow.spend(uid)
        if left is None: return jsonify({"error": "Нужен радужный круг: сообщение стоит 1 🌈", "need_circles": True}), 402
    ok, err, rec = GAME_HUB.chat(uid, text)
    if not ok:
        if not staff and left is not None and left >= 0: rainbow.refund(uid)
        return jsonify({"error": err}), 400
    game_chatlog.save(game.SEASON, rec[0], uid, rec[1], rec[2], rec[3])
    return jsonify({"ok": True, "circles": None if staff or left < 0 else left, "free": staff})

@app.route("/api/game/daily")
def api_game_daily():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    try: tasks = daily.list_tasks(user)
    except Exception as e:
        print(f"[daily] список: {e!r}"); return jsonify({"error": "Бонусы пока недоступны"}), 503
    return jsonify({"tasks": tasks, "active": game_active(), "can_play": user.get("role") == "user"})

@app.route("/api/game/daily/claim", methods=["POST"])
def api_game_daily_claim():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"dailyclaim_{user['id']}", 20, 60): return jsonify({"error": "Слишком часто, подожди минуту"}), 429
    tid = to_int(json_body().get("task_id"))
    if not tid: return jsonify({"error": "Нет задания"}), 400
    try: got, err = daily.claim(user, tid, game_active())
    except Exception as e:
        print(f"[daily] claim: {e!r}"); return jsonify({"error": "Не удалось забрать, попробуй ещё раз"}), 503
    if err: return jsonify({"error": err}), 400
    return jsonify({"ok": True, "got": got, "tasks": daily.list_tasks(user)})

@app.route("/api/rainbow/me")
def api_rainbow_me():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    try: w = rainbow.wallet(user["id"])
    except Exception as e:
        print(f"[rainbow] кошелёк: {e!r}"); return jsonify({"error": "Магазин кругов пока недоступен"}), 503
    return jsonify({"circles": w["circles"], "packs": rainbow.public_packs(w["free_used"]), "balance": float(user.get("balance") or 0), "staff": user.get("role") in STAFF_ROLES, "username": user.get("username")})

@app.route("/api/rainbow/buy", methods=["POST"])
def api_rainbow_buy():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    if not check_rate_limit(f"rainbowbuy_{user['id']}", 10, 60): return jsonify({"error": "Слишком часто, подожди минуту"}), 429
    d = json_body()
    name = str_field(d, "username").lstrip("@").strip()
    if not name: return jsonify({"error": "Укажи юзернейм, кому зачислить круги"}), 400
    target = get_user_by_username(name) or get_collection_holder_user(name)   # обычный ник или коллекционный
    if not target or target.get("banned"): return jsonify({"error": f"Пользователь @{name[:32]} не найден"}), 404
    try: ok, err, w, bal = rainbow.buy(user["id"], target["id"], str_field(d, "pack"))
    except Exception as e:
        print(f"[rainbow] покупка: {e!r}"); return jsonify({"error": "Покупка сейчас недоступна"}), 503
    if not ok: return jsonify({"error": err}), 400
    mine = target["id"] == user["id"]
    return jsonify({"ok": True, "circles": w["circles"], "balance": bal, "packs": rainbow.public_packs(w["free_used"]), "to": {"id": target["id"], "username": target.get("username") or name, "me": mine}})

@app.route("/api/admin/game/chat")
def api_admin_game_chat():
    owner = require_owner()
    if not owner: return jsonify({"error": "Нет доступа"}), 403
    try: text, n = game_chatlog.export_txt(int_arg("days", 1), 5000)
    except Exception as e:
        print(f"[game_chatlog] выгрузка: {e!r}"); return jsonify({"error": "Журнал недоступен (применена ли миграция 20261125?)"}), 500
    return jsonify({"text": text, "count": n})

@app.route("/api/game/leave", methods=["POST"])
def api_game_leave():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    GAME_HUB.leave(user["id"])
    return jsonify({"ok": True})

@app.route("/api/game/prizes")
def api_game_prizes():
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    return jsonify(game.my_prizes(user["id"]))

@app.route("/api/messages/<int:message_id>", methods=["DELETE"])
def api_message_delete(message_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    msg = get_message_by_id(message_id)
    if not msg: return jsonify({"error": "Сообщение не найдено"}), 404
    if msg["sender_id"] != user["id"]: return jsonify({"error": "Можно удалять только свои"}), 403
    delete_message(message_id)
    return jsonify({"ok": True})

# ==================== РЕАКЦИИ ====================
REACTION_EMOJIS = ("👍", "❤️", "😂", "😮", "😢", "🔥", "👎", "🎉")
# «Версия» реакций чата: растёт при каждой реакции, опрос присылает полный список, только если версия клиента устарела.
# Старт от времени, чтобы после перезапуска сервера клиенты с прежней версией получили свежие данные. Работает при одном процессе.
_reaction_ver = defaultdict(lambda: int(time.time()))
_reaction_ver_lock = threading.Lock()

def reactions_summary(rows, user_id):
    """[{message_id, emoji, count, mine}] из строк message_reactions."""
    agg = {}
    for r in rows or []:
        e = agg.setdefault((r["message_id"], r["emoji"]), {"message_id": r["message_id"], "emoji": r["emoji"], "count": 0, "mine": False})
        e["count"] += 1
        if r["user_id"] == user_id: e["mine"] = True
    return list(agg.values())

@app.route("/api/messages/<int:message_id>/react", methods=["POST"])
def api_message_react(message_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    data = json_body()
    emoji = data.get("emoji")
    if emoji is not None and emoji not in REACTION_EMOJIS: return jsonify({"error": "Такой реакции нет"}), 400
    if not check_rate_limit(f"react_{user['id']}", 30, 10): return jsonify({"error": "Слишком часто"}), 429
    msg = get_message_by_id(message_id)
    if not msg: return jsonify({"error": "Сообщение не найдено"}), 404
    chat = get_chat_cached(msg["chat_id"])
    if not chat or not has_chat_access(chat, user["id"]): return jsonify({"error": "Нет доступа"}), 403
    blocked = private_chat_blocked(chat, user)   # реакция это тоже запись в чужой личный чат
    if blocked: return blocked
    set_reaction(message_id, msg["chat_id"], user["id"], emoji)
    with _reaction_ver_lock:
        _reaction_ver[msg["chat_id"]] += 1
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
    out = {"messages": msgs, "typing": len(typing_users) > 0}
    ver = _reaction_ver[chat_id]; out["rv"] = ver
    if to_int(request.args.get("rv")) != ver:
        out["reactions"] = reactions_summary(get_reactions(chat_id, to_int(request.args.get("rmin")) or 0), user["id"])
    return jsonify(out)

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

# Пересланные сообщения начинаются с невидимого знака-метки и строки «↪ Переслано от ...». Знак ставит только сервер:
# из пользовательского текста и подписей к фото он вырезается, иначе можно было бы подделать «переслано от @owner».
FWD_MARK = "\u2063"
def strip_fwd_mark(t): return t.replace(FWD_MARK, "") if isinstance(t, str) else t

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
    if mime in VIDEO_AVATAR_MIME:
        if (user.get("role") or "user") not in VIDEO_AVATAR_ROLES: return jsonify({"error": "Видео-аватарка доступна только владельцу и разработчикам"}), 403
        processed = file.read(VIDEO_AVATAR_MAX + 1)
        if len(processed) > VIDEO_AVATAR_MAX: return jsonify({"error": "Видео больше 10 МБ"}), 400
        ext = sniff_video(processed)
        if not ext:
            if len(processed) > 376 and processed[0] == 0x47 and processed[188] == 0x47 and processed[376] == 0x47:
                return jsonify({"error": "Это не настоящий MP4: внутри формат MPEG-TS (так бывает у роликов из некоторых приложений). Пересохрани видео как MP4 (например, через экспорт или конвертер) и загрузи снова."}), 400
            head = processed[:12]
            brand = head[8:12].decode("ascii", "replace") if head[4:8] == b"ftyp" else "".join(chr(b) if 32 <= b < 127 else "." for b in head[:8])
            return jsonify({"error": "Нужен MP4 (H.264) или WebM. Тип файла: " + brand.strip()}), 400
        dur = video_duration(processed, ext)
        if not dur or dur <= 0: return jsonify({"error": "Не удалось определить длительность видео. Сохрани ролик как обычный MP4 или WebM."}), 400
        if dur > VIDEO_AVATAR_MAX_SEC: return jsonify({"error": "Видео длиннее 1 минуты"}), 400
        content_type = "video/mp4" if ext == "mp4" else "video/webm"
    else:
        if mime not in ALLOWED_MIME: return jsonify({"error": "Только JPG, PNG, WEBP, GIF"}), 400
        try:
            file_bytes = file.read(); processed, ext = _process_image(file_bytes, mime)
        except ValueError as e: return jsonify({"error": str(e)}), 400
        content_type = f"image/{ext if ext != 'jpg' else 'jpeg'}"
    filename = f"user_{user['id']}_{int(time.time())}.{ext}"
    try:
        supabase.storage.from_("avatars").upload(filename, processed, file_options={"content-type": content_type})
        public_url = supabase.storage.from_("avatars").get_public_url(filename)
    except Exception as e:
        print(f"[upload] {e!r}"); return jsonify({"error": "Не удалось загрузить файл. Попробуй ещё раз."}), 500
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
    caption = strip_fwd_mark(request.form.get("caption") or "").strip()
    if len(caption) > 500: return jsonify({"error": "Подпись максимум 500 символов"}), 400
    try:
        file_bytes = file.read(); processed, ext = _process_image(file_bytes, mime)
    except ValueError as e: return jsonify({"error": str(e)}), 400
    # Случайное имя: раньше user_id + время легко перебирались, а бакет публичный
    filename = f"photo_{secrets.token_urlsafe(24)}.{ext}"
    try:
        supabase.storage.from_("chat-photos").upload(filename, processed, file_options={"content-type": f"image/{ext if ext != 'jpg' else 'jpeg'}"})
        public_url = supabase.storage.from_("chat-photos").get_public_url(filename)
    except Exception as e:
        print(f"[upload] {e!r}"); return jsonify({"error": "Не удалось загрузить файл. Попробуй ещё раз."}), 500
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
    except Exception as e:
        print(f"[upload] {e!r}"); return jsonify({"error": "Не удалось загрузить файл. Попробуй ещё раз."}), 500
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
        conflict = username_conflict(username, user["id"], as_channel=True)
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
    if not check_rate_limit(f"chsearch_{user['id']}", SEARCH_RATE_PER_MIN, 60): return jsonify({"error": "Слишком часто. Подожди немного."}), 429
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
    if ch.get("username") and is_username_on_frigdmant(ch["username"]) and (ch_type is not None and ch_type != ch["type"] or (username is not None and username.strip().lstrip("@").lower() != ch["username"].lower())):
        return jsonify({"error": "Юзернейм канала сейчас на продаже на Frigdmant. Сними лот, чтобы изменить его"}), 400
    if final_type == "public":
        if username is None: username = ch.get("username")
        if username is not None: username = username.strip().lstrip("@")
        if not username:
            # Канал без юзернейма (имя продано): название менять можно, нового @username не требуем.
            if ch.get("username") or ch_type is not None: return jsonify({"error": "Для публичного канала нужен @username"}), 400
            username = None
        else:
            ok, err = validate_username(username, "user")
            if not ok: return jsonify({"error": err}), 400
            if username.lower() != (ch.get("username") or "").lower():
                existing = get_channel_by_username(username)
                if existing and existing["id"] != channel_id: return jsonify({"error": "Этот @username канала занят"}), 400
                conflict = username_conflict(username, user["id"], as_channel=True)
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
    except Exception as e:
        print(f"[upload] {e!r}"); return jsonify({"error": "Не удалось загрузить файл. Попробуй ещё раз."}), 500
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
    return jsonify({"messages": messages, "channel": {"id": ch["id"], "name": ch["name"], "username": ch.get("username"), "description": ch.get("description"), "type": ch["type"], "avatar_url": ch.get("avatar_url"), "subscribers_count": len(subs.data or []), "is_owner": ch["owner_id"] == user["id"], "can_post": can_post_channel(ch, user), "comments_enabled": linked_group(ch) is not None, "owner": user_public_dict(owner) if owner else None}})

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

def can_post_channel(ch, user):
    """Писать в канал может владелец; в канале с флагом staff_post ещё и весь персонал (owner, developer, moderator)."""
    if ch["owner_id"] == user["id"]: return True
    return bool(ch.get("staff_post")) and (user.get("role") or "user") in STAFF_ROLES

@app.route("/api/channels/<int:channel_id>/send", methods=["POST"])
def api_channel_send(channel_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch = get_channel_by_id(channel_id)
    if not ch: return jsonify({"error": "Канал не найден"}), 404
    if not can_post_channel(ch, user): return jsonify({"error": "Только владелец может писать"}), 403
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

@app.route("/api/channels/<int:channel_id>/messages/<int:msg_id>/react", methods=["POST"])
def api_channel_react(channel_id, msg_id):
    user = get_current_user()
    if not user: return jsonify({"error": "Не авторизован"}), 401
    ch, err = channel_reader(channel_id, user)
    if err: return err
    emoji = json_body().get("emoji")
    if emoji is not None and emoji not in REACTION_EMOJIS: return jsonify({"error": "Такой реакции нет"}), 400
    if not check_rate_limit(f"react_{user['id']}", 30, 10): return jsonify({"error": "Слишком часто"}), 429
    if not get_channel_message(msg_id, channel_id): return jsonify({"error": "Пост не найден"}), 404
    set_channel_reaction(msg_id, user["id"], emoji)
    return jsonify({"ok": True})

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
    if ch.get("username") and is_username_on_frigdmant(ch["username"]): return jsonify({"error": "Юзернейм канала на продаже на Frigdmant. Сначала сними лот"}), 400
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
    if not ch or not can_post_channel(ch, user): return jsonify({"error": "Нет доступа"}), 403
    post = get_channel_message(msg_id, channel_id)
    # Сотрудник (не владелец) удаляет только свои посты
    if post and ch["owner_id"] != user["id"] and post.get("sender_id") != user["id"]: return jsonify({"error": "Нет доступа"}), 403
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

# ==================== ПРИЗЫ ТУРНИРА (только owner) ====================
@app.route("/api/admin/game")
def api_admin_game():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    ov = game.admin_overview()
    holders = get_users_by_ids([r["given_to"] for r in ov["accounts"] + ov["usernames"] if r.get("given_to")])
    def who(r): return (holders.get(r.get("given_to")) or {}).get("username") or (r.get("given_to") and f"id {r['given_to']}") or None
    return jsonify({
        "accounts": [{"id": r["id"], "email": r["email"], "given_to": r.get("given_to"), "given_name": who(r)} for r in ov["accounts"]],
        "usernames": [{"username": r["username"], "pool": r["pool"], "series_no": r.get("series_no"), "given_to": r.get("given_to"), "given_name": who(r)} for r in ov["usernames"]],
    })

@app.route("/api/admin/game/accounts", methods=["POST"])
def api_admin_game_accounts():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    raw = json_body().get("text")
    if not isinstance(raw, str) or not raw.strip(): return jsonify({"error": "Список пуст"}), 400
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) > 100: return jsonify({"error": "Не больше 100 строк за раз"}), 400
    pairs, skipped = [], []
    for l in lines:
        email, sep, pw = l.partition(":")
        email = email.strip().lower()
        if not sep or not email or not pw or len(email) > 254 or len(pw) > 72:
            skipped.append({"email": email or l[:30], "reason": "Формат строки: почта:пароль"}); continue
        # Аккаунт должен существовать, пароль подходить и роль быть обычной: иначе победитель получит нерабочие данные
        # (или аккаунт сотрудника).
        acc = get_user_by_email(email)
        if not acc or not check_password(pw, acc["password_hash"]): skipped.append({"email": email, "reason": "Нет такого аккаунта или пароль не подходит"}); continue
        if acc.get("role") != "user": skipped.append({"email": email, "reason": "Аккаунт не обычный пользователь"}); continue
        pairs.append((email, pw))
    added, dup = game.add_accounts(pairs)
    return jsonify({"ok": True, "added": added, "skipped": skipped + dup})

@app.route("/api/admin/game/usernames", methods=["POST"])
def api_admin_game_usernames():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    data = json_body()
    pool = data.get("pool")
    if pool not in ("case", "top"): return jsonify({"error": "pool: case или top"}), 400
    raw = data.get("usernames")
    if isinstance(raw, str): raw = re.split(r"[\s,;]+", raw)
    if not isinstance(raw, list): return jsonify({"error": "Нужен список юзернеймов"}), 400
    names = []
    for n in raw:
        n = n.strip().lstrip("@") if isinstance(n, str) else ""
        if n and n.lower() not in [x.lower() for x in names]: names.append(n)
    if not names: return jsonify({"error": "Список пуст"}), 400
    if len(names) > 20: return jsonify({"error": "Не больше 20 имён за раз"}), 400
    added, skipped = [], []
    for n in names:   # порядок списка = порядок выпадения из кейса
        # Для призов допустимо имя с цифры (например 2FA): обычная проверка требует первую букву.
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_]{0,31}", n): skipped.append({"username": n, "reason": "Username: только латиница, цифры, _ (до 32 символов)"}); continue
        if game.is_prize_username(n): skipped.append({"username": n, "reason": "Уже в списке призов"}); continue
        if not is_username_reserved(n):   # имя, уже лежащее в резерве, берём как есть
            conflict = username_conflict(n, None)
            if not conflict and get_channel_by_username(n): conflict = "Этот юзернейм занят каналом"
            if not conflict and is_username_on_frigdmant(n): conflict = "Этот юзернейм сейчас на продаже на Frigdmant"
            if conflict: skipped.append({"username": n, "reason": conflict}); continue
            if not add_reserved_username(admin["id"], n): skipped.append({"username": n, "reason": "Не удалось сохранить"}); continue
        try: game.register_prize_username(n, pool); added.append(n)
        except Exception as e: print(f"[game usernames] {e!r}"); skipped.append({"username": n, "reason": "Не удалось сохранить"})
    return jsonify({"ok": True, "added": added, "skipped": skipped})

@app.route("/api/admin/game/remove", methods=["POST"])
def api_admin_game_remove():
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    data = json_body()
    if data.get("kind") == "account":
        acc_id = to_int(data.get("id"))
        if acc_id is None or not game.remove_account(acc_id): return jsonify({"error": "Не найден или уже выдан"}), 404
        return jsonify({"ok": True})
    if data.get("kind") == "username":
        name = str_field(data, "username").lstrip("@")
        if not name or not game.remove_prize_username(name): return jsonify({"error": "Не найден или уже выдан"}), 404
        remove_reserved_username(name)   # имя возвращается в свободные
        return jsonify({"ok": True})
    return jsonify({"error": "kind: account или username"}), 400

@app.route("/api/admin/game/award", methods=["POST"])
def api_admin_game_award():
    """Наградить победителя турнира конкретным призовым юзернеймом (например, @Legend за 2 место)."""
    admin = require_owner()
    if not admin: return jsonify({"error": "Нет доступа"}), 403
    data = json_body(); name = str_field(data, "username").lstrip("@"); uid = to_int(data.get("user_id"))
    if not name or uid is None: return jsonify({"error": "Нужны username и user_id"}), 400
    target = get_user_by_id(uid)
    if not target: return jsonify({"error": "Пользователь с таким ID не найден"}), 404
    if target.get("role") != "user": return jsonify({"error": "Сотрудникам призы турнира не выдаются"}), 400
    try: got = game.award_username(uid, name)
    except Exception as e: print(f"[game award] {e!r}"); return jsonify({"error": "Не удалось выдать"}), 500
    if not got: return jsonify({"error": "Это имя не в списке призов или уже выдано"}), 404
    return jsonify({"ok": True, "to": target.get("username"), "series_no": got["series_no"]})

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
