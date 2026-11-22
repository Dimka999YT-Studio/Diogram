"""Верификация организациями. SQL: supabase/migrations/20261124_organizations.sql.
Организация (название, значок у названия и серый значок для подписи) верифицирует пользователей, ботов и каналы; значок стоит рядом с обычной галочкой.
Организации и их участников (владелец организации, сотрудники) ведёт owner Diogram. Верифицировать от имени организации
могут owner Diogram и участники этой организации. Значки лежат в публичном бакете avatars (папка org/)."""
import io
import threading
import time

from db import supabase

NAME_MIN, NAME_MAX = 2, 60
ICON_MAX_BYTES = 2 * 1024 * 1024
ICON_PX = 128
MAX_ORGS = 200
MAX_MEMBERS = 50
ROLES = ("owner", "staff")
CACHE_TTL = 15

_lock = threading.Lock()
_cache = {"t": 0.0, "orgs": {}, "marks": {}, "cmarks": {}}
TARGETS = ("user", "channel")


def invalidate():
    with _lock:
        _cache["t"] = 0.0


def _load():
    """Организации и значки пользователей/каналов из базы (без таблиц: пусто, повтор через минуту)."""
    try:
        orgs = {o["id"]: o for o in (supabase.table("organizations").select("id, name, icon_url, note_icon_url").limit(MAX_ORGS + 50).execute().data or [])}
        rows = supabase.table("organization_verified").select("org_id, target_type, target_id").limit(100000).execute().data or []
    except Exception as e:
        print(f"[orgs] {e!r}")
        return {}, {}, {}, 60
    marks, cmarks = {}, {}
    for r in rows:
        o = orgs.get(r["org_id"])
        if o: (cmarks if r["target_type"] == "channel" else marks).setdefault(r["target_id"], []).append({"id": o["id"], "name": o["name"], "icon_url": o.get("icon_url"), "note_icon_url": o.get("note_icon_url")})
    for lst in list(marks.values()) + list(cmarks.values()): lst.sort(key=lambda m: m["id"])
    return orgs, marks, cmarks, CACHE_TTL


def _fresh():
    now = time.time()
    if now - _cache["t"] < _cache.get("ttl", CACHE_TTL): return _cache
    with _lock:
        if now - _cache["t"] < _cache.get("ttl", CACHE_TTL): return _cache
        orgs, marks, cmarks, ttl = _load()
        _cache.update(orgs=orgs, marks=marks, cmarks=cmarks, ttl=ttl, t=time.time())
    return _cache


def marks_for(uid, target_type="user"):
    return _fresh()["cmarks" if target_type == "channel" else "marks"].get(uid) or []


def _is_id(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _bot_ids():
    try:
        from gold import BOT_IDS
        return set(BOT_IDS)
    except Exception:
        return set()


def annotate(obj, depth=0):
    """Дописывает orgs (и org_kind: user / bot / channel, если значки есть) пользователям и каналам в ответе. Меняет объект на месте.
    Пользователь: словарь с id, role и verified. Канал: type == "channel" с channel_id (список чатов) или type public/secret с subscribers_count и id."""
    if depth > 6: return obj
    if isinstance(obj, dict):
        if "verified" in obj and "role" in obj and _is_id(obj.get("id")):
            m = marks_for(obj["id"]); obj["orgs"] = m
            if m: obj["org_kind"] = "bot" if obj["id"] in _bot_ids() else "user"
        else:
            cid = obj.get("channel_id") if obj.get("type") == "channel" and _is_id(obj.get("channel_id")) else (obj["id"] if obj.get("type") in ("public", "secret") and "subscribers_count" in obj and _is_id(obj.get("id")) else None)
            if cid is not None:
                m = marks_for(cid, "channel"); obj["orgs"] = m
                if m: obj["org_kind"] = "channel"
        for v in obj.values():
            if isinstance(v, (dict, list)): annotate(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)): annotate(v, depth + 1)
    return obj


def _clean_name(name):
    name = " ".join(str(name or "").split())
    if len(name) < NAME_MIN or len(name) > NAME_MAX: return None
    return name


def process_icon(data):
    """Значок (у названия и для подписи): картинка до 2 МБ, проверка Pillow, 128 px, PNG с прозрачностью. Возвращает (байты, ошибка)."""
    if not data: return None, "Файл не передан"
    if len(data) > ICON_MAX_BYTES: return None, "Значок больше 2 МБ"
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)); img.verify(); img = Image.open(io.BytesIO(data))
        img = img.convert("RGBA"); img.thumbnail((ICON_PX, ICON_PX))
        out = io.BytesIO(); img.save(out, format="PNG", optimize=True)
        return out.getvalue(), None
    except Exception:
        return None, "Неверный формат изображения"


def list_admin():
    orgs = supabase.table("organizations").select("id, name, icon_url, note_icon_url, created_at").order("id").limit(MAX_ORGS + 50).execute().data or []
    members = supabase.table("organization_members").select("org_id, user_id, role").limit(20000).execute().data or []
    counts = {}
    for r in supabase.table("organization_verified").select("org_id").limit(100000).execute().data or []:
        counts[r["org_id"]] = counts.get(r["org_id"], 0) + 1
    for o in orgs:
        o["members"] = [{"user_id": m["user_id"], "role": m["role"]} for m in members if m["org_id"] == o["id"]]
        o["verified_count"] = counts.get(o["id"], 0)
    return orgs


