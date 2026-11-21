import json
import random
import tempfile
import unittest
from pathlib import Path

from jukebox.engine import Engine, EngineError
from jukebox.library import decade_label
from tests.helpers import FakeClock, FakeLibrary, ManualPlayer


def make_engine(**settings):
    clock = FakeClock()
    player = ManualPlayer()
    engine = Engine(FakeLibrary(), player, None, "4321", clock=clock, rng=random.Random(7))
    engine.update_settings({"autofill": False, **settings})
    return engine, player, clock


def codes(engine, gid, is_host, song_id, kind="add"):
    try:
        engine.add_song(gid, is_host, song_id, kind)
    except EngineError as exc:
        return exc.code
    return "ok"


class QueueAndCreditsTest(unittest.TestCase):
    def test_first_song_starts_playing_and_costs_a_credit(self):
        engine, player, _ = make_engine()
        engine.add_song("guest-aaaa", False, 1)
        state = engine.state_for("guest-aaaa", False)
        self.assertEqual(state["now"]["id"], 1)
        self.assertEqual(state["credits"]["have"], 2)       # started with 3
        self.assertEqual(len(player.started), 1)
        self.assertEqual(engine.lib.plays, [1])

    def test_guest_runs_out_of_credits_then_one_comes_back(self):
        engine, _, clock = make_engine()
        for song_id in (1, 2, 3):
            self.assertEqual(codes(engine, "guest-aaaa", False, song_id), "ok")
        self.assertEqual(codes(engine, "guest-aaaa", False, 4), "credits")
        clock.advance(121)                                    # one credit arrives every 2 minutes
        self.assertEqual(codes(engine, "guest-aaaa", False, 4), "ok")

    def test_credit_error_says_when_the_next_one_arrives(self):
        engine, _, clock = make_engine()
        for song_id in (1, 2, 3):
            engine.add_song("guest-aaaa", False, song_id)
        clock.advance(30)
        with self.assertRaises(EngineError) as raised:
            engine.add_song("guest-aaaa", False, 4)
        self.assertIn("1:30", raised.exception.message)

    def test_credits_never_exceed_the_maximum(self):
        engine, _, clock = make_engine()
        engine.state_for("guest-aaaa", False)
        clock.advance(10 * 3600)
        self.assertEqual(engine.state_for("guest-aaaa", False)["credits"],
                         {"have": 5, "max": 5, "next_in": None})

    def test_guests_have_separate_credits(self):
        engine, _, _ = make_engine()
        for song_id in (1, 2, 3):
            engine.add_song("guest-aaaa", False, song_id)
        self.assertEqual(codes(engine, "guest-bbbb", False, 4), "ok")

    def test_play_next_costs_two_and_jumps_the_line(self):
        engine, _, _ = make_engine()
        engine.add_song("guest-aaaa", False, 1)          # starts playing, 2 credits left
        engine.add_song("guest-bbbb", False, 2)
        engine.add_song("guest-aaaa", False, 3, "next")  # costs 2
        self.assertEqual([it["id"] for it in engine.queue], [3, 2])
        self.assertEqual(engine.state_for("guest-aaaa", False)["credits"]["have"], 0)

    def test_play_next_not_possible_when_it_costs_more_than_a_guest_can_hold(self):
        engine, _, _ = make_engine(credit_max=1)
        engine.add_song("guest-aaaa", False, 1)
        self.assertEqual(codes(engine, "guest-aaaa", False, 2, "next"), "credits")

    def test_duplicates_are_refused_without_charging(self):
        engine, _, _ = make_engine()
        engine.add_song("guest-aaaa", False, 1)
        self.assertEqual(codes(engine, "guest-bbbb", False, 1), "queued")
        engine.add_song("guest-aaaa", False, 2)
        self.assertEqual(codes(engine, "guest-bbbb", False, 2), "queued")
        self.assertEqual(engine.state_for("guest-bbbb", False)["credits"]["have"], 3)

    def test_unknown_songs_and_bad_ids(self):
        engine, _, _ = make_engine()
        for bad in (999, "1", None, True, 1.5):
            self.assertEqual(codes(engine, "guest-aaaa", False, bad), "no_song", bad)

    def test_free_play_costs_nothing(self):
        engine, _, _ = make_engine(free_play=True)
        for song_id in (1, 2, 3, 4, 5):
            engine.add_song("guest-aaaa", False, song_id)
        self.assertEqual(engine.state_for("guest-aaaa", False)["credits"]["have"], 3)

    def test_host_plays_free_and_items_are_marked_host(self):
        engine, _, _ = make_engine()
        engine.add_song("host-device", True, 1)
        engine.add_song("host-device", True, 2)
        self.assertEqual(engine.state_for("guest-aaaa", False)["queue"][0]["by"], "host")
        self.assertEqual(engine.state_for("host-device", True)["credits"]["have"], 3)

    def test_queue_limit(self):
        engine, _, _ = make_engine(free_play=True, queue_max=2)
        engine.add_song("g-aaaaaaaa", False, 1)            # playing
        engine.add_song("g-aaaaaaaa", False, 2)
        engine.add_song("g-aaaaaaaa", False, 3)
        self.assertEqual(codes(engine, "g-aaaaaaaa", False, 4), "full")

    def test_remove_own_song_but_not_someone_elses(self):
        engine, _, _ = make_engine()
        engine.add_song("guest-aaaa", False, 1)
        engine.add_song("guest-aaaa", False, 2)
        qid = engine.queue[0]["qid"]
        with self.assertRaises(EngineError) as raised:
            engine.remove("guest-bbbb", False, qid)
        self.assertEqual(raised.exception.code, "forbidden")
        engine.remove("guest-aaaa", False, qid)
        self.assertEqual(engine.queue, [])
        with self.assertRaises(EngineError):
            engine.remove("guest-aaaa", False, qid)         # already gone

    def test_host_can_remove_anything(self):
        engine, _, _ = make_engine()
        engine.add_song("guest-aaaa", False, 1)
        engine.add_song("guest-aaaa", False, 2)
        engine.remove("host-device", True, engine.queue[0]["qid"])
        self.assertEqual(engine.queue, [])

    def test_state_marks_ownership_from_each_guests_point_of_view(self):
        engine, _, _ = make_engine()
        engine.add_song("guest-aaaa", False, 1)
        engine.add_song("guest-aaaa", False, 2)
        engine.add_song("guest-bbbb", False, 3)
        mine = [it["by"] for it in engine.state_for("guest-aaaa", False)["queue"]]
        theirs = [it["by"] for it in engine.state_for("guest-bbbb", False)["queue"]]
        self.assertEqual((mine, theirs), (["you", "other"], ["other", "you"]))


