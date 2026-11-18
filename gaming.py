"""Кейсы @Gaming: виртуальные предметы за голду (gold.py; 1 Di = 1 голда). Кейсы и предметы ведёт owner в админке.
Выпавший предмет попадает в инвентарь, «Вывести» создаёт заявку владельцу (статусы have/new/work/given/rejected).
Таблицы: supabase/migrations/20261116_gaming_cases.sql. Всё считается на сервере."""
import io
import ipaddress
import secrets
import socket
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx
from PIL import Image

import gold
from db import supabase, get_or_create_chat, save_message

BOT_ID = 1000020
MSK = timezone(timedelta(hours=3))
DAILY_OPENS = 30
MAX_IMG_BYTES = 3 * 1024 * 1024
STATUS_TITLES = {"have": "в инвентаре", "new": "заявка отправлена", "work": "владелец связался", "given": "выдано", "rejected": "отклонено"}
INVITE_TEXT = ("🎰 Drop4ik\n\nОткрывай кейсы за голду (1 Di = 1 голда) и получай виртуальные предметы для игры. "
               "Выпавший предмет можно вывести: нажми «Вывести», и владелец свяжется с тобой.")


def _clean(s, n):
    return s.strip()[:n] if isinstance(s, str) else ""


def _chance_ok(items):
    return abs(sum(float(i["chance"]) for i in items) - 100) < 0.0005


KEEP = object()   # «не менять» для необязательных полей save_case
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _opens_dt(c):
    """Когда кейс станет доступен (datetime с поясом) или None."""
    v = c.get("opens_at")
    if not v: return None
    try: d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError: return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_locked(c):
    d = _opens_dt(c)
    return bool(d and d > datetime.now(timezone.utc))


def opens_label(c):
    d = _opens_dt(c)
    if not d: return None
    d = d.astimezone(MSK)
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year} года"


def _avail(i):
    """Предмет ещё можно выиграть: включён и не закончился (stock пусто = без ограничения)."""
    return bool(i.get("active")) and (i.get("stock") is None or i["stock"] > 0)


# ---------- кейсы и предметы ----------
def _cases(active_only):
    q = supabase.table("gcase_cases").select("*").order("sort_no").order("id")
    if active_only: q = q.eq("active", True)
    cases = q.execute().data or []
    items = supabase.table("gcase_items").select("*").order("id").execute().data or []
    by = {}
    for it in items: by.setdefault(it["case_id"], []).append(it)
    for c in cases:
        its = by.get(c["id"], [])
        c["items"] = [i for i in its if _avail(i)] if active_only else its   # игроку: без закончившихся
        c["ready"] = _chance_ok([i for i in its if i["active"]]) and any(_avail(i) for i in its)
        c["chance_sum"] = round(sum(float(i["chance"]) for i in its if i["active"]), 3)
    return cases


def public_cases():
    out = []
    for c in _cases(True):
        # у закончившихся предметов шанс перераспределяется между оставшимися пропорционально
        total = sum(float(i["chance"]) for i in c["items"]) or 100.0
        out.append({"id": c["id"], "title": c["title"], "price": c["price"], "ready": c["ready"], "locked": is_locked(c), "opens_label": opens_label(c),
                    "drops": [{"id": i["id"], "kind": "item", "label": i["name"], "image": i.get("image_url"), "chance": round(float(i["chance"]) * 100 / total, 3),
                               "info": i.get("info"), "stock": i.get("stock")} for i in c["items"]]})
    return out


def roll(items, rnd=None):
    """Предмет по шансам (точность 0,001%). rnd(n) -> 0..n-1 (в тестах подменяется)."""
    w = [int(round(float(i["chance"]) * 1000)) for i in items]
    total = sum(w)   # закончившиеся предметы в список не попадают, вес оставшихся нормируется сам
    if total <= 0: raise RuntimeError("в кейсе нет предметов")
    r = (rnd or secrets.randbelow)(total); acc = 0
    for it, x in zip(items, w):
        acc += x
        if r < acc: return it
    raise RuntimeError("шансы не покрывают диапазон")


def opens_today(user_id):
    start = datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    res = supabase.table("gcase_inventory").select("id", count="exact").eq("user_id", user_id).gte("created_at", start).execute()
    return res.count if res.count is not None else len(res.data or [])


def _take_stock(item):
    """Забрать один экземпляр предмета из кейса (атомарно: обновление с проверкой старого значения). True, если удалось."""
    if item.get("stock") is None: return True
    cur = item["stock"]
    for _ in range(6):
        if cur <= 0: return False
        done = supabase.table("gcase_items").update({"stock": cur - 1}).eq("id", item["id"]).eq("stock", cur).execute().data
        if done: item["stock"] = cur - 1; return True
        fresh = supabase.table("gcase_items").select("stock").eq("id", item["id"]).execute().data
        if not fresh: return False
        if fresh[0]["stock"] is None: return True
        cur = fresh[0]["stock"]
    return False


