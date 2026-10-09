"""
Philips WiZ Extended — FastAPI Backend
=================================
Serves the web UI and bridges browser → UDP → WiZ lights.
Also manages the audio engine for music sync.

Run:
    python server.py
Then open:
    http://localhost:8899
"""
import asyncio
import json
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import base64
from io import BytesIO
from PIL import Image
import subprocess
import urllib.request
import colorsys

from wiz_bridge import WizBridge
from audio_engine import AudioEngine
from screen_engine import ScreenEngine
from weather_engine import WeatherEngine
from pomodoro_engine import PomodoroEngine
from alarm_engine import AlarmEngine
from bpm_engine import BPMEngine

# ─── Paths ──────────────────────────────────────────────────────────────────────

BASE   = Path(__file__).parent
DATA   = BASE / "data"
STATIC = BASE / "static"

PRESETS_F  = DATA / "presets.json"
SETTINGS_F = DATA / "settings.json"
LIGHTS_F = DATA / "lights.json"

DATA.mkdir(exist_ok=True)

# ─── JSON helpers ───────────────────────────────────────────────────────────────

def _load(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except Exception:
        return default

def _save(path: Path, data):
    path.write_text(json.dumps(data, indent=2))

# ─── Bootstrap defaults ─────────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "sync_mode":      "fire",
    "sensitivity":    1.0,
    "sync_light_ips": [],
    "circadian_enabled": False,
    "groups": [],
}

_DEFAULT_PRESETS = [
    {"id": "sunset", "name": "Sunset",
     "mode": "color", "r": 255, "g": 80,  "b": 20,  "brightness": 70},
    {"id": "ocean",  "name": "Ocean",
     "mode": "color", "r": 0,   "g": 120, "b": 255, "brightness": 65},
    {"id": "focus",  "name": "Focus",
     "mode": "white", "temp": 5000, "brightness": 100},
    {"id": "relax",  "name": "Relax",
     "mode": "white", "temp": 2700, "brightness": 40},
    {"id": "party",  "name": "Party",
     "mode": "color", "r": 160, "g": 0,   "b": 255, "brightness": 90},
    {"id": "forest", "name": "Forest",
     "mode": "color", "r": 30,  "g": 200, "b": 60,  "brightness": 55},
]

if not PRESETS_F.exists():  _save(PRESETS_F,  _DEFAULT_PRESETS)
if not SETTINGS_F.exists(): _save(SETTINGS_F, _DEFAULT_SETTINGS)

# ─── In-memory lights store (resets on server restart) ───────────────────────────
import time as time_mod
_lights: dict[str, dict] = _load(LIGHTS_F, {})
for l in _lights.values():
    l["online"] = False
    l["last_seen"] = 0

def _update_registry(found):
    if isinstance(found, list):
        found = {d.get("mac"): d for d in found if isinstance(d, dict) and "mac" in d}
    if not isinstance(found, dict):
        return
        
    changed = False
    now = time_mod.time()
    for mac, data in found.items():
        if mac in _lights:
            _lights[mac]["online"] = True
            _lights[mac]["last_seen"] = now
            if _lights[mac]["ip"] != data["ip"]:
                _lights[mac]["ip"] = data["ip"]
                changed = True
        else:
            # We don't auto-add completely new unknown bulbs in background to prevent spam,
            # we just track known ones. Manual scan is still needed for brand new bulbs.
            pass
            
    # Mark offline if not seen
    for l in _lights.values():
        if l.get("online", False) and (now - l.get("last_seen", 0)) > 90:
            l["online"] = False
            
    if changed:
        _save(LIGHTS_F, _lights)

async def _run_fast_discover(known_ips: list[str]):
    try:
        found = await _loop.run_in_executor(_pool, wiz.fast_discover, known_ips, 1.0)
        _update_registry(found)
    except Exception as e:
        import sys
        print(f"Fast discover error: {e}", file=sys.stderr)

