"""
Audio Engine
============
Captures system audio via a loopback device, runs a real-time FFT, applies a 
psychoacoustic A-weighting curve and a 16-band Mel Filterbank, and maps the 
surgical frequency bands to light colors using Spectral Flux.
"""
import colorsys
import time
from typing import Callable, Optional

import numpy as np
import sounddevice as sd
from scipy.signal import windows as scipy_windows

# ─── Config ─────────────────────────────────────────────────────────
NUM_BANDS = 16

# Smooth alphas: fast for bass/treble transients, slow for mids
# Bass (0-2): 0.6, Mids (3-11): 0.3, Highs (12-15): 0.6
SMOOTH_ALPHAS = [0.85, 0.85, 0.80, 0.5, 0.35, 0.30, 0.25, 0.25, 0.25, 0.30, 0.35, 0.45, 0.6, 0.7, 0.75, 0.80]

SAMPLE_RATE  = 44100
CHUNK_SIZE   = 512           # ~11 ms/chunk at 44100 Hz for extremely fast response

AGC_SAMPLES = int(SAMPLE_RATE / CHUNK_SIZE * 3.0) 
MAX_HZ_CMD   = 50            # max light commands / second

# Candidate loopback device name fragments
LOOPBACK_KEYWORDS = [
    "blackhole",        # Mac
    "soundflower",      # Mac
    "loopback",         # Mac / Generic
    "stereomix",        # Windows
    "stereo mix",       # Windows
    "what u hear",      # Windows (SoundBlaster)
    "voicemeeter",      # Windows
    "vb-cable",         # Windows/Mac
    "cable output",     # Windows
    "monitor",          # Linux (PulseAudio)
    "pulse",            # Linux
    "pipewire",         # Linux
    "virtual",          # Generic
]

# ─── DSP Mathematics ─────────────────────────────────────────────────────────

def hz_to_mel(hz):
    return 2595.0 * np.log10(1.0 + hz / 700.0)

def mel_to_hz(mel):
    return 700.0 * (10.0**(mel / 2595.0) - 1.0)

def a_weight_multiplier(f):
    if f < 1e-6:
        return 0.0
    f2 = f**2
    rA = (12194**2 * f2**2) / ( (f2 + 20.6**2) * np.sqrt((f2 + 107.7**2) * (f2 + 737.9**2)) * (f2 + 12194**2) )
    return rA / 0.794 # Normalize so 1000 Hz is approx 1.0

def create_mel_filterbank(sr, n_fft, n_mels=16, fmin=20, fmax=20000):
    n_bins = n_fft // 2 + 1
    min_mel = hz_to_mel(fmin)
    max_mel = hz_to_mel(fmax)
    mels = np.linspace(min_mel, max_mel, n_mels + 2)
    hz = mel_to_hz(mels)
    bins = np.floor((n_fft + 1) * hz / sr).astype(int)
    
    fb = np.zeros((n_mels, n_bins))
    for i in range(1, n_mels + 1):
        left = bins[i - 1]
        center = bins[i]
        right = bins[i + 1]
        
        if center > left:
            fb[i - 1, left:center] = (np.arange(left, center) - left) / (center - left)
        if right > center:
            fb[i - 1, center:right] = (right - np.arange(center, right)) / (right - center)
            
    # Normalize
    enorm = 2.0 / (hz[2:n_mels+2] - hz[0:n_mels])
    fb *= enorm[:, np.newaxis]
    return fb

# ─── Helpers ────────────────────────────────────────────────────────────────────

def clamp_color(v: float) -> int:
    return max(0, min(255, int(v)))

def _clamp_int(v: float, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))

# ── Color Harmony & Interpolation ──
def complementary(hue: float) -> float:
    return (hue + 0.5) % 1.0

def analogous(hue: float, offset: float = 0.08) -> float:
    return (hue + offset) % 1.0

def triadic(hue: float) -> tuple[float, float]:
    return ((hue + 0.333) % 1.0, (hue + 0.667) % 1.0)

def split_complementary(hue: float, offset: float = 0.08) -> tuple[float, float]:
    return ((hue + 0.5 + offset) % 1.0, (hue + 0.5 - offset) % 1.0)

def _circular_lerp(a: float, b: float, alpha: float) -> float:
    d = b - a
    if d > 0.5:
        d -= 1.0
    elif d < -0.5:
        d += 1.0
    return (a + d * alpha) % 1.0

def lerp_color(curr: tuple[float, float, float, float], target: tuple[float, float, float, float], alpha: float) -> tuple[float, float, float, float]:
    cr, cg, cb, cdim = curr
    tr, tg, tb, tdim = target
    
    ch, cs, cv = colorsys.rgb_to_hsv(cr / 255.0, cg / 255.0, cb / 255.0)
    th, ts, tv = colorsys.rgb_to_hsv(tr / 255.0, tg / 255.0, tb / 255.0)
    
    if ts < 0.01: th = ch
    if cs < 0.01: ch = th
    
    h = _circular_lerp(ch, th, alpha)
    s = cs + (ts - cs) * alpha
    v = cv + (tv - cv) * alpha
    dim = cdim + (tdim - cdim) * alpha
    
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return (r * 255.0, g * 255.0, b * 255.0, dim)

