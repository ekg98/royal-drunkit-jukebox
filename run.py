#!/usr/bin/env python3
"""Start The Royal Drunkit Jukebox.

    python run.py --music "D:\\Music"

then open the address it prints from any phone or tablet on the same Wi-Fi.
"""
from __future__ import annotations

import socket
import sys

from jukebox import __version__
from jukebox.config import DEFAULT_PIN, ROOT, load
from jukebox.engine import Engine
from jukebox.library import Library
from jukebox.player import MpvPlayer, NullPlayer, PlayerUnavailable, list_audio_devices
from jukebox.server import create_app
from jukebox.tags import ToolMissing, require_tools
from jukebox.tidy import Tidy


def lan_address() -> str | None:
    """The computer's address on the local network, if it has one."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))      # no packet is sent; this just picks the right interface
            return sock.getsockname()[0]
    except OSError:
        return None


def make_player(config):
    """mpv when it is available, otherwise a simulated player (with a clear warning)."""
    if config.simulate:
        return NullPlayer(config.song_seconds)
    try:
        return MpvPlayer(audio_device=config.audio_device)
    except PlayerUnavailable as exc:
        print(f"WARNING: no sound: {exc}")
        print("         Falling back to the simulated player. Install mpv, then start the jukebox again.")
        return NullPlayer()


def main() -> int:
    config = load()
    if config.list_audio:
        try:
            print(list_audio_devices())
        except PlayerUnavailable as exc:
            print(exc)
            return 2
        return 0
    try:
        require_tools()
    except ToolMissing as exc:
        print(exc)
        return 2
    if not config.music_dir.is_dir():
        print(f"The music folder does not exist: {config.music_dir}")
        print('Point to yours with:  python run.py --music "path/to/your/music"')
        return 2

    library = Library(config.music_dir, config.data_dir)
    if config.scan_on_start:
        print(f"Scanning {config.music_dir} ...")
        report = library.scan(log=print)
        print(f"Library: {library.count()} songs "
              f"({report.added} added, {report.updated} updated, {report.removed} removed, "
              f"{len(report.unreadable)} unreadable) in {report.seconds:.1f}s")
        for name in report.unreadable[:10]:
            print(f"  could not read: {name}")
    else:
        print(f"Library: {library.count()} songs (scan skipped)")

    player = make_player(config)
    engine = Engine(library, player, config.data_dir / "settings.json", config.host_pin)
    engine.name = config.arcade_name
    engine.tidy = Tidy(library, contact=config.contact)
    engine.start()
    app = create_app(engine, library, ROOT / "static")

    print()
    print(f"{config.arcade_name} Jukebox (version {__version__})")
    if player.name == "simulated":
        print("NOTE: playback is simulated. The queue, credits and screens are real, but no sound comes out.")
    else:
        print(f"Sound: mpv, audio output = {config.audio_device or 'system default'}")
    if config.host_pin == DEFAULT_PIN:
        print(f"NOTE: the host PIN is still the default ({DEFAULT_PIN}). Change it with --pin or in config.toml.")
    print()
    print(f"Jukebox is running. On this computer open:  http://localhost:{config.port}")
    address = lan_address()
    if address:
        print(f"Phones and tablets on your Wi-Fi open:      http://{address}:{config.port}")
    print("Press Ctrl+C to stop.")
    try:
        app.run(host=config.host, port=config.port, threaded=True, use_reloader=False)
    finally:
        engine.stop()
        if hasattr(player, "close"):
            player.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