def _handle_light_offline(ip: str):
    mac = next((m for m, l in _lights.items() if l["ip"] == ip), None)
    if mac:
        _lights[mac]["online"] = False
    if _loop:
        _loop.create_task(_run_fast_discover([ip]))

async def _background_discovery_worker():
    """Loops every 30s to keep IP addresses fresh."""
    while True:
        try:
            has_offline = any(not l.get("online", False) for l in _lights.values())
            if has_offline or not _lights:
                # Escalate to full subnet sweep to find lost bulbs
                found = await _loop.run_in_executor(_pool, wiz.discover, 3.0)
                import sys
                print(f"BG discover found: {found}", file=sys.stderr)
                _update_registry(found)
            else:
                # Fast heartbeat
                known_ips = [l["ip"] for l in _lights.values()]
                if known_ips:
                    await _run_fast_discover(known_ips)
        except Exception as e:
            import sys
            print(f"BG discover error: {e}", file=sys.stderr)
        await asyncio.sleep(30)

# ─── Circadian Engine ──────────────────────────────────────────────────────────

import math
from datetime import datetime

def calculate_circadian_state(hour: float) -> dict:
    points = [
        (6.0,  3000, 50),
        (9.0,  5500, 100),
        (17.0, 5500, 100),
        (19.0, 3500, 70),
        (22.0, 2200, 20),
        (30.0, 3000, 50), # wrap to next 6AM
    ]
    if hour < 6.0:
        hour += 24.0
    for i in range(len(points) - 1):
        t1, temp1, dim1 = points[i]
        t2, temp2, dim2 = points[i+1]
        if t1 <= hour < t2:
            progress = (hour - t1) / (t2 - t1)
            progress = -(math.cos(math.pi * progress) - 1) / 2
            return {
                "temp": int(temp1 + (temp2 - temp1) * progress),
                "dimming": int(dim1 + (dim2 - dim1) * progress)
            }
    return {"temp": 2200, "dimming": 20}

async def _circadian_loop():
    """Runs every 60s. Adjusts lights if circadian mode is enabled and light is ON."""
    while True:
        try:
            settings = _load(SETTINGS_F, _DEFAULT_SETTINGS)
            if settings.get("circadian_enabled"):
                now = datetime.now()
                hour_float = now.hour + now.minute / 60.0
                target_state = calculate_circadian_state(hour_float)
                
                for mac, l in _lights.items():
                    if l.get("online"):
                        ip = l["ip"]
                        # Check bridge state cache to see if light is physically ON
                        bridge_state = wiz._state_cache.get(ip, {})
                        if bridge_state.get("state", False):
                            wiz.set_white(ip, target_state["temp"], target_state["dimming"])
        except Exception as e:
            import traceback
            traceback.print_exc()
        await asyncio.sleep(60)

# ─── WebSocket manager ──────────────────────────────────────────────────────────

class _WSManager:
    def __init__(self):
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        self._clients = [c for c in self._clients if c is not ws]

    @property
    def active_connections(self):
        return len(self._clients) > 0

    async def broadcast(self, data: dict):
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

# ─── Globals ────────────────────────────────────────────────────────────────────

wiz = WizBridge()
audio_engine = AudioEngine()
screen_engine = ScreenEngine(wiz)
weather_engine = WeatherEngine(wiz)
pomodoro_engine = PomodoroEngine(wiz)
alarm_engine = AlarmEngine(wiz)
bpm_engine = BPMEngine(wiz)
_loop:    Optional[asyncio.AbstractEventLoop] = None
_pool     = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wiz")

try:
    _cached_brightness = _load(SETTINGS_F, _DEFAULT_SETTINGS).get("brightness", 100)
except:
    _cached_brightness = 100

# ─── Audio sync state & loops ──────────────────────────────────────────────────
_active_sync_ips = []
_last_sent_state = {}

_audio_state_lock = threading.Lock()
_latest_audio_state = {"channels": {}, "bands": [0.0] * 10}

