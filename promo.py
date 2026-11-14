"""Промокоды: создание (owner/developer), активация, награды.
Типы: timed (до даты, без лимита активаций) и limited (лимит активаций 1..∞, без даты).
Награды (rewards): di, pdg_days, clouds, cases {ключ: шт}, username (bool, имена в promo_usernames).
Одна активация на человека. Атомарность — SQL-функции promo_claim / promo_release."""
import re
from datetime import datetime, timezone

import game
from db import supabase, add_balance, activate_premium

CODE_RE = re.compile(r"[A-Za-z0-9_\-]{3,32}")
USERNAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_]{0,31}")
MAX_DI, MAX_PDG, MAX_CLOUDS, MAX_CASES = 1_000_000, 3650, 1000, 100

CLAIM_ERRORS = {
    "not_found": "Такого промокода нет",
    "inactive": "Промокод отключён",
    "expired": "Срок действия промокода закончился",
    "exhausted": "Активации промокода закончились",
    "used": "Ты уже активировал этот промокод",
    "no_username": "Юзернеймы по этому промокоду закончились",
}


def _int(v, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v): return None
    v = int(v)
    return v if lo <= v <= hi else None


def clean_rewards(raw):
    """Проверка наград из админки. Возвращает (rewards, ошибка). Юзернеймы тут не учитываются."""
    if not isinstance(raw, dict): return None, "Нужны награды"
    out = {}
    if raw.get("di") not in (None, 0, ""):
        v = _int(raw["di"], 1, MAX_DI)
        if v is None: return None, f"Di: от 1 до {MAX_DI}"
        out["di"] = v
    if raw.get("pdg_days") not in (None, 0, ""):
        v = _int(raw["pdg_days"], 1, MAX_PDG)
        if v is None: return None, f"Дни PDG: от 1 до {MAX_PDG}"
        out["pdg_days"] = v
    if raw.get("clouds") not in (None, 0, ""):
        v = _int(raw["clouds"], 1, MAX_CLOUDS)
        if v is None: return None, f"Облачка: от 1 до {MAX_CLOUDS}"
        out["clouds"] = v
    cases = raw.get("cases")
    if cases:
        if not isinstance(cases, dict): return None, "Кейсы: неверный формат"
        cc = {}
        for k, q in cases.items():
            if q in (None, 0, ""): continue
            if k not in game.CASES: return None, "Такого кейса нет"
            v = _int(q, 1, MAX_CASES)
            if v is None: return None, f"Кейсы: от 1 до {MAX_CASES} шт."
            cc[k] = v
        if cc: out["cases"] = cc
    if raw.get("username"): out["username"] = True
    return out, None


def create_code(admin_id, code, kind, rewards, expires_at=None, max_activations=None, note=None):
    """Создать код. rewards уже проверены clean_rewards. Возвращает (строка, ошибка)."""
    if not isinstance(code, str) or not CODE_RE.fullmatch(code.strip()): return None, "Код: 3–32 символа, латиница, цифры, _ и -"
    code = code.strip()
    if kind not in ("timed", "limited"): return None, "Тип: timed или limited"
    row = {"code": code, "kind": kind, "rewards": rewards, "created_by": admin_id, "note": (note or "")[:200] or None}
    if kind == "timed":
        try: dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except Exception: return None, "Укажи дату окончания"
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        if dt <= datetime.now(timezone.utc): return None, "Дата окончания уже прошла"
        row["expires_at"] = dt.isoformat()
    else:
        if max_activations in (None, "", 0, "inf"): row["max_activations"] = None   # бесконечно
        else:
            v = _int(max_activations, 1, 10_000_000)
            if v is None: return None, "Активаций: от 1 или пусто (без лимита)"
            row["max_activations"] = v
    if not rewards: return None, "Выбери хотя бы одну награду"
    try:
        exists = supabase.table("promo_codes").select("id").ilike("code", code).limit(5).execute().data or []
        if any((r.get("id")) for r in exists): return None, "Такой код уже есть"
        res = supabase.table("promo_codes").insert(row).execute()
        return res.data[0], None
    except Exception as e:
        print(f"[promo create] {e!r}")
        return None, "Не удалось создать (возможно, такой код уже есть)"


def add_usernames(code_id, names):
    """Привязать юзернеймы к коду (они уже лежат в резерве владельца). Порядок = порядок выдачи."""
    last = supabase.table("promo_usernames").select("sort_no").eq("code_id", code_id).order("sort_no", desc=True).limit(1).execute().data
    n = (last[0]["sort_no"] if last else 0)
    for name in names:
        n += 1
        supabase.table("promo_usernames").insert({"username": name, "code_id": code_id, "sort_no": n}).execute()


