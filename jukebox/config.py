"""Settings for the jukebox.

Values come from config.toml (if there is one) and are overridden by command-line options.
"""
from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PIN = "1234"
DEFAULT_NAME = "The Royal Drunkit"
MAX_NAME = 40


def clean_name(raw) -> str:
    """The arcade's name as shown on the screens: one line, no control characters, at most 40 characters."""
    text = " ".join(str(raw or "").split())
    text = "".join(ch for ch in text if ch.isprintable())[:MAX_NAME].strip()
    return text or DEFAULT_NAME


@dataclass
class Config:
    music_dir: Path
    data_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    host_pin: str = DEFAULT_PIN
    song_seconds: float | None = None
    scan_on_start: bool = True
    audio_device: str | None = None
    simulate: bool = False
    list_audio: bool = False
    contact: str = ""
    arcade_name: str = DEFAULT_NAME


def load(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(description="The Royal Drunkit Jukebox")
    parser.add_argument("--version", action="version", version=f"The Royal Drunkit Jukebox {__version__}")
    parser.add_argument("--config", type=Path, default=ROOT / "config.toml",
                        help="settings file (default: config.toml next to run.py)")
    parser.add_argument("--music", type=Path, help="folder that holds your music files")
    parser.add_argument("--data", type=Path, help="folder for the catalog, cover art and saved settings")
    parser.add_argument("--host", help="address to listen on (default 0.0.0.0, every network interface)")
    parser.add_argument("--port", type=int, help="web port (default 8080)")
    parser.add_argument("--pin", help="host PIN for the Host panel")
    parser.add_argument("--name", help=f'name of your arcade, shown on the screens (default "{DEFAULT_NAME}")')
    parser.add_argument("--song-seconds", type=float,
                        help="pretend every song lasts this many seconds (for testing the simulated player)")
    parser.add_argument("--audio-device", help="audio output for mpv (see --list-audio-devices); default is the system's")
    parser.add_argument("--list-audio-devices", action="store_true", help="show the audio outputs mpv can use, then exit")
    parser.add_argument("--simulate", action="store_true", help="do not play sound; pretend (for testing)")
    parser.add_argument("--no-scan", action="store_true", help="skip scanning the music folder at startup")
    args = parser.parse_args(argv)

    values: dict = {}
    config_dir = ROOT
    if args.config.is_file():
        config_dir = args.config.resolve().parent
        with open(args.config, "rb") as fh:
            values = tomllib.load(fh)

    def from_file_path(key: str, default: Path) -> Path:
        raw = values.get(key)
        if raw is None:
            return default
        path = Path(str(raw)).expanduser()
        return path if path.is_absolute() else config_dir / path

    music = args.music.expanduser() if args.music else from_file_path("music_dir", ROOT / "music")
    data = args.data.expanduser() if args.data else from_file_path("data_dir", ROOT / "data")

    return Config(
        music_dir=music,
        data_dir=data,
        host=args.host or values.get("host", "0.0.0.0"),
        port=args.port or int(values.get("port", 8080)),
        host_pin=str(args.pin or values.get("host_pin", DEFAULT_PIN)),
        song_seconds=args.song_seconds,
        scan_on_start=not args.no_scan,
        audio_device=args.audio_device or values.get("audio_device") or None,
        simulate=bool(args.simulate or args.song_seconds or values.get("simulate", False)),
        list_audio=args.list_audio_devices,
        contact=str(values.get("contact", "")),
        arcade_name=clean_name(args.name or values.get("arcade_name")),
    )
