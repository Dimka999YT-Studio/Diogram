"""Капча «нарисуй круг», включается при подозрительной активности (много неудачных входов, всплеск регистраций
с одного IP, спам-отправки). Рисунок проверяется на сервере и сохраняется анонимно (без аккаунта и IP) для отбора
владельцем. Всё состояние (флаги, одноразовые токены) в памяти процесса, как и лимиты: при нескольких воркерах
нужен Redis. Описание сбора есть в политике конфиденциальности (раздел 11), версия в POLICY_VERSION."""
import base64, hashlib, hmac, json, math, os, secrets, threading, time

from db import supabase

POLICY_VERSION = 1
CHALLENGE_TTL = 300        # на рисунок 5 минут
PASS_TTL = 600             # выданный пропуск действует 10 минут и только один раз
FLAG_TTL = 900             # после подозрительной активности капча нужна 15 минут
MAX_POINTS, MAX_STROKES, MAX_DURATION_MS = 2000, 3, 30000

_lock = threading.Lock()
_flags = {}                # (kind, key) -> до какого времени требуется капча
_used = {}                 # nonce -> до какого времени помним, что токен использован


def _secret():
    s = os.environ.get("JWT_SECRET", "")
    return (s or "dev-secret").encode()


def _sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    mac = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return raw + "." + mac


def _unsign(token):
    if not isinstance(token, str) or token.count(".") != 1 or len(token) > 400: return None
    raw, mac = token.split(".")
    good = hmac.new(_secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(mac, good): return None
    try: return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception: return None


def _cleanup(now):
    for d in (_flags, _used):
        for k in [k for k, until in d.items() if until < now]: d.pop(k, None)


def flag(kind, key, seconds=FLAG_TTL):
    """Требовать капчу для (kind, key), например ('login', ip) или ('send', user_id)."""
    with _lock:
        now = time.time(); _cleanup(now); _flags[(kind, str(key))] = now + seconds


def is_flagged(kind, key):
    with _lock:
        until = _flags.get((kind, str(key)))
        return bool(until and until > time.time())


def make_challenge(trigger):
    return _sign({"k": "c", "n": secrets.token_hex(8), "t": int(time.time()), "g": trigger if trigger in ("login", "register", "send") else None})


def consume_pass(token):
    """Проверить пропуск (одноразовый). True, если токен настоящий, свежий и ещё не использован."""
    p = _unsign(token)
    if not p or p.get("k") != "p": return False
    now = time.time()
    if now - p.get("t", 0) > PASS_TTL: return False
    with _lock:
        _cleanup(now)
        if p["n"] in _used: return False
        _used[p["n"]] = now + PASS_TTL + 60
    return True


def analyze_circle(strokes):
    """Проверка рисунка. Возвращает (ok, score, сообщение). Координаты 0..1000, время в мс."""
    if not isinstance(strokes, list) or not 1 <= len(strokes) <= MAX_STROKES: return False, 0.0, "Нарисуй круг одним движением"
    pts = []
    for st in strokes:
        if not isinstance(st, list) or len(st) < 2: return False, 0.0, "Нарисуй круг одним движением"
        for p in st:
            if not (isinstance(p, (list, tuple)) and len(p) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)):
                return False, 0.0, "Неверный рисунок"
            if not (0 <= p[0] <= 1000 and 0 <= p[1] <= 1000 and 0 <= p[2] <= 10 * MAX_DURATION_MS): return False, 0.0, "Неверный рисунок"
            pts.append((float(p[0]), float(p[1]), float(p[2])))
        if len(pts) > MAX_POINTS: return False, 0.0, "Слишком длинный рисунок"
    if len(pts) < 15: return False, 0.0, "Нарисуй круг медленнее и крупнее"
    ts = [p[2] for p in pts]
    if any(b < a for a, b in zip(ts, ts[1:])): return False, 0.0, "Неверный рисунок"
    dur = ts[-1] - ts[0]
    if dur < 300: return False, 0.0, "Слишком быстро, нарисуй круг рукой"
    if dur > MAX_DURATION_MS: return False, 0.0, "Слишком долго, попробуй ещё раз"
    if len(set(ts)) < 10: return False, 0.0, "Неверный рисунок"
    cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
    rs = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    rm = sum(rs) / len(rs)
    if rm < 100: return False, 0.0, "Круг слишком маленький, нарисуй крупнее"
    std = math.sqrt(sum((r - rm) ** 2 for r in rs) / len(rs))
    rough = std / rm
    if rough > 0.22: return False, 0.0, "Получилось не очень круглое, попробуй ещё раз"
    # охват по углу: круг должен быть замкнут (не дуга)
    ang = 0.0; prev = math.atan2(pts[0][1] - cy, pts[0][0] - cx)
    for p in pts[1:]:
        a = math.atan2(p[1] - cy, p[0] - cx); d = a - prev
        while d > math.pi: d -= 2 * math.pi
        while d < -math.pi: d += 2 * math.pi
        ang += d; prev = a
    if abs(ang) < 2 * math.pi * 0.85: return False, 0.0, "Круг не замкнут, обведи полностью"
    gap = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
    if gap > rm * 0.8: return False, 0.0, "Круг не замкнут, обведи полностью"
    return True, round(max(0.0, 1 - rough / 0.22), 3), ""