class PlaybackFlowTest(unittest.TestCase):
    def test_next_song_starts_when_the_current_one_finishes(self):
        engine, player, _ = make_engine(free_play=True)
        engine.add_song("g-aaaaaaaa", False, 1)
        engine.add_song("g-aaaaaaaa", False, 2)
        engine.tick()
        self.assertEqual(engine.current["id"], 1)
        player.done = True
        engine.tick()
        self.assertEqual(engine.current["id"], 2)
        self.assertEqual(engine.queue, [])
        player.done = True
        engine.tick()
        self.assertIsNone(engine.current)                    # autofill is off, so silence

    def test_votes_skip_the_song_once_enough_guests_agree(self):
        engine, _, _ = make_engine(free_play=True)
        engine.add_song("g-aaaaaaaa", False, 1)
        engine.add_song("g-aaaaaaaa", False, 2)
        self.assertFalse(engine.vote("guest-aaaa"))
        with self.assertRaises(EngineError):
            engine.vote("guest-aaaa")                        # no double voting
        self.assertFalse(engine.vote("guest-bbbb"))
        self.assertTrue(engine.vote("guest-cccc"))           # third vote skips
        self.assertEqual(engine.current["id"], 2)
        self.assertEqual(engine.state_for("guest-aaaa", False)["votes"], 0)   # votes reset for the new song

    def test_vote_when_idle(self):
        engine, _, _ = make_engine()
        with self.assertRaises(EngineError):
            engine.vote("guest-aaaa")

    def test_host_controls(self):
        engine, player, _ = make_engine(free_play=True)
        engine.add_song("g-aaaaaaaa", False, 1)
        engine.add_song("g-aaaaaaaa", False, 2)
        engine.control("pause")
        self.assertTrue(player.is_paused)
        self.assertFalse(engine.state_for("g-aaaaaaaa", False)["now"]["playing"])
        player.done = True
        engine.tick()
        self.assertEqual(engine.current["id"], 1)            # paused songs do not advance
        engine.control("toggle")
        self.assertFalse(player.is_paused)
        engine.control("skip")
        self.assertEqual(engine.current["id"], 2)
        engine.add_song("g-aaaaaaaa", False, 3)
        engine.control("clear")
        self.assertEqual(engine.queue, [])
        with self.assertRaises(EngineError):
            engine.control("explode")

    def test_unplayable_file_is_skipped(self):
        engine, player, _ = make_engine(free_play=True)
        engine.add_song("g-aaaaaaaa", False, 1)
        engine.add_song("g-aaaaaaaa", False, 2)
        engine.add_song("g-aaaaaaaa", False, 3)
        original = player.start

        def flaky(path, duration):
            if path.name == "Slow Burn.mp3":
                raise OSError("file is gone")
            original(path, duration)

        player.start = flaky
        engine.control("skip")
        self.assertEqual(engine.current["id"], 3)