def _return_stock(item_id):
    """Вернуть экземпляр при сбое выдачи (только для предметов с ограниченным запасом)."""
    try:
        for _ in range(6):
            cur = supabase.table("gcase_items").select("stock").eq("id", item_id).execute().data
            if not cur or cur[0]["stock"] is None: return
            if supabase.table("gcase_items").update({"stock": cur[0]["stock"] + 1}).eq("id", item_id).eq("stock", cur[0]["stock"]).execute().data: return
    except Exception as e: print(f"[gaming] не удалось вернуть предмет {item_id} в кейс: {e!r}")


def open_case(user_id, case_id, rnd=None):
    """Купить и открыть кейс. Возвращает (строка инвентаря, ошибка). Сбой после списания возвращает голду."""
    try:
        res = supabase.table("gcase_cases").select("*").eq("id", case_id).eq("active", True).execute().data
    except Exception as e:
        print(f"[gaming] {e!r}"); return None, "Не удалось открыть кейс. Попробуй ещё раз."
    if not res: return None, "Такого кейса нет"
    case = res[0]
    if is_locked(case): return None, f"Этот кейс будет добавлен {opens_label(case)}"
    all_items = [i for i in (supabase.table("gcase_items").select("*").eq("case_id", case_id).eq("active", True).execute().data or [])]
    if not all_items or not _chance_ok(all_items): return None, "Этот кейс ещё настраивается"
    if not any(_avail(i) for i in all_items): return None, "В этом кейсе все предметы разыграны"
    if opens_today(user_id) >= DAILY_OPENS: return None, f"Лимит {DAILY_OPENS} кейсов в день. Приходи завтра."
    parts, err = gold.spend(user_id, case["price"], f"case:{case['id']}")
    if err: return None, err
    item = None
    try:
        for _ in range(6):   # предмет мог закончиться между чтением и списанием: перебрасываем по оставшимся
            avail = [i for i in all_items if _avail(i)]
            if not avail: break
            cand = roll(avail, rnd)
            if _take_stock(cand): item = cand; break
            cand["stock"] = 0
        if item is None:
            gold.refund(user_id, parts, f"case:{case['id']}")
            return None, "В этом кейсе все предметы разыграны. Голда возвращена."
        row = supabase.table("gcase_inventory").insert({
            "user_id": user_id, "item_id": item["id"], "case_id": case["id"], "case_title": case["title"], "price_paid": case["price"],
            "name": item["name"], "image_url": item.get("image_url"), "info": item.get("info")}).execute().data[0]
    except Exception as e:
        print(f"[gaming] выдача не удалась ({e!r}), возвращаю голду пользователю {user_id}")
        if item is not None: _return_stock(item["id"])
        gold.refund(user_id, parts, f"case:{case['id']}")
        return None, "Не удалось выдать предмет. Голда возвращена, попробуй ещё раз."
    gold.commission(user_id, parts[1])   # 5% пригласившему только с купленной голды
    return row, None


def inventory(user_id):
    return supabase.table("gcase_inventory").select("id,case_title,name,image_url,info,status,admin_note,created_at,requested_at").eq("user_id", user_id).order("id", desc=True).limit(200).execute().data or []


def _tell(user_id, text):
    try:
        chat = get_or_create_chat(BOT_ID, user_id)
        if chat: save_message(chat["id"], BOT_ID, text, "text")
    except Exception as e:
        print(f"[gaming notify] {e!r}")


def request_withdraw(user, inv_id):
    """Заявка на вывод: только свой предмет в статусе have (одним условным UPDATE, повторно не пройдёт)."""
    now = datetime.now(timezone.utc).isoformat()
    rows = supabase.table("gcase_inventory").update({"status": "new", "requested_at": now, "updated_at": now}) \
        .eq("id", inv_id).eq("user_id", user["id"]).eq("status", "have").execute().data
    if not rows: return None
    row = rows[0]
    who = (f"@{user['username']}" if user.get("username") else (user.get("first_name") or "без имени")) + f" (ID {user['id']})"
    for o in supabase.table("users").select("id").eq("role", "owner").execute().data or []:
        _tell(o["id"], f"📤 Заявка на вывод\nИгрок: {who}\nПредмет: {row['name']}\nКейс: {row['case_title']}\nСвяжись с игроком и выдай предмет, потом поставь статус в админке (🎰 Кейсы @Gaming).")
    return row


# ---------- админка (только owner) ----------
def admin_overview():
    reqs = supabase.table("gcase_inventory").select("*").in_("status", ["new", "work"]).order("requested_at").limit(200).execute().data or []
    return {"cases": _cases(False), "requests": reqs}


