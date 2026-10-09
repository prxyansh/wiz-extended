"""
WiZ Light UDP Bridge
Implements the WiZ local protocol — JSON over UDP port 38899.
No external WiZ libraries needed; raw sockets give us full rate control.
"""
import json
import socket
import time
import threading
import math
from typing import Optional


WIZ_PORT       = 38899
BROADCAST_ADDR = "255.255.255.255"


# ─── Rate limiter (token bucket) ────────────────────────────────────────────────

class _TokenBucket:
    """Per-IP token bucket. Default: 50 commands / second."""

    def __init__(self, rate: float = 50.0):
        self._rate:  float            = rate
        self._tok:   dict[str, float] = {}
        self._last:  dict[str, float] = {}

    def consume(self, key: str) -> bool:
        now = time.monotonic()
        if key not in self._tok:
            self._tok[key]  = self._rate
            self._last[key] = now
            return True
        elapsed        = now - self._last[key]
        self._tok[key] = min(self._rate, self._tok[key] + elapsed * self._rate)
        self._last[key] = now
        if self._tok[key] >= 1.0:
            self._tok[key] -= 1.0
            return True
        return False


# ─── Helper ─────────────────────────────────────────────────────────────────────

def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


def _get_local_ips() -> list[str]:
    """Get all local IP addresses for this machine."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
        
    try:
        host_info = socket.gethostbyname_ex(socket.gethostname())
        for ip in host_info[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass
        
    return list(ips)


def _subnet_broadcast(ip: str) -> str:
    """Convert 192.168.1.42 → 192.168.1.255 (assumes /24 subnet)."""
    parts = ip.rsplit(".", 1)
    return parts[0] + ".255" if len(parts) == 2 else BROADCAST_ADDR


# ─── WizBridge ──────────────────────────────────────────────────────────────────

class WizBridge:
    """
    Thin wrapper around the WiZ UDP protocol.

    Regular commands (on/off, set_color, set_white) always send immediately.
    set_color_sync() is rate-limited to ~10 Hz so the firmware isn't flooded
    during music sync.
    """

    def __init__(self):
        self._limiter = _TokenBucket(rate=50.0)
        # Persistent socket for high-frequency music-sync sends
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # State tracking for smooth crossfading
        self._fades = {}           # type: dict[str, threading.Thread]
        self._fade_cancel = {}     # type: dict[str, threading.Event]
        self._state_cache = {}     # type: dict[str, dict]

    # ── Internal ────────────────────────────────────────────────────────────────

    def _send(self, ip: str, payload: dict) -> bool:
        try:
            self._sock.sendto(json.dumps(payload).encode(), (ip, WIZ_PORT))
            return True
        except Exception:
            return False

    def _set_pilot(self, ip: str, params: dict, force: bool = False) -> bool:
        if not force and not self._limiter.consume(ip):
            return False
            
        # Update local cache for crossfading math
        if ip not in self._state_cache:
            self._state_cache[ip] = {}
        for k, v in params.items():
            self._state_cache[ip][k] = v
            
        return self._send(ip, {"id": 1, "method": "setPilot", "params": params})

    # ── Read ────────────────────────────────────────────────────────────────────

    def get_pilot(self, ip: str, timeout: float = 2.0) -> Optional[dict]:
        """Return the bulb's current state dict, or None on timeout/error."""
        msg = json.dumps({"id": 1, "method": "getPilot", "params": {}}).encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(msg, (ip, WIZ_PORT))
            data, _ = sock.recvfrom(1024)
            return json.loads(data.decode()).get("result")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"get_pilot failed for {ip}: {e}")
            return None
        finally:
            sock.close()

    # ── Software Crossfading Engine ─────────────────────────────────────────────

    def _crossfade_task(self, ip: str, target: dict, duration: float = 0.5, steps: int = 20):
        cancel_event = self._fade_cancel.get(ip)
        
        # Determine starting values: ALWAYS fetch live state first to avoid snapping to a stale cache!
        start_state = self.get_pilot(ip, timeout=0.3)
        if not start_state:
            start_state = self._state_cache.get(ip, {})
            
        is_on = start_state.get("state", False)
        start_r = start_state.get("r", 0) if is_on else 0
        start_g = start_state.get("g", 0) if is_on else 0
        start_b = start_state.get("b", 0) if is_on else 0
        start_dim = start_state.get("dimming", 10) if is_on else 0
        start_temp = start_state.get("temp", 3000) if is_on else 3000
        
        is_fading_down = target.pop("_fade_down", False)
        end_dim = target.get("dimming", start_dim)
        loop_end_dim = 1 if is_fading_down else end_dim
        
        # If the bulb is currently OFF and we are turning it on,
        # we MUST wake up the hardware first and give it time to physically ignite
        # before we start blasting it with high-frequency UDP fade packets.
        if not is_on and not is_fading_down:
            self._set_pilot(ip, {"state": True, "dimming": 1}, force=True)
            time.sleep(0.15)
        
        step_time = duration / steps
        
        for i in range(1, steps + 1):
            if cancel_event and cancel_event.is_set():
                break
                
            # Use Sine Ease-In-Out for buttery smooth transitions instead of linear
            linear_progress = i / steps
            progress = -(math.cos(math.pi * linear_progress) - 1) / 2
            
            payload = {"state": True}
            
            curr_dim = int(start_dim + (loop_end_dim - start_dim) * progress)
            if curr_dim < 1:
                curr_dim = 1
            payload["dimming"] = curr_dim
            
            if "r" in target:
                end_r = target["r"]
                end_g = target["g"]
                end_b = target["b"]
                payload["r"] = int(start_r + (end_r - start_r) * progress)
                payload["g"] = int(start_g + (end_g - start_g) * progress)
                payload["b"] = int(start_b + (end_b - start_b) * progress)
            elif "temp" in target:
                end_temp = target["temp"]
                payload["temp"] = int(start_temp + (end_temp - start_temp) * progress)
                
            self._set_pilot(ip, payload, force=True)
            time.sleep(step_time)
            
        # Ensure final target is exact, unless cancelled
        if not cancel_event or not cancel_event.is_set():
            if is_fading_down:
                # If we are fading off, just send state: False to avoid flashing up to previous brightness.
                self._set_pilot(ip, {"state": False}, force=True)
                # Manually restore the ORIGINAL accurate brightness in the cache so turn_on remembers it.
                if ip not in self._state_cache: self._state_cache[ip] = {}
                self._state_cache[ip]["dimming"] = start_dim
            else:
                self._set_pilot(ip, target, force=True)

    def _start_fade(self, ip: str, target: dict, duration: float = 0.5, steps: int = 20):
        if ip in self._fade_cancel:
            self._fade_cancel[ip].set()
        if ip in self._fades and self._fades[ip].is_alive():
            self._fades[ip].join(timeout=0.1)
            
        self._fade_cancel[ip] = threading.Event()
        t = threading.Thread(target=self._crossfade_task, args=(ip, target, duration, steps), daemon=True)
        self._fades[ip] = t
        t.start()

    # ── Write (force = bypass rate-limiter) ─────────────────────────────────────

    def turn_on(self, ip: str):
        # Rely on the bulb's native hardware ignition fade for perfect smoothness without the 10% pop
        self._set_pilot(ip, {"state": True}, force=True)

    def turn_off(self, ip: str):
        # Rely on the bulb's native hardware power-off fade for perfect smoothness without the 1% step
        self._set_pilot(ip, {"state": False}, force=True)

    def set_color(self, ip: str, r: int, g: int, b: int, brightness: int, instant: bool = False):
        target = {
            "state":   True,
            "r":       _clamp(r, 0, 255),
            "g":       _clamp(g, 0, 255),
            "b":       _clamp(b, 0, 255),
            "dimming": _clamp(brightness, 10, 100),
        }
        if instant:
            if ip in self._fade_cancel:
                self._fade_cancel[ip].set()
            self._set_pilot(ip, target, force=True)
        else:
            self._start_fade(ip, target)

    def set_white(self, ip: str, temp: int, brightness: int):
        target = {
            "state":   True,
            "temp":    _clamp(temp, 2200, 6500),
            "dimming": _clamp(brightness, 10, 100),
        }
        self._start_fade(ip, target)

    def set_scene(self, ip: str, scene_id: int, speed: int = 100, brightness: int = 100) -> bool:
        """Set a predefined WiZ hardware scene."""
        # Cancel any active crossfade
        if ip in self._fade_cancel:
            self._fade_cancel[ip].set()
        return self._set_pilot(ip, {
            "state":   True,
            "sceneId": scene_id,
            "speed":   _clamp(speed, 20, 200),
            "dimming": _clamp(brightness, 10, 100)
        }, force=True)

    def set_color_sync(self, ip: str, r: int, g: int, b: int, brightness: int) -> bool:
        """Rate-limited setPilot for music sync (~25 Hz max)."""
        return self._set_pilot(ip, {
            "state":   True,
            "r":       _clamp(r, 0, 255),
            "g":       _clamp(g, 0, 255),
            "b":       _clamp(b, 0, 255),
            "dimming": _clamp(brightness, 10, 100),
        }, force=False)

    # ── Discovery ───────────────────────────────────────────────────────────────

    def discover(self, timeout: float = 3.0) -> dict[str, dict]:
        """Broadcast getPilot to find bulbs on the network."""
        import socket
        import time

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)
        
        # Bulbs are highly reliable at answering getPilot but flaky with registration
        probe = json.dumps({"method": "getPilot"}).encode()
        
        # Add unicast addresses to bypass IGMP snooping/broadcast filters
        targets = [BROADCAST_ADDR]
        for local_ip in _get_local_ips():
            base = ".".join(local_ip.split(".")[:3])
            targets.extend([f"{base}.{i}" for i in range(1, 255)])

        found: dict[str, dict] = {}
        deadline = time.monotonic() + timeout

        # Send Phase: Blast all targets (paced)
        for _ in range(2): # 2 rounds of sends for reliability
            for addr in targets:
                try:
                    sock.sendto(probe, (addr, WIZ_PORT))
                    time.sleep(0.002)
                except Exception:
                    pass

        # Receive Phase: Collect all responses until deadline
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
                resp = json.loads(data.decode())
                if "result" in resp:
                    res = resp["result"]
                    ip  = addr[0]
                    mac = res.get("mac", ip)
                    found[mac] = {
                        "mac": mac,
                        "ip": ip,
                        "name": f"WiZ {mac[-5:]}"
                    }
            except socket.timeout:
                pass
            except Exception:
                pass

        sock.close()
        return list(found.values())

    def fast_discover(self, known_ips: list[str], timeout: float = 1.0) -> dict[str, dict]:
        """Lightweight discovery: only sweeps BROADCAST and previously known IPs."""
        import socket
        import time

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)

        probe = json.dumps({"method": "getPilot"}).encode()
        targets = [BROADCAST_ADDR] + known_ips

        found: dict[str, dict] = {}
        deadline = time.monotonic() + timeout

        # Send Phase
        for _ in range(2):
            for addr in targets:
                try:
                    sock.sendto(probe, (addr, WIZ_PORT))
                    time.sleep(0.002)
                except Exception:
                    pass

        # Receive Phase
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(1024)
                resp = json.loads(data.decode())
                if "result" in resp:
                    res = resp["result"]
                    ip  = addr[0]
                    mac = res.get("mac", ip)
                    found[mac] = {
                        "mac": mac,
                        "ip": ip,
                        "name": f"WiZ {mac[-5:]}"
                    }
            except socket.timeout:
                pass
            except Exception:
                pass

        sock.close()
        return found

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