# ── Color Palettes ──
class Palettes:
    CYBERPUNK = [(0.83, 1.0, 1.0), (0.75, 1.0, 1.0), (0.50, 1.0, 1.0), (0.90, 1.0, 1.0)] 
    SUNSET = [(0.0, 1.0, 1.0), (0.05, 1.0, 1.0), (0.10, 1.0, 1.0), (0.85, 0.8, 0.5)] 
    DEEP_OCEAN = [(0.55, 1.0, 0.8), (0.60, 1.0, 1.0), (0.65, 1.0, 0.6), (0.50, 0.8, 1.0)] 
    FOREST = [(0.30, 1.0, 0.8), (0.35, 1.0, 1.0), (0.40, 1.0, 0.6), (0.15, 0.8, 0.8)] 
    VAPORWAVE = [(0.95, 1.0, 1.0), (0.80, 0.8, 1.0), (0.45, 1.0, 1.0), (0.05, 0.6, 1.0)] 
    LIQUID_GOLD = [(0.12, 1.0, 0.8), (0.10, 1.0, 1.0), (0.08, 0.8, 1.0), (0.05, 1.0, 0.6)] 
    
    @staticmethod
    def get_color(palette, position: float):
        position = max(0.0, min(1.0, position))
        idx = position * (len(palette) - 1)
        i = int(idx)
        f = idx - i
        if i >= len(palette) - 1:
            return palette[-1]
        c1, c2 = palette[i], palette[i+1]
        h = _circular_lerp(c1[0], c2[0], f)
        s = c1[1] + (c2[1] - c1[1]) * f
        v = c1[2] + (c2[2] - c1[2]) * f
        return h, s, v

class RollingAGC:
    def __init__(self, window_size=AGC_SAMPLES):
        self.buf = []
        self.window_size = window_size

    def push(self, val: float):
        self.buf.append(val)
        if len(self.buf) > self.window_size:
            self.buf.pop(0)

    def get_ref(self) -> float:
        if len(self.buf) < 3: return 0.0001
        return float(np.percentile(self.buf, 92))

class DSPChannel:
    def __init__(self, fft_size, window, mel_fb, a_weights, num_bands, smooth_alphas):
        self.band_energy = [0.0] * num_bands
        self.band_energy_exp = [0.0] * num_bands
        self.band_transient = [0.0] * num_bands
        self.band_envelope = [0.0] * num_bands
        self._prev_raw = [0.0] * num_bands
        self._band_maxes = [0.05] * num_bands
        self._dyn_thresh = [0.0] * num_bands
        
        self.spectral_centroid = 4.5
        self.energy_total = 0.0
        self.spectral_tilt = 0.5      # 0.0 = all treble, 1.0 = all bass
        self.spectral_tilt_slow = 0.5  # glacially slow version for ambient presets
        
        self._fft_buffer = np.zeros(fft_size, dtype=np.float32)
        
        self._fft_size = fft_size
        self._window = window
        self._mel_fb = mel_fb
        self._a_weights = a_weights
        self._num_bands = num_bands
        self._smooth_alphas = smooth_alphas
        
    def _ema(self, cur: float, prev: float, alpha: float) -> float:
        return alpha * cur + (1.0 - alpha) * prev
        
    def process(self, audio_data: np.ndarray, global_agc, sensitivity: float) -> None:
        rms_total = np.sqrt(np.mean(audio_data**2))
        is_silence = rms_total < 0.015
        
        shift = len(audio_data)
        if shift > 0:
            if shift >= self._fft_size:
                self._fft_buffer[:] = audio_data[-self._fft_size:]
            else:
                self._fft_buffer = np.roll(self._fft_buffer, -shift)
                self._fft_buffer[-shift:] = audio_data

        fft = np.abs(np.fft.rfft(self._fft_buffer * self._window))
        weighted_fft = fft * self._a_weights
        mel_energy = np.dot(self._mel_fb, weighted_fft)
        
        s = max(sensitivity, 0.1)
        max_energy = float(np.max(mel_energy))
        
        if not is_silence:
            global_agc.push(max_energy)
            
        ref = max(global_agc.get_ref(), 0.001)

        for i in range(self._num_bands):
            raw = mel_energy[i]
            
            if not is_silence:
                if raw > self._band_maxes[i]:
                    self._band_maxes[i] = raw
                else:
                    self._band_maxes[i] *= 0.997  # Slow decay — slightly slower so highs don't collapse
                    
            local_ref = max(self._band_maxes[i], 1e-6)
            blended_ref = (ref * 0.15) + (local_ref * 0.85)
            
            if is_silence:
                norm = 0.0
                spectral_flux = 0.0
            else:
                linear_norm = min(1.0, max(0.0, (raw / blended_ref) * s))
                norm = linear_norm ** 1.3
                spectral_flux = max(0.0, (raw - self._prev_raw[i]) / max(ref, 1e-6))
                
            self._prev_raw[i] = raw
            
            self.band_energy[i] = self._ema(norm, self.band_energy[i], self._smooth_alphas[i])
            self.band_energy_exp[i] = self.band_energy[i] ** 2.5
            
            trigger_thresh = (0.3 / s) if i < 4 else (0.2 / s)
            
            if spectral_flux > trigger_thresh and spectral_flux > self._dyn_thresh[i]:
                hit_strength = min(1.0, spectral_flux * s * 1.5)
                self.band_transient[i] = hit_strength
                self.band_envelope[i] = max(self.band_envelope[i], hit_strength)
                self._dyn_thresh[i] = spectral_flux * 0.8
            else:
                self.band_transient[i] = 0.0
                self._dyn_thresh[i] *= 0.85
                
            self.band_envelope[i] *= 0.72
            
        weights = np.array(self.band_energy)
        self.energy_total = np.mean(weights)
        if self.energy_total > 0.01:
            indices = np.arange(self._num_bands)
            centroid = np.sum(indices * weights) / np.sum(weights)
            self.spectral_centroid = self._ema(centroid, self.spectral_centroid, 0.1)

        # Spectral tilt: ratio of low energy to total
        low_sum = sum(self.band_energy[0:5])
        high_sum = sum(self.band_energy[9:16])
        total = low_sum + high_sum + 0.001
        raw_tilt = low_sum / total
        self.spectral_tilt = self._ema(raw_tilt, self.spectral_tilt, 0.15)
        self.spectral_tilt_slow = self._ema(raw_tilt, self.spectral_tilt_slow, 0.02)

# ─── AudioEngine ────────────────────────────────────────────────────────────────

