"""The arcade's name: configurable, cleaned up, and put safely into the page."""
import random
import tempfile
import unittest
from pathlib import Path

from jukebox.config import DEFAULT_NAME, MAX_NAME, clean_name, load
from jukebox.engine import Engine
from jukebox.server import create_app
from tests.helpers import FakeClock, FakeLibrary, ManualPlayer

PAGE = ("<title>Royal Drunkit Jukebox</title><h1>The Royal Drunkit</h1>"
        "<div class='tv-brand'>The Royal Drunkit</div><div class='big'>The Royal Drunkit</div>")


class CleanNameTests(unittest.TestCase):
    def test_blank_gives_default(self):
        for raw in (None, "", "   ", "\n\t"):
            self.assertEqual(clean_name(raw), DEFAULT_NAME)

    def test_one_line_and_trimmed(self):
        self.assertEqual(clean_name("  Joe's\n  Bar  "), "Joe's Bar")

    def test_control_characters_removed(self):
        self.assertEqual(clean_name("Bar\x00\x07 Room"), "Bar Room")

    def test_length_limit(self):
        self.assertEqual(len(clean_name("x" * 100)), MAX_NAME)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default(self):
        self.assertEqual(load(["--config", str(self.cfg)]).arcade_name, DEFAULT_NAME)

    def test_from_file(self):
        self.cfg.write_text('arcade_name = "Moe\'s Arcade"\n')
        self.assertEqual(load(["--config", str(self.cfg)]).arcade_name, "Moe's Arcade")

    def test_flag_beats_file(self):
        self.cfg.write_text('arcade_name = "From File"\n')
        self.assertEqual(load(["--config", str(self.cfg), "--name", "From Flag"]).arcade_name, "From Flag")


class PageTests(unittest.TestCase):
    def client(self, name=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "static").mkdir()
        (base / "static" / "index.html").write_text(PAGE)
        (base / "covers").mkdir()
        library = FakeLibrary(covers_dir=base / "covers")
        engine = Engine(library, ManualPlayer(), None, "4321", clock=FakeClock(), rng=random.Random(1))
        if name is not None:
            engine.name = name
        return create_app(engine, library, base / "static").test_client(), engine

    def test_default_page_unchanged(self):
        client, _ = self.client()
        self.assertEqual(client.get("/").get_data(as_text=True), PAGE)

    def test_custom_name_in_page(self):
        client, _ = self.client("Moe's Arcade")
        page = client.get("/").get_data(as_text=True)
        self.assertIn("<title>Moe&#x27;s Arcade Jukebox</title>", page)
        self.assertEqual(page.count("Moe&#x27;s Arcade"), 4)
        self.assertNotIn("Royal Drunkit", page)

    def test_name_is_escaped(self):
        client, _ = self.client("<script>alert(1)</script>")
        page = client.get("/").get_data(as_text=True)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_state_carries_name(self):
        client, _ = self.client("Moe's Arcade")
        self.assertEqual(client.get("/api/state").get_json()["state"]["name"], "Moe's Arcade")


if __name__ == "__main__":
    unittest.main()
