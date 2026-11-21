"""The jukebox brain: queue, credits, votes, auto-fill and host controls.

Nothing here knows about the web or about real audio, so the rules can be tested directly. The web layer
(server.py) calls these methods, and the player (player.py) is whatever object plays the sound.
"""
from __future__ import annotations

import hmac
import json
import math
import os
import random
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from .library import decade_label


class EngineError(Exception):
    """A request the jukebox refuses, with a message that is safe to show a guest."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _int_range(lo: int, hi: int):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise ValueError("must be a whole number")
        value = int(value)
        if not lo <= value <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return value
    return check


def _boolean(value):
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 80:
        raise ValueError("must be short text")
    return value


# setting name -> (default, validator)
SETTING_SPECS = {
    "volume": (75, _int_range(0, 100)),
    "autofill": (True, _boolean),
    "mode": ("all", _text),
    "credit_max": (5, _int_range(1, 20)),
    "refill_min": (2, _int_range(1, 60)),
    "start_credits": (3, _int_range(0, 20)),
    "vote_need": (3, _int_range(1, 20)),
    "free_play": (False, _boolean),
    "queue_max": (10, _int_range(1, 50)),
}


@dataclass
class Guest:
    credits: int
    next_at: float | None
    seen: float


def fmt_wait(seconds: float) -> str:
    seconds = max(0, math.ceil(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Engine:
    HOST_TOKEN_TTL = 12 * 3600
    LOGIN_MAX_FAILS = 5
    LOGIN_WINDOW = 300
    LOGIN_LOCK = 60
    MAX_GUESTS = 5000

    def __init__(self, library, player, settings_path: Path | str | None, host_pin: str,
                 clock=time.time, rng: random.Random | None = None):
        self.lib = library
        self.player = player
        self.host_pin = str(host_pin)
        self.clock = clock
        self.rng = rng or random.Random()
        self.lock = threading.RLock()
        self.settings = {key: spec[0] for key, spec in SETTING_SPECS.items()}
        self.settings_path = Path(settings_path) if settings_path else None
        self._load_settings()

        self.current: dict | None = None       # {"id": song id, "auto": bool}
        self.paused = False
        self.queue: list[dict] = []            # {"qid", "id", "by", "auto"}
        self._next_qid = 1
        self.votes: set[str] = set()
        self.guests: dict[str, Guest] = {}
        self.tokens: dict[str, float] = {}
        self._login_fails: dict[str, dict] = {}
        self.recent: deque[int] = deque(maxlen=30)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_prune = 0.0

    # ---------- settings ----------

    def _load_settings(self) -> None:
        if not self.settings_path or not self.settings_path.is_file():
            return
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, value in (saved or {}).items():
            if key in SETTING_SPECS:
                try:
                    self.settings[key] = SETTING_SPECS[key][1](value)
                except ValueError:
                    pass

    def _save_settings(self) -> None:
        if not self.settings_path:
            return
        tmp = self.settings_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
            os.replace(tmp, self.settings_path)
        except OSError as exc:
            print(f"[engine] could not save settings: {exc!r}")

    def mode_options(self) -> list[dict]:
        options = [{"id": "all", "label": "Anything goes", "group": ""}]
        for label, _count in self.lib.decades():
            options.append({"id": f"dec:{label}", "label": f"{label} night", "group": "Decades"})
        for genre, _count in self.lib.genres()[:30]:
            label = "Dance floor" if genre.lower() == "dance" else f"{genre} night"
            options.append({"id": f"gen:{genre}", "label": label, "group": "Genres"})
        return options

    def update_settings(self, changes: dict) -> None:
        with self.lock:
            clean = {}
            for key, value in changes.items():
                if key not in SETTING_SPECS:
                    raise EngineError("bad_setting", f"Unknown setting: {key}")
                try:
                    clean[key] = SETTING_SPECS[key][1](value)
                except ValueError as exc:
                    raise EngineError("bad_setting", f"{key} {exc}")
            if "mode" in clean and clean["mode"] not in {m["id"] for m in self.mode_options()}:
                raise EngineError("bad_setting", "That party mode is not available for this library.")
            self.settings.update(clean)
            if "volume" in clean:
                self.player.set_volume(self.settings["volume"])
            if "mode" in clean or "autofill" in clean:
                self.queue = [it for it in self.queue if not it["auto"]]
                self._ensure_auto()
            self._save_settings()

    # ---------- guests and credits ----------

    def _guest(self, gid: str, now: float) -> Guest:
        guest = self.guests.get(gid)
        if guest is None:
            cap = self.settings["credit_max"]
            start = min(self.settings["start_credits"], cap)
            guest = Guest(credits=start, next_at=None, seen=now)
            if start < cap:
                guest.next_at = now + self.settings["refill_min"] * 60
            self.guests[gid] = guest
        guest.seen = now
        self._refill(guest, now)
        return guest

    def _refill(self, guest: Guest, now: float) -> None:
        cap = self.settings["credit_max"]
        interval = self.settings["refill_min"] * 60
        if guest.credits >= cap:
            guest.credits = cap
            guest.next_at = None
            return
        if guest.next_at is None:
            guest.next_at = now + interval
        while guest.credits < cap and now >= guest.next_at:
            guest.credits += 1
            guest.next_at += interval
        if guest.credits >= cap:
            guest.next_at = None

    def _cost(self, is_host: bool, kind: str) -> int:
        if is_host or self.settings["free_play"]:
            return 0
        return 2 if kind == "next" else 1

    def _spend(self, guest: Guest, amount: int, now: float) -> None:
        guest.credits -= amount
        if guest.next_at is None:
            guest.next_at = now + self.settings["refill_min"] * 60

    def refill_all(self) -> None:
        """Give every guest a full set of credits."""
        with self.lock:
            for guest in self.guests.values():
                guest.credits = self.settings["credit_max"]
                guest.next_at = None

    # ---------- queue ----------

    def _in_queue(self, song_id: int) -> bool:
        return (self.current is not None and self.current["id"] == song_id) or any(
            it["id"] == song_id for it in self.queue)

    def add_song(self, gid: str, is_host: bool, song_id, kind: str = "add") -> None:
        with self.lock:
            kind = "next" if kind == "next" else "add"
            if isinstance(song_id, bool) or not isinstance(song_id, int) or self.lib.get(song_id) is None:
                raise EngineError("no_song", "That song is not in the library.", 404)
            if self._in_queue(song_id):
                raise EngineError("queued", "That song is already playing or queued.", 409)

            now = self.clock()
            guest = self._guest(gid, now)
            cost = self._cost(is_host, kind)
            cap = self.settings["credit_max"]
            if cost > cap:
                raise EngineError("credits", f"Play next costs {cost} credits, but guests can only hold {cap}. "
                                  "Ask the host.", 402)
            if guest.credits < cost:
                missing = cost - guest.credits
                interval = self.settings["refill_min"] * 60
                wait = (guest.next_at - now) + (missing - 1) * interval
                noun = "credit" if cost == 1 else "credits"
                raise EngineError("credits", f"You need {cost} {noun}. Next credit in {fmt_wait(wait)}.", 402)

            real = sum(1 for it in self.queue if not it["auto"])
            if self.current is not None and real >= self.settings["queue_max"]:
                raise EngineError("full", "The queue is full. Try again in a bit.", 409)

            if cost:
                self._spend(guest, cost, now)
            self.queue = [it for it in self.queue if not it["auto"]]
            if self.current is None:
                if not self._start(song_id, auto=False):
                    raise EngineError("unplayable", "That song could not be played.", 500)
            else:
                item = {"qid": self._next_qid, "id": song_id, "by": "host" if is_host else gid, "auto": False}
                self._next_qid += 1
                if kind == "next":
                    self.queue.insert(0, item)
                else:
                    self.queue.append(item)
            self._ensure_auto()

    def remove(self, gid: str, is_host: bool, qid) -> None:
        with self.lock:
            index = next((i for i, it in enumerate(self.queue) if it["qid"] == qid), None)
            if index is None:
                raise EngineError("gone", "That song is no longer in the queue.", 404)
            if not is_host and self.queue[index]["by"] != gid:
                raise EngineError("forbidden", "You can only remove your own songs.", 403)
            del self.queue[index]
            self._ensure_auto()

    def vote(self, gid: str) -> bool:
        """Count a skip vote. Returns True if it was enough to skip the song."""
        with self.lock:
            if self.current is None:
                raise EngineError("idle", "Nothing is playing.", 409)
            if gid in self.votes:
                raise EngineError("voted", "You already voted on this song.", 409)
            self.votes.add(gid)
            if len(self.votes) >= self.settings["vote_need"]:
                self._advance()
                return True
            return False

    # ---------- host ----------

    def host_login(self, pin, ip: str) -> str:
        with self.lock:
            now = self.clock()
            record = self._login_fails.get(ip)
            if record and record["until"] > now:
                raise EngineError("locked", "Too many wrong PINs. Wait a minute and try again.", 429)
            if isinstance(pin, str) and hmac.compare_digest(pin.encode(), self.host_pin.encode()):
                self._login_fails.pop(ip, None)
                self.tokens = {t: exp for t, exp in self.tokens.items() if exp > now}
                token = secrets.token_hex(16)
                self.tokens[token] = now + self.HOST_TOKEN_TTL
                return token
            record = self._login_fails.setdefault(ip, {"n": 0, "first": now, "until": 0.0})
            if now - record["first"] > self.LOGIN_WINDOW:
                record.update(n=0, first=now)
            record["n"] += 1
            if record["n"] >= self.LOGIN_MAX_FAILS:
                record.update(until=now + self.LOGIN_LOCK, n=0, first=now)
            raise EngineError("bad_pin", "That PIN is not right.", 403)

    def is_host(self, token: str | None) -> bool:
        with self.lock:
            expiry = self.tokens.get(token or "")
            return bool(expiry and expiry > self.clock())

    def host_logout(self, token: str | None) -> None:
        with self.lock:
            self.tokens.pop(token or "", None)

    def control(self, action: str) -> None:
        with self.lock:
            if action == "toggle":
                action = "resume" if self.paused or self.current is None else "pause"
            if action == "pause":
                if self.current is not None and not self.paused:
                    self.player.pause()
                    self.paused = True
            elif action in ("resume", "play"):
                if self.current is None:
                    self._advance()
                elif self.paused:
                    self.player.resume()
                    self.paused = False
            elif action == "skip":
                if self.current is not None:
                    self._advance()
            elif action == "clear":
                self.queue = []
                self._ensure_auto()
            elif action == "refill":
                self.refill_all()
            else:
                raise EngineError("bad_action", f"Unknown action: {action}")

    # ---------- playback flow ----------

    def _mode_allows(self, song: dict) -> bool:
        kind, _, value = self.settings["mode"].partition(":")
        if kind == "dec":
            return decade_label(song["year"]) == value
        if kind == "gen":
            return song["genre"].lower() == value.lower()
        return True

    def _pick_auto(self) -> int | None:
        candidates = [s for s in self.lib.all() if not self._in_queue(s["id"])]
        pool = [s for s in candidates if self._mode_allows(s)] or candidates   # never go silent over a mode
        fresh = [s for s in pool if s["id"] not in self.recent]
        choice = fresh or pool
        return self.rng.choice(choice)["id"] if choice else None

    def _ensure_auto(self) -> None:
        """Keep one auto-fill song lined up so the 'Up next' list is never empty."""
        if self.settings["autofill"] and self.current is not None and not self.queue:
            song_id = self._pick_auto()
            if song_id is not None:
                self.queue.append({"qid": self._next_qid, "id": song_id, "by": "auto", "auto": True})
                self._next_qid += 1

    def _start(self, song_id: int, auto: bool) -> bool:
        song = self.lib.get(song_id)
        try:
            self.player.start(self.lib.abs_path(song), song["dur"])
            self.player.set_volume(self.settings["volume"])
        except Exception as exc:
            print(f"[engine] could not play {song['path']}: {exc!r}")
            self.current = None
            return False
        self.current = {"id": song_id, "auto": auto}
        self.paused = False
        self.votes = set()
        self.recent.append(song_id)
        self.lib.record_play(song_id)
        return True

    def _advance(self) -> None:
        """Move on to the next song: the queue first, then auto-fill, otherwise silence."""
        self.votes = set()
        self.current = None
        self.player.stop()
        for _attempt in range(10):
            if self.queue:
                item = self.queue.pop(0)
                if self._start(item["id"], item["auto"]):
                    break
            elif self.settings["autofill"]:
                song_id = self._pick_auto()
                if song_id is None or not self._start(song_id, True):
                    break
                break
            else:
                break
        self._ensure_auto()

    def tick(self) -> None:
        with self.lock:
            if self.current is None:
                if self.queue or self.settings["autofill"]:
                    self._advance()
            elif not self.paused and self.player.finished():
                self._advance()
            now = self.clock()
            if now - self._last_prune > 60:
                self._last_prune = now
                self._prune(now)

    def _prune(self, now: float) -> None:
        stale = [gid for gid, g in self.guests.items() if now - g.seen > 24 * 3600]
        for gid in stale:
            del self.guests[gid]
        if len(self.guests) > self.MAX_GUESTS:
            oldest = sorted(self.guests, key=lambda gid: self.guests[gid].seen)
            for gid in oldest[: len(self.guests) - self.MAX_GUESTS]:
                del self.guests[gid]
        self.tokens = {t: exp for t, exp in self.tokens.items() if exp > now}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(0.25):
                try:
                    self.tick()
                except Exception as exc:
                    print(f"[engine] {exc!r}")

        self._thread = threading.Thread(target=loop, name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.player.stop()

    # ---------- what the web page sees ----------

    def state_for(self, gid: str, is_host: bool) -> dict:
        with self.lock:
            now = self.clock()
            guest = self._guest(gid, now)
            now_playing = None
            if self.current is not None:
                song = self.lib.get(self.current["id"])
                now_playing = {
                    "id": song["id"], "auto": self.current["auto"], "playing": not self.paused,
                    "position": round(self.player.position(), 1),
                    "duration": (getattr(self.player, "length", lambda: None)() or song["dur"]),
                }

            def owner(item):
                if item["auto"]:
                    return "auto"
                if item["by"] == "host":
                    return "host"
                return "you" if item["by"] == gid else "other"

            state = {
                "lib": self.lib.version,
                "songs": self.lib.count(),
                "now": now_playing,
                "queue": [{"qid": it["qid"], "id": it["id"], "by": owner(it), "auto": it["auto"]}
                          for it in self.queue],
                "votes": len(self.votes),
                "voted": gid in self.votes,
                "vote_need": self.settings["vote_need"],
                "credits": {
                    "have": guest.credits,
                    "max": self.settings["credit_max"],
                    "next_in": round(max(0.0, guest.next_at - now), 1) if guest.next_at else None,
                },
                "free_play": self.settings["free_play"],
                "queue_max": self.settings["queue_max"],
                "autofill": self.settings["autofill"],
                "mode": self.settings["mode"],
                "player": getattr(self.player, "name", "unknown"),
                "host": is_host,
            }
            if is_host:
                state["settings"] = dict(self.settings)
                state["modes"] = self.mode_options()
                state["scan"] = self.lib.scan_status()
            return state
