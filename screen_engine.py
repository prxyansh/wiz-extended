import mss
import time
import threading
from PIL import Image
from typing import List, Optional
import colorsys

class ScreenEngine:
    def __init__(self, bridge):
        self.bridge = bridge
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ips: List[str] = []
        self._mode: str = "average"
        self._brightness: int = 100
        
        # Initialize mss
        self._sct = mss.mss()

    def start(self, ips: List[str], mode: str, brightness: int = 100):
        self.stop()
        self._ips = ips
        self._mode = mode
        self._brightness = brightness
        self._stop_event.clear()
        
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=2.0)

    def _loop(self):
        monitor = self._sct.monitors[1] # Primary monitor
        
        target_fps = 15
        frame_time = 1.0 / target_fps
        
        while not self._stop_event.is_set():
            t0 = time.time()
            
            try:
                # Capture screen
                img = self._sct.grab(monitor)
                
                # Convert to PIL image
                pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
                
                # Process color
                r, g, b = self._get_color(pil_img, self._mode)
                
                # Color enhancement for better lighting effect
                h, s, v = colorsys.rgb_to_hsv(r/255.0, g/255.0, b/255.0)
                
                if self._mode == "neon":
                    s = min(1.0, s * 2.0)
                    v = max(0.2, min(1.0, v * 1.5))
                elif self._mode == "cinema":
                    v = v ** 1.5 # Exaggerate darkness
                    s = min(1.0, s * 1.2)
                else:
                    s = min(1.0, s * 1.5) # Boost saturation significantly
                    v = max(0.1, v)       # Prevent it from going completely dark
                
                r_f, g_f, b_f = colorsys.hsv_to_rgb(h, s, v)
                r, g, b = int(r_f*255), int(g_f*255), int(b_f*255)
                
                # Send to lights using the same high-frequency UDP socket as music sync
                for ip in self._ips:
                    self.bridge.set_color_sync(ip, r, g, b, self._brightness)
                    
            except Exception as e:
                print(f"Screen capture error: {e}")
                time.sleep(1)
                
            elapsed = time.time() - t0
            sleep_time = frame_time - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _get_color(self, img: Image.Image, mode: str):
        if mode == "center_action":
            w, h = img.size
            img = img.crop((w//4, h//4, 3*w//4, 3*h//4))
            img.thumbnail((1, 1))
            return img.getpixel((0, 0))
            
        if mode == "edges_average":
            img.thumbnail((32, 32))
            w, h = img.size
            pixels = []
            for x in range(w):
                pixels.append(img.getpixel((x, 0)))
                pixels.append(img.getpixel((x, h-1)))
            for y in range(1, h-1):
                pixels.append(img.getpixel((0, y)))
                pixels.append(img.getpixel((w-1, y)))
            if not pixels: return (0,0,0)
            avg_r = sum(p[0] for p in pixels) // len(pixels)
            avg_g = sum(p[1] for p in pixels) // len(pixels)
            avg_b = sum(p[2] for p in pixels) // len(pixels)
            return (avg_r, avg_g, avg_b)

        if mode == "dominant":
            # Downscale heavily for speed
            img.thumbnail((64, 64))
            # Quantize to 8 colors
            q_img = img.convert('P', palette=Image.ADAPTIVE, colors=8)
            colors = q_img.getcolors()
            if colors:
                colors.sort(key=lambda x: x[0], reverse=True)
                palette = q_img.getpalette()
                dom_idx = colors[0][1]
                r = palette[dom_idx*3]
                g = palette[dom_idx*3 + 1]
                b = palette[dom_idx*3 + 2]
                return r, g, b
        
        # Default: Average color
        img.thumbnail((1, 1))
        return img.getpixel((0, 0))
