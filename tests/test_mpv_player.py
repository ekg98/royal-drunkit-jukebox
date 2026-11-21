import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from jukebox.player import MpvPlayer, PlayerUnavailable

FAKE = [sys.executable, str(Path(__file__).with_name("fake_mpv.py"))]


def wait_for(condition, timeout=4.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.03)
    return False


class MpvPlayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.log = self.dir / "commands.log"
        self.song = self.dir / "song.mp3"
        self.song.write_bytes(b"x")
        self.broken = self.dir / "broken.mp3"
        self.broken.write_bytes(b"x")
        env = dict(os.environ, FAKE_MPV_SECONDS="0.6", FAKE_MPV_LOG=str(self.log))
        self.player = MpvPlayer(binary=FAKE, env=env, audio_device="alsa/test")

    def tearDown(self):
        self.player.close()
        self.tmp.cleanup()

    def commands(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_plays_to_the_end(self):
        self.player.start(self.song, 200)
        self.assertFalse(self.player.finished())
        self.assertTrue(wait_for(lambda: self.player.position() > 0.2))
        self.assertAlmostEqual(self.player.length(), 0.6, delta=0.01)       # the player's own length is reported
        self.assertTrue(wait_for(self.player.finished))
        self.assertTrue(any(c[0] == "loadfile" and c[1] == str(self.song) for c in self.commands()))

    def test_pause_holds_the_position(self):
        self.player.start(self.song, 200)
        self.assertTrue(wait_for(lambda: self.player.position() > 0.1))
        self.player.pause()
        time.sleep(0.2)
        held = self.player.position()
        time.sleep(0.3)
        self.assertEqual(self.player.position(), held)
        self.assertFalse(self.player.finished())
        self.player.resume()
        self.assertTrue(wait_for(self.player.finished))

    def test_stop_is_not_the_end_of_a_song(self):
        self.player.start(self.song, 200)
        self.assertTrue(wait_for(lambda: self.player.position() > 0.1))
        self.player.stop()
        time.sleep(0.3)
        self.assertFalse(self.player.finished())
        self.assertEqual(self.player.position(), 0.0)

    def test_replacing_a_song_does_not_look_like_the_end(self):
        self.player.start(self.song, 200)
        self.assertTrue(wait_for(lambda: self.player.position() > 0.1))
        self.player.start(self.song, 200)                 # skip straight into the next song
        time.sleep(0.25)
        self.assertFalse(self.player.finished())
        self.assertTrue(wait_for(self.player.finished))

    def test_a_file_mpv_cannot_play_counts_as_finished(self):
        self.player.start(self.broken, 200)
        self.assertTrue(wait_for(self.player.finished))

    def test_missing_file_is_refused_up_front(self):
        with self.assertRaises(OSError):
            self.player.start(self.dir / "nope.mp3", 100)

    def test_volume_and_device_reach_mpv(self):
        self.player.set_volume(40)
        self.assertTrue(wait_for(lambda: ["set_property", "volume", 40] in self.commands()))

    def test_mpv_is_restarted_if_it_dies(self):
        self.player.start(self.song, 200)
        self.assertTrue(wait_for(lambda: self.player.position() > 0.1))
        self.player._proc.kill()
        self.assertTrue(wait_for(self.player.finished))     # the engine would now move on...
        self.player.start(self.song, 200)                   # ...and the next song brings mpv back
        self.assertFalse(self.player.finished())
        self.assertTrue(wait_for(lambda: self.player.position() > 0.1))
        self.assertTrue(wait_for(self.player.finished))


class MissingMpvTest(unittest.TestCase):
    def test_clear_error_when_mpv_is_not_installed(self):
        import shutil
        real = shutil.which
        shutil.which = lambda name: None
        try:
            with self.assertRaises(PlayerUnavailable):
                MpvPlayer()
        finally:
            shutil.which = real


if __name__ == "__main__":
    unittest.main()
