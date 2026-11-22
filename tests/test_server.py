import random
import tempfile
import unittest
from pathlib import Path

from jukebox.engine import Engine
from jukebox.server import create_app
from tests.helpers import FakeClock, FakeLibrary, ManualPlayer

GUEST = {"X-Guest-Id": "guest-1234abcd"}
OTHER = {"X-Guest-Id": "guest-9999zzzz"}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "static").mkdir()
        (base / "static" / "index.html").write_text("<title>jukebox</title>")
        (base / "covers").mkdir()
        (base / "covers" / "0123456789abcdef.jpg").write_bytes(b"\xff\xd8fakejpeg")
        (base / "secret.txt").write_text("secret")
        self.library = FakeLibrary(covers_dir=base / "covers")
        self.engine = Engine(self.library, ManualPlayer(), None, "4321", clock=FakeClock(), rng=random.Random(1))
        self.engine.update_settings({"autofill": False})
        self.client = create_app(self.engine, self.library, base / "static").test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, path, data=None, headers=None):
        return self.client.post(path, json=data if data is not None else {}, headers=headers or GUEST)

    def host_headers(self):
        token = self.post("/api/host/login", {"pin": "4321"}).get_json()["token"]
        return {**GUEST, "X-Host-Token": token}

    # ---------- pages and files ----------

    def test_index_and_health(self):
        self.assertIn(b"jukebox", self.client.get("/").data)
        health = self.client.get("/api/health").get_json()
        self.assertEqual((health["ok"], health["songs"]), (True, 8))

    def test_songs_have_no_paths_and_support_etags(self):
        resp = self.client.get("/api/songs")
        data = resp.get_json()
        self.assertEqual(len(data["songs"]), 8)
        self.assertNotIn("path", data["songs"][0])
        again = self.client.get("/api/songs", headers={"If-None-Match": resp.headers["ETag"]})
        self.assertEqual(again.status_code, 304)

    def test_covers_are_served_only_by_safe_names(self):
        self.assertEqual(self.client.get("/covers/0123456789abcdef.jpg").status_code, 200)
        for bad in ("../secret.txt", "..%2Fsecret.txt", "secret.txt", "0123456789abcdef.png", "x.jpg"):
            self.assertEqual(self.client.get("/covers/" + bad).status_code, 404, bad)

    def test_unknown_api_paths_return_json(self):
        resp = self.client.get("/api/nothing")
        self.assertEqual((resp.status_code, resp.get_json()["ok"]), (404, False))

    def test_api_responses_are_not_cached(self):
        self.assertEqual(self.client.get("/api/state").headers["Cache-Control"], "no-store")

    # ---------- guests ----------

    def test_adding_a_song_through_the_api(self):
        resp = self.post("/api/queue", {"song_id": 3})
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["state"]["now"]["id"], 3)
        self.assertEqual(data["state"]["credits"]["have"], 2)

    def test_refusals_come_back_as_json_with_a_message(self):
        self.post("/api/queue", {"song_id": 3})
        resp = self.post("/api/queue", {"song_id": 3}, OTHER)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["code"], "queued")
        bad = self.post("/api/queue", {"song_id": "three"})
        self.assertEqual((bad.status_code, bad.get_json()["code"]), (404, "no_song"))

    def test_out_of_credits_status(self):
        for song_id in (1, 2, 3):
            self.post("/api/queue", {"song_id": song_id})
        resp = self.post("/api/queue", {"song_id": 4})
        self.assertEqual((resp.status_code, resp.get_json()["code"]), (402, "credits"))

    def test_garbage_bodies_do_not_crash(self):
        for payload in ("nonsense", "[1,2]", "", "null"):
            resp = self.client.post("/api/queue", data=payload, headers={**GUEST, "Content-Type": "application/json"})
            self.assertIn(resp.status_code, (400, 404))
        huge = self.client.post("/api/queue", data="x" * 20000, headers={**GUEST, "Content-Type": "application/json"})
        self.assertEqual(huge.status_code, 413)

    def test_guest_ids_are_validated(self):
        weird = {"X-Guest-Id": "../../etc/passwd"}
        resp = self.client.get("/api/state", headers=weird)
        self.assertEqual(resp.status_code, 200)             # falls back to the network address

    def test_remove_and_vote(self):
        self.post("/api/queue", {"song_id": 1})
        self.post("/api/queue", {"song_id": 2})
        qid = self.engine.queue[0]["qid"]
        denied = self.post("/api/queue/remove", {"qid": qid}, OTHER)
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(self.post("/api/queue/remove", {"qid": qid}).get_json()["ok"])
        vote = self.post("/api/vote").get_json()
        self.assertEqual((vote["skipped"], vote["state"]["votes"], vote["state"]["voted"]), (False, 1, True))

    # ---------- host ----------

    def test_host_endpoints_need_the_token(self):
        for path in ("/api/host/control", "/api/host/settings", "/api/host/rescan"):
            self.assertEqual(self.post(path, {"action": "skip"}).status_code, 403, path)

    def test_login_wrong_pin(self):
        resp = self.post("/api/host/login", {"pin": "nope"})
        self.assertEqual((resp.status_code, resp.get_json()["code"]), (403, "bad_pin"))

    def test_host_flow(self):
        headers = self.host_headers()
        self.assertTrue(self.client.get("/api/state", headers=headers).get_json()["state"]["host"])
        self.post("/api/queue", {"song_id": 1}, headers)
        self.post("/api/queue", {"song_id": 2}, headers)
        self.assertEqual(self.post("/api/host/control", {"action": "skip"}, headers).get_json()["state"]["now"]["id"], 2)
        settings = self.post("/api/host/settings", {"free_play": True, "volume": 30}, headers).get_json()["state"]
        self.assertTrue(settings["free_play"])
        self.assertEqual(settings["settings"]["volume"], 30)
        self.assertEqual(self.post("/api/host/settings", {"volume": 500}, headers).status_code, 400)
        self.assertTrue(self.post("/api/host/rescan", {}, headers).get_json()["started"])
        self.assertEqual(self.library.scans, 1)
        # Guests see public information only
        public = self.client.get("/api/state", headers=GUEST).get_json()["state"]
        self.assertNotIn("settings", public)
        self.assertTrue(public["free_play"])

    def test_logout_ends_the_session(self):
        headers = self.host_headers()
        self.post("/api/host/logout", {}, headers)
        self.assertEqual(self.post("/api/host/control", {"action": "skip"}, headers).status_code, 403)

    def test_tidy_endpoints_are_host_only_and_work(self):
        class FakeTidy:
            def __init__(self): self.started = 0; self.cancelled = 0
            def status(self): return {"running": self.started > 0, "done": 0, "total": 2}
            def preview(self, force=False): return {"albums": 2, "seconds": 6}
            def start(self, force=False, log=print): self.started += 1; return True
            def cancel(self): self.cancelled += 1
        for path in ("/api/host/tidy", "/api/host/tidy/preview", "/api/host/tidy/cancel"):
            self.assertEqual(self.post(path, {}).status_code, 403)               # guests are refused
        headers = self.host_headers()
        self.assertEqual(self.post("/api/host/tidy", {}, headers).status_code, 404)   # not set up
        tidy = self.engine.tidy = FakeTidy()
        self.assertEqual(self.post("/api/host/tidy/preview", {}, headers).get_json()["albums"], 2)
        reply = self.post("/api/host/tidy", {"force": True}, headers).get_json()
        self.assertTrue(reply["started"])
        self.assertTrue(reply["state"]["tidy"]["running"])
        self.post("/api/host/tidy/cancel", {}, headers)
        self.assertEqual((tidy.started, tidy.cancelled), (1, 1))
        self.assertNotIn("tidy", self.client.get("/api/state", headers=GUEST).get_json()["state"])

    def test_never_play_endpoint(self):
        self.assertEqual(self.post("/api/host/never-play", {"song_id": 2}).status_code, 403)    # guests refused
        headers = self.host_headers()
        reply = self.post("/api/host/never-play", {"song_id": 2, "banned": True}, headers).get_json()
        self.assertEqual(reply["changed"], 1)
        self.assertEqual(reply["state"]["banned_count"], 1)
        self.assertEqual(self.post("/api/queue", {"song_id": 2}).status_code, 403)
        songs = self.client.get("/api/songs").get_json()["songs"]
        self.assertTrue(next(s for s in songs if s["id"] == 2)["banned"])
        self.assertEqual(self.post("/api/host/never-play", {"song_id": 999}, headers).status_code, 404)
        self.post("/api/host/never-play", {"song_id": 2, "banned": False}, headers)
        self.assertEqual(self.post("/api/queue", {"song_id": 2}).status_code, 200)

    def test_popular(self):
        self.post("/api/queue", {"song_id": 5})
        self.assertEqual(self.client.get("/api/popular").get_json()["ids"], [5])


if __name__ == "__main__":
    unittest.main()
