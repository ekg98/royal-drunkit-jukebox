"""The song catalog: scans the music folder into a SQLite database and keeps it in memory for fast lookups.

Nothing in your music folder is ever changed. The catalog, extracted cover art and play history live in the
data folder, so deleting that folder only costs you a rescan.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import tags as tagmod

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL DEFAULT '',
    album_artist TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT '',
    year INTEGER,
    duration REAL NOT NULL,
    cover TEXT,
    added REAL NOT NULL,
    plays INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    banned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS play_log (song_id INTEGER NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ratings (
    song_id INTEGER NOT NULL,
    guest TEXT NOT NULL,
    vote INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (song_id, guest)
);
CREATE TABLE IF NOT EXISTS tidy_cache (
    key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    year INTEGER,
    genre TEXT NOT NULL DEFAULT '',
    checked REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS play_log_ts ON play_log (ts);
"""

COVER_NAMES = ("cover", "folder", "front", "album", "albumart")
COVER_EXTS = (".jpg", ".jpeg", ".png")


def decade_label(year: int | None) -> str | None:
    """1985 -> '80s', 2003 -> '00s', 1942 -> '1940s'."""
    if not year:
        return None
    start = (int(year) // 10) * 10
    return f"{start}s" if start < 1950 else f"{start % 100:02d}s"


def decade_start(label: str) -> int:
    if len(label) == 5:
        return int(label[:4])
    n = int(label[:2])
    return 1900 + n if n >= 50 else 2000 + n


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class ScanReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    covers_linked: int = 0
    unreadable: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def changed(self) -> bool:
        return bool(self.added or self.updated or self.removed or self.covers_linked)

    def as_dict(self) -> dict:
        return {
            "added": self.added, "updated": self.updated, "removed": self.removed,
            "unchanged": self.unchanged, "unreadable": len(self.unreadable),
            "unreadable_files": self.unreadable[:20], "seconds": round(self.seconds, 1),
        }


class Library:
    def __init__(self, music_dir: Path | str, data_dir: Path | str):
        self.music_dir = Path(music_dir).expanduser().resolve()
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.covers_dir = self.data_dir / "covers"
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "library.db"
        self.version = 1
        self.last_report: ScanReport | None = None
        self._scan_lock = threading.Lock()
        self._state = {"running": False, "done": 0, "total": 0}
        self._lock = threading.RLock()
        self._songs: list[dict] = []
        self._by_id: dict[int, dict] = {}
        self._ratings: dict[int, list[int]] = {}        # song id -> [thumbs up, thumbs down]
        conn = self._connect()
        try:
            with conn:
                conn.executescript(SCHEMA)
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(songs)")}
                if "banned" not in columns:      # a catalog made by an older version of the jukebox
                    conn.execute("ALTER TABLE songs ADD COLUMN banned INTEGER NOT NULL DEFAULT 0")
        finally:
            conn.close()
        self.reload(bump=False)
        self._load_ratings()

    # ---------- database ----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def reload(self, bump: bool = True) -> None:
        """Rebuild the in-memory catalog from the database."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM songs ORDER BY title COLLATE NOCASE, artist COLLATE NOCASE").fetchall()
        finally:
            conn.close()

        # "rock", "Rock" and "ROCK" should be one genre: use the most common spelling.
        spellings: dict[str, Counter] = {}
        for row in rows:
            if row["genre"]:
                spellings.setdefault(row["genre"].lower(), Counter())[row["genre"]] += 1
        canonical = {k: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for k, c in spellings.items()}

        songs = []
        for row in rows:
            songs.append({
                "id": row["id"], "path": row["path"], "title": row["title"], "artist": row["artist"],
                "album": row["album"], "album_artist": row["album_artist"],
                "genre": canonical.get(row["genre"].lower(), "Unknown") if row["genre"] else "Unknown",
                "year": row["year"], "dur": round(row["duration"], 1), "added": int(row["added"]),
                "plays": row["plays"], "cover": row["cover"], "banned": bool(row["banned"]),
            })
        with self._lock:
            self._songs = songs
            self._by_id = {s["id"]: s for s in songs}
            if bump:
                self.version += 1

    # ---------- lookups ----------

    def count(self) -> int:
        return len(self._songs)

    def all(self) -> list[dict]:
        return self._songs

    def get(self, song_id: int) -> dict | None:
        return self._by_id.get(song_id)

    def abs_path(self, song: dict) -> Path:
        return self.music_dir / song["path"]

    def api_songs(self) -> list[dict]:
        """The songs as the web page sees them (no file paths)."""
        keys = ("id", "title", "artist", "album", "genre", "year", "dur", "added", "plays", "cover", "banned")
        return [{k: s[k] for k in keys} for s in self._songs]

    def genres(self) -> list[tuple[str, int]]:
        counts = Counter(s["genre"] for s in self._songs if s["genre"] != "Unknown")
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))

    def decades(self) -> list[tuple[str, int]]:
        counts = Counter(decade_label(s["year"]) for s in self._songs if s["year"])
        return sorted(counts.items(), key=lambda kv: decade_start(kv[0]))

    # ---------- guest ratings (thumbs up / thumbs down) ----------

    def _load_ratings(self) -> None:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT song_id, "
                                "SUM(vote = 1) AS up, SUM(vote = -1) AS down FROM ratings GROUP BY song_id").fetchall()
        finally:
            conn.close()
        with self._lock:
            self._ratings = {r["song_id"]: [r["up"] or 0, r["down"] or 0] for r in rows}

    def rate(self, song_id: int, guest: str, vote: int) -> None:
        """Record a guest's vote: 1 = thumbs up, -1 = thumbs down, 0 = take the vote back. One vote per guest per song."""
        conn = self._connect()
        try:
            with conn:
                if vote == 0:
                    conn.execute("DELETE FROM ratings WHERE song_id = ? AND guest = ?", (song_id, guest))
                else:
                    conn.execute("INSERT OR REPLACE INTO ratings (song_id, guest, vote, ts) VALUES (?,?,?,?)",
                                 (song_id, guest, vote, time.time()))
        finally:
            conn.close()
        self._load_ratings()

    def my_votes(self, guest: str) -> dict[int, int]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT song_id, vote FROM ratings WHERE guest = ?", (guest,)).fetchall()
        finally:
            conn.close()
        return {r["song_id"]: r["vote"] for r in rows if r["song_id"] in self._by_id}

    def rating_of(self, song_id: int) -> tuple[int, int]:
        up, down = self._ratings.get(song_id, (0, 0))
        return up, down

    def rating_totals(self) -> dict[int, list[int]]:
        """Songs that have any votes: id -> [up, down]."""
        with self._lock:
            return {i: list(v) for i, v in self._ratings.items() if i in self._by_id and (v[0] or v[1])}

    def clear_ratings(self) -> int:
        conn = self._connect()
        try:
            with conn:
                count = conn.execute("DELETE FROM ratings").rowcount
        finally:
            conn.close()
        self._load_ratings()
        return count

    # ---------- the never-play list ----------

    def banned_count(self) -> int:
        return sum(1 for s in self._songs if s["banned"])

    def album_ids(self, song_id: int) -> list[int]:
        """The song itself plus every other song on the same album (same album name and artist)."""
        song = self._by_id.get(song_id)
        if song is None:
            return []
        if not song["album"]:
            return [song_id]
        who = (song["album_artist"] or song["artist"]).lower()
        return [s["id"] for s in self._songs
                if s["album"].lower() == song["album"].lower() and (s["album_artist"] or s["artist"]).lower() == who]

    def set_banned(self, song_ids: list[int], banned: bool) -> int:
        """Put songs on (or take them off) the never-play list. Returns how many songs changed."""
        ids = [i for i in song_ids if i in self._by_id]
        if not ids:
            return 0
        conn = self._connect()
        try:
            with conn:
                marks = ",".join("?" * len(ids))
                changed = conn.execute(f"UPDATE songs SET banned = ? WHERE banned != ? AND id IN ({marks})",
                                       (int(banned), int(banned)) + tuple(ids)).rowcount
        finally:
            conn.close()
        if changed:
            self.reload(bump=True)
        return changed

    def record_play(self, song_id: int) -> None:
        now = time.time()
        conn = self._connect()
        try:
            with conn:
                conn.execute("UPDATE songs SET plays = plays + 1, last_played = ? WHERE id = ?", (now, song_id))
                conn.execute("INSERT INTO play_log (song_id, ts) VALUES (?, ?)", (song_id, now))
        finally:
            conn.close()
        song = self._by_id.get(song_id)
        if song:
            song["plays"] += 1

    def popular(self, hours: float = 12, limit: int = 12) -> list[int]:
        """Song ids played most in the last few hours."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT song_id, COUNT(*) AS n, MAX(ts) AS latest FROM play_log WHERE ts > ? "
                "GROUP BY song_id ORDER BY n DESC, latest DESC LIMIT ?",
                (time.time() - hours * 3600, limit),
            ).fetchall()
        finally:
            conn.close()
        return [r["song_id"] for r in rows if r["song_id"] in self._by_id]

    # ---------- scanning ----------

    def scan_status(self) -> dict:
        status = dict(self._state)
        status["last_report"] = self.last_report.as_dict() if self.last_report else None
        return status

    def scan(self, log=lambda message: None) -> ScanReport:
        """Scan the music folder and wait until it is done."""
        with self._scan_lock:
            return self._scan_locked(log)

    def scan_async(self, log=lambda message: None) -> bool:
        """Start a scan in the background. Returns False if one is already running."""
        if not self._scan_lock.acquire(blocking=False):
            return False

        def work():
            try:
                self._scan_locked(log)
            except Exception as exc:  # keep the jukebox alive whatever happens to a scan
                log(f"Scan failed: {exc!r}")
                self._state["running"] = False
            finally:
                self._scan_lock.release()

        threading.Thread(target=work, name="library-scan", daemon=True).start()
        return True

    def _walk(self) -> list[tuple[str, Path, float, int]]:
        found = []
        for root, dirs, files in os.walk(self.music_dir, followlinks=True):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                if name.startswith(".") or Path(name).suffix.lower() not in tagmod.AUDIO_EXTS:
                    continue
                path = Path(root) / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append((path.relative_to(self.music_dir).as_posix(), path, stat.st_mtime, stat.st_size))
        return found

    def _scan_locked(self, log) -> ScanReport:
        started = time.time()
        report = ScanReport()
        self._state.update(running=True, done=0, total=0)
        conn = self._connect()
        try:
            files = self._walk()
            self._state["total"] = len(files)
            existing = {r["path"]: r for r in conn.execute("SELECT id, path, mtime, size FROM songs")}
            first_scan = not existing

            todo = []
            for rel, path, mtime, size in files:
                row = existing.get(rel)
                if row and abs(row["mtime"] - mtime) < 0.001 and row["size"] == size:
                    report.unchanged += 1
                    self._state["done"] += 1
                else:
                    todo.append((rel, path, mtime, size, row))

            if todo:
                log(f"Reading {len(todo)} new or changed files...")
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = pool.map(lambda item: tagmod.read_tags(item[1]), todo)
                pending = 0
                for (rel, path, mtime, size, row), tags in zip(todo, results):
                    self._state["done"] += 1
                    if tags is None:
                        report.unreadable.append(rel)
                        continue
                    cover = self._make_cover(path, rel, tags)
                    values = (mtime, size, tags.title, tags.artist, tags.album, tags.album_artist,
                              tags.genre, tags.year, tags.duration, cover)
                    if row:
                        conn.execute(
                            "UPDATE songs SET mtime=?, size=?, title=?, artist=?, album=?, album_artist=?, "
                            "genre=?, year=?, duration=?, cover=? WHERE id=?", values + (row["id"],))
                        report.updated += 1
                    else:
                        # On the very first scan use the file's own date, so the whole library is not "new".
                        added = mtime if first_scan else time.time()
                        conn.execute(
                            "INSERT INTO songs (mtime, size, title, artist, album, album_artist, genre, year, "
                            "duration, cover, path, added) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values + (rel, added))
                        report.added += 1
                    pending += 1
                    if pending >= 25:
                        conn.commit()
                        pending = 0
                        log(f"  {self._state['done']} / {len(files)}")

            present = {f[0] for f in files}
            gone = [row["id"] for path, row in existing.items() if path not in present]
            for song_id in gone:
                conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
                conn.execute("DELETE FROM play_log WHERE song_id = ?", (song_id,))
            report.removed = len(gone)
            report.covers_linked = self._find_late_folder_art(conn) + self._apply_album_covers(conn)
            report.covers_linked += self._apply_tidy_cache(conn)
            conn.commit()
        finally:
            conn.close()
            self._state["running"] = False

        report.seconds = time.time() - started
        self.last_report = report
        if report.changed():
            self.reload(bump=True)
        return report

    # ---------- cover art ----------

    def _folder_art(self, directory: Path) -> Path | None:
        try:
            names = {entry.name.lower(): entry for entry in directory.iterdir() if entry.is_file()}
        except OSError:
            return None
        for base in COVER_NAMES:
            for ext in COVER_EXTS:
                if base + ext in names:
                    return names[base + ext]
        return None

    def _album_key(self, tags: tagmod.Tags) -> str:
        return _key("album", (tags.album_artist or tags.artist).lower(), tags.album.lower())

    def _make_cover(self, path: Path, rel: str, tags: tagmod.Tags) -> str | None:
        """Find or extract cover art for a song. Returns the cover's file name in the covers folder, or None."""
        if tags.album:
            embedded_key = folder_key = self._album_key(tags)
        else:
            embedded_key = _key("file", rel)
            folder_key = _key("dir", str(Path(rel).parent))
        for key in (embedded_key, folder_key):
            if (self.covers_dir / f"{key}.jpg").exists():
                return f"{key}.jpg"
        if tags.has_art and self._extract_cover(path, embedded_key):
            return f"{embedded_key}.jpg"
        art = self._folder_art(path.parent)
        if art and self._extract_cover(art, folder_key):
            return f"{folder_key}.jpg"
        return None

    def _extract_cover(self, source: Path, key: str) -> bool:
        """Turn an embedded picture or an image file into a 600 px wide JPEG in the covers folder."""
        target = self.covers_dir / f"{key}.jpg"
        tmp = self.covers_dir / f"{key}.tmp.jpg"
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-frames:v", "1",
               "-vf", "scale='min(600,iw)':-2,format=yuvj420p", "-q:v", "4", "-update", "1", str(tmp)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            os.replace(tmp, target)
            return True
        tmp.unlink(missing_ok=True)
        return False

    def _find_late_folder_art(self, conn: sqlite3.Connection) -> int:
        """Songs without a cover look in their folder again, so art added after the first scan is noticed."""
        linked = 0
        tried: dict[str, bool] = {}
        rows = conn.execute("SELECT id, path, artist, album, album_artist FROM songs WHERE cover IS NULL").fetchall()
        for row in rows:
            rel = row["path"]
            if row["album"]:
                key = _key("album", (row["album_artist"] or row["artist"]).lower(), row["album"].lower())
            else:
                key = _key("dir", str(Path(rel).parent))
            if key not in tried:
                tried[key] = False
                if (self.covers_dir / f"{key}.jpg").exists():
                    tried[key] = True
                else:
                    art = self._folder_art((self.music_dir / rel).parent)
                    tried[key] = bool(art and self._extract_cover(art, key))
            if tried[key]:
                conn.execute("UPDATE songs SET cover = ? WHERE id = ?", (f"{key}.jpg", row["id"]))
                linked += 1
        return linked

    def _apply_album_covers(self, conn: sqlite3.Connection) -> int:
        """Songs scanned before their album's cover turned up (for example, track 1 had none) get it now."""
        linked = 0
        rows = conn.execute(
            "SELECT id, artist, album, album_artist FROM songs WHERE cover IS NULL AND album != ''").fetchall()
        for row in rows:
            key = _key("album", (row["album_artist"] or row["artist"]).lower(), row["album"].lower())
            if (self.covers_dir / f"{key}.jpg").exists():
                conn.execute("UPDATE songs SET cover = ? WHERE id = ?", (f"{key}.jpg", row["id"]))
                linked += 1
        return linked

    # ---------- "Tidy my library": filling in what the files themselves do not say ----------

    @staticmethod
    def _tidy_key(row) -> str:
        if row["album"]:
            return _key("album", (row["album_artist"] or row["artist"]).lower(), row["album"].lower())
        return _key("file", row["path"])

    def tidy_groups(self, retry_days: float = 30, force: bool = False) -> list[dict]:
        """Albums (or loose songs) that are missing a cover, a year or a genre and are worth looking up.

        Something already looked up (found or not) is left alone for `retry_days` unless `force` is set.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, path, title, artist, album, album_artist, genre, year, cover FROM songs "
                "WHERE cover IS NULL OR year IS NULL OR genre = '' ORDER BY path").fetchall()
            cache = {r["key"]: r for r in conn.execute("SELECT key, status, checked FROM tidy_cache")}
        finally:
            conn.close()
        cutoff = time.time() - retry_days * 86400
        groups: dict[str, dict] = {}
        for row in rows:
            artist = row["album_artist"] or row["artist"]
            if not artist or artist == tagmod.UNKNOWN_ARTIST or not (row["album"] or row["title"]):
                continue
            key = self._tidy_key(row)
            cached = cache.get(key)
            if cached and not force and cached["checked"] > cutoff:
                continue
            group = groups.setdefault(key, {
                "key": key, "album": row["album"], "artist": artist, "title": row["title"] if not row["album"] else "",
                "song_ids": [], "need_cover": False, "need_year": False, "need_genre": False})
            group["song_ids"].append(row["id"])
            group["need_cover"] |= row["cover"] is None
            group["need_year"] |= row["year"] is None
            group["need_genre"] |= row["genre"] == ""
        return list(groups.values())

    def tidy_store(self, group: dict, found: bool, *, year: int | None = None, genre: str = "",
                   cover_file: Path | None = None) -> dict:
        """Save one lookup result. Only blanks are filled; anything the files already say is kept."""
        key = group["key"]
        filled = {"cover": 0, "year": 0, "genre": 0}
        has_cover = False
        if cover_file is not None:
            try:
                has_cover = self._extract_cover(cover_file, key)
            finally:
                Path(cover_file).unlink(missing_ok=True)
        conn = self._connect()
        try:
            with conn:
                marks = ",".join("?" * len(group["song_ids"]))
                ids = tuple(group["song_ids"])
                if has_cover:
                    filled["cover"] = conn.execute(
                        f"UPDATE songs SET cover = ? WHERE cover IS NULL AND id IN ({marks})", (f"{key}.jpg",) + ids).rowcount
                if year:
                    filled["year"] = conn.execute(
                        f"UPDATE songs SET year = ? WHERE year IS NULL AND id IN ({marks})", (int(year),) + ids).rowcount
                if genre:
                    filled["genre"] = conn.execute(
                        f"UPDATE songs SET genre = ? WHERE genre = '' AND id IN ({marks})", (genre,) + ids).rowcount
                conn.execute("INSERT OR REPLACE INTO tidy_cache (key, status, year, genre, checked) VALUES (?,?,?,?,?)",
                             (key, "found" if found else "none", int(year) if year else None, genre, time.time()))
        finally:
            conn.close()
        return filled

    def tidy_reload(self) -> None:
        self.reload(bump=True)

    def _apply_tidy_cache(self, conn: sqlite3.Connection) -> int:
        """After a rescan re-read a file's tags, put back the year and genre that were looked up earlier."""
        fixed = 0
        rows = conn.execute(
            "SELECT id, path, artist, album, album_artist, year, genre FROM songs WHERE year IS NULL OR genre = ''").fetchall()
        if not rows:
            return 0
        cache = {r["key"]: r for r in conn.execute("SELECT key, year, genre FROM tidy_cache WHERE status = 'found'")}
        for row in rows:
            hit = cache.get(self._tidy_key(row))
            if not hit:
                continue
            if row["year"] is None and hit["year"]:
                conn.execute("UPDATE songs SET year = ? WHERE id = ?", (hit["year"], row["id"]))
                fixed += 1
            if row["genre"] == "" and hit["genre"]:
                conn.execute("UPDATE songs SET genre = ? WHERE id = ?", (hit["genre"], row["id"]))
                fixed += 1
        return fixed