class AutofillTest(unittest.TestCase):
    def make(self, **settings):
        clock = FakeClock()
        engine = Engine(FakeLibrary(), ManualPlayer(), None, "4321", clock=clock, rng=random.Random(3))
        engine.update_settings(settings)
        return engine

    def test_starts_playing_by_itself_and_lines_up_the_next_song(self):
        engine = self.make()
        engine.tick()
        self.assertIsNotNone(engine.current)
        self.assertTrue(engine.current["auto"])
        self.assertEqual(len(engine.queue), 1)
        self.assertTrue(engine.queue[0]["auto"])

    def test_a_guests_pick_replaces_the_auto_song(self):
        engine = self.make()
        engine.tick()
        engine.add_song("guest-aaaa", False, 8)
        self.assertEqual([it["id"] for it in engine.queue if not it["auto"]], [8])
        self.assertEqual(engine.queue[0]["id"], 8)
        self.assertEqual(len([it for it in engine.queue if it["auto"]]), 0)

    def test_it_does_not_repeat_recent_songs_while_others_are_available(self):
        engine = self.make()
        engine.tick()
        seen = [engine.current["id"]]
        for _ in range(7):
            engine.control("skip")
            seen.append(engine.current["id"])
        self.assertEqual(len(set(seen)), 8)                  # all eight songs, no repeats

    def test_party_mode_limits_auto_fill_to_a_decade_or_genre(self):
        engine = self.make(mode="dec:80s")
        engine.tick()                                        # starts the first auto song
        picks = {engine.current["id"]}
        for _ in range(12):
            engine.control("skip")
            picks.add(engine.current["id"])
        years = {decade_label(engine.lib.get(i)["year"]) for i in picks}
        self.assertEqual(years, {"80s"})

        engine.update_settings({"mode": "gen:Dance"})
        genres = set()
        for _ in range(8):
            engine.control("skip")
            genres.add(engine.lib.get(engine.current["id"])["genre"])
        self.assertEqual(genres, {"Dance"})

    def test_a_mode_with_no_matching_songs_falls_back_to_anything(self):
        engine = self.make()
        engine.lib._songs[:] = [s for s in engine.lib._songs if s["genre"] != "Country"]
        engine.settings["mode"] = "gen:Country"
        engine.tick()
        self.assertIsNotNone(engine.current)

    def test_changing_mode_replaces_the_lined_up_song(self):
        engine = self.make()
        engine.tick()
        engine.update_settings({"mode": "gen:Dance"})
        self.assertEqual(engine.lib.get(engine.queue[0]["id"])["genre"], "Dance")

    def test_empty_library_stays_quiet(self):
        engine = Engine(FakeLibrary(songs=[]), ManualPlayer(), None, "4321")
        engine.tick()
        engine.tick()
        self.assertIsNone(engine.current)


