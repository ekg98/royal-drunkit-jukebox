#!/usr/bin/env python3
"""Make a small library of fake songs (a few seconds of a tone each) to try the jukebox with.

    python tools/make_sample_music.py music-demo --count 120
    python run.py --music music-demo

The files have real tags, years, genres and cover art, laid out in Artist/Album folders, so the browsing screens
have something to show. Needs ffmpeg.
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
from pathlib import Path

GENRES = ["Rock", "Pop", "Country", "Dance", "Hip-Hop", "R&B", "Jazz", "Metal", "Soul", "Blues"]
ADJ = ["Neon", "Velvet", "Crimson", "Hollow", "Electric", "Silver", "Midnight", "Golden", "Broken", "Lazy", "Wild",
       "Cosmic", "Rusty", "Paper", "Glass", "Static", "Magenta", "Copper", "Fading", "Turbo", "Lucky", "Dusty"]
NOUN = ["Kings", "Pines", "Drifters", "Echoes", "Rebels", "Wolves", "Sparks", "Roads", "Lanterns", "Ghosts", "Jets",
        "Pilots", "Dreamers", "Engines", "Strangers", "Comets", "Rangers", "Saints", "Hearts"]
TA = ["Neon", "Midnight", "Slow", "Burning", "Paper", "Electric", "Golden", "Lost", "Faded", "Wild", "Summer",
      "Winter", "Hollow", "Crystal", "Loud", "Gravel", "Velvet", "Turbo", "Cherry", "Rooftop"]
TB = ["Rain", "Heart", "Highway", "Lights", "Motion", "Dreams", "Fever", "Parade", "Skyline", "Static", "Weekend",
      "Roads", "Thunder", "Mirror", "Moon", "Engine", "Candy", "Letters", "Waves", "Fireworks"]
EXTS = [".mp3", ".mp3", ".mp3", ".flac", ".m4a", ".ogg", ".wav"]
CODECS = {".mp3": "libmp3lame", ".flac": "flac", ".m4a": "aac", ".ogg": "libvorbis", ".wav": "pcm_s16le"}


def run(cmd: list[str]) -> bool:
    return subprocess.run(cmd, capture_output=True).returncode == 0


def make_cover(path: Path, rng: random.Random) -> bool:
    c0, c1 = (f"0x{rng.randrange(0x1000000):06x}" for _ in range(2))
    ok = run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"gradients=s=400x400:c0={c0}:c1={c1}:d=1",
              "-frames:v", "1", str(path)])
    return ok or run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={c0}:s=400x400",
                      "-frames:v", "1", str(path)])


def make_song(path: Path, meta: dict, seconds: float, freq: int, art: Path | None) -> bool:
    ext = path.suffix.lower()
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}"]
    embed = art is not None and ext in (".mp3", ".flac")
    if embed:
        cmd += ["-i", str(art), "-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    cmd += ["-c:a", CODECS[ext]]
    if ext == ".mp3":
        cmd += ["-id3v2_version", "3"]
    for key, value in meta.items():
        if value not in (None, ""):
            cmd += ["-metadata", f"{key}={value}"]
    cmd.append(str(path))
    return run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="where to put the sample music")
    parser.add_argument("--count", type=int, default=120, help="how many songs (default 120)")
    parser.add_argument("--seconds", type=float, default=2.0, help="length of each song (default 2)")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    args.folder.mkdir(parents=True, exist_ok=True)
    made = 0
    artists: list[tuple[str, str]] = []
    while made < args.count:
        if not artists or rng.random() < 0.5:
            artists.append((f"The {rng.choice(ADJ)} {rng.choice(NOUN)}", rng.choice(GENRES)))
        artist, genre = rng.choice(artists)
        album = f"{rng.choice(TA)} {rng.choice(TB)}"
        year = rng.randint(1962, 2024)
        album_dir = args.folder / artist / f"{album} ({year})"
        album_dir.mkdir(parents=True, exist_ok=True)

        art = album_dir / "cover.jpg"
        has_art = rng.random() < 0.7 and make_cover(art, rng)
        use_folder_art = has_art and rng.random() < 0.4
        if has_art and not use_folder_art:
            embedded = album_dir / ".art.jpg"          # hidden helper, scanned past by the jukebox
            art.replace(embedded)
            art = embedded

        for track in range(1, rng.randint(2, 6) + 1):
            if made >= args.count:
                break
            title = f"{rng.choice(TA)} {rng.choice(TB)}"
            ext = rng.choice(EXTS)
            path = album_dir / f"{track:02d} - {title}{ext}"
            if path.exists():
                continue
            meta = {"title": title, "artist": artist, "album": album, "genre": genre, "date": str(year),
                    "track": str(track)}
            ok = make_song(path, meta, args.seconds, rng.randint(220, 880), art if has_art and not use_folder_art else None)
            if ok:
                made += 1
                print(f"\r{made}/{args.count}", end="", flush=True)
        hidden = album_dir / ".art.jpg"
        if hidden.exists():
            hidden.unlink()
    print(f"\nMade {made} songs in {args.folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
