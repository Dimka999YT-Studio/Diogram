"""Мультиплеерный мир сезонной игры: лобби, карта, движение, коробки, поимка, итоги раунда.

Всё хранится в памяти процесса (как «печатает…» и лимиты): сервис работает одним процессом gunicorn.
Сервер авторитетен: клиент шлёт только направление движения и «сломать коробку», позиции, поимку,
облачка и итоги считает сервер. Время идёт «лениво»: мир досчитывается при каждом запросе игрока.
"""
import math
import random
import threading
import time

W, H = 1400, 900
PLAYER_R = 14
HIDER_SPEED = 150.0
SEEKER_SPEED = 140.0
VIS_R = 260.0            # туман: дальше этого радиуса игрок других не видит
BUSH_REVEAL = 70.0       # спрятавшегося в кусте видно только вплотную
CATCH_DIST = 30.0        # касание искателем
CLOUD_PICK = 28.0
BOX_RANGE = 70.0
BOX_TIME = 10.0          # клик по коробке и ждать 10 секунд
BOX_DAMAGE = 25
SEEKER_HP = 100
ROUND_SEC = 300          # 5 минут
FREEZE_SEC = 8           # в начале раунда искатели стоят, прячущиеся разбегаются
START_DELAY = 12         # когда в лобби есть искатель и прячущийся, раунд стартует через столько секунд
FULL_START_DELAY = 3
MAX_PLAYERS = 5
MAX_LOBBIES = 5
IDLE_KICK = 15           # нет запросов дольше этого: игрок вышел
ENDED_KEEP = 45          # сколько лобби с итогами живёт после конца раунда
CLOUDS_ON_MAP = 9
MAX_CLOUDS_PER_GAME = 10
WIN_BONUS = 1


# ---------------- карта (одна и та же для всех, считается один раз) ----------------
def _build_map():
    rnd = random.Random(20261103)
    solid = []   # деревья: непроходимые круги
    bushes = []  # кусты: проходимые, внутри игрока не видно
    boxes = []
    spawns = []

    def free(x, y, r, margin=0):
        for (ox, oy, orr) in solid + [(b[0], b[1], b[2]) for b in bushes] + [(b["x"], b["y"], 22) for b in boxes]:
            if math.hypot(x - ox, y - oy) < r + orr + margin: return False
        return True
    anchors = [(380, 230), (1020, 230), (380, 670), (1020, 670)]
    for i, (ax, ay) in enumerate(anchors):
        boxes.append({"id": i + 1, "x": ax + rnd.randint(-60, 60), "y": ay + rnd.randint(-40, 40)})
    for _ in range(900):
        if len(solid) >= 44: break
        x, y, r = rnd.randint(40, W - 40), rnd.randint(40, H - 40), rnd.randint(20, 28)
        if math.hypot(x - W / 2, y - H / 2) < 110: continue   # центр свободен: там стартуют искатели
        if free(x, y, r, 40): solid.append((x, y, r))
    for _ in range(900):
        if len(bushes) >= 16: break
        x, y, r = rnd.randint(60, W - 60), rnd.randint(60, H - 60), rnd.randint(40, 55)
        if all(math.hypot(x - sx, y - sy) > r + sr + 10 for sx, sy, sr in solid) and \
           all(math.hypot(x - b[0], y - b[1]) > r + b[2] for b in bushes) and \
           all(math.hypot(x - bx["x"], y - bx["y"]) > r + 30 for bx in boxes):
            bushes.append((x, y, r))
    for _ in range(400):
        if len(spawns) >= 12: break
        x, y = rnd.randint(60, W - 60), rnd.randint(60, H - 60)
        if math.hypot(x - W / 2, y - H / 2) > 300 and free(x, y, PLAYER_R + 6, 20): spawns.append((x, y))
    snow = [(rnd.randint(0, W), rnd.randint(0, H), rnd.randint(18, 60)) for _ in range(26)]
    return {"w": W, "h": H, "trees": solid, "bushes": bushes, "boxes": boxes, "spawns": spawns, "snow": snow,
            "start": (W / 2, H / 2), "vis_r": VIS_R, "box_time": BOX_TIME, "box_range": BOX_RANGE,
            "player_r": PLAYER_R, "hider_speed": HIDER_SPEED, "seeker_speed": SEEKER_SPEED}

