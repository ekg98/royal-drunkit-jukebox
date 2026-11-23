import tempfile
import time
import unittest
from pathlib import Path

from jukebox.library import Library, decade_label, decade_start
from tests.helpers import build_sample_library, make_audio


class DecadeTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(decade_label(1985), "80s")
        self.assertEqual(decade_label(2003), "00s")
        self.assertEqual(decade_label(2024), "20s")
        self.assertEqual(decade_label(1962), "60s")
        self.assertEqual(decade_label(1942), "1940s")
        self.assertIsNone(decade_label(None))

    def test_sorting_by_decade(self):
        labels = ["20s", "00s", "1940s", "80s", "50s"]
        self.assertEqual(sorted(labels, key=decade_start), ["1940s", "50s", "80s", "00s", "20s"])


class ScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.music = build_sample_library(base / "music")
        cls.lib = Library(cls.music, base / "data")
        cls.report = cls.lib.scan()
        cls.by_title = {s["title"]: s for s in cls.lib.all()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cover_file(self, song):
        return self.lib.covers_dir / song["cover"]

    def test_counts(self):
        self.assertEqual(self.report.added, 8)
        self.assertEqual(self.lib.count(), 8)
        self.assertEqual(len(self.report.unreadable), 1)
        self.assertTrue(self.report.unreadable[0].endswith("broken.mp3"))

    def test_tags_and_embedded_cover(self):
        song = self.by_title["Neon Rain"]
        self.assertEqual((song["artist"], song["album"], song["genre"], song["year"]),
                         ("The Neon Kings", "Night Drive", "Rock", 1985))
        self.assertIsNotNone(song["cover"])
        self.assertTrue(self.cover_file(song).stat().st_size > 0)

    def test_album_cover_is_shared_with_tracks_that_have_none(self):
        self.assertEqual(self.by_title["Chrome Highway"]["cover"], self.by_title["Neon Rain"]["cover"])

    def test_folder_art_is_found_case_insensitively(self):
        song = self.by_title["Slow Burn"]
        self.assertIsNotNone(song["cover"])
        self.assertTrue(self.cover_file(song).exists())
        self.assertNotEqual(song["cover"], self.by_title["Neon Rain"]["cover"])

    def test_songs_without_art_have_no_cover(self):
        self.assertIsNone(self.by_title["Dusty Highway"]["cover"])

    def test_genres_are_tidied_and_merged(self):
        # "rock", "Rock" and the old numeric "(17)" are all one genre
        self.assertEqual(self.by_title["Chrome Highway"]["genre"], "Rock")
        self.assertEqual(self.by_title["Old School"]["genre"], "Rock")
        self.assertEqual(dict(self.lib.genres())["Rock"], 3)
        self.assertEqual(self.by_title["Some Song"]["genre"], "Unknown")
        self.assertNotIn("Unknown", dict(self.lib.genres()))

    def test_file_name_fallbacks(self):
        self.assertEqual(self.by_title["Some Song"]["artist"], "The Band")
        self.assertEqual(self.by_title["lonely road"]["artist"], "Unknown Artist")

    def test_decades(self):
        self.assertEqual([label for label, _ in self.lib.decades()], ["70s", "80s", "90s", "00s", "10s"])

    def test_api_songs_hide_paths(self):
        songs = self.lib.api_songs()
        self.assertEqual(len(songs), 8)
        self.assertTrue(all("path" not in s for s in songs))

    def test_plays_and_popular(self):
        song = self.by_title["Neon Rain"]
        self.assertEqual(self.lib.popular(), [])
        self.lib.record_play(song["id"])
        self.lib.record_play(song["id"])
        self.lib.record_play(self.by_title["Slow Burn"]["id"])
        self.assertEqual(self.lib.popular()[0], song["id"])
        self.assertEqual(self.lib.get(song["id"])["plays"], 2)


class RescanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.music = build_sample_library(base / "music")
        self.lib = Library(self.music, base / "data")
        self.first = self.lib.scan()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unchanged_library_is_skipped_quickly(self):
        version = self.lib.version
        report = self.lib.scan()
        self.assertEqual((report.added, report.updated, report.removed, report.unchanged), (0, 0, 0, 8))
        self.assertEqual(self.lib.version, version)

    def test_added_removed_and_changed_files(self):
        version = self.lib.version
        before = time.time()
        make_audio(self.music / "Misc" / "brand_new.mp3", title="Brand New", artist="Fresh Act", date="2024")
        (self.music / "Misc" / "oldschool.mp3").unlink()
        make_audio(self.music / "Misc" / "dusty.ogg", title="Dusty Highway (Remix)", artist="Hollow Pines",
                   genre="Country", date="1994", seconds=2)
        report = self.lib.scan()
        self.assertEqual((report.added, report.updated, report.removed), (1, 1, 1))
        titles = {s["title"]: s for s in self.lib.all()}
        self.assertIn("Brand New", titles)
        self.assertNotIn("Old School", titles)
        self.assertIn("Dusty Highway (Remix)", titles)
        self.assertGreaterEqual(titles["Brand New"]["added"], int(before))     # new files count as "just added"
        self.assertGreater(self.lib.version, version)

    def test_first_scan_does_not_mark_everything_new(self):
        # Sample files were just created, so back-date one and rebuild the catalog from scratch.
        import os
        old = time.time() - 90 * 86400
        path = self.music / "Misc" / "dusty.ogg"
        os.utime(path, (old, old))
        fresh = Library(self.music, Path(self.tmp.name) / "data2")
        fresh.scan()
        dusty = next(s for s in fresh.all() if s["title"] == "Dusty Highway")
        self.assertAlmostEqual(dusty["added"], old, delta=2)

    def test_cover_added_after_the_first_scan_is_found_on_rescan(self):
        song = next(s for s in self.lib.all() if s["title"] == "Dusty Highway")
        self.assertIsNone(song["cover"])
        version = self.lib.version
        from tests.helpers import make_image
        make_image(self.music / "Misc" / "cover.jpg", "0x44aa66")
        report = self.lib.scan()
        self.assertGreaterEqual(report.covers_linked, 1)
        self.assertIsNotNone(self.lib.get(song["id"])["cover"])
        self.assertGreater(self.lib.version, version)

    def test_play_history_survives_a_rescan(self):
        song = next(s for s in self.lib.all() if s["title"] == "Neon Rain")
        self.lib.record_play(song["id"])
        self.lib.scan()
        self.assertEqual(self.lib.get(song["id"])["plays"], 1)

    def test_scan_async_runs_once_at_a_time(self):
        self.assertTrue(self.lib.scan_async())
        self.assertFalse(self.lib.scan_async())     # second request while the first is running
        for _ in range(100):
            if not self.lib.scan_status()["running"] and self.lib.scan_status()["last_report"]:
                break
            time.sleep(0.1)
        self.assertFalse(self.lib.scan_status()["running"])


class NeverPlayStorageTest(unittest.TestCase):
    def test_ban_survives_a_rescan_and_a_restart_and_an_old_database_is_upgraded(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            music = build_sample_library(base / "music")
            lib = Library(music, base / "data")
            lib.scan()
            target = next(s for s in lib.all() if s["title"] == "Neon Rain")
            album = lib.album_ids(target["id"])
            self.assertEqual(len(album), 2)                          # Neon Rain and Chrome Highway
            self.assertEqual(lib.set_banned(album, True), 2)
            self.assertEqual(lib.set_banned(album, True), 0)         # nothing new
            version = lib.version
            lib.scan()                                               # unchanged files: ban must stay
            self.assertEqual(lib.banned_count(), 2)
            again = Library(music, base / "data")                    # a restart
            self.assertEqual(again.banned_count(), 2)
            self.assertTrue(all("banned" in s for s in again.api_songs()))
            self.assertEqual(again.set_banned(album, False), 2)
            self.assertEqual(again.banned_count(), 0)
            # a catalog from before this feature has no banned column
            conn = sqlite3.connect(base / "data" / "library.db")
            conn.execute("ALTER TABLE songs DROP COLUMN banned")
            conn.commit(); conn.close()
            upgraded = Library(music, base / "data")
            self.assertEqual(upgraded.banned_count(), 0)
            self.assertGreater(len(upgraded.all()), 3)


class RatingsStorageTest(unittest.TestCase):
    def test_votes_are_saved_counted_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            music = build_sample_library(base / "music")
            lib = Library(music, base / "data")
            lib.scan()
            a, b = [s["id"] for s in lib.all()[:2]]
            lib.rate(a, "g1", 1); lib.rate(a, "g2", 1); lib.rate(a, "g3", -1); lib.rate(b, "g1", -1)
            self.assertEqual(lib.rating_of(a), (2, 1))
            self.assertEqual(lib.my_votes("g1"), {a: 1, b: -1})
            lib.rate(a, "g1", -1)                                   # change of mind replaces, never adds
            self.assertEqual(lib.rating_of(a), (1, 2))
            lib.rate(a, "g3", 0)                                    # withdrawn
            self.assertEqual(lib.rating_of(a), (1, 1))
            again = Library(music, base / "data")                   # restart keeps the votes
            self.assertEqual(again.rating_of(a), (1, 1))
            self.assertEqual(again.rating_totals(), {a: [1, 1], b: [0, 1]})
            self.assertEqual(again.clear_ratings(), 3)
            self.assertEqual(again.rating_totals(), {})


if __name__ == "__main__":
    unittest.main()
