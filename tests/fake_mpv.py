"""A stand-in for mpv that speaks just enough of its JSON command protocol for the player tests.

Environment: FAKE_MPV_SECONDS = how long each song lasts, FAKE_MPV_LOG = file that records every command received.
"""
import json
import os
import socket
import sys
import threading
import time

sock_path = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--input-ipc-server="))
seconds = float(os.environ.get("FAKE_MPV_SECONDS", "0.6"))
log_path = os.environ.get("FAKE_MPV_LOG")

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(sock_path)
server.listen(1)
conn, _ = server.accept()
send_lock = threading.Lock()
state = {"playing": False, "paused": False, "token": 0}


def emit(obj):
    with send_lock:
        try:
            conn.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            os._exit(0)


def play(token):
    emit({"event": "start-file"})
    emit({"event": "file-loaded"})
    emit({"event": "property-change", "id": 2, "name": "duration", "data": seconds})
    pos = 0.0
    while pos < seconds:
        time.sleep(0.05)
        if state["token"] != token:
            return
        if not state["paused"]:
            pos += 0.05
            emit({"event": "property-change", "id": 1, "name": "time-pos", "data": round(pos, 2)})
    if state["token"] == token:
        state["playing"] = False
        emit({"event": "end-file", "reason": "eof"})


def end_current(reason):
    if state["playing"]:
        state["playing"] = False
        state["token"] += 1
        emit({"event": "end-file", "reason": reason})


for line in conn.makefile("r"):
    cmd = json.loads(line)["command"]
    if log_path:
        with open(log_path, "a") as fh:
            fh.write(json.dumps(cmd) + "\n")
    name = cmd[0]
    if name == "loadfile":
        end_current("stop")
        state["token"] += 1
        if "broken" in cmd[1]:
            emit({"event": "start-file"})
            emit({"event": "end-file", "reason": "error"})
        else:
            state["playing"] = True
            threading.Thread(target=play, args=(state["token"],), daemon=True).start()
    elif name == "set_property" and cmd[1] == "pause":
        state["paused"] = bool(cmd[2])
    elif name == "stop":
        end_current("stop")
    elif name == "quit":
        break
