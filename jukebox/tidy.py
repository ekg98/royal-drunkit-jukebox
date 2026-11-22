"""Tidy my library: look up missing cover art, years and genres online.

Sources: MusicBrainz (song and album information) and the Cover Art Archive (album covers). Both are free and
need no account. MusicBrainz asks programs to send at most one request per second and to identify themselves,
and this module does both.

Rules this follows:
  * Your music files are never changed. Results go into the jukebox's own catalog and cover folder.
  * Only blanks are filled. A cover, year or genre that your files already have is never replaced.
  * Albums are looked up once, not once per song. A match must agree on the album name and the artist, so a wrong
    cover is much less likely than a missing one.
  * Something that was looked up (found or not) is not asked again for 30 days, unless you ask for a full retry.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

MB_URL = "https://musicbrainz.org/ws/2/"
CAA_URL = "https://coverartarchive.org/"
VARIOUS = {"various artists", "various", "va"}
MIN_SCORE = 90                       # MusicBrainz's own match score, 0 to 100
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class NetworkError(Exception):
    """The internet (or the service) could not be reached."""


class Http:
    """The only part that touches the network. Tests replace it with a fake."""

    def __init__(self, user_agent: str, timeout: float = 20):
        self.user_agent = user_agent
        self.timeout = timeout

    def _open(self, url: str):
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json, image/*"})
        return urllib.request.urlopen(request, timeout=self.timeout)

    def get_json(self, url: str) -> dict | None:
        """The parsed JSON, or None when the service says "no such thing" (404)."""
        try:
            with self._open(url) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise NetworkError(f"{exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise NetworkError(str(getattr(exc, "reason", exc))) from exc

    def get_file(self, url: str, dest: Path) -> bool:
        """Download an image. False when there is none (404)."""
        try:
            with self._open(url) as resp:
                data = resp.read(MAX_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 400):
                return False
            raise NetworkError(f"{exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise NetworkError(str(getattr(exc, "reason", exc))) from exc
        if not data or len(data) > MAX_IMAGE_BYTES:
            return False
        dest.write_bytes(data)
        return True


# ---------- matching ----------

def norm(text: str) -> str:
    """Lower case, no accents, no (bracketed extras), no 'the', only letters and digits."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"^the\s+", "", text.strip())
    return re.sub(r"[^a-z0-9]+", "", text)


def same_album(a: str, b: str) -> bool:
    return bool(norm(a)) and norm(a) == norm(b)


def same_artist(wanted: str, names: list[str]) -> bool:
    w = norm(wanted)
    if not w:
        return False
    for name in names:
        n = norm(name)
        if not n:
            continue
        if n == w or (min(len(n), len(w)) >= 5 and (n in w or w in n)):
            return True
    return False


def credit_names(entry: dict) -> list[str]:
    names = []
    for part in entry.get("artist-credit") or []:
        if not isinstance(part, dict):
            continue
        if part.get("name"):
            names.append(part["name"])
        if isinstance(part.get("artist"), dict) and part["artist"].get("name"):
            names.append(part["artist"]["name"])
    joined = "".join((p.get("name", "") + p.get("joinphrase", "")) for p in entry.get("artist-credit") or [] if isinstance(p, dict))
    if joined:
        names.append(joined)
    return names


def year_of(date: str | None) -> int | None:
    match = re.match(r"^(\d{4})", date or "")
    year = int(match.group(1)) if match else None
    return year if year and 1900 <= year <= 2100 else None