MAP = _build_map()


def public_map():
    m = dict(MAP)
    m["trees"] = [list(t) for t in MAP["trees"]]; m["bushes"] = [list(b) for b in MAP["bushes"]]
    m["snow"] = [list(s) for s in MAP["snow"]]; m["spawns"] = []; m["start"] = list(MAP["start"])
    return m


def _in_bush(x, y):
    return any(math.hypot(x - bx, y - by) < br for bx, by, br in MAP["bushes"])


def _push_out(x, y, extra_circles=()):
    for (ox, oy, orr) in list(MAP["trees"]) + list(extra_circles):
        d = math.hypot(x - ox, y - oy); need = PLAYER_R + orr
        if d < need:
            if d < 1e-6: x += need; continue
            x = ox + (x - ox) / d * need; y = oy + (y - oy) / d * need
    return min(max(x, PLAYER_R), W - PLAYER_R), min(max(y, PLAYER_R), H - PLAYER_R)


class Player:
    def __init__(self, uid, name, staff, now):
        self.uid, self.name, self.staff = uid, name, staff
        self.role = "seeker" if staff else "hider"
        self.x = self.y = 0.0
        self.dx = self.dy = 0.0
        self.seen = now
        self.caught = False
        self.clouds = 0
        self.breaking = None


class Lobby:
    def __init__(self, num, now):
        self.num = num
        self.state = "waiting"          # waiting -> playing -> ended
        self.players = {}
        self.created = now
        self.start_at = None
        self.round_start = self.round_end = None
        self.boxes = []
        self.clouds = []
        self.seeker_hp = SEEKER_HP
        self.last_step = now
        self.result = None
        self.ended_at = None
        self.events = []
        self.ev_id = 0
        self.lock = threading.Lock()

    def event(self, text):
        self.ev_id += 1; self.events.append({"id": self.ev_id, "text": text}); self.events = self.events[-8:]

    def seekers(self): return [p for p in self.players.values() if p.role == "seeker"]
    def hiders(self): return [p for p in self.players.values() if p.role == "hider"]