def verify(token, strokes, device, trigger_hint=None):
    """Проверить рисунок по токену. Возвращает (результат, ошибка). Рисунок сохраняется в любом случае."""
    p = _unsign(token)
    if not p or p.get("k") != "c" or time.time() - p.get("t", 0) > CHALLENGE_TTL: return None, "Задание устарело, обнови страницу"
    with _lock:
        _cleanup(time.time())
        if p["n"] in _used: return None, "Задание уже использовано, обнови окно"
        _used[p["n"]] = time.time() + CHALLENGE_TTL + 60
    ok, score, msg = analyze_circle(strokes)
    _save(p.get("g"), strokes, device, ok, score)
    if not ok: return None, msg
    return _sign({"k": "p", "n": secrets.token_hex(8), "t": int(time.time())}), None


def _save(trigger, strokes, device, passed, score):
    try:
        flat = [pt for st in strokes if isinstance(st, list) for pt in st if isinstance(pt, (list, tuple)) and len(pt) == 3]
        if not flat or len(flat) > MAX_POINTS: return
        clean = [[[int(round(p[0])), int(round(p[1])), int(round(p[2]))] for p in st] for st in strokes]
        supabase.table("captcha_samples").insert({
            "kind": "circle", "trigger": trigger, "strokes": clean, "points": len(flat),
            "duration_ms": int(flat[-1][2] - flat[0][2]), "device": device if device in ("touch", "mouse") else None,
            "passed": bool(passed), "score": float(score), "policy_version": POLICY_VERSION}).execute()
    except Exception as e:
        print(f"[captcha save] {e!r}")


# ---- админка (owner) ----
def list_samples(quality=None, passed=None, limit=60):
    q = supabase.table("captcha_samples").select("id,trigger,points,duration_ms,device,passed,score,quality,created_at")
    if quality == "none": q = q.is_("quality", "null")
    elif quality in ("good", "bad"): q = q.eq("quality", quality)
    if passed is not None: q = q.eq("passed", bool(passed))
    return q.order("id", desc=True).limit(limit).execute().data or []


def get_sample(sample_id):
    r = supabase.table("captcha_samples").select("*").eq("id", sample_id).limit(1).execute().data
    return r[0] if r else None


def rate_sample(sample_id, quality):
    if quality not in ("good", "bad", None): return False
    supabase.table("captcha_samples").update({"quality": quality}).eq("id", sample_id).execute()
    return True


def stats():
    out = {}
    for name, f in (("total", None), ("passed", ("passed", True)), ("good", ("quality", "good")), ("bad", ("quality", "bad"))):
        q = supabase.table("captcha_samples").select("id", count="exact")
        if f: q = q.eq(*f)
        out[name] = q.limit(1).execute().count or 0
    return out


def export_good(limit=1000):
    return supabase.table("captcha_samples").select("id,kind,strokes,points,duration_ms,device,score,policy_version,created_at").eq("quality", "good").order("id").limit(limit).execute().data or []