class AudioEngine:

    def __init__(self):
        self.is_running  = False
        self.color_mode  = "precision_kick"
        self.sensitivity = 1.0

        self._current_color = {
            "ch1": (0.0, 0.0, 0.0, 10.0),
            "ch2": (0.0, 0.0, 0.0, 10.0)
        }
        
        self._global_agc = RollingAGC()

        # Pre-computed DSP constants
        self._fft_size = 2048
        self._window = scipy_windows.hann(self._fft_size).astype(np.float32)
        self._freqs  = np.fft.rfftfreq(self._fft_size, d=1.0 / SAMPLE_RATE)
        self._mel_fb = create_mel_filterbank(SAMPLE_RATE, self._fft_size, NUM_BANDS)
        self._a_weights = np.array([a_weight_multiplier(f) for f in self._freqs], dtype=np.float32)
        
        # Instantiate 3 independent DSP Channels
        self.dsp_mono = DSPChannel(self._fft_size, self._window, self._mel_fb, self._a_weights, NUM_BANDS, SMOOTH_ALPHAS)
        self.dsp_L = DSPChannel(self._fft_size, self._window, self._mel_fb, self._a_weights, NUM_BANDS, SMOOTH_ALPHAS)
        self.dsp_R = DSPChannel(self._fft_size, self._window, self._mel_fb, self._a_weights, NUM_BANDS, SMOOTH_ALPHAS)
        
        # Rate limit
        self._min_dt   = 1.0 / MAX_HZ_CMD
        self._last_t   = 0.0

        self._stream:   Optional[sd.InputStream] = None
        self._callback: Optional[Callable]       = None

    # Properties to alias legacy preset methods to the mono DSP channel
    @property
    def band_energy(self): return self.dsp_mono.band_energy
    
    @property
    def band_energy_exp(self): return self.dsp_mono.band_energy_exp
    
    @property
    def band_transient(self): return self.dsp_mono.band_transient
    
    @property
    def band_envelope(self): return self.dsp_mono.band_envelope
    
    @property
    def spectral_centroid(self): return self.dsp_mono.spectral_centroid
    
    @property
    def energy_total(self): return self.dsp_mono.energy_total

    # ── Device helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def list_input_devices() -> list[dict]:
        result = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                result.append({
                    "index":    i,
                    "name":     d["name"],
                    "channels": d["max_input_channels"],
                })
        return result

    @staticmethod
    def find_loopback() -> tuple[Optional[int], str]:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                n = d["name"].lower()
                for kw in LOOPBACK_KEYWORDS:
                    if kw in n:
                        return i, d["name"]
        return None, ""

    # ── DSP ─────────────────────────────────────────────────────────────────────

    def _ema(self, cur: float, prev: float, alpha: float) -> float:
        return alpha * cur + (1.0 - alpha) * prev

    def _process(self, data: np.ndarray):
        if data.ndim > 1 and data.shape[1] >= 2:
            left = data[:, 0].astype(np.float32)
            right = data[:, 1].astype(np.float32)
            mono = data.mean(axis=1).astype(np.float32)
            
            # Process all three channels independently
            self.dsp_mono.process(mono, self._global_agc, self.sensitivity)
            self.dsp_L.process(left, self._global_agc, self.sensitivity)
            self.dsp_R.process(right, self._global_agc, self.sensitivity)
        else:
            mono = data.ravel().astype(np.float32)
            self.dsp_mono.process(mono, self._global_agc, self.sensitivity)
            self.dsp_L.process(mono, self._global_agc, self.sensitivity)
            self.dsp_R.process(mono, self._global_agc, self.sensitivity)

    # ── Category 1: Punchy & Percussive ─────────────────────────────────────────

    def _precision_kick(self) -> tuple[dict, float, float]:
        env = max(self.band_envelope[0:3])
        r = _clamp_int(10 + env * 245, 0, 255)
        g = _clamp_int(0 + env * 100, 0, 255)
        b = _clamp_int(20 + env * 235, 0, 255)
        dim = _clamp_int(10 + env * 90, 10, 100)
        c = (r, g, b, dim)
        return {"ch1": c, "ch2": c}, 1.0, 0.4

    def _rhythm_lead(self) -> tuple[dict, float, float]:
        rhythm_env = max(self.band_envelope[0:4])
        lead_env = max(self.band_envelope[10:16])
        
        rh, rs, rv = Palettes.get_color(Palettes.CYBERPUNK, 0.1)
        lh, ls, lv = Palettes.get_color(Palettes.CYBERPUNK, 0.8)
        
        r1, g1, b1 = colorsys.hsv_to_rgb(rh, rs, rv)
        c1 = (int(r1*255), int(g1*255), int(b1*255), _clamp_int(10 + rhythm_env * 90, 10, 100))
        
        r2, g2, b2 = colorsys.hsv_to_rgb(lh, ls, lv)
        c2 = (int(r2*255), int(g2*255), int(b2*255), _clamp_int(10 + lead_env * 90, 10, 100))
        
        return {"ch1": c1, "ch2": c2}, 1.0, 0.5

    def _strobe_matrix(self) -> tuple[dict, float, float]:
        if not hasattr(self, "_strobe_state"):
            self._strobe_state = {"last_hue": 0.0, "toggle": True}
            
        trans_any = any(t > 0.4 for t in self.band_transient)
        
        if trans_any:
            import random
            self._strobe_state["last_hue"] = random.random()
            self._strobe_state["toggle"] = not self._strobe_state["toggle"]
            
            r, g, b = colorsys.hsv_to_rgb(self._strobe_state["last_hue"], 1.0, 1.0)
            flash = (int(r*255), int(g*255), int(b*255), 100)
            dim = (0, 0, 0, 10)
            
            if self._strobe_state["toggle"]:
                return {"ch1": flash, "ch2": dim}, 1.0, 0.2
            else:
                return {"ch1": dim, "ch2": flash}, 1.0, 0.2
                
        return {"ch1": (0,0,0,10), "ch2": (0,0,0,10)}, 1.0, 0.2

    # ── Category 2: Flowing & Ambient ───────────────────────────────────────────

    def _spectral_drift(self) -> tuple[dict, float, float]:
        hue = self.spectral_centroid / (NUM_BANDS - 1)
        e = min(1.0, self.energy_total * self.sensitivity)
        bright = _clamp_int(20 + e * 80, 10, 100)
        
        r1, g1, b1 = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        c1 = (int(r1*255), int(g1*255), int(b1*255), bright)
        
        r2, g2, b2 = colorsys.hsv_to_rgb((hue + 0.1) % 1.0, 1.0, 1.0)
        c2 = (int(r2*255), int(g2*255), int(b2*255), bright)
        
        return {"ch1": c1, "ch2": c2}, 0.1, 0.1

    def _aurora_breathing(self) -> tuple[dict, float, float]:
        low_e = min(1.0, (sum(self.band_energy[0:5]) / 5.0) * self.sensitivity * 1.5)
        mid_e = min(1.0, (sum(self.band_energy[5:12]) / 7.0) * self.sensitivity * 1.5)
        
        h1, s1, v1 = Palettes.get_color(Palettes.DEEP_OCEAN, low_e)
        h2, s2, v2 = Palettes.get_color(Palettes.DEEP_OCEAN, mid_e)
        
        r1, g1, b1 = colorsys.hsv_to_rgb(h1, s1, v1)
        c1 = (int(r1*255), int(g1*255), int(b1*255), _clamp_int(20 + low_e * 80, 10, 100))
        
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v2)
        c2 = (int(r2*255), int(g2*255), int(b2*255), _clamp_int(20 + mid_e * 80, 10, 100))
        
        return {"ch1": c1, "ch2": c2}, 0.15, 0.1

    def _synesthesia(self) -> tuple[dict, float, float]:
        e1 = min(1.0, (sum(self.band_energy_exp[0:8]) / 8.0) * self.sensitivity * 1.5)
        e2 = min(1.0, (sum(self.band_energy_exp[8:16]) / 8.0) * self.sensitivity * 1.5)
        
        h1, s1, v1 = Palettes.get_color(Palettes.SUNSET, e1)
        h2, s2, v2 = Palettes.get_color(Palettes.CYBERPUNK, e2)
        
        r1, g1, b1 = colorsys.hsv_to_rgb(h1, s1, v1)
        c1 = (int(r1*255), int(g1*255), int(b1*255), _clamp_int(10 + e1 * 90, 10, 100))
        
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v2)
        c2 = (int(r2*255), int(g2*255), int(b2*255), _clamp_int(10 + e2 * 90, 10, 100))
        
        return {"ch1": c1, "ch2": c2}, 0.6, 0.2

    def _neon_heartbeat(self) -> tuple[dict, float, float]:
        kick_env = min(1.0, max(self.band_envelope[0:3]) * 1.8)
        snare_env = min(1.0, max(self.band_envelope[10:14]) * 1.8)
        
        h1, s1, v1 = Palettes.get_color(Palettes.VAPORWAVE, kick_env)
        h2, s2, v2 = Palettes.get_color(Palettes.VAPORWAVE, 1.0 - snare_env) 
        
        r1, g1, b1 = colorsys.hsv_to_rgb(h1, s1, v1)
        c1 = (int(r1*255), int(g1*255), int(b1*255), _clamp_int(10 + kick_env * 90, 10, 100))
        
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v2)
        c2 = (int(r2*255), int(g2*255), int(b2*255), _clamp_int(10 + snare_env * 90, 10, 100))
        
        return {"ch1": c1, "ch2": c2}, 1.0, 0.4

    def _liquid_gold(self) -> tuple[dict, float, float]:
        mid_e = min(1.0, (sum(self.band_energy[4:10]) / 6.0) * self.sensitivity * 1.5)
        high_e = min(1.0, (sum(self.band_energy[10:16]) / 6.0) * self.sensitivity * 1.5)
        
        h1, s1, v1 = Palettes.get_color(Palettes.LIQUID_GOLD, mid_e)
        h2, s2, v2 = Palettes.get_color(Palettes.LIQUID_GOLD, high_e)
        
        r1, g1, b1 = colorsys.hsv_to_rgb(h1, s1, v1)
        c1 = (int(r1*255), int(g1*255), int(b1*255), _clamp_int(15 + mid_e * 85, 10, 100))
        
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v2)
        c2 = (int(r2*255), int(g2*255), int(b2*255), _clamp_int(15 + high_e * 85, 10, 100))
        
        return {"ch1": c1, "ch2": c2}, 0.2, 0.08

    def _stellar_pulse(self) -> tuple[dict, float, float]:
        hi_trans = max(self.band_transient[12:16]) * self.sensitivity
        low_env = min(1.0, max(self.band_envelope[0:3]) * 1.5)
        
        vh = 0.75
        vr, vg, vb = colorsys.hsv_to_rgb(vh, 1.0, 0.8)
        violet = (int(vr*255), int(vg*255), int(vb*255), _clamp_int(10 + low_env * 70, 10, 80))
        
        if hi_trans > 0.25:
            flash = (255, 255, 255, 100)
            dim = (10, 0, 30, 10)
            
            if not hasattr(self, "_stellar_toggle"):
                self._stellar_toggle = True
            self._stellar_toggle = not self._stellar_toggle
            if self._stellar_toggle:
                return {"ch1": flash, "ch2": dim}, 1.0, 0.15
            else:
                return {"ch1": dim, "ch2": flash}, 1.0, 0.15
                
        return {"ch1": violet, "ch2": violet}, 1.0, 0.2

    def _quantum_flux(self) -> tuple[dict, float, float]:
        hue1 = (self.spectral_centroid / (NUM_BANDS - 1)) % 1.0
        hue2 = (hue1 + 0.3) % 1.0
        
        env = min(1.0, max(self.band_envelope) * 1.5)
        bright = _clamp_int(10 + env * 90, 10, 100)
        
        r1, g1, b1 = colorsys.hsv_to_rgb(hue1, 1.0, 1.0)
        c1 = (int(r1*255), int(g1*255), int(b1*255), bright)
        
        r2, g2, b2 = colorsys.hsv_to_rgb(hue2, 1.0, 1.0)
        c2 = (int(r2*255), int(g2*255), int(b2*255), bright)
        
        return {"ch1": c1, "ch2": c2}, 0.8, 0.4

    # ── Category 3: Spatial Stereo ──────────────────────────────────────────────

    def _surround_sound(self) -> tuple[dict, float, float]:
        # ch1 gets mapped to Left audio
        env_L = min(1.0, max(self.dsp_L.band_envelope) * 1.5)
        hue_L = (self.dsp_L.spectral_centroid / (NUM_BANDS - 1)) % 1.0
        bright_L = _clamp_int(10 + env_L * 90, 10, 100)
        rL, gL, bL = colorsys.hsv_to_rgb(hue_L, 1.0, 1.0)
        c1 = (int(rL*255), int(gL*255), int(bL*255), bright_L)

        # ch2 gets mapped to Right audio
        env_R = min(1.0, max(self.dsp_R.band_envelope) * 1.5)
        hue_R = (self.dsp_R.spectral_centroid / (NUM_BANDS - 1)) % 1.0
        bright_R = _clamp_int(10 + env_R * 90, 10, 100)
        rR, gR, bR = colorsys.hsv_to_rgb(hue_R, 1.0, 1.0)
        c2 = (int(rR*255), int(gR*255), int(bR*255), bright_R)

        return {"ch1": c1, "ch2": c2}, 1.0, 0.2

    # ── Category 4: Full-Spectrum 16-Band Presets ───────────────────────────────

    def _tectonic(self) -> tuple[dict, float, float]:
        """The Room Splits in Half — seismic bass vs cerebral highs."""
        be = self.band_energy
        env = self.band_envelope

        # Ch1: LOW half (bands 0-4). Local centroid within low range.
        low_energies = [be[i] for i in range(5)]
        low_total = sum(low_energies) + 0.001
        low_centroid = sum(i * low_energies[i] for i in range(5)) / low_total
        low_pos = low_centroid / 4.0  # 0..1 within the low range
        low_env = min(1.0, max(env[0:5]) * 1.5)

        # Palette: deep red (sub) → amber (upper bass)
        h1 = 0.0 + low_pos * 0.08  # hue 0.0 to 0.08
        s1 = 1.0
        v1 = 0.3 + low_env * 0.7
        r1, g1, b1 = colorsys.hsv_to_rgb(h1, s1, v1)
        dim1 = _clamp_int(10 + low_env * 90, 10, 100)
        c1 = (int(r1*255), int(g1*255), int(b1*255), dim1)

        # Ch2: HIGH half (bands 5-15). Local centroid within high range.
        high_energies = [be[i] for i in range(5, 16)]
        high_total = sum(high_energies) + 0.001
        high_centroid = sum(i * high_energies[i] for i in range(11)) / high_total
        high_pos = high_centroid / 10.0  # 0..1 within the high range
        high_env = min(1.0, max(env[5:16]) * 1.5)

        # Palette: electric blue (vocals) → violet (cymbals) → white-ish (air)
        h2 = 0.55 + high_pos * 0.2  # hue 0.55 to 0.75
        s2 = max(0.4, 1.0 - high_pos * 0.6)
        v2 = 0.3 + high_env * 0.7
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v2)
        dim2 = _clamp_int(10 + high_env * 90, 10, 100)
        c2 = (int(r2*255), int(g2*255), int(b2*255), dim2)

        return {"ch1": c1, "ch2": c2}, 0.9, 0.15

    def _vocal_isolation(self) -> tuple[dict, float, float]:
        """The Singer Steps Forward — warm vocals vs cool instruments."""
        be = self.band_energy
        s = self.sensitivity

        # Vocal range: bands 5-8 (~500 Hz to 2 kHz)
        vocal_energy = min(1.0, (sum(be[5:9]) / 4.0) * s * 1.8)

        # Instrument range: everything else
        inst_bands = be[0:5] + be[9:16]
        instrument_energy = min(1.0, (sum(inst_bands) / 11.0) * s * 1.5)

        # Vocal centroid within 5-8 to shift hue
        vocal_weights = [be[i] for i in range(5, 9)]
        vw_total = sum(vocal_weights) + 0.001
        vocal_centroid = sum((i-5) * vocal_weights[i-5] for i in range(5, 9)) / vw_total
        vocal_pos = vocal_centroid / 3.0  # 0..1

        # Ch1: warm amber → rose based on vocal position
        h1 = 0.08 + vocal_pos * 0.90  # amber(0.08) → rose(0.98)
        if h1 > 1.0: h1 -= 1.0
        s1 = 0.85
        v1 = 0.2 + vocal_energy * 0.8
        r1, g1, b1 = colorsys.hsv_to_rgb(h1, s1, v1)
        dim1 = _clamp_int(15 + vocal_energy * 85, 10, 100)
        c1 = (int(r1*255), int(g1*255), int(b1*255), dim1)

        # Ch2: teal → violet based on low-vs-high instrument balance
        low_inst = sum(be[0:5])
        high_inst = sum(be[9:16])
        inst_balance = high_inst / (low_inst + high_inst + 0.001)
        h2 = 0.50 + inst_balance * 0.25  # teal(0.50) → violet(0.75)
        s2 = 0.8
        v2 = 0.2 + instrument_energy * 0.8
        r2, g2, b2 = colorsys.hsv_to_rgb(h2, s2, v2)
        dim2 = _clamp_int(15 + instrument_energy * 85, 10, 100)
        c2 = (int(r2*255), int(g2*255), int(b2*255), dim2)

        return {"ch1": c1, "ch2": c2}, 0.3, 0.12

    def _spectral_prism(self) -> tuple[dict, float, float]:
        """The Full Rainbow Breathes — 7 spectral groups → weighted color blend."""
        be = self.band_energy

        # 7 perceptual groups with anchor hues
        groups = [
            (max(be[0:1] or [0]),   0.00),  # rumble → red
            (max(be[1:3] or [0]),   0.07),  # punch → orange
            (max(be[3:5] or [0]),   0.12),  # warmth → gold
            (max(be[5:9] or [0]),   0.30),  # voice → green
            (max(be[9:11] or [0]),  0.55),  # snap → cyan
            (max(be[11:13] or [0]), 0.72),  # shimmer → blue
            (max(be[13:16] or [0]), 0.85),  # air → violet
        ]

        # Ch1: warm half (groups 0-3)
        warm_groups = groups[0:4]
        warm_weight_total = sum(g[0]**2 for g in warm_groups) + 0.001
        warm_hue = sum(g[0]**2 * g[1] for g in warm_groups) / warm_weight_total
        warm_max = max(g[0] for g in warm_groups)
        warm_val = 0.15 + min(1.0, warm_max * 1.3) * 0.85
        r1, g1, b1 = colorsys.hsv_to_rgb(warm_hue % 1.0, 0.9, warm_val)
        dim1 = _clamp_int(10 + warm_max * 90, 10, 100)
        c1 = (int(r1*255), int(g1*255), int(b1*255), dim1)

        # Ch2: cool half (groups 3-6, voice shared)
        cool_groups = groups[3:7]
        cool_weight_total = sum(g[0]**2 for g in cool_groups) + 0.001
        cool_hue = sum(g[0]**2 * g[1] for g in cool_groups) / cool_weight_total
        cool_max = max(g[0] for g in cool_groups)
        cool_val = 0.15 + min(1.0, cool_max * 1.3) * 0.85
        r2, g2, b2 = colorsys.hsv_to_rgb(cool_hue % 1.0, 0.9, cool_val)
        dim2 = _clamp_int(10 + cool_max * 90, 10, 100)
        c2 = (int(r2*255), int(g2*255), int(b2*255), dim2)

        return {"ch1": c1, "ch2": c2}, 0.25, 0.08

    def _percussion_anatomy(self) -> tuple[dict, float, float]:
        """You Can See Every Hit — kick/snare/hat decomposed across bulbs."""
        bt = self.band_transient
        be = self.band_energy

        kick_hit = max(bt[0:2] or [0])
        snare_hit = max(bt[3:5] or [0])
        hat_hit = max(bt[11:13] or [0])
        groove = min(1.0, (sum(be[5:9]) / 4.0) * self.sensitivity * 1.5)

        # Ch1: kick (deep red) or snare (white) or dim purple glow
        if kick_hit > 0.3:
            c1 = (255, 20, 0, 100)
        elif snare_hit > 0.25:
            c1 = (255, 255, 255, 95)
        else:
            dim1 = _clamp_int(10 + groove * 40, 10, 50)
            c1 = (40, 0, 60, dim1)

        # Ch2: hat (electric cyan) or dim purple glow
        if hat_hit > 0.2:
            c2 = (0, 255, 220, 90)
        else:
            dim2 = _clamp_int(10 + groove * 40, 10, 50)
            c2 = (30, 0, 50, dim2)

        return {"ch1": c1, "ch2": c2}, 1.0, 0.35

    def _slow_burn(self) -> tuple[dict, float, float]:
        """Lava Under Glass — glacially slow color migration based on spectral tilt."""
        tilt = self.dsp_mono.spectral_tilt_slow  # 0=treble, 1=bass
        e = min(1.0, self.energy_total * self.sensitivity)

        # Hue: red (bass-heavy) → warm white (treble-heavy)
        h = 0.0 + (1.0 - tilt) * 0.15  # 0.0 to 0.15
        s = 0.3 + tilt * 0.7           # 0.3 (white-ish) to 1.0 (saturated)
        v = 0.15 + e * 0.5             # never fully bright — molten look

        r1, g1, b1 = colorsys.hsv_to_rgb(h, s, v)
        dim1 = _clamp_int(20 + e * 60, 10, 80)
        c1 = (int(r1*255), int(g1*255), int(b1*255), dim1)

        # Ch2: offset by tiny hue shift for depth
        r2, g2, b2 = colorsys.hsv_to_rgb((h + 0.02) % 1.0, s * 0.95, v * 0.9)
        dim2 = _clamp_int(18 + e * 55, 10, 75)
        c2 = (int(r2*255), int(g2*255), int(b2*255), dim2)

        return {"ch1": c1, "ch2": c2}, 0.02, 0.02

    def _neural_storm(self) -> tuple[dict, float, float]:
        """Chaos Theory in RGB — calm blue until full-spectrum hits trigger sensory overload."""
        be = self.band_energy
        bt = self.band_transient

        if not hasattr(self, "_storm_state"):
            self._storm_state = {"hue": 0.65, "frame": 0}

        # Count how many of 7 perceptual groups are active
        group_maxes = [
            max(be[0:1] or [0]),    # rumble
            max(be[1:3] or [0]),    # punch
            max(be[3:5] or [0]),    # warmth
            max(be[5:9] or [0]),    # voice
            max(be[9:11] or [0]),   # snap
            max(be[11:13] or [0]),  # shimmer
            max(be[13:16] or [0]), # air
        ]
        activation_count = sum(1 for g in group_maxes if g > 0.25)
        chaos = activation_count / 7.0

        has_any_transient = any(t > 0.3 for t in bt)
        if has_any_transient:
            self._storm_state["frame"] += 1

        if chaos < 0.3:
            # Calm: deep navy breathing
            e = min(1.0, self.energy_total * self.sensitivity)
            v = 0.15 + e * 0.2
            r, g, b = colorsys.hsv_to_rgb(0.65, 0.8, v)
            dim = _clamp_int(15 + e * 35, 10, 50)
            c = (int(r*255), int(g*255), int(b*255), dim)
            return {"ch1": c, "ch2": c}, 0.1, 0.1
        else:
            # Chaos: rapid hue cycling, alternating brightness
            self._storm_state["hue"] = (self._storm_state["hue"] + chaos * 0.15) % 1.0
            h1 = self._storm_state["hue"]
            h2 = (h1 + 0.5) % 1.0  # complementary

            frame = self._storm_state["frame"]
            is_even = (frame % 2) == 0

            r1, g1, b1 = colorsys.hsv_to_rgb(h1, 1.0, 1.0)
            r2, g2, b2 = colorsys.hsv_to_rgb(h2, 1.0, 1.0)

            if is_even:
                c1 = (int(r1*255), int(g1*255), int(b1*255), 100)
                c2 = (int(r2*255), int(g2*255), int(b2*255), 20)
            else:
                c1 = (int(r1*255), int(g1*255), int(b1*255), 20)
                c2 = (int(r2*255), int(g2*255), int(b2*255), 100)

            attack = 1.0
            decay = 0.6 if chaos > 0.5 else 0.1
            return {"ch1": c1, "ch2": c2}, attack, decay

    def _concert_hall(self) -> tuple[dict, float, float]:
        """You're in the Third Row — warm spotlight on lead + cool room wash."""
        be = self.band_energy
        s = self.sensitivity

        # Find the dominant mid-range band (4-10) — this is the "lead instrument"
        mid_bands = be[4:11]
        peak_idx = int(np.argmax(mid_bands))  # 0..6 within mid_bands
        peak_band = peak_idx + 4              # actual band index 4..10
        peak_energy = min(1.0, mid_bands[peak_idx] * s * 1.5)

        # Ch1: warm spotlight — hue shifts based on which mid band dominates
        # band 4 → warm amber (0.06), band 10 → rose-pink (0.95)
        spot_hue = 0.06 + (peak_idx / 6.0) * 0.89  # 0.06 to 0.95
        if spot_hue > 1.0: spot_hue -= 1.0
        spot_v = 0.15 + peak_energy * 0.85
        r1, g1, b1 = colorsys.hsv_to_rgb(spot_hue, 0.85, spot_v)
        dim1 = _clamp_int(15 + peak_energy * 85, 10, 100)
        c1 = (int(r1*255), int(g1*255), int(b1*255), dim1)

        # Ch2: cool room wash — sub-bass rumble + high-freq reverb tails
        room_energy = min(1.0, ((sum(be[0:3])/3.0 + sum(be[12:16])/4.0) / 2.0) * s * 1.5)
        room_v = 0.1 + room_energy * 0.6
        r2, g2, b2 = colorsys.hsv_to_rgb(0.58, 0.6, room_v)
        dim2 = _clamp_int(15 + room_energy * 70, 10, 85)
        c2 = (int(r2*255), int(g2*255), int(b2*255), dim2)

        return {"ch1": c1, "ch2": c2}, 0.4, 0.15

    # ── 3-Band Presets ────────────────────────────────────────────────────────
    
    def _3band_classic(self) -> tuple[dict, float, float]:
        """A simple, punchy 3-band preset that drives Ch1 with lows, Ch2 with highs, and overrides the visualizer."""
        be = self.band_energy
        s = self.sensitivity
        
        # Aggregate 16 bands into 3 bands
        low = min(1.0, (sum(be[0:4]) / 4.0) * s * 1.5)
        mid = min(1.0, (sum(be[4:10]) / 6.0) * s * 1.5)
        high = min(1.0, (sum(be[10:16]) / 6.0) * s * 1.5)
        
        # Override the UI payload directly on the engine instance
        self._current_visualizer_bands = [low, mid, high]
        
        # Light Logic
        # Ch1 (Bass): Orange to Red
        r1, g1, b1 = colorsys.hsv_to_rgb(0.05, 1.0, 0.4 + low * 0.6)
        dim1 = _clamp_int(10 + low * 90, 10, 100)
        c1 = (int(r1*255), int(g1*255), int(b1*255), dim1)
        
        # Ch2 (Treble): Cyan to Blue
        r2, g2, b2 = colorsys.hsv_to_rgb(0.55, 1.0, 0.4 + high * 0.6)
        dim2 = _clamp_int(10 + high * 90, 10, 100)
        c2 = (int(r2*255), int(g2*255), int(b2*255), dim2)
        
        return {"ch1": c1, "ch2": c2}, 1.0, 0.5
        
    def _3band_split(self) -> tuple[dict, float, float]:
        """Contrasting 3-band preset."""
        be = self.band_energy
        s = self.sensitivity
        
        low = min(1.0, (sum(be[0:4]) / 4.0) * s * 1.5)
        mid = min(1.0, (sum(be[4:10]) / 6.0) * s * 1.5)
        high = min(1.0, (sum(be[10:16]) / 6.0) * s * 1.5)
        
        self._current_visualizer_bands = [low, mid, high]
        
        # High contrast strobe mapping
        c1 = (255, 0, 255, _clamp_int(10 + low * 90, 10, 100)) # Magenta lows
        c2 = (0, 255, 0, _clamp_int(10 + high * 90, 10, 100))  # Green highs
        
        return {"ch1": c1, "ch2": c2}, 1.0, 0.4

    # ── Router ──────────────────────────────────────────────────────────────────

    def _map_color(self) -> tuple[dict, float, float]:
        mode = self.color_mode
        attack_alpha = 1.0
        decay_alpha  = 0.55

        if   mode == "precision_kick":      res = self._precision_kick()
        elif mode == "rhythm_lead":         res = self._rhythm_lead()
        elif mode == "strobe_matrix":       res = self._strobe_matrix()
        elif mode == "neon_heartbeat":      res = self._neon_heartbeat()
        elif mode == "stellar_pulse":       res = self._stellar_pulse()
        elif mode == "quantum_flux":        res = self._quantum_flux()
        elif mode == "spectral_drift":      res = self._spectral_drift()
        elif mode == "aurora_breathing":    res = self._aurora_breathing()
        elif mode == "synesthesia":         res = self._synesthesia()
        elif mode == "liquid_gold":         res = self._liquid_gold()
        elif mode == "surround_sound":      res = self._surround_sound()
        # Full-Spectrum 16-Band presets
        elif mode == "tectonic":            res = self._tectonic()
        elif mode == "vocal_isolation":     res = self._vocal_isolation()
        elif mode == "spectral_prism":      res = self._spectral_prism()
        elif mode == "percussion_anatomy":  res = self._percussion_anatomy()
        elif mode == "slow_burn":           res = self._slow_burn()
        elif mode == "neural_storm":        res = self._neural_storm()
        elif mode == "concert_hall":        res = self._concert_hall()
        # 3-Band Engine presets
        elif mode == "3band_classic":       res = self._3band_classic()
        elif mode == "3band_split":         res = self._3band_split()
        else: 
            res = self._precision_kick()

        if isinstance(res, tuple) and len(res) == 3:
            return res
        if isinstance(res, tuple) and len(res) == 2:
            channels, preset_alpha = res
            return channels, 1.0, preset_alpha
        return res, attack_alpha, decay_alpha

    # ── sounddevice callback (runs in audio thread) ──────────────────────────────

    def _sd_callback(self, indata, frames, time_info, status):
        try:
            self._process(indata)
            
            now = time.monotonic()
            if now - self._last_t < self._min_dt:
                return
            self._last_t = now

            try:
                target_channels, attack_alpha, decay_alpha = self._map_color()
                
                has_transient = any(t > 0.3 for t in self.dsp_mono.band_transient)
                
                for ch_key in ("ch1", "ch2"):
                    curr = self._current_color.get(ch_key, (0.0, 0.0, 0.0, 10.0))
                    tgt  = target_channels.get(ch_key, (0.0, 0.0, 0.0, 0.0))
                    
                    if has_transient:
                        self._current_color[ch_key] = tgt
                    else:
                        curr_brightness = curr[3]
                        tgt_brightness  = tgt[3]
                        alpha = attack_alpha if tgt_brightness >= curr_brightness else decay_alpha
                        self._current_color[ch_key] = lerp_color(curr, tgt, alpha)
                
                out_channels = {
                    ch: (int(self._current_color[ch][0]),
                         int(self._current_color[ch][1]),
                         int(self._current_color[ch][2]),
                         int(self._current_color[ch][3]))
                    for ch in ("ch1", "ch2")
                }
            except Exception as e:
                import sys, traceback
                print(f"Preset error in {self.color_mode}: {e}\n{traceback.format_exc()}", file=sys.stderr)
                out_channels = {"ch1": (0, 0, 0, 0), "ch2": (0, 0, 0, 0)}

            if self._callback:
                bands_to_send = getattr(self, '_current_visualizer_bands', self.band_energy.copy())
                self._callback(out_channels, bands_to_send)
                # Reset override so it doesn't stick when switching back to 16-band
                if hasattr(self, '_current_visualizer_bands'):
                    del self._current_visualizer_bands

        except Exception as e:
            import sys, traceback
            sys.stderr.write(f"CRITICAL Callback error: {e}\n{traceback.format_exc()}\n")
            sys.stderr.flush()

    # ── Public API ──────────────────────────────────────────────────────────────

    def start(self, device_index: int, callback: Callable,
              color_mode: str = "precision_kick", sensitivity: float = 1.0) -> tuple[bool, str]:
        if self.is_running:
            self.stop()

        self.color_mode  = color_mode
        self.sensitivity = sensitivity
        self._callback   = callback
        self._last_t     = 0.0

        try:
            device_info = sd.query_devices(device_index, 'input')
            channels = min(2, int(device_info['max_input_channels']))
            
            self._stream = sd.InputStream(
                device=device_index,
                channels=channels,
                samplerate=SAMPLE_RATE,
                blocksize=CHUNK_SIZE,
                dtype="float32",
                callback=self._sd_callback,
                latency="low",
            )
            self._stream.start()
            self.is_running = True
            return True, ""
        except Exception as exc:
            self._stream = None
            msg = str(exc)
            if "permission" in msg.lower() or "PermissionError" in type(exc).__name__:
                msg = ("Microphone access denied. Open System Settings → "
                       "Privacy & Security → Microphone and allow your Terminal.")
            return False, msg

    def stop(self):
        self.is_running = False
        self._callback  = None
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        
        # Reset DSP Channels
        for dsp in [self.dsp_mono, self.dsp_L, self.dsp_R]:
            dsp.band_energy = [0.0] * NUM_BANDS
            dsp.band_energy_exp = [0.0] * NUM_BANDS
            dsp.band_transient = [0.0] * NUM_BANDS
            dsp.band_envelope = [0.0] * NUM_BANDS
            dsp._prev_raw = [0.0] * NUM_BANDS
            dsp._band_maxes = [0.05] * NUM_BANDS
            dsp._dyn_thresh = [0.0] * NUM_BANDS
            dsp.energy_total = 0.0
            dsp.spectral_tilt = 0.5
            dsp.spectral_tilt_slow = 0.5
            dsp._fft_buffer.fill(0)
            
        self._current_color = {
            "ch1": (0.0, 0.0, 0.0, 10.0),
            "ch2": (0.0, 0.0, 0.0, 10.0)
        }
        self._global_agc.buf.clear()

    def set_sensitivity(self, v: float):
        self.sensitivity = max(0.1, min(5.0, float(v)))

    def set_mode(self, mode: str):
        self.color_mode = mode
