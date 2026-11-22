import tempfile
import unittest
from pathlib import Path

from jukebox.library import Library
from jukebox.tidy import NetworkError, Tidy, norm, same_artist, year_of
from tests.helpers import make_audio, make_image


class FakeHttp:
    """Stands in for MusicBrainz and the Cover Art Archive."""

    def __init__(self, image: Path):
        self.image = image
        self.urls: list[str] = []
        self.releases = {}      # album title -> release dict list
        self.recordings = []
        self.genres = {}
        self.covers = set()     # url fragments that have a cover
        self.down = False

    def get_json(self, url):
        self.urls.append(url)
        if self.down:
            raise NetworkError("offline")
        if "/release-group/" in url:
            return {"genres": self.genres.get(url.split("/release-group/")[1].split("?")[0], [])}
        if "/release/" in url:
            return {"releases": self.releases.get("all", [])}
        if "/recording/" in url:
            return {"recordings": self.recordings}
        return None

    def get_file(self, url, dest):
        self.urls.append(url)
        if self.down:
            raise NetworkError("offline")
        if any(fragment in url for fragment in self.covers):
            dest.write_bytes(self.image.read_bytes())
            return True
        return False


def release(title, artist, date="1985-03-01", score=100, rid="rel1", rgid="rg1", status="Official"):
    return {"id": rid, "score": score, "title": title, "date": date, "status": status,
            "artist-credit": [{"name": artist, "artist": {"name": artist}}], "release-group": {"id": rgid}}


class MatchingTest(unittest.TestCase):
    def test_norm_ignores_case_accents_brackets_and_the(self):
        self.assertEqual(norm("The Café Blue (Deluxe Edition)"), norm("cafe blue"))
        self.assertEqual(norm("Guns & Roses"), norm("guns and roses"))

    def test_artist_match(self):
        self.assertTrue(same_artist("The Neon Kings", ["Neon Kings"]))
        self.assertFalse(same_artist("Neon Kings", ["Velvet Circuit"]))
        self.assertFalse(same_artist("", ["x"]))

    def test_year(self):
        self.assertEqual(year_of("1985-03-01"), 1985)
        self.assertIsNone(year_of(""))
        self.assertIsNone(year_of("0000"))


class TidyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.music = base / "music"
        make_audio(self.music / "a" / "1.mp3", title="One", artist="Neon Kings", album="Night Drive")
        make_audio(self.music / "a" / "2.mp3", title="Two", artist="Neon Kings", album="Night Drive")
        make_audio(self.music / "b" / "3.mp3", title="Lost Song", artist="Hollow Pines", genre="Country", date="1994",
                   album="Gravel")
        make_audio(self.music / "c" / "4.mp3", title="Loose Song", artist="Magenta Run")
        self.lib = Library(self.music, base / "data")
        self.lib.scan()
        self.http = FakeHttp(make_image(base / "x.jpg"))
        self.sleeps: list[float] = []
        self.tidy = Tidy(self.lib, self.http, sleep=self.sleeps.append, clock=lambda: 0.0)

    def tearDown(self):
        self.tmp.cleanup()

    def song(self, title):
        return next(s for s in self.lib.all() if s["title"] == title)

    def test_groups_are_per_album_and_skip_complete_songs(self):
        groups = self.lib.tidy_groups()
        names = sorted(g["album"] or g["title"] for g in groups)
        self.assertEqual(names, ["Gravel", "Loose Song", "Night Drive"])   # Gravel has no cover
        night = next(g for g in groups if g["album"] == "Night Drive")
        self.assertEqual(len(night["song_ids"]), 2)
        self.assertTrue(night["need_cover"] and night["need_year"] and night["need_genre"])

    def test_fills_cover_year_and_genre_for_an_album(self):
        self.http.releases["all"] = [release("Night Drive", "The Neon Kings")]
        self.http.genres["rg1"] = [{"name": "synth-pop", "count": 3}, {"name": "rock", "count": 9}]
        self.http.covers.add("release-group/rg1")
        status = self.tidy.run()
        for title in ("One", "Two"):
            song = self.song(title)
            self.assertEqual(song["year"], 1985)
            self.assertEqual(song["genre"], "Rock")
            self.assertTrue(song["cover"] and (self.lib.covers_dir / song["cover"]).exists())
        self.assertEqual(status["covers"], 2)
        self.assertEqual(status["years"], 2)
        self.assertEqual(list(self.lib.covers_dir.glob("*.dl")), [])        # temp download cleaned up

    def test_existing_tags_are_never_replaced(self):
        self.http.releases["all"] = [release("Gravel", "Hollow Pines", date="2001")]
        self.http.genres["rg1"] = [{"name": "pop", "count": 1}]
        self.http.covers.add("release-group/rg1")
        self.tidy.run()
        song = self.song("Lost Song")
        self.assertEqual(song["year"], 1994)           # file said 1994, MusicBrainz says 2001
        self.assertEqual(song["genre"], "Country")
        self.assertTrue(song["cover"])                  # only the blank was filled

    def test_wrong_album_or_artist_is_rejected(self):
        self.http.releases["all"] = [release("Night Drive", "Somebody Else"), release("Night Drives", "The Neon Kings"),
                                     release("Night Drive", "The Neon Kings", score=60)]
        self.http.covers.add("release-group")
        status = self.tidy.run()
        self.assertIsNone(self.song("One")["year"])
        self.assertIsNone(self.song("One")["cover"])
        self.assertEqual(status["not_found"], 3)

    def test_earliest_year_and_official_release_win(self):
        self.http.releases["all"] = [release("Night Drive", "Neon Kings", date="2015-01-01", rid="r2", status="Official"),
                                     release("Night Drive", "Neon Kings", date="1985-05-05", rid="r1", status="Official"),
                                     release("Night Drive", "Neon Kings", date="1984-01-01", rid="r0", status="Bootleg")]
        self.tidy.run()
        self.assertEqual(self.song("One")["year"], 1984)

    def test_album_less_song_is_found_by_title(self):
        self.http.recordings = [{"score": 100, "title": "Loose Song", "artist-credit": [{"name": "Magenta Run"}],
                                 "releases": [{"id": "r9", "date": "2019-02-02", "status": "Official",
                                               "release-group": {"id": "rgx"}}]}]
        self.http.covers.add("release-group/rgx")
        self.tidy.run()
        song = self.song("Loose Song")
        self.assertEqual(song["year"], 2019)
        self.assertTrue(song["cover"])

    def test_not_found_is_remembered_and_not_asked_again(self):
        self.tidy.run()
        asked = len(self.http.urls)
        self.assertGreater(asked, 0)
        self.assertEqual(self.lib.tidy_groups(), [])
        self.tidy.run()
        self.assertEqual(len(self.http.urls), asked)
        self.assertEqual(len(self.lib.tidy_groups(force=True)), 3)           # a full retry asks again

    def test_offline_gives_up_politely_and_remembers_nothing(self):
        self.http.down = True
        status = self.tidy.run()
        self.assertIn("Could not reach", status["error"])
        self.assertEqual(len(self.lib.tidy_groups()), 3)                       # still to do next time

    def test_requests_are_spaced_out(self):
        tidy = Tidy(self.lib, self.http, sleep=self.sleeps.append, clock=lambda: 0.0)
        tidy.run()
        self.assertTrue(any(abs(s - 1.1) < 0.01 for s in self.sleeps))

    def test_looked_up_year_survives_a_rescan_that_rereads_the_file(self):
        self.http.releases["all"] = [release("Night Drive", "Neon Kings")]
        self.tidy.run()
        path = self.music / "a" / "1.mp3"
        make_audio(path, title="One", artist="Neon Kings", album="Night Drive", seconds=2.0)   # file changed
        self.lib.scan()
        self.assertEqual(self.song("One")["year"], 1985)

    def test_cancel_stops_the_job(self):
        self.tidy.cancel()
        self.tidy._cancel.set()
        status = self.tidy.run()
        self.assertTrue(status["cancelled"])
        self.assertEqual(self.http.urls, [])


if __name__ == "__main__":
    unittest.main()
