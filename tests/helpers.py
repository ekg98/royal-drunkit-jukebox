"""Shared test helpers: real sample audio made with ffmpeg, and fakes for the engine and server tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

from jukebox.library import decade_label, decade_start  # noqa: F401  (re-exported for tests)

CODECS = {".mp3": "libmp3lame", ".flac": "flac", ".m4a": "aac", ".ogg": "libvorbis", ".opus": "libopus",
          ".wav": "pcm_s16le"}


def run(cmd):
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))


def make_image(path: Path, color: str = "0xcc2288") -> Path:
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=300x300",
         "-frames:v", "1", str(path)])
    return path


def make_audio(path: Path, *, art: Path | None = None, seconds: float = 1.0, **meta) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if art is not None:
        cmd += ["-i", str(art), "-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += ["-c:a", CODECS[ext]]
    if ext == ".mp3":
        cmd += ["-id3v2_version", "3"]
    for key, value in meta.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(path))
    run(cmd)
    return path


def build_sample_library(root: Path) -> Path:
    """A small library that exercises tags, fallbacks, covers and bad files."""
    root.mkdir(parents=True, exist_ok=True)
    cover = make_image(root / "_art.jpg")
    # Fully tagged MP3 with embedded cover art
    make_audio(root / "Neon Kings" / "Night Drive (1985)" / "01 - Neon Rain.mp3", art=cover,
               title="Neon Rain", artist="The Neon Kings", album="Night Drive", genre="Rock", date="1985", track="1/9")
    # Second track of that album: no embedded art, but the album's cover should still reach it
    make_audio(root / "Neon Kings" / "Night Drive (1985)" / "02 - Chrome Highway.flac",
               title="Chrome Highway", artist="The Neon Kings", album="Night Drive", genre="rock", date="1985-06-01")
    # Folder art (cover.jpg) for an album with no embedded pictures
    make_audio(root / "Velvet" / "Slow Burn" / "01 - Slow Burn.m4a",
               title="Slow Burn", artist="Velvet Circuit", album="Slow Burn", genre="Pop", date="2003")
    make_image(root / "Velvet" / "Slow Burn" / "Cover.JPG", "0x2288cc")
    # Ogg and Opus keep tags on the stream
    make_audio(root / "Misc" / "dusty.ogg", title="Dusty Highway", artist="Hollow Pines", album="Gravel",
               genre="Country", date="1994")
    make_audio(root / "Misc" / "turbo.opus", title="Turbo Heart", artist="Magenta Run", genre="Dance", date="2019")
    # Old-style numeric genre
    make_audio(root / "Misc" / "oldschool.mp3", title="Old School", artist="Cal Dawson", genre="(17)", date="1971")
    # No tags at all: name and artist come from the file name
    make_audio(root / "Misc" / "03 - The Band - Some Song.wav")
    make_audio(root / "Misc" / "lonely_road.mp3")
    # Not audio, and a broken audio file
    (root / "Misc" / "notes.txt").write_text("not music")
    (root / "Misc" / "broken.mp3").write_bytes(b"this is not an mp3 at all" * 40)
    (root / "_art.jpg").unlink()
    return root


class FakeLibrary:
    """Just enough of Library for the engine and server tests, with no files involved."""

    def __init__(self, songs=None, covers_dir: Path | None = None):
        self.version = 1
        self.covers_dir = covers_dir or Path(".")
        self._songs = songs if songs is not None else self.default_songs()
        self._by_id = {s["id"]: s for s in self._songs}
        self.plays: list[int] = []
        self.scans = 0

    @staticmethod
    def default_songs():
        spec = [("Neon Rain", "Neon Kings", "Rock", 1985), ("Slow Burn", "Velvet", "Pop", 2003),
                ("Dusty Highway", "Hollow Pines", "Country", 1994), ("Turbo Heart", "Magenta Run", "Dance", 2019),
                ("Old School", "Cal Dawson", "Rock", 1971), ("Strobe", "Lumen Club", "Dance", 1988),
                ("Tilt", "Brass Monkey", "Rock", 1989), ("Corner Booth", "Ella Marsh", "Pop", 1999)]
        return [{"id": i + 1, "path": f"{t}.mp3", "title": t, "artist": a, "album": "", "album_artist": "",
                 "genre": g, "year": y, "dur": 200.0, "added": 1000 + i, "plays": 0, "cover": None}
                for i, (t, a, g, y) in enumerate(spec)]

    def count(self): return len(self._songs)
    def all(self): return self._songs
    def get(self, song_id): return self._by_id.get(song_id)
    def abs_path(self, song): return Path("/music") / song["path"]
    def record_play(self, song_id): self.plays.append(song_id)
    def popular(self, hours=12, limit=12): return list(dict.fromkeys(self.plays))[:limit]
    def scan_status(self): return {"running": False, "done": 0, "total": 0, "last_report": None}

    def scan_async(self, log=print):
        self.scans += 1
        return True

    def api_songs(self):
        keys = ("id", "title", "artist", "album", "genre", "year", "dur", "added", "plays", "cover")
        return [{k: s[k] for k in keys} for s in self._songs]

    def genres(self):
        counts = {}
        for s in self._songs:
            counts[s["genre"]] = counts.get(s["genre"], 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def decades(self):
        counts = {}
        for s in self._songs:
            label = decade_label(s["year"])
            counts[label] = counts.get(label, 0) + 1
        return sorted(counts.items(), key=lambda kv: decade_start(kv[0]))


class ManualPlayer:
    """A player whose clock the test controls."""

    def __init__(self):
        self.started: list = []
        self.is_paused = False
        self.done = False
        self.volume = None
        self.stopped = 0

    def start(self, path, duration):
        self.started.append((path, duration))
        self.is_paused = False
        self.done = False

    def pause(self): self.is_paused = True
    def resume(self): self.is_paused = False
    def stop(self): self.stopped += 1
    def set_volume(self, percent): self.volume = percent
    def position(self): return 12.3
    def finished(self): return self.done


class FakeClock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds
