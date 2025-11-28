# app.py

import os
import uuid
import math
import json
from datetime import datetime
from flask import Flask, render_template, send_from_directory, request
from flask_socketio import SocketIO, emit

# --- CONFIG ---
HOST = '0.0.0.0'
PORT = int(os.environ.get("PORT", 5000))  # Render sets PORT
ADMIN_EMAIL = "admin@event.com"
ADMIN_PASSWORD = "admin"

# --- STATE ---
ZONES = {}
USER_LOCATIONS = {}    # key = sid -> {role, lat, lon, connected_at, ...}

# Flask app
app = Flask(__name__, static_folder='static', template_folder='templates')
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

def point_in_circle(ulat, ulon, zone):
    R = 6371000 # Earth radius in meters
    lat1, lat2 = math.radians(ulat), math.radians(zone['lat'])
    dlat = math.radians(zone['lat'] - ulat)
    dlon = math.radians(zone['lon'] - ulon)
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return (R * c) <= zone['radius']

def update_crowd_counts():
    for z in ZONES.values():
        z['count'] = 0

    for u in USER_LOCATIONS.values():
        if u.get('role') == 'user' and 'lat' in u:
            for z in ZONES.values():
                if point_in_circle(u['lat'], u['lon'], z):
                    z['count'] += 1

    for z in ZONES.values():
        z['is_crowded'] = z['count'] > z.get('threshold', 0)

def _build_state_for_role(role):
    if role == 'admin':
        return {"zones": ZONES, "users": USER_LOCATIONS}
    else:
        return {"zones": ZONES, "users": {}}

def broadcast_state():
    update_crowd_counts()
    for sid, u in list(USER_LOCATIONS.items()):
        role = u.get('role', 'user')
        payload = _build_state_for_role(role)
        try:
            socketio.emit('state_update', payload, to=sid)
        except Exception as e:
            print("Emit error:", e)

# --- HTTP routes ---
@app.route('/')
def root():
    return send_from_directory('templates', 'login.html')

@app.route('/<path:filename>')
def static_from_templates(filename):
    if os.path.exists(os.path.join('templates', filename)):
        return send_from_directory('templates', filename)
    return send_from_directory('static', filename)

# --- Socket.IO events ---
@socketio.on('connect')
def on_connect():
    sid = request.sid
    print(f"Client connected: {sid}")
    USER_LOCATIONS[sid] = {"role": "user", "connected_at": datetime.now().isoformat()}

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    print(f"Client disconnected: {sid}")
    if sid in USER_LOCATIONS:
        del USER_LOCATIONS[sid]
    update_crowd_counts()
    broadcast_state()

@socketio.on('message')
def handle_message(msg):
    try:
        data = msg if isinstance(msg, dict) else json.loads(msg)
    except:
        print("Malformed message:", msg)
        return

    mtype = data.get('type')
    payload = data.get('payload', {})
    sid = request.sid
    user_role = USER_LOCATIONS.get(sid, {}).get('role', 'user')

    # LOGIN
    if mtype == 'login':
        email = payload.get('email')
        password = payload.get('password')
        role = "admin" if email == ADMIN_EMAIL and password == ADMIN_PASSWORD else "user"
        USER_LOCATIONS[sid] = {"role": role, "connected_at": datetime.now().isoformat()}
        emit('message', {"type": "login_success", "payload": {"role": role}})
        broadcast_state()
        return

    # LOCATION UPDATE
    if mtype == 'location_update':
        if sid in USER_LOCATIONS:
            USER_LOCATIONS[sid].update(payload)
            update_crowd_counts()
            broadcast_state()
        return

    # ADMIN ACTIONS
    if USER_LOCATIONS.get(sid, {}).get('role') == 'admin':
        if mtype == 'create_zone':
            zid = str(uuid.uuid4())
            z = dict(payload)
            z.update({"count": 0, "is_crowded": False, "id": zid})
            ZONES[zid] = z

        elif mtype == 'update_zone':
            zid = payload.get('id')
            if zid in ZONES:
                ZONES[zid].update(payload)

        elif mtype == 'delete_zone':
            zid = payload.get('id')
            if zid in ZONES:
                del ZONES[zid]

        update_crowd_counts()
        broadcast_state()
        return

    print("Unhandled message type:", mtype)

if __name__ == '__main__':
    print(f"Starting app on {HOST}:{PORT}")
    socketio.run(app, host=HOST, port=PORT)