def save_case(cid, title, price, active, opens_at=KEEP):
    title = _clean(title, 60)
    if not title: return None, "Нужно название"
    if not isinstance(price, int) or isinstance(price, bool) or not 1 <= price <= 100000: return None, "Цена от 1 до 100000 голды"
    data = {"title": title, "price": price, "active": bool(active)}
    if opens_at is not KEEP:   # дата открытия: "ГГГГ-ММ-ДД" (00:00 по Москве) или пусто = сразу
        if opens_at in (None, ""): data["opens_at"] = None
        else:
            try: day = datetime.strptime(str(opens_at).strip()[:10], "%Y-%m-%d")
            except ValueError: return None, "Дата в формате ГГГГ-ММ-ДД"
            if not 2020 <= day.year <= 2100: return None, "Дата в формате ГГГГ-ММ-ДД"
            data["opens_at"] = day.replace(tzinfo=MSK).astimezone(timezone.utc).isoformat()
    if cid: res = supabase.table("gcase_cases").update(data).eq("id", cid).execute().data
    else: res = supabase.table("gcase_cases").insert(dict(data, sort_no=int(time.time()) % 1000000)).execute().data
    return (res[0] if res else None), (None if res else "Кейс не найден")


def _num(v):
    try: x = float(v)
    except (TypeError, ValueError): return None
    return round(x, 3) if 0 < x <= 100 else None


def save_item(iid, case_id, name, image_url, info, chance, active, stock=None):
    name = _clean(name, 80)
    if not name: return None, "Нужно название предмета"
    ch = _num(chance)
    if ch is None: return None, "Шанс от 0,001 до 100 (%)"
    if stock in (None, ""): stock = None
    elif isinstance(stock, bool) or not isinstance(stock, int) or not (0 <= stock <= 1_000_000): return None, "Количество: целое от 0 или пусто (без ограничения)"
    img = None
    if isinstance(image_url, str) and image_url.strip():
        img, err = store_image(image_url.strip())
        if err: return None, err
    data = {"case_id": case_id, "name": name, "info": _clean(info, 1000) or None, "chance": ch, "active": bool(active), "stock": stock}
    if img: data["image_url"] = img
    elif iid is None: data["image_url"] = None
    if iid: res = supabase.table("gcase_items").update(data).eq("id", iid).execute().data
    else:
        if not supabase.table("gcase_cases").select("id").eq("id", case_id).execute().data: return None, "Кейса нет"
        res = supabase.table("gcase_items").insert(data).execute().data
    return (res[0] if res else None), (None if res else "Предмет не найден")


def delete_item(iid):
    supabase.table("gcase_items").delete().eq("id", iid).execute()


def set_status(inv_id, status, note):
    if status not in ("work", "given", "rejected"): return None, "Неверный статус"
    now = datetime.now(timezone.utc).isoformat()
    data = {"status": status, "updated_at": now, "admin_note": _clean(note, 500) or None}
    rows = supabase.table("gcase_inventory").update(data).eq("id", inv_id).in_("status", ["new", "work"]).execute().data
    if not rows: return None, "Заявка не найдена или уже закрыта"
    r = rows[0]
    _tell(r["user_id"], f"📦 Заявка на «{r['name']}»: {STATUS_TITLES[status]}." + (f"\n{r['admin_note']}" if r.get("admin_note") else ""))
    return r, None


# ---------- картинка по ссылке: копируем в наше хранилище (CSP пускает картинки только с нашего Supabase) ----------
def _public_host(host):
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
            if not ipaddress.ip_address(sa[0]).is_global: return False
        return True
    except Exception:
        return False


def store_image(url):
    p = urllib.parse.urlparse(url)
    if p.scheme != "https" or not p.hostname: return None, "Нужна ссылка https://…"
    if p.hostname.endswith(".supabase.co"): return url, None
    if not _public_host(p.hostname): return None, "Ссылка не подходит"
    try:
        with httpx.stream("GET", url, timeout=8, follow_redirects=False, headers={"User-Agent": "Diogram"}) as r:
            if r.status_code != 200: return None, f"Ссылка не открылась (код {r.status_code}). Прямая ссылка на картинку?"
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_IMG_BYTES: return None, "Картинка больше 3 МБ"
    except Exception as e:
        print(f"[gaming img] {e!r}"); return None, "Не удалось скачать картинку по ссылке"
    try:
        img = Image.open(io.BytesIO(bytes(buf))); img.verify(); img = Image.open(io.BytesIO(bytes(buf)))
        img.thumbnail((512, 512))
        out = io.BytesIO()
        if img.mode in ("RGBA", "P", "LA"): img.convert("RGBA").save(out, format="PNG", optimize=True); ext = "png"
        else: img.convert("RGB").save(out, format="JPEG", quality=88, optimize=True); ext = "jpg"
    except Exception:
        return None, "По ссылке не картинка (нужны JPG, PNG, WEBP, GIF)"
    name = f"gcase/{secrets.token_hex(12)}.{ext}"
    try:
        supabase.storage.from_("avatars").upload(name, out.getvalue(), file_options={"content-type": "image/png" if ext == "png" else "image/jpeg"})
        return supabase.storage.from_("avatars").get_public_url(name), None
    except Exception as e:
        print(f"[gaming img upload] {e!r}"); return None, "Не удалось сохранить картинку"
