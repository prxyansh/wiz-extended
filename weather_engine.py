import time
import threading
import requests
import random
import colorsys
import math
from typing import List, Optional

class WeatherEngine:
    def __init__(self, bridge):
        self.bridge = bridge
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ips: List[str] = []
        
        self.latitude = None
        self.longitude = None
        
        self.current_weathercode = 0
        self.is_day = 1
        self._last_fetch = 0
        
        self._anim_t = 0.0

    def start(self, ips: List[str]):
        self.stop()
        self._ips = ips
        self._stop_event.clear()
        
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=2.0)

    def _fetch_location(self):
        try:
            r = requests.get("http://ip-api.com/json/", timeout=5)
            if r.status_code == 200:
                data = r.json()
                self.latitude = data.get("lat")
                self.longitude = data.get("lon")
                print(f"WeatherEngine: Located at {self.latitude}, {self.longitude}")
        except Exception as e:
            print(f"WeatherEngine Location Error: {e}")

    def _fetch_weather(self):
        if not self.latitude or not self.longitude:
            return
            
        try:
            url = f"https://api.open-meteo.com/v1/forecast?latitude={self.latitude}&longitude={self.longitude}&current_weather=true"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                cw = r.json().get("current_weather", {})
                self.current_weathercode = cw.get("weathercode", 0)
                self.is_day = cw.get("is_day", 1)
                print(f"WeatherEngine: Weather Code {self.current_weathercode}, Day: {self.is_day}")
        except Exception as e:
            print(f"WeatherEngine Weather Error: {e}")

    def _loop(self):
        target_fps = 15
        dt = 1.0 / target_fps
        
        self._fetch_location()
        self._fetch_weather()
        self._last_fetch = time.time()
        
        lightning_flash = 0.0
        
        while not self._stop_event.is_set():
            t0 = time.time()
            self._anim_t += dt
            
            # Fetch weather every 5 minutes
            if t0 - self._last_fetch > 300:
                self._fetch_weather()
                self._last_fetch = t0

            # Base color based on WMO code
            c = self.current_weathercode
            brightness = 100
            
            r, g, b = 255, 255, 255 # default
            
            if c in (0, 1): # Clear
                if self.is_day:
                    # Golden Sun, slow pulse
                    pulse = (math.sin(self._anim_t * 0.5) + 1) / 2 # 0 to 1
                    r, g, b = 255, 180 + int(40 * pulse), 50
                    brightness = 80 + int(20 * pulse)
                else:
                    # Night sky, deep blue static
                    r, g, b = 10, 20, 80
                    brightness = 40
            
            elif c in (2, 3, 45, 48): # Cloudy / Fog
                # Cool grey/blue, slow shifting
                pulse = (math.sin(self._anim_t * 0.3) + 1) / 2
                r, g, b = 100, 120 + int(10 * pulse), 140
                brightness = 60
                
            elif c in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82): # Rain
                # Deep cyan/blue, pulsing faster
                pulse = (math.sin(self._anim_t * 2.0) + 1) / 2
                r = 0
                g = 100 + int(50 * pulse)
                b = 200 + int(55 * pulse)
                brightness = 70 + int(30 * pulse)
                
            elif c in (71, 73, 75, 77, 85, 86): # Snow
                # Crisp white-blue
                pulse = (math.sin(self._anim_t * 1.0) + 1) / 2
                r, g, b = 200, 230, 255
                brightness = 80 + int(10 * pulse)
                
            elif c in (95, 96, 99): # Thunderstorm
                # Dark blue base
                r, g, b = 5, 10, 40
                brightness = 30
                
                # Lightning logic
                if lightning_flash > 0:
                    r, g, b = 255, 255, 255
                    brightness = 100
                    lightning_flash -= dt * 5.0 # fade fast
                else:
                    if random.random() < 0.02: # 2% chance per frame
                        lightning_flash = 1.0
            
            else:
                # Fallback
                r, g, b = 100, 100, 100

            # Send to lights
            r = max(0, min(255, int(r)))
            g = max(0, min(255, int(g)))
            b = max(0, min(255, int(b)))
            brightness = max(10, min(100, int(brightness)))

            for ip in self._ips:
                self.bridge.set_color_sync(ip, r, g, b, brightness)
                
            elapsed = time.time() - t0
            sleep_time = dt - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