def lucene(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def pretty_genre(name: str) -> str:
    return name.strip().title() if name else ""


# ---------- the job ----------

class Tidy:
    def __init__(self, library, http: Http | None = None, contact: str = "", pause: float = 1.1,
                 sleep=time.sleep, clock=time.monotonic):
        agent = f"RoyalDrunkitJukebox/{__version__} ( {contact or 'home jukebox, personal use'} )"
        self.lib = library
        self.http = http or Http(agent)
        self.pause = pause
        self._sleep = sleep
        self._clock = clock
        self._last_mb = 0.0
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._status = self._blank_status()
        self._running = False

    @staticmethod
    def _blank_status() -> dict:
        return {"running": False, "done": 0, "total": 0, "covers": 0, "years": 0, "genres": 0, "not_found": 0,
                "current": "", "error": "", "cancelled": False, "finished": False}

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, **values) -> None:
        with self._lock:
            self._status.update(values)

    def cancel(self) -> None:
        self._cancel.set()

    def preview(self, force: bool = False) -> dict:
        """How much there is to look up, without looking anything up."""
        groups = self.lib.tidy_groups(force=force)
        return {"albums": len(groups), "seconds": int(len(groups) * 3)}

    def start(self, force: bool = False, log=lambda message: None) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._status = self._blank_status()
            self._status["running"] = True
        self._cancel.clear()

        def work():
            try:
                self.run(force=force, log=log)
            except Exception as exc:  # never take the jukebox down with a lookup problem
                self._set(error=f"Stopped by an unexpected problem: {exc}")
            finally:
                with self._lock:
                    self._running = False
                    self._status["running"] = False
                    self._status["finished"] = True

        threading.Thread(target=work, name="tidy-library", daemon=True).start()
        return True

    def run(self, force: bool = False, log=lambda message: None) -> dict:
        """Look everything up and wait until done. Returns the final status."""
        groups = self.lib.tidy_groups(force=force)
        self._set(total=len(groups), done=0, covers=0, years=0, genres=0, not_found=0, error="", cancelled=False)
        log(f"Tidy: {len(groups)} albums or songs to look up")
        failures = 0
        changed = 0
        for index, group in enumerate(groups):
            if self._cancel.is_set():
                self._set(cancelled=True)
                break
            label = f"{group['artist']} - {group['album'] or group['title']}"
            self._set(current=label)
            try:
                result = self._lookup(group)
            except NetworkError as exc:
                failures += 1
                log(f"  network problem ({exc}) for {label}")
                if failures >= 3:
                    self._set(error="Could not reach MusicBrainz. Check the internet connection and try again later.")
                    break
                self._sleep(3)
                continue
            failures = 0
            filled = self.lib.tidy_store(group, result is not None, **(result or {}))
            with self._lock:
                if result is None:
                    self._status["not_found"] += 1
                self._status["covers"] += filled["cover"]
                self._status["years"] += filled["year"]
                self._status["genres"] += filled["genre"]
                self._status["done"] = index + 1
            changed += sum(filled.values())
            if changed and (index + 1) % 10 == 0:
                self.lib.tidy_reload()
        if changed:
            self.lib.tidy_reload()
        self._set(current="")
        return self.status()

    # ---------- talking to the services ----------

    def _mb(self, path: str, **params) -> dict | None:
        wait = self.pause - (self._clock() - self._last_mb)
        if wait > 0:
            self._sleep(wait)
        url = MB_URL + path + "?" + urllib.parse.urlencode({**params, "fmt": "json"})
        try:
            return self._with_retry(lambda: self.http.get_json(url))
        finally:
            self._last_mb = self._clock()

    def _with_retry(self, call):
        try:
            return call()
        except NetworkError:
            self._sleep(5)                       # a busy server (503) often works a few seconds later
            return call()

    def _lookup(self, group: dict) -> dict | None:
        """Find the best match and gather what is missing. None when nothing trustworthy was found."""
        if group["album"]:
            match = self._find_album(group["artist"], group["album"])
        else:
            match = self._find_song(group["artist"], group["title"])
        if not match:
            return None
        result: dict = {}
        if match["year"] and group["need_year"]:
            result["year"] = match["year"]
        if group["need_genre"] and match["rgid"]:
            genre = self._genre(match["rgid"])
            if genre:
                result["genre"] = genre
        if group["need_cover"]:
            tmp = self.lib.covers_dir / f"{group['key']}.dl"
            try:
                if self._cover(match, tmp):
                    result["cover_file"] = tmp       # the library turns it into a proper cover and deletes this file
            except NetworkError:
                tmp.unlink(missing_ok=True)
                raise
        return result

    def _find_album(self, artist: str, album: str) -> dict | None:
        is_various = artist.lower() in VARIOUS
        query = f'release:"{lucene(album)}"' + ("" if is_various else f' AND artist:"{lucene(artist)}"')
        data = self._mb("release/", query=query, limit=10)
        matches = []
        for rel in (data or {}).get("releases", []):
            if int(rel.get("score", 0)) < MIN_SCORE or not same_album(album, rel.get("title", "")):
                continue
            if not is_various and not same_artist(artist, credit_names(rel)):
                continue
            matches.append(rel)
        return self._summarize(matches)

    def _find_song(self, artist: str, title: str) -> dict | None:
        query = f'recording:"{lucene(title)}" AND artist:"{lucene(artist)}"'
        data = self._mb("recording/", query=query, limit=5)
        for rec in (data or {}).get("recordings", []):
            if int(rec.get("score", 0)) < MIN_SCORE or norm(rec.get("title", "")) != norm(title):
                continue
            if not same_artist(artist, credit_names(rec)):
                continue
            releases = [dict(r, **{"artist-credit": rec.get("artist-credit")}) for r in rec.get("releases", [])]
            found = self._summarize(releases)
            if found:
                return found
        return None

    @staticmethod
    def _summarize(releases: list[dict]) -> dict | None:
        """From the matching releases, pick one to take the cover and genre from, and the earliest year."""
        if not releases:
            return None

        def rank(rel):
            official = 0 if rel.get("status") == "Official" else 1
            return (official, 0 if year_of(rel.get("date")) else 1, rel.get("date") or "9999")

        ordered = sorted(releases, key=rank)
        years = [y for y in (year_of(r.get("date")) for r in releases) if y]
        groups = [r.get("release-group", {}).get("id") for r in ordered if isinstance(r.get("release-group"), dict)]
        return {
            "year": min(years) if years else None,
            "rgid": next((g for g in groups if g), None),
            "releases": [r["id"] for r in ordered if r.get("id")][:3],
        }

    def _genre(self, rgid: str) -> str:
        data = self._mb(f"release-group/{rgid}", inc="genres")
        genres = (data or {}).get("genres") or []
        if not genres:
            return ""
        best = sorted(genres, key=lambda g: (-int(g.get("count", 0)), g.get("name", "")))[0]
        return pretty_genre(best.get("name", ""))

    def _cover(self, match: dict, dest: Path) -> bool:
        urls = []
        if match["rgid"]:
            urls.append(f"{CAA_URL}release-group/{match['rgid']}/front-500")
        urls += [f"{CAA_URL}release/{rid}/front-500" for rid in match["releases"]]
        for url in urls:
            if self._with_retry(lambda u=url: self.http.get_file(u, dest)):
                return True
        return False