def list_codes():
    codes = supabase.table("promo_codes").select("*").order("id", desc=True).limit(200).execute().data or []
    names = supabase.table("promo_usernames").select("code_id,username,given_to,series_no,resales").execute().data or []
    by = {}
    for r in names: by.setdefault(r["code_id"], []).append(r)
    for c in codes:
        lst = by.get(c["id"], [])
        c["usernames_total"] = len(lst)
        c["usernames_given"] = sum(1 for r in lst if r.get("given_to"))
        c["usernames_free"] = [r["username"] for r in lst if not r.get("given_to")]
        c["resales"] = sum(r.get("resales") or 0 for r in lst)
    return codes


def code_activations(code_id):
    return supabase.table("promo_activations").select("user_id,created_at").eq("code_id", code_id).order("created_at", desc=True).limit(200).execute().data or []


def set_active(code_id, active):
    supabase.table("promo_codes").update({"active": bool(active)}).eq("id", code_id).execute()


def username_info(username):
    """Для окна «Юзернейм»: промо-имя → {series_no, total, given_at, resales}, иначе None."""
    if not username: return None
    res = supabase.table("promo_usernames").select("*").ilike("username", username).limit(20).execute()
    row = next((r for r in (res.data or []) if (r.get("username") or "").lower() == username.lower()), None)
    if not row or not row.get("given_at"): return None
    total = supabase.table("promo_usernames").select("username", count="exact").eq("code_id", row["code_id"]).execute().count or 0
    return {"series_no": row["series_no"], "total": total, "given_at": row["given_at"], "resales": row.get("resales") or 0}


def admin_of(code_id, fallback):
    try:
        r = supabase.table("promo_codes").select("created_by").eq("id", code_id).limit(1).execute().data
        return (r[0].get("created_by") if r else None) or fallback
    except Exception: return fallback


def claim(user_id, code):
    """Активировать код. Возвращает (результат, ошибка)."""
    if not isinstance(code, str) or not CODE_RE.fullmatch(code.strip()): return None, "Такого промокода нет"
    try:
        res = supabase.rpc("promo_claim", {"p_code": code.strip(), "p_user": user_id}).execute()
    except Exception as e:
        print(f"[promo_claim] {e!r}")
        return None, "Не удалось активировать, попробуй ещё раз"
    row = (res.data or [None])[0]
    if not row: return None, "Не удалось активировать, попробуй ещё раз"
    if row["status"] != "ok": return None, CLAIM_ERRORS.get(row["status"], "Промокод недоступен")
    rw = row.get("rewards") or {}
    got = {}
    try:
        if rw.get("di"):
            if not add_balance(user_id, int(rw["di"]), f"Промокод {code.strip()}"): raise RuntimeError("di")
            got["di"] = int(rw["di"])
        if rw.get("pdg_days"):
            ok, err = activate_premium(user_id, int(rw["pdg_days"]), 0, f"Промокод {code.strip()}")
            if not ok: raise RuntimeError(f"pdg {err}")
            got["pdg_days"] = int(rw["pdg_days"])
        if rw.get("clouds"):
            game.credit_map_clouds(user_id, int(rw["clouds"]))
            got["clouds"] = int(rw["clouds"])
        for k, q in (rw.get("cases") or {}).items():
            now, err = game.grant_cases(admin_of(row["code_id"], user_id), user_id, k, int(q))
            if err: raise RuntimeError(f"case {err}")
            got.setdefault("cases", {})[k] = int(q)
    except Exception as e:
        # Сбой выдачи: активацию откатываем, чтобы код можно было применить снова.
        print(f"[promo reward] {e!r} уже выдано: {got}")
        if got:   # часть наград уже выдана: активацию оставляем, чтобы не выдать их дважды
            return None, "Часть наград выдана, остальное не удалось. Напиши в поддержку, мы доначислим."
        try: supabase.rpc("promo_release", {"p_code_id": row["code_id"], "p_user": user_id}).execute()
        except Exception as e2: print(f"[promo_release] {e2!r}")
        return None, "Не удалось выдать награду. Попробуй ещё раз; если повторится — напиши в поддержку."
    if row.get("username"):
        got["username"] = {"name": row["username"], "no": row["uname_no"], "total": row["uname_total"]}
    return got, None