class Hub:
    """consume(uid) -> bool: списать попытку; credit(uid, clouds): начислить облачки. Оба вызываются под замком лобби."""
    def __init__(self, consume=None, credit=None, clock=time.time):
        self.lobbies = {}
        self.user_lobby = {}
        self.lock = threading.Lock()
        self.consume = consume or (lambda uid: True)
        self.credit = credit or (lambda uid, n: None)
        self.clock = clock

    # ----- вход / выход -----
    def slots(self):
        now = self.clock()
        with self.lock:
            self._cleanup(now)
            out = []
            for n in range(1, MAX_LOBBIES + 1):
                lb = self.lobbies.get(n)
                if not lb: out.append({"num": n, "state": "empty", "players": 0, "seeker": False, "max": MAX_PLAYERS}); continue
                out.append({"num": n, "state": lb.state, "players": len(lb.players), "seeker": bool(lb.seekers()), "max": MAX_PLAYERS})
            return out

    def _cleanup(self, now):
        for n, lb in list(self.lobbies.items()):
            dead = lb.state == "ended" and lb.ended_at is not None and now - lb.ended_at > ENDED_KEEP
            empty = not lb.players
            if dead or empty:
                for uid in [u for u, num in self.user_lobby.items() if num == n]: self.user_lobby.pop(uid, None)
                self.lobbies.pop(n, None)

    def join(self, uid, name, staff, wanted=None):
        """Возвращает (лобби, ошибка)."""
        now = self.clock()
        with self.lock:
            self._cleanup(now)
            cur = self.user_lobby.get(uid)
            if cur is not None:
                lb = self.lobbies.get(cur)
                if lb and lb.state != "ended" and uid in lb.players:
                    lb.players[uid].seen = now; return lb, None
                self.user_lobby.pop(uid, None)
                if lb and uid in lb.players and lb.state == "ended": lb.players.pop(uid, None)

            def can_join(lb):
                if lb.state != "waiting" or len(lb.players) >= MAX_PLAYERS: return False
                if staff: return True
                return bool(lb.seekers()) or len(lb.players) < MAX_PLAYERS - 1   # последнее место держим для искателя
            target = None
            if wanted is not None:
                lb = self.lobbies.get(wanted)
                if wanted < 1 or wanted > MAX_LOBBIES: return None, "Такого лобби нет"
                if lb is None:
                    target = self._create(wanted, now)
                elif can_join(lb): target = lb
                else: return None, "В это лобби сейчас не войти (идёт раунд или оно заполнено)"
            else:
                cands = [lb for lb in self.lobbies.values() if can_join(lb)]
                if cands:
                    if staff: cands.sort(key=lambda l: (bool(l.seekers()), -len(l.players)))   # сначала туда, где ждут искателя
                    else: cands.sort(key=lambda l: (not l.seekers(), -len(l.players)))
                    target = cands[0]
                else:
                    free = [n for n in range(1, MAX_LOBBIES + 1) if n not in self.lobbies]
                    if not free: return None, "Все лобби заняты, попробуй чуть позже"
                    target = self._create(free[0], now)
            target.players[uid] = Player(uid, name, staff, now)
            self.user_lobby[uid] = target.num
            target.event(f"{name} вошёл в лобби")
            return target, None

    def _create(self, num, now):
        lb = Lobby(num, now); self.lobbies[num] = lb; return lb

    def leave(self, uid):
        with self.lock:
            num = self.user_lobby.pop(uid, None)
            lb = self.lobbies.get(num) if num else None
        if not lb: return
        with lb.lock:
            p = lb.players.pop(uid, None)
            if p and lb.state != "ended": lb.event(f"{p.name} вышел")

    def lobby_of(self, uid):
        with self.lock:
            num = self.user_lobby.get(uid)
            return self.lobbies.get(num) if num else None

    # ----- ход времени -----
    def tick(self, uid, dx, dy, brk=None):
        """Один запрос игрока: применить ввод, досчитать мир, вернуть состояние. None, если игрок не в лобби."""
        lb = self.lobby_of(uid)
        if not lb: return None
        now = self.clock()
        with lb.lock:
            p = lb.players.get(uid)
            if not p: return None
            p.seen = now
            l = math.hypot(dx, dy)
            p.dx, p.dy = (dx / l, dy / l) if l > 1e-6 else (0.0, 0.0)
            if brk: self._start_break(lb, p, brk, now)
            self._step(lb, now)
            if uid not in lb.players: return None
            return self._state(lb, p, now)

    def _start_break(self, lb, p, box_id, now):
        if lb.state != "playing" or p.role != "hider" or p.caught: return
        box = next((b for b in lb.boxes if b["id"] == box_id), None)
        if not box or box["broken"] or box["by"] not in (None, p.uid): return
        if math.hypot(p.x - box["x"], p.y - box["y"]) > BOX_RANGE: return
        for b in lb.boxes:            # один человек ломает одну коробку
            if b["by"] == p.uid and b is not box: b["by"] = None; b["start"] = None
        box["by"] = p.uid; box["start"] = now; p.breaking = box_id

    def _step(self, lb, now):
        if lb.state == "ended": return
        # вылетевшие (нет запросов)
        for uid, pl in list(lb.players.items()):
            if now - pl.seen > IDLE_KICK:
                lb.players.pop(uid, None)
                if self.user_lobby.get(uid) == lb.num: self.user_lobby.pop(uid, None)
                lb.event(f"{pl.name} отключился")
        if lb.state == "waiting":
            has = bool(lb.seekers()) and bool(lb.hiders())
            if not has: lb.start_at = None
            else:
                target = now + (FULL_START_DELAY if len(lb.players) >= MAX_PLAYERS else START_DELAY)
                if lb.start_at is None or target < lb.start_at: lb.start_at = target
                if now >= lb.start_at: self._start_round(lb, now)
            lb.last_step = now
            return
        # playing: моделируем шагами по 50 мс, не больше 2 секунд за раз
        t = max(lb.last_step, now - 2.0)
        while t < now - 1e-9:
            dt = min(0.05, now - t); t += dt
            self._simulate(lb, t, dt)
            if lb.state == "ended": return
        lb.last_step = now
        self._check_end(lb, now)

    def _start_round(self, lb, now):
        for p in list(lb.hiders()):
            if not p.staff and not self.consume(p.uid):
                lb.players.pop(p.uid, None); self.user_lobby.pop(p.uid, None); lb.event(f"{p.name} не хватило игр")
        if not lb.hiders() or not lb.seekers():
            lb.start_at = None; return
        rnd = random.Random()
        spawns = list(MAP["spawns"]); rnd.shuffle(spawns)
        for i, p in enumerate(lb.hiders()):
            p.x, p.y = spawns[i % len(spawns)]
        for p in lb.seekers(): p.x, p.y = MAP["start"]
        for p in lb.players.values(): p.dx = p.dy = 0.0; p.caught = False; p.clouds = 0; p.breaking = None
        lb.boxes = [{"id": b["id"], "x": b["x"], "y": b["y"], "broken": False, "by": None, "start": None} for b in MAP["boxes"]]
        lb.clouds = []
        for i in range(CLOUDS_ON_MAP):
            for _ in range(200):
                x, y = rnd.randint(50, W - 50), rnd.randint(50, H - 50)
                if _push_out(x, y) == (x, y) and not _in_bush(x, y): lb.clouds.append({"id": i + 1, "x": float(x), "y": float(y)}); break
        lb.seeker_hp = SEEKER_HP
        lb.state = "playing"; lb.round_start = now; lb.round_end = now + ROUND_SEC; lb.last_step = now; lb.start_at = None
        lb.event("Раунд начался! Искатели ждут 8 секунд")

    def _simulate(self, lb, t, dt):
        boxes_pos = [(b["x"], b["y"], 18) for b in lb.boxes if not b["broken"]]
        frozen = t < lb.round_start + FREEZE_SEC
        for p in lb.players.values():
            if p.caught: continue
            if p.role == "seeker" and frozen: continue
            sp = SEEKER_SPEED if p.role == "seeker" else HIDER_SPEED
            if p.dx or p.dy:
                p.x, p.y = _push_out(p.x + p.dx * sp * dt, p.y + p.dy * sp * dt, boxes_pos)
        hiders = [p for p in lb.hiders() if not p.caught]
        if not frozen:
            for s in lb.seekers():
                for h in hiders:
                    if not h.caught and math.hypot(s.x - h.x, s.y - h.y) <= CATCH_DIST:
                        h.caught = True
                        if h.breaking:
                            for b in lb.boxes:
                                if b["by"] == h.uid: b["by"] = None; b["start"] = None
                            h.breaking = None
                        lb.event(f"{s.name} поймал {h.name}" + (f", облачка ({h.clouds}) потеряны" if h.clouds else ""))
                        h.clouds = 0
        for h in hiders:
            if h.caught: continue
            for c in list(lb.clouds):
                if h.clouds < MAX_CLOUDS_PER_GAME - WIN_BONUS and math.hypot(h.x - c["x"], h.y - c["y"]) <= CLOUD_PICK:
                    lb.clouds.remove(c); h.clouds += 1
        for b in lb.boxes:
            if b["broken"] or b["by"] is None: continue
            h = lb.players.get(b["by"])
            if not h or h.caught or math.hypot(h.x - b["x"], h.y - b["y"]) > BOX_RANGE + 30:
                if h: h.breaking = None
                b["by"] = None; b["start"] = None; continue
            if t - b["start"] >= BOX_TIME:
                b["broken"] = True; b["by"] = None; h.breaking = None
                lb.seeker_hp = max(0, lb.seeker_hp - BOX_DAMAGE)
                lb.event(f"{h.name} сломал коробку! Искателю −{BOX_DAMAGE} HP (осталось {lb.seeker_hp})")
        self._check_end(lb, t)

    def _check_end(self, lb, now):
        if lb.state != "playing": return
        hiders = lb.hiders()
        if lb.seeker_hp <= 0: return self._finish(lb, now, "hiders", "Все коробки сломаны: искатель уничтожен")
        if not lb.seekers(): return self._finish(lb, now, "hiders", "Искатель ушёл")
        if not hiders: return self._finish(lb, now, "abandoned", "Все прячущиеся вышли")
        if all(h.caught for h in hiders): return self._finish(lb, now, "seekers", "Все прячущиеся пойманы")
        if now >= lb.round_end: return self._finish(lb, now, "hiders", "Время вышло: прячущиеся продержались")

    def _finish(self, lb, now, winner, reason):
        rows = []
        for p in lb.players.values():
            earned = 0
            if p.role == "hider" and not p.staff and winner != "abandoned":
                earned = p.clouds + (WIN_BONUS if winner == "hiders" else 0)
                earned = min(earned, MAX_CLOUDS_PER_GAME)
                if earned > 0:
                    try: self.credit(p.uid, earned)
                    except Exception as e: print(f"[game credit] {p.uid}: {e!r}")
            rows.append({"id": p.uid, "name": p.name, "role": p.role, "caught": p.caught, "clouds": earned})
        lb.result = {"winner": winner, "reason": reason, "players": rows}
        lb.state = "ended"; lb.ended_at = now
        lb.event(reason)

    # ----- что видит игрок -----
    def _state(self, lb, me, now):
        spectator = me.caught
        def sees(x, y, bush_ok=True):
            return spectator or math.hypot(me.x - x, me.y - y) <= VIS_R
        ents = []
        for o in lb.players.values():
            if o.uid == me.uid: continue
            if lb.state == "playing":
                if not spectator:
                    d = math.hypot(me.x - o.x, me.y - o.y)
                    if d > VIS_R: continue
                    if o.role == "hider" and not o.caught and _in_bush(o.x, o.y) and d > BUSH_REVEAL: continue
                sp = (SEEKER_SPEED if o.role == "seeker" else HIDER_SPEED)
                ents.append({"id": o.uid, "name": o.name, "role": o.role, "x": round(o.x, 1), "y": round(o.y, 1),
                             "vx": 0 if o.caught else round(o.dx * sp, 1), "vy": 0 if o.caught else round(o.dy * sp, 1), "caught": o.caught})
            else:
                ents.append({"id": o.uid, "name": o.name, "role": o.role})
        out = {"lobby": {"num": lb.num, "state": lb.state, "players": [{"name": o.name, "role": o.role, "me": o.uid == me.uid} for o in lb.players.values()],
                         "max": MAX_PLAYERS, "start_in": round(max(0.0, lb.start_at - now), 1) if lb.start_at else None,
                         "has_seeker": bool(lb.seekers())},
               "me": {"id": me.uid, "role": me.role, "x": round(me.x, 1), "y": round(me.y, 1), "caught": me.caught, "clouds": me.clouds},
               "others": ents, "events": lb.events[-6:]}
        if lb.state == "playing":
            out["time_left"] = round(max(0.0, lb.round_end - now), 1)
            out["freeze_left"] = round(max(0.0, lb.round_start + FREEZE_SEC - now), 1)
            out["seeker_hp"] = lb.seeker_hp
            out["boxes"] = [{"id": b["id"], "broken": b["broken"], "progress": round(min(1.0, (now - b["start"]) / BOX_TIME), 2) if b["by"] is not None and b["start"] else 0, "by_me": b["by"] == me.uid} for b in lb.boxes]
            out["clouds"] = [{"id": c["id"], "x": c["x"], "y": c["y"]} for c in lb.clouds if sees(c["x"], c["y"])]
        if lb.state == "ended": out["result"] = lb.result
        return out
