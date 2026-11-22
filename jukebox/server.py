"""The web layer: serves the jukebox page and a small JSON API for it."""
from __future__ import annotations

import re
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import __version__
from .engine import Engine, EngineError

GUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
COVER_NAME = re.compile(r"^[0-9a-f]{16}\.jpg$")


def create_app(engine: Engine, library, static_dir: Path | str) -> Flask:
    static_dir = Path(static_dir)
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

    # ---------- who is asking ----------

    def guest_id() -> str:
        """Each browser keeps a random id. Without one, fall back to the network address."""
        gid = request.headers.get("X-Guest-Id", "")
        return gid if GUEST_ID.match(gid) else "ip-" + (request.remote_addr or "unknown")

    def host_token() -> str:
        return request.headers.get("X-Host-Token", "")

    def is_host() -> bool:
        return engine.is_host(host_token())

    def body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def state():
        return engine.state_for(guest_id(), is_host())

    def require_host():
        if not is_host():
            raise EngineError("not_host", "Unlock the Host panel first.", 403)

    # ---------- errors and headers ----------

    @app.errorhandler(EngineError)
    def engine_error(err: EngineError):
        return jsonify(ok=False, error=err.message, code=err.code), err.status

    @app.errorhandler(404)
    def not_found(_err):
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="Not found.", code="not_found"), 404
        return "Not found", 404

    @app.errorhandler(413)
    def too_big(_err):
        return jsonify(ok=False, error="Request too large.", code="too_big"), 413

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---------- pages and files ----------

    @app.get("/")
    def index():
        resp = send_from_directory(static_dir, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/covers/<name>")
    def cover(name: str):
        if not COVER_NAME.match(name):
            return "Not found", 404
        return send_from_directory(library.covers_dir, name, max_age=86400)

    # ---------- read-only API ----------

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, version=__version__, songs=library.count())

    @app.get("/api/songs")
    def songs():
        resp = jsonify(lib=library.version, songs=library.api_songs())
        resp.set_etag(f"lib{library.version}")
        resp.headers["Cache-Control"] = "no-cache"
        return resp.make_conditional(request)

    @app.get("/api/popular")
    def popular():
        return jsonify(ok=True, ids=library.popular())

    @app.get("/api/state")
    def get_state():
        return jsonify(ok=True, state=state())

    # ---------- guest actions ----------

    @app.post("/api/queue")
    def add_to_queue():
        data = body()
        engine.add_song(guest_id(), is_host(), data.get("song_id"), data.get("kind", "add"))
        return jsonify(ok=True, state=state())

    @app.post("/api/queue/remove")
    def remove_from_queue():
        engine.remove(guest_id(), is_host(), body().get("qid"))
        return jsonify(ok=True, state=state())

    @app.post("/api/vote")
    def vote():
        skipped = engine.vote(guest_id())
        return jsonify(ok=True, skipped=skipped, state=state())

    # ---------- host actions ----------

    @app.post("/api/host/login")
    def host_login():
        token = engine.host_login(body().get("pin"), request.remote_addr or "unknown")
        return jsonify(ok=True, token=token, state=engine.state_for(guest_id(), True))

    @app.post("/api/host/logout")
    def host_logout():
        engine.host_logout(host_token())
        return jsonify(ok=True, state=engine.state_for(guest_id(), False))

    @app.post("/api/host/control")
    def host_control():
        require_host()
        engine.control(str(body().get("action", "")))
        return jsonify(ok=True, state=state())

    @app.post("/api/host/settings")
    def host_settings():
        require_host()
        engine.update_settings(body())
        return jsonify(ok=True, state=state())

    @app.post("/api/host/rescan")
    def host_rescan():
        require_host()
        started = library.scan_async(log=print)
        return jsonify(ok=True, started=started, state=state())

    @app.post("/api/host/never-play")
    def host_never_play():
        require_host()
        data = body()
        scope = "album" if data.get("scope") == "album" else "song"
        changed = engine.set_never_play(data.get("song_id"), scope, bool(data.get("banned", True)))
        return jsonify(ok=True, changed=changed, state=state())

    def require_tidy():
        if engine.tidy is None:
            raise EngineError("no_tidy", "Tidy my library is not available.", 404)
        return engine.tidy

    @app.post("/api/host/tidy")
    def host_tidy():
        require_host()
        tidy = require_tidy()
        data = body()
        started = tidy.start(force=bool(data.get("force")), log=print)
        return jsonify(ok=True, started=started, state=state())

    @app.post("/api/host/tidy/preview")
    def host_tidy_preview():
        require_host()
        return jsonify(ok=True, **require_tidy().preview(force=bool(body().get("force"))))

    @app.post("/api/host/tidy/cancel")
    def host_tidy_cancel():
        require_host()
        require_tidy().cancel()
        return jsonify(ok=True, state=state())

    return app