def save_org(org_id, name):
    """Создаёт (org_id пусто) или переименовывает. Возвращает (строка, ошибка)."""
    name = _clean_name(name)
    if not name: return None, f"Название от {NAME_MIN} до {NAME_MAX} символов"
    try:
        allrows = supabase.table("organizations").select("id, name").limit(MAX_ORGS + 50).execute().data or []
        if any(r["id"] != org_id and r["name"].casefold() == name.casefold() for r in allrows): return None, "Организация с таким названием уже есть"
        if org_id:
            rows = supabase.table("organizations").update({"name": name}).eq("id", org_id).execute().data or []
            if not rows: return None, "Организация не найдена"
        else:
            if len(allrows) >= MAX_ORGS: return None, "Слишком много организаций"
            rows = supabase.table("organizations").insert({"name": name}).execute().data or []
            if not rows: return None, "Не удалось создать организацию"
    except Exception as e:
        print(f"[orgs] save {e!r}"); return None, "Не удалось сохранить (применена ли миграция?)"
    invalidate()
    return rows[0], None


def set_icon(org_id, png_bytes, kind="main"):
    """Кладёт значок в бакет avatars/org/ и записывает ссылку: main (у названия) или note (серый, для подписи). Возвращает (url, ошибка)."""
    if kind not in ("main", "note"): return None, "kind: main или note"
    col = "icon_url" if kind == "main" else "note_icon_url"
    try:
        if not (supabase.table("organizations").select("id").eq("id", org_id).limit(1).execute().data or []): return None, "Организация не найдена"
        path = f"org/{org_id}_{kind}_{int(time.time())}.png"
        supabase.storage.from_("avatars").upload(path, png_bytes, file_options={"content-type": "image/png"})
        url = supabase.storage.from_("avatars").get_public_url(path)
        supabase.table("organizations").update({col: url}).eq("id", org_id).execute()
    except Exception as e:
        print(f"[orgs] icon {e!r}"); return None, "Не удалось загрузить значок"
    invalidate()
    return url, None


def delete_org(org_id):
    try:
        rows = supabase.table("organizations").delete().eq("id", org_id).execute().data or []
    except Exception as e:
        print(f"[orgs] delete {e!r}"); return False
    invalidate()
    return bool(rows)


def set_member(org_id, user_id, role):
    """role owner / staff добавляет или меняет, None убирает. Возвращает ошибку или None."""
    if role is not None and role not in ROLES: return "Роль: owner или staff"
    try:
        if not (supabase.table("organizations").select("id").eq("id", org_id).limit(1).execute().data or []): return "Организация не найдена"
        if role is None:
            supabase.table("organization_members").delete().eq("org_id", org_id).eq("user_id", user_id).execute()
            return None
        cur = supabase.table("organization_members").select("user_id").eq("org_id", org_id).limit(MAX_MEMBERS + 1).execute().data or []
        if len(cur) >= MAX_MEMBERS and not any(r["user_id"] == user_id for r in cur): return "Слишком много участников"
        supabase.table("organization_members").upsert({"org_id": org_id, "user_id": user_id, "role": role}).execute()
    except Exception as e:
        print(f"[orgs] member {e!r}"); return "Не удалось сохранить участника"
    return None


def my_orgs(uid):
    try:
        mem = supabase.table("organization_members").select("org_id, role").eq("user_id", uid).execute().data or []
        if not mem: return []
        orgs = {o["id"]: o for o in (supabase.table("organizations").select("id, name, icon_url, note_icon_url").in_("id", [m["org_id"] for m in mem]).execute().data or [])}
    except Exception as e:
        print(f"[orgs] my {e!r}"); return []
    return [dict(orgs[m["org_id"]], role=m["role"]) for m in mem if m["org_id"] in orgs]


def can_verify(user, org_id):
    """owner Diogram может от имени любой организации, остальные только участники этой организации."""
    if not user or not org_id: return False
    try:
        if user.get("role") == "owner":
            return bool(supabase.table("organizations").select("id").eq("id", org_id).limit(1).execute().data)
        return bool(supabase.table("organization_members").select("user_id").eq("org_id", org_id).eq("user_id", user["id"]).limit(1).execute().data)
    except Exception as e:
        print(f"[orgs] can {e!r}"); return False


def set_verified(org_id, target_type, target_id, on, by_user_id):
    if target_type not in TARGETS: return False
    try:
        q = supabase.table("organization_verified")
        if on: q.upsert({"org_id": org_id, "target_type": target_type, "target_id": target_id, "by_user_id": by_user_id}).execute()
        else: q.delete().eq("org_id", org_id).eq("target_type", target_type).eq("target_id", target_id).execute()
    except Exception as e:
        print(f"[orgs] verify {e!r}"); return False
    invalidate()
    return True