def _on_audio(channels: dict, bands: list[float]):
    with _audio_state_lock:
        _latest_audio_state["channels"] = channels
        _latest_audio_state["bands"] = bands
        
    # Instantly dispatch UDP packets without polling delay
    brightness = _cached_brightness
    now = time_mod.time()
    
    for i, ip in enumerate(list(_active_sync_ips)):
        try:
            c = channels.get(f"ch{i+1}") or channels.get("ch1")
            if not c: continue
            r, g, b, dim = c
            dim_adj = int(dim * (brightness / 100.0))
            
            state_key = (r, g, b, dim_adj)
            last_time, last_state = _last_sent_state.get(ip, (0.0, None))
            
            # Rate limit to ~50 FPS (20ms) to avoid saturating the bulb's network stack
            if (state_key != last_state and (now - last_time) >= 0.020) or (now - last_time) >= 0.5:
                _last_sent_state[ip] = (now, state_key)
                _pool.submit(wiz.set_color_sync, ip, r, g, b, dim_adj)
        except Exception as e:
            pass

async def _ui_broadcast_worker():
    """Async task that broadcasts UI state over websockets independently."""
    while True:
        await asyncio.sleep(0.04)  # ~25Hz
        if not manager.active_connections:
            continue
            
        with _audio_state_lock:
            if not _latest_audio_state["channels"]:
                continue
            channels = _latest_audio_state["channels"]
            bands = _latest_audio_state["bands"]
            
        brightness = _load(SETTINGS_F, _DEFAULT_SETTINGS).get("brightness", 100)
        payload = {
            "type":       "audio_sync",
            "channels":   channels,
            "brightness": brightness,
            "bands":      [round(b, 3) for b in bands],
        }
        try:
            await manager.broadcast(payload)
        except Exception as e:
            import sys
            print(f"UI broadcast error: {e}", file=sys.stderr)

# Polling latency eliminated: UDP sync is now instantly driven by _on_audio


# ─── App lifecycle ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _loop
    _loop = asyncio.get_running_loop()
    task1 = _loop.create_task(_background_discovery_worker())
    task2 = _loop.create_task(_circadian_loop())
    ui_task = asyncio.create_task(_ui_broadcast_worker())
    
    # Start audio engine if sync mode is not off
    settings = _load(SETTINGS_F, _DEFAULT_SETTINGS)
    if settings.get("sync_mode") != "off":
        try:
            audio_engine.start(_on_audio, settings.get("sync_light_ips", []))
        except Exception as e:
            import sys
            print(f"Startup Audio warning: {e}", file=sys.stderr)
            
    yield
    
    task1.cancel()
    task2.cancel()
    ui_task.cancel()
    audio_engine.stop()
    screen_engine.stop()
    weather_engine.stop()
    pomodoro_engine.stop()
    wiz.close()
    _pool.shutdown(wait=False)

