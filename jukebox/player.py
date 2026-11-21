"""Audio players.

NullPlayer pretends to play, so the whole jukebox can be built and tested without sound.
MpvPlayer runs the mpv program in the background and plays through whatever audio output the computer uses
(the headphone jack, HDMI, USB, Bluetooth ...). Both fit the same small interface, which the engine uses.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path


class PlayerUnavailable(Exception):
    """The real player cannot be used (mpv is not installed, or it would not start)."""


class Player:
    """What the engine needs from an audio player."""

    name = "unknown"          # shown to the web page, so it can say when no sound is being produced

    def start(self, path: Path, duration: float) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def resume(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def set_volume(self, percent: int) -> None:
        raise NotImplementedError

    def position(self) -> float:
        """Seconds played so far in the current song."""
        raise NotImplementedError

    def finished(self) -> bool:
        """True once the current song has played to its end."""
        raise NotImplementedError

    def length(self) -> float | None:
        """Length of the current song in seconds if the player knows it better than the catalog does."""
        return None


class NullPlayer(Player):
    """Pretends to play. Time passes, but nothing comes out of the speakers."""

    name = "simulated"

    def length(self) -> float | None:
        return self._duration if self._active else None

    def __init__(self, song_seconds: float | None = None, clock=time.monotonic):
        self.song_seconds = song_seconds
        self.volume = 75
        self._clock = clock
        self._duration = 0.0
        self._offset = 0.0
        self._since: float | None = None
        self._active = False

    def start(self, path: Path, duration: float) -> None:
        self._duration = float(self.song_seconds or duration)
        self._offset = 0.0
        self._since = self._clock()
        self._active = True

    def pause(self) -> None:
        if self._since is not None:
            self._offset += self._clock() - self._since
            self._since = None

    def resume(self) -> None:
        if self._active and self._since is None:
            self._since = self._clock()

    def stop(self) -> None:
        self._active = False
        self._since = None
        self._offset = 0.0

    def set_volume(self, percent: int) -> None:
        self.volume = percent

    def position(self) -> float:
        if not self._active:
            return 0.0
        elapsed = self._offset + (self._clock() - self._since if self._since is not None else 0.0)
        return min(elapsed, self._duration)

    def finished(self) -> bool:
        return self._active and self.position() >= self._duration


class MpvPlayer(Player):
    """Plays through mpv, controlled over mpv's JSON command socket.

    mpv is started once and kept running; each song is a `loadfile` command. mpv reports the position, the length
    and the end of each song back to us as events. If mpv ever dies, the next song starts a fresh one.
    """

    name = "mpv"

    def __init__(self, binary: list[str] | None = None, audio_device: str | None = None, volume: int = 75,
                 env: dict | None = None):
        if binary:
            self._cmd = list(binary)
        else:
            found = shutil.which("mpv")
            if not found:
                raise PlayerUnavailable("mpv is not installed (Debian/Ubuntu: sudo apt install mpv)")
            self._cmd = [found]
        self._device = audio_device
        self._volume = volume
        self._env = env
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._dir: str | None = None
        self._pos = 0.0
        self._len = 0.0
        self._finished = False
        self._awaiting_start = False
        self._active = False
        atexit.register(self.close)
        self._ensure_running()                      # fail early, with a clear message, if mpv will not start

    # ---------- process and socket ----------

    def _ensure_running(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None and self._sock is not None:
                return
            self._shutdown()
            self._dir = tempfile.mkdtemp(prefix="jukebox-mpv-")
            path = os.path.join(self._dir, "mpv.sock")
            args = self._cmd + ["--idle=yes", "--no-video", "--audio-display=no", "--no-terminal", "--really-quiet",
                                "--force-window=no", f"--volume={self._volume}", f"--input-ipc-server={path}"]
            if self._device:
                args.append(f"--audio-device={self._device}")
            try:
                self._proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL, env=self._env)
            except OSError as exc:
                raise PlayerUnavailable(f"could not start mpv: {exc}") from exc
            sock = None
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    break
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(path)
                    break
                except OSError:
                    sock.close()
                    sock = None
                    time.sleep(0.05)
            if sock is None:
                self._shutdown()
                raise PlayerUnavailable("mpv started but did not accept connections")
            self._sock = sock
            self._pos, self._len, self._active = 0.0, 0.0, False
            threading.Thread(target=self._read_events, args=(sock,), daemon=True).start()
            self._send("observe_property", 1, "time-pos")
            self._send("observe_property", 2, "duration")

    def _read_events(self, sock: socket.socket) -> None:
        try:
            with sock.makefile("r", encoding="utf-8", errors="replace") as lines:
                for line in lines:
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    self._on_message(msg)
        except OSError:
            pass
        with self._lock:
            if self._sock is sock:                   # mpv went away: let the engine move on, and restart on demand
                self._sock = None
                if self._active:
                    self._finished = True

    def _on_message(self, msg: dict) -> None:
        event = msg.get("event")
        if event == "property-change":
            value = msg.get("data")
            if msg.get("name") == "time-pos" and isinstance(value, (int, float)):
                self._pos = float(value)
            elif msg.get("name") == "duration" and isinstance(value, (int, float)):
                self._len = float(value)
        elif event == "start-file":
            self._awaiting_start = False
        elif event == "end-file":
            # "eof" is a song ending naturally and "error" a file that would not play. "stop" is us skipping.
            if msg.get("reason") in ("eof", "error") and not self._awaiting_start:
                self._finished = True

    def _send(self, *command) -> None:
        data = (json.dumps({"command": list(command)}) + "\n").encode()
        with self._lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(data)
            except OSError:
                pass

    def _shutdown(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            if self._proc is not None:
                if self._proc.poll() is None:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                self._proc = None
            if self._dir:
                shutil.rmtree(self._dir, ignore_errors=True)
                self._dir = None

    def close(self) -> None:
        self._shutdown()

    # ---------- the Player interface ----------

    def start(self, path: Path, duration: float) -> None:
        if not Path(path).is_file():
            raise OSError(f"file not found: {path}")
        with self._lock:
            self._ensure_running()
            self._pos, self._len = 0.0, 0.0
            self._finished = False
            self._awaiting_start = True              # ignore an "end" left over from the song we are replacing
            self._active = True
            self._send("set_property", "pause", False)
            self._send("loadfile", str(path), "replace")

    def pause(self) -> None:
        self._send("set_property", "pause", True)

    def resume(self) -> None:
        self._send("set_property", "pause", False)

    def stop(self) -> None:
        with self._lock:
            self._active = False
            self._pos = 0.0
            self._send("stop")

    def set_volume(self, percent: int) -> None:
        self._volume = int(percent)
        self._send("set_property", "volume", self._volume)

    def position(self) -> float:
        return self._pos if self._active else 0.0

    def finished(self) -> bool:
        return self._active and self._finished

    def length(self) -> float | None:
        return self._len if self._active and self._len > 0 else None


def list_audio_devices(binary: str | None = None) -> str:
    """The audio outputs mpv can use, as mpv prints them."""
    exe = binary or shutil.which("mpv")
    if not exe:
        raise PlayerUnavailable("mpv is not installed (Debian/Ubuntu: sudo apt install mpv)")
    proc = subprocess.run([exe, "--audio-device=help"], capture_output=True, text=True, timeout=20)
    return (proc.stdout or proc.stderr).strip()
