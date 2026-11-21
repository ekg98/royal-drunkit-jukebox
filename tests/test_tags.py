import tempfile
import unittest
from pathlib import Path

from jukebox import tags
from tests.helpers import make_audio, make_image


class PureFunctionsTest(unittest.TestCase):
    def test_parse_year(self):
        self.assertEqual(tags.parse_year("1985"), 1985)
        self.assertEqual(tags.parse_year("1985-06-01"), 1985)
        self.assertEqual(tags.parse_year("2003/04/02"), 2003)
        self.assertIsNone(tags.parse_year("198"))
        self.assertIsNone(tags.parse_year("track 12345"))
        self.assertIsNone(tags.parse_year(None))

    def test_clean_genre(self):
        self.assertEqual(tags.clean_genre("(17)"), "Rock")
        self.assertEqual(tags.clean_genre("17"), "Rock")
        self.assertEqual(tags.clean_genre("(17)Rock"), "Rock")
        self.assertEqual(tags.clean_genre("Rock; Pop"), "Rock")
        self.assertEqual(tags.clean_genre("Hip-Hop/Rap"), "Hip-Hop/Rap")
        self.assertEqual(tags.clean_genre("  Soul "), "Soul")
        self.assertEqual(tags.clean_genre("(999)"), "")
        self.assertEqual(tags.clean_genre(None), "")

    def test_guess_from_filename(self):
        guess = tags.guess_from_filename
        self.assertEqual(guess(Path("03 - The Band - Some Song.wav")), ("The Band", "Some Song"))
        self.assertEqual(guess(Path("lonely_road.mp3")), ("", "lonely road"))
        self.assertEqual(guess(Path("07. Moon River.mp3")), ("", "Moon River"))
        self.assertEqual(guess(Path("99 Luftballons.mp3")), ("", "99 Luftballons"))
        self.assertEqual(guess(Path("1979.mp3")), ("", "1979"))


class ReadTagsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_mp3_with_embedded_art(self):
        art = make_image(self.dir / "a.jpg")
        path = make_audio(self.dir / "a.mp3", art=art, title="Neon Rain", artist="The Neon Kings",
                          album="Night Drive", genre="Rock", date="1985", track="3/9", seconds=2)
        t = tags.read_tags(path)
        self.assertEqual((t.title, t.artist, t.album, t.genre, t.year, t.track),
                         ("Neon Rain", "The Neon Kings", "Night Drive", "Rock", 1985, 3))
        self.assertTrue(t.has_art)
        self.assertAlmostEqual(t.duration, 2.0, delta=0.3)

    def test_flac_without_art(self):
        t = tags.read_tags(make_audio(self.dir / "b.flac", title="Chrome", artist="Kings", date="1999-01-01"))
        self.assertEqual((t.title, t.year, t.has_art), ("Chrome", 1999, False))

    def test_ogg_tags_live_on_the_stream(self):
        t = tags.read_tags(make_audio(self.dir / "c.ogg", title="Dusty", artist="Pines", genre="Country"))
        self.assertEqual((t.title, t.artist, t.genre), ("Dusty", "Pines", "Country"))

    def test_untagged_file_falls_back_to_file_name(self):
        t = tags.read_tags(make_audio(self.dir / "05 - Hollow Band - Quiet Town.wav"))
        self.assertEqual((t.title, t.artist, t.genre, t.year), ("Quiet Town", "Hollow Band", "", None))

    def test_untagged_file_without_artist_in_name(self):
        t = tags.read_tags(make_audio(self.dir / "mystery_song.mp3"))
        self.assertEqual((t.title, t.artist), ("mystery song", tags.UNKNOWN_ARTIST))

    def test_not_audio_returns_none(self):
        bad = self.dir / "bad.mp3"
        bad.write_bytes(b"definitely not audio" * 50)
        self.assertIsNone(tags.read_tags(bad))
        self.assertIsNone(tags.read_tags(self.dir / "missing.mp3"))


if __name__ == "__main__":
    unittest.main()
