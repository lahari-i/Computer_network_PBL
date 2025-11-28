import os
import uuid
import math
import json
from datetime import datetime
from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO, emit

# -----------------------
# CONFIG
# -----------------------
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 5000))

ADMIN_EMAIL = "admin@event.com"
ADMIN_PASSWORD = "admin"

# -----------------------
# STATE
# -----------------------
ZONES = {}
USER_LOCATIONS = {}   # sid -> user data

# -----------------------
# FLASK APP
# -----------------------
app = Flask(__name__, static_folder="static", template_folder="templates")

# IMPORTANT: eventlet async mode for WebSockets
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="asgi")


# -----------------------
# UTILS
# -----------------------
def point_in_circle(ulat, ulon, zone):
    R = 6371000
    lat1, lat2 = math.radians(ulat), math.radians(zone["lat"])
    dlat = math.radians(zone["lat"] - ulat)
    dlon = math.radians(zone["lon"] - ulon)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return (R * c) <= zone["radius"]


def update_crowd_counts():
    for z in ZONES.values():
        z["count"] = 0

    for u in USER_LOCATIONS.values():
        if u.get("role") == "user" and "lat" in u:
            for z in ZONES.values():
                if point_in_circle(u["lat"], u["lon"], z):
                    z["count"] += 1

    for z in ZONES.values():
        z["is_crowded"] = z["count"] > z.get("threshold", 0)


def build_state(role):
    if role == "admin":
        return {"zones": ZONES, "users": USER_LOCATIONS}
    return {"zones": ZONES, "users": {}}


def broadcast_state():
    update_crowd_counts()
    for sid in list(USER_LOCATIONS.keys()):
        role = USER_LOCATIONS[sid].get("role", "user")
        try:
            socketio.emit("state_update", build_state(role), to=sid)
        except Exception as e:
            print("Error sending:", e)


# -----------------------
# HTTP ROUTES
# -----------------------
@app.route("/")
def root():
    return send_from_directory("templates", "login.html")


@app.route("/<path:filename>")
def serve_files(filename):
    if os.path.exists(os.path.join("templates", filename)):
        return send_from_directory("templates", filename)
    return send_from_directory("static", filename)


# -----------------------
# SOCKET EVENTS
# -----------------------
@socketio.on("connect")
def on_connect():
    sid = request.sid
    print("Client connected:", sid)
    USER_LOCATIONS[sid] = {"role": "user", "connected_at": datetime.now().isoformat()}


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    print("Disconnected:", sid)
    if sid in USER_LOCATIONS:
        del USER_LOCATIONS[sid]
    broadcast_state()


@socketio.on("message")
def handle_message(msg):
    try:
        data = msg if isinstance(msg, dict) else json.loads(msg)
    except:
        print("Malformed:", msg)
        return

    mtype = data.get("type")
    payload = data.get("payload", {})
    sid = request.sid

    # ---- LOGIN ----
    if mtype == "login":
        email = payload.get("email")
        password = payload.get("password")

        role = "admin" if email == ADMIN_EMAIL and password == ADMIN_PASSWORD else "user"
        USER_LOCATIONS[sid].update({"role": role})

        emit("message", {"type": "login_success", "payload": {"role": role}})
        broadcast_state()
        return

    # ---- LOCATION ----
    if mtype == "location_update":
        USER_LOCATIONS[sid].update(payload)
        broadcast_state()
        return

    # ---- ADMIN ACTIONS ----
    if USER_LOCATIONS[sid]["role"] == "admin":

        if mtype == "create_zone":
            zid = str(uuid.uuid4())
            z = dict(payload)
            z.update({"id": zid, "count": 0, "is_crowded": False})
            ZONES[zid] = z

        elif mtype == "update_zone":
            zid = payload.get("id")
            if zid in ZONES:
                ZONES[zid].update(payload)

        elif mtype == "delete_zone":
            zid = payload.get("id")
            if zid in ZONES:
                del ZONES[zid]

        broadcast_state()
        return

    print("Unhandled:", mtype)


# -----------------------
# START SERVER
# -----------------------
if __name__ == "__main__":
    print(f"Running on port {PORT}")
    socketio.run(app, host=HOST, port=PORT)