class HostAndSettingsTest(unittest.TestCase):
    def test_pin_login_and_logout(self):
        engine, _, clock = make_engine()
        token = engine.host_login("4321", "10.0.0.5")
        self.assertTrue(engine.is_host(token))
        self.assertFalse(engine.is_host("nonsense"))
        self.assertFalse(engine.is_host(None))
        engine.host_logout(token)
        self.assertFalse(engine.is_host(token))

    def test_token_expires(self):
        engine, _, clock = make_engine()
        token = engine.host_login("4321", "10.0.0.5")
        clock.advance(13 * 3600)
        self.assertFalse(engine.is_host(token))

    def test_wrong_pins_lock_the_address_out_for_a_minute(self):
        engine, _, clock = make_engine()
        for _ in range(5):
            with self.assertRaises(EngineError) as raised:
                engine.host_login("0000", "10.0.0.9")
            self.assertEqual(raised.exception.code, "bad_pin")
        with self.assertRaises(EngineError) as raised:
            engine.host_login("4321", "10.0.0.9")            # even the right PIN is refused while locked
        self.assertEqual(raised.exception.code, "locked")
        self.assertTrue(engine.is_host(engine.host_login("4321", "10.0.0.10")))   # other addresses unaffected
        clock.advance(61)
        self.assertTrue(engine.is_host(engine.host_login("4321", "10.0.0.9")))

    def test_non_string_pin(self):
        engine, _, _ = make_engine()
        for bad in (4321, None, ["4321"], {"pin": 1}):
            with self.assertRaises(EngineError):
                engine.host_login(bad, "10.0.0.5")

    def test_settings_are_validated(self):
        engine, player, _ = make_engine()
        for bad in ({"volume": 101}, {"volume": "loud"}, {"volume": True}, {"nope": 1}, {"autofill": "yes"},
                    {"credit_max": 0}, {"mode": "dec:1800s"}, {"vote_need": 2.5}):
            with self.assertRaises(EngineError, msg=str(bad)):
                engine.update_settings(bad)
        engine.update_settings({"volume": 40, "vote_need": 2})
        self.assertEqual((engine.settings["volume"], engine.settings["vote_need"], player.volume), (40, 2, 40))

    def test_settings_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            first = Engine(FakeLibrary(), ManualPlayer(), path, "4321")
            first.update_settings({"free_play": True, "refill_min": 5, "mode": "gen:Rock"})
            second = Engine(FakeLibrary(), ManualPlayer(), path, "4321")
            self.assertEqual((second.settings["free_play"], second.settings["refill_min"], second.settings["mode"]),
                             (True, 5, "gen:Rock"))

    def test_a_damaged_settings_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{not json")
            engine = Engine(FakeLibrary(), ManualPlayer(), path, "4321")
            self.assertEqual(engine.settings["volume"], 75)
            path.write_text(json.dumps({"volume": 9000, "free_play": True}))
            engine = Engine(FakeLibrary(), ManualPlayer(), path, "4321")
            self.assertEqual((engine.settings["volume"], engine.settings["free_play"]), (75, True))

    def test_lowering_the_credit_maximum_trims_guests(self):
        engine, _, _ = make_engine()
        engine.state_for("guest-aaaa", False)
        engine.update_settings({"credit_max": 2})
        self.assertEqual(engine.state_for("guest-aaaa", False)["credits"]["have"], 2)

    def test_refill_gives_everyone_full_credits(self):
        engine, _, _ = make_engine()
        for song_id in (1, 2, 3):
            engine.add_song("guest-aaaa", False, song_id)
        engine.control("refill")
        self.assertEqual(engine.state_for("guest-aaaa", False)["credits"]["have"], 5)

    def test_host_sees_settings_and_modes_but_guests_do_not(self):
        engine, _, _ = make_engine()
        guest = engine.state_for("guest-aaaa", False)
        host = engine.state_for("host-device", True)
        self.assertNotIn("settings", guest)
        self.assertEqual(host["settings"]["credit_max"], 5)
        ids = [m["id"] for m in host["modes"]]
        self.assertIn("all", ids)
        self.assertIn("dec:80s", ids)
        self.assertIn("gen:Dance", ids)


if __name__ == "__main__":
    unittest.main()