app = FastAPI(title="Philips WiZ Extended", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# ─── Pages ──────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))

# Inline SVG favicon — fire emoji style
_FAVICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<text y=".9em" font-size="85">🔥</text></svg>'''

@app.get("/favicon.ico")
def favicon():
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml")

# ─── Lights CRUD ────────────────────────────────────────────────────────────────

@app.get("/api/lights")
def get_lights():
    settings = _load(SETTINGS_F, _DEFAULT_SETTINGS)
    groups = settings.get("groups", [])
    
    result = dict(_lights)
    for g in groups:
        online = any(ip in [l["ip"] for l in _lights.values() if l.get("online")] for ip in g["ips"])
        state = {}
        if online:
            # find the first online bulb to represent the group's state
            for ip in g["ips"]:
                bulb = next((l for l in _lights.values() if l["ip"] == ip), None)
                if bulb and bulb.get("online") and "state" in bulb:
                    state = bulb["state"]
                    break

        result[g["id"]] = {
            "mac": g["id"],
            "ip": f"group:{g['id']}",
            "name": f"{g['name']} (Group)",
            "online": online,
            "state": state,
            "is_group": True,
            "ips": g["ips"]
        }
    return result

@app.post("/api/lights")
def save_light(body: dict = Body(...)):
    mac = body["mac"]
    _lights[mac] = {
        "mac":  mac,
        "ip":   body["ip"],
        "name": body.get("name", f"WiZ {mac[-5:]}"),
    }
    _save(LIGHTS_F, _lights)
    return _lights[mac]

@app.delete("/api/lights")
def clear_all_lights():
    """Wipe the entire lights list — called before a fresh scan."""
    _lights.clear()
    _save(LIGHTS_F, _lights)
    return {"ok": True}

@app.delete("/api/lights/{mac}")
def delete_light(mac: str):
    _lights.pop(mac, None)
    _save(LIGHTS_F, _lights)
    return {"ok": True}

@app.patch("/api/lights/{mac}")
def patch_light(mac: str, body: dict = Body(...)):
    if mac in _lights:
        _lights[mac].update(body)
        _save(LIGHTS_F, _lights)
    return {"ok": True}

# ─── Group management ───────────────────────────────────────────────────────────

@app.post("/api/groups")
def save_group(body: dict = Body(...)):
    settings = _load(SETTINGS_F, _DEFAULT_SETTINGS)
    groups = settings.setdefault("groups", [])
    
    group_id = body.get("id", str(uuid.uuid4())[:8])
    new_group = {
        "id": group_id,
        "name": body["name"],
        "ips": body["ips"]
    }
    
    # Update if exists, else append
    for i, g in enumerate(groups):
        if g["id"] == group_id:
            groups[i] = new_group
            break
    else:
        groups.append(new_group)
        
    _save(SETTINGS_F, settings)
    return new_group

@app.delete("/api/groups/{group_id}")
def delete_group(group_id: str):
    settings = _load(SETTINGS_F, _DEFAULT_SETTINGS)
    settings["groups"] = [g for g in settings.get("groups", []) if g["id"] != group_id]
    _save(SETTINGS_F, settings)
    return {"ok": True}

# ─── Light control ──────────────────────────────────────────────────────────────

def _get_target_ips(ip: str):
    if ip.startswith("group:"):
        group_id = ip.split("group:")[1]
        settings = _load(SETTINGS_F, _DEFAULT_SETTINGS)
        for g in settings.get("groups", []):
            if g["id"] == group_id:
                return g["ips"]
        return []
    return [ip]

@app.post("/api/lights/{ip}/on")
def light_on(ip: str):
    for target in _get_target_ips(ip):
        wiz.turn_on(target)
    return {"ok": True}

@app.post("/api/lights/{ip}/off")
def light_off(ip: str):
    for target in _get_target_ips(ip):
        wiz.turn_off(target)
    return {"ok": True}

class ColorRequest(BaseModel):
    r: int
    g: int
    b: int
    brightness: int = 100
    instant: bool = False

@app.post("/api/lights/{ip}/color")
def set_color(ip: str, body: dict = Body(...)):
    for target in _get_target_ips(ip):
        res = wiz.set_color(
            target, 
            body["r"], 
            body["g"], 
            body["b"], 
            body.get("brightness", 100), 
            body.get("instant", False)
        )
        if res is False:
            _handle_light_offline(target)
    return {"ok": True}

@app.post("/api/lights/{ip}/white")
def set_white(ip: str, body: dict = Body(...)):
    for target in _get_target_ips(ip):
        res = wiz.set_white(target, body["temp"], body.get("brightness", 100))
        if not res:
            _handle_light_offline(target)
    return {"ok": True}

@app.post("/api/lights/{ip}/scene")
def set_scene(ip: str, body: dict = Body(...)):
    for target in _get_target_ips(ip):
        wiz.set_scene(target, body["sceneId"], body.get("speed", 100), body.get("brightness", 100))
    return {"ok": True}

@app.get("/api/lights/{ip}/state")
def get_state(ip: str):
    targets = _get_target_ips(ip)
    if not targets:
        raise HTTPException(404, "Light/Group not found")
    
    # Return state of the first bulb for groups
    state = wiz.get_pilot(targets[0])
    if state is None:
        _handle_light_offline(targets[0])
        raise HTTPException(503, "Light unreachable")
    return state

# ─── Discovery (runs in thread pool to avoid blocking) ──────────────────────────

@app.post("/api/discover")
async def discover():
    loop = asyncio.get_running_loop()
    found = await loop.run_in_executor(_pool, wiz.discover, 3.0)
    # wiz.discover now returns a dict, but frontend expects a list
    if isinstance(found, dict):
        found = list(found.values())
    return {"lights": found}

# ─── Presets ────────────────────────────────────────────────────────────────────

@app.get("/api/presets")
def get_presets():
    return _load(PRESETS_F, _DEFAULT_PRESETS)

@app.post("/api/presets")
def create_preset(body: dict = Body(...)):
    presets = _load(PRESETS_F, _DEFAULT_PRESETS)
    body["id"] = uuid.uuid4().hex[:8]
    presets.append(body)
    _save(PRESETS_F, presets)
    return body

@app.delete("/api/presets/{pid}")
def delete_preset(pid: str):
    presets = [p for p in _load(PRESETS_F, _DEFAULT_PRESETS) if p.get("id") != pid]
    _save(PRESETS_F, presets)
    return {"ok": True}

@app.patch("/api/presets/{pid}")
def rename_preset(pid: str, body: dict = Body(...)):
    presets = _load(PRESETS_F, _DEFAULT_PRESETS)
    for p in presets:
        if p.get("id") == pid:
            p["name"] = body["name"]
    _save(PRESETS_F, presets)
    return {"ok": True}

@app.get("/api/settings")
def get_settings():
    return _load(SETTINGS_F, _DEFAULT_SETTINGS)

@app.post("/api/settings")
def update_settings(updates: dict = Body(...)):
    s = _load(SETTINGS_F, _DEFAULT_SETTINGS)
    changed = False
    
    if "circadian_enabled" in updates:
        s["circadian_enabled"] = bool(updates["circadian_enabled"])
        changed = True
        
    if "sync_mode" in updates:
        s["sync_mode"] = updates["sync_mode"]
        changed = True

    if changed:
        _save(SETTINGS_F, s)
        if "brightness" in s:
            global _cached_brightness
            _cached_brightness = s["brightness"]
        
    # If circadian was just enabled, force a tick immediately
    if changed and updates.get("circadian_enabled"):
        now = datetime.now()
        target = calculate_circadian_state(now.hour + now.minute / 60.0)
        for mac, l in _lights.items():
            if l.get("online"):
                ip = l["ip"]
                if wiz._state_cache.get(ip, {}).get("state", False):
                    wiz.set_white(ip, target["temp"], target["dimming"])

    return {"ok": True, "settings": s}

# ─── Audio / Music sync ─────────────────────────────────────────────────────────

@app.get("/api/audio/devices")
def get_devices():
    devices = audio_engine.list_input_devices()
    idx, name = audio_engine.find_loopback()
    return {
        "devices":         devices,
        "loopback_index":  idx,
        "loopback_name":   name,
        "blackhole_found": idx is not None,
    }

@app.get("/api/audio/status")
def get_audio_status():
    return {
        "is_running":  audio_engine.is_running,
        "color_mode":  audio_engine.color_mode,
        "sensitivity": audio_engine.sensitivity,
    }

@app.post("/api/audio/start")
def start_sync(body: dict = Body(...)):
    global _active_sync_ips
    settings = _load(SETTINGS_F, _DEFAULT_SETTINGS)
    ips = body.get("light_ips", [])
    settings["sync_light_ips"] = ips
    settings["sync_mode"]      = body.get("mode", "fire")
    settings["sensitivity"]    = body.get("sensitivity", 1.0)
    _save(SETTINGS_F, settings)
    
    _active_sync_ips = ips

    ok, err = audio_engine.start(
        device_index=int(body["device_index"]),
        callback=_on_audio,
        color_mode=body.get("mode", "fire"),
        sensitivity=float(body.get("sensitivity", 1.0)),
    )
    return {"ok": ok, "error": err}

@app.patch("/api/audio/sensitivity")
def set_sensitivity(body: dict = Body(...)):
    audio_engine.set_sensitivity(float(body["value"]))
    return {"ok": True}

@app.patch("/api/audio/mode")
def set_mode(body: dict = Body(...)):
    audio_engine.set_mode(body["mode"])
    return {"ok": True}

@app.post("/api/audio/stop")
def stop_audio():
    audio_engine.stop()
    return {"ok": True}

# ─── Local BPM Sync ─────────────────────────────────────────────────────────────
@app.post("/api/bpm/start")
def start_bpm(body: dict = Body(...)):
    ips = body.get("light_ips", [])
    distribution = body.get("distribution", "uniform")
    device_index = body.get("device_index", None)
    
    if not ips: return {"error": "No lights selected"}
    
    if device_index is None:
        idx, name = audio_engine.find_loopback()
        if idx is None: return {"error": "No loopback device found"}
        device_index = idx
    
    bpm_engine.start(device_index, ips, distribution)
    return {"ok": True}

@app.post("/api/bpm/stop")
def stop_bpm():
    bpm_engine.stop()
    return {"ok": True}

@app.get("/api/bpm/status")
def get_bpm_status():
    return bpm_engine.get_status()

# ─── Sunrise Alarm ──────────────────────────────────────────────────────────────
@app.post("/api/alarm/set")
def set_alarm(body: dict = Body(...)):
    ips = body.get("light_ips", [])
    time_str = body.get("time", "")
    
    if not ips: return {"error": "No IP provided"}
    if not time_str: return {"error": "No time provided"}
    
    alarm_engine.set_alarm(time_str, ips)
    return {"ok": True}

@app.post("/api/alarm/clear")
def clear_alarm():
    alarm_engine.clear_alarm()
    return {"ok": True}

@app.get("/api/alarm/status")
def get_alarm_status():
    return alarm_engine.get_status()


# ─── Pomodoro Sync ──────────────────────────────────────────────────────────────
@app.post("/api/pomodoro/start")
def start_pomodoro(body: dict = Body(...)):
    ips = body.get("light_ips", [])
    if not ips:
        return {"error": "No IP provided"}
    
    # Optional overrides for testing
    focus = body.get("focus_duration")
    if focus:
        pomodoro_engine.focus_duration = focus
        pomodoro_engine.warning_threshold = max(5, int(focus * 0.1)) # 10% warning or at least 5s
        
    brk = body.get("break_duration")
    if brk:
        pomodoro_engine.break_duration = brk
        
    pomodoro_engine.start(ips)
    return {"ok": True}

@app.post("/api/pomodoro/stop")
def stop_pomodoro():
    pomodoro_engine.stop()
    return {"ok": True}

@app.get("/api/pomodoro/status")
def get_pomodoro_status():
    return pomodoro_engine.get_status()

# ─── Screen Sync ────────────────────────────────────────────────────────────────
@app.post("/api/screen/start")
def start_screen(body: dict = Body(...)):
    # e.g. {"light_ips": ["192.168.1.x"], "mode": "average", "brightness": 100}
    ips = []
    for raw_ip in body.get("light_ips", []):
        ips.extend(_get_target_ips(raw_ip))
    if not ips:
        return {"error": "No valid IPs provided"}
    
    mode = body.get("mode", "average")
    brightness = body.get("brightness", 100)
    screen_engine.start(ips, mode, brightness)
    return {"ok": True}

@app.post("/api/screen/stop")
def stop_screen():
    screen_engine.stop()
    return {"ok": True}

@app.post("/api/weather/start")
def start_weather_sync(body: dict = Body(...)):
    global _active_sync_ips
    ips = body.get("light_ips", [])
    _active_sync_ips = ips
    weather_engine.start(ips)
    return {"ok": True}

@app.post("/api/weather/stop")
def stop_weather_sync():
    weather_engine.stop()
    pomodoro_engine.stop()
    return {"ok": True}

class PaletteRequest(BaseModel):
    image_base64: str

def extract_popping_colors(img, num_colors=5):
    img.thumbnail((150, 150))
    # Extract a larger palette to find the vibrant ones
    q_img = img.convert('P', palette=Image.ADAPTIVE, colors=40)
    colors = q_img.getcolors()
    
    if not colors:
        return []
        
    palette = q_img.getpalette()
    scored_colors = []
    
    for count, idx in colors:
        r = palette[idx*3]
        g = palette[idx*3 + 1]
        b = palette[idx*3 + 2]
        h, s, v = colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)
        
        # High saturation & value are "popping". Weight count slightly to avoid single stray pixels.
        score = (s * v) * (count ** 0.3)
        scored_colors.append((score, r, g, b))
        
    # Sort by popping score
    scored_colors.sort(key=lambda x: x[0], reverse=True)
    
    final_colors = []
    for score, r, g, b in scored_colors:
        too_close = False
        for _, pr, pg, pb in final_colors:
            # Simple euclidean distance for distinctness
            dist = ((r - pr)**2 + (g - pg)**2 + (b - pb)**2) ** 0.5
            if dist < 45: 
                too_close = True
                break
        if not too_close:
            final_colors.append((score, r, g, b))
        if len(final_colors) >= num_colors:
            break
            
    # Fallback if we filtered too many out
    if len(final_colors) < num_colors:
        for score, r, g, b in scored_colors:
            if not any(r == pr and g == pg and b == pb for _, pr, pg, pb in final_colors):
                final_colors.append((score, r, g, b))
            if len(final_colors) >= num_colors:
                break
                
    hex_colors = []
    for _, r, g, b in final_colors:
        hex_colors.append(f"#{r:02x}{g:02x}{b:02x}")
        
    return hex_colors

@app.post("/api/palette")
async def extract_palette(req: PaletteRequest):
    try:
        header, encoded = req.image_base64.split(",", 1)
        data = base64.b64decode(encoded)
        img = Image.open(BytesIO(data))
        
        if img.mode != "RGB":
            img = img.convert("RGB")
        
        hex_colors = extract_popping_colors(img, 5)
        return {"palette": hex_colors}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/palette/spotify")
def get_spotify_palette():
    try:
        script = 'tell application "Spotify" to get artwork url of current track'
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, check=True)
        url = result.stdout.strip()
        
        if not url or not url.startswith("http"):
            raise Exception("Spotify is not playing or URL not found")
            
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = response.read()
            
        img = Image.open(BytesIO(data))
        if img.mode != "RGB":
            img = img.convert("RGB")
            
        hex_colors = extract_popping_colors(img, 5)
            
        b64_img = base64.b64encode(data).decode('utf-8')
        artwork_data_uri = f"data:image/jpeg;base64,{b64_img}"
            
        return {"palette": hex_colors, "artwork_url": artwork_data_uri}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not get Spotify artwork: {e}")

# ─── WebSocket ───────────────────────────────────────────────────────────────────

manager = _WSManager()

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()   # keep-alive ping from client
    except WebSocketDisconnect:
        manager.disconnect(ws)

# ─── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🔥  Philips WiZ Extended starting…")
    print("    Open in browser → http://localhost:8899")
    print("    On any device on your WiFi → http://<your-mac-ip>:8899\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8899, reload=False)
