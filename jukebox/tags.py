"""Read song information from audio files with ffprobe (part of ffmpeg).

One tool reads every format (MP3, FLAC, M4A, OGG, Opus, WAV and more), so there is nothing format-specific
to maintain here. When a file has no usable tags, the file name is used as a fallback.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wma", ".aif", ".aiff"}

# Old MP3 files store the genre as a number, for example "(17)" or "17".
ID3_GENRES = (
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop", "Jazz", "Metal",
    "New Age", "Oldies", "Other", "Pop", "R&B", "Rap", "Reggae", "Rock", "Techno", "Industrial",
    "Alternative", "Ska", "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop", "Vocal",
    "Jazz+Funk", "Fusion", "Trance", "Classical", "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel",
    "Noise", "Alternative Rock", "Bass", "Soul", "Punk", "Space", "Meditative", "Instrumental Pop",
    "Instrumental Rock", "Ethnic", "Gothic", "Darkwave", "Techno-Industrial", "Electronic", "Pop-Folk",
    "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40", "Christian Rap", "Pop/Funk",
    "Jungle", "Native American", "Cabaret", "New Wave", "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi",
    "Tribal", "Acid Punk", "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll", "Hard Rock",
)

UNKNOWN_ARTIST = "Unknown Artist"


class ToolMissing(RuntimeError):
    """ffmpeg or ffprobe is not installed (or not on the PATH)."""


def require_tools() -> None:
    missing = [name for name in ("ffprobe", "ffmpeg") if shutil.which(name) is None]
    if missing:
        raise ToolMissing(
            "Could not find " + " and ".join(missing) + ". Install ffmpeg and make sure it is on your PATH:\n"
            "  Windows:  winget install ffmpeg\n"
            "  Raspberry Pi / Debian:  sudo apt install ffmpeg\n"
            "  macOS:  brew install ffmpeg"
        )


@dataclass
class Tags:
    title: str
    artist: str
    album: str
    album_artist: str
    genre: str
    year: int | None
    duration: float
    track: int | None
    has_art: bool


def parse_year(value: str | None) -> int | None:
    match = re.search(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)", value or "")
    return int(match.group(1)) if match else None


def clean_genre(raw: str | None) -> str:
    """Tidy a genre tag: first genre only, and old numeric ID3 codes turned into names."""
    genre = (raw or "").replace("\x00", ";").strip()
    if not genre:
        return ""
    genre = genre.split(";")[0].strip()
    numeric = re.fullmatch(r"\(?(\d{1,3})\)?", genre)
    if numeric:
        index = int(numeric.group(1))
        return ID3_GENRES[index] if index < len(ID3_GENRES) else ""
    prefixed = re.match(r"\(\d{1,3}\)\s*(.+)", genre)
    if prefixed:
        genre = prefixed.group(1).strip()
    return genre


def guess_from_filename(path: Path) -> tuple[str, str]:
    """Return (artist, title) guessed from a name like '03 - Artist - Title.mp3'. Artist may be empty."""
    stem = path.stem.replace("_", " ")
    stem = re.sub(r"^\s*\d{1,3}\s*[-._)]\s*", "", stem)
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        if artist.strip() and title.strip():
            return artist.strip(), title.strip()
    return "", stem.strip()


def _probe(path: Path) -> dict | None:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None


def _merged_tags(info: dict) -> dict[str, str]:
    """Tags can sit on the file or on the audio stream (Ogg and Opus use the stream). Merge both, lowercase keys."""
    sources = [(info.get("format") or {}).get("tags") or {}]
    sources += [s.get("tags") or {} for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    merged: dict[str, str] = {}
    for source in sources:
        for key, value in source.items():
            text = str(value).strip()
            if text:
                merged.setdefault(key.lower(), text)
    return merged


def read_tags(path: Path) -> Tags | None:
    """Return the song's information, or None if the file cannot be read as audio."""
    info = _probe(path)
    if not info:
        return None
    streams = info.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        return None

    duration = 0.0
    for candidate in [(info.get("format") or {}).get("duration")] + [s.get("duration") for s in audio]:
        try:
            duration = float(candidate)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            break
    if duration <= 0:
        return None

    tags = _merged_tags(info)
    guess_artist, guess_title = guess_from_filename(path)
    album_artist = tags.get("album_artist") or tags.get("albumartist") or ""
    artist = tags.get("artist") or album_artist or guess_artist or UNKNOWN_ARTIST

    year = None
    for key in ("originaldate", "originalyear", "date", "year", "tdrc", "tyer", "releasedate"):
        year = parse_year(tags.get(key))
        if year:
            break

    track = None
    track_match = re.match(r"\d+", tags.get("track", ""))
    if track_match:
        track = int(track_match.group(0))

    has_art = any(
        s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic") == 1 for s in streams
    )
    return Tags(
        title=tags.get("title") or guess_title or path.stem,
        artist=artist,
        album=tags.get("album", ""),
        album_artist=album_artist,
        genre=clean_genre(tags.get("genre")),
        year=year,
        duration=duration,
        track=track,
        has_art=has_art,
    )
