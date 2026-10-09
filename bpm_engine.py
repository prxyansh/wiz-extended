"""
BPM Engine — Real-Time Beat Detection & Light Pulsing
=====================================================
Uses energy-based onset detection on system audio (BlackHole) to detect BPM
and pulse WiZ lights in sync with the beat.

Algorithm:
1. Compute spectral flux (bass-weighted) every ~11ms chunk
2. Detect onsets when flux exceeds adaptive threshold
3. Maintain rolling buffer of onset timestamps (last 8 seconds)
4. Compute BPM via autocorrelation of inter-onset intervals
5. Phase-lock a beat grid to detected onsets
6. Fire light commands precisely on the beat grid ticks
"""

import threading
import time
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 44100
CHUNK_SIZE = 512        # ~11.6ms
BPM_MIN = 60
BPM_MAX = 200
ONSET_HISTORY = 8.0     # seconds of onset history to keep

class BPMEngine:
    def __init__(self, wiz_bridge):
        self.wiz = wiz_bridge
        self._stream = None
        self._stop_event = threading.Event()
        self._beat_thread = None
        
        # State
        self.active_ips = []
        self.distribution = "uniform"
        self.current_bpm = 0.0
        self.confidence = 0.0
        self.is_running = False
        self.beat_color = (0, 255, 128)  # Default neon green
        
        # DSP state
        self._prev_flux = 0.0
        self._onset_times = []           # list of time.monotonic() timestamps
        self._adaptive_threshold = 0.0
        self._prev_spectrum = None
        self._fft_size = 1024
        self._window = np.hanning(self._fft_size).astype(np.float32)
        self._fft_buffer = np.zeros(self._fft_size, dtype=np.float32)
        
        # Beat grid
        self._grid_phase = 0.0           # monotonic time of last grid beat
        self._grid_period = 0.5          # seconds per beat (120 BPM default)
    
    def start(self, device_index: int, ips: list, distribution: str = "uniform"):
        self.stop()
        self.active_ips = ips
        self.distribution = distribution
        self._stop_event.clear()
        self._onset_times.clear()
        self._prev_spectrum = None
        self.is_running = True
        
        self._stream = sd.InputStream(
            device=device_index,
            channels=1,
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SIZE,
            dtype="float32",
            callback=self._audio_callback,
            latency="low",
        )
        self._stream.start()
        
        self._beat_thread = threading.Thread(target=self._beat_loop, daemon=True)
        self._beat_thread.start()
        print(f"BPMEngine: Started on device {device_index}")
    
    def stop(self):
        self._stop_event.set()
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except: pass
            self._stream = None
        if self._beat_thread:
            self._beat_thread.join(timeout=1.0)
        self.is_running = False
        self.current_bpm = 0.0
        print("BPMEngine: Stopped")
    
    def get_status(self):
        return {
            "is_running": self.is_running,
            "bpm": round(self.current_bpm, 1),
            "confidence": round(self.confidence, 2),
        }
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Called ~86 times/sec. Detects onsets via bass-weighted spectral flux."""
        mono = indata[:, 0] if indata.ndim > 1 else indata.ravel()
        
        # Shift buffer
        shift = len(mono)
        if shift >= self._fft_size:
            self._fft_buffer[:] = mono[-self._fft_size:]
        else:
            self._fft_buffer = np.roll(self._fft_buffer, -shift)
            self._fft_buffer[-shift:] = mono
        
        # FFT
        spectrum = np.abs(np.fft.rfft(self._fft_buffer * self._window))
        
        # Bass-weight: only look at bins below ~200 Hz for kick detection
        bass_cutoff = int(200 * self._fft_size / SAMPLE_RATE)
        bass_spectrum = spectrum[:bass_cutoff]
        
        if self._prev_spectrum is not None and len(self._prev_spectrum) == len(bass_spectrum):
            # Spectral flux (half-wave rectified — only increases)
            flux = np.sum(np.maximum(0, bass_spectrum - self._prev_spectrum))
        else:
            flux = 0.0
        
        self._prev_spectrum = bass_spectrum.copy()
        
        # Adaptive threshold with fast attack, slow decay
        if flux > self._adaptive_threshold:
            self._adaptive_threshold = flux * 0.9  # fast attack
        else:
            self._adaptive_threshold *= 0.995       # slow decay (~200ms to halve)
        
        # Onset detection: flux must exceed threshold AND minimum interval (50ms debounce)
        now = time.monotonic()
        min_interval = 60.0 / BPM_MAX  # fastest possible beat
        last_onset = self._onset_times[-1] if self._onset_times else 0.0
        
        if flux > self._adaptive_threshold * 1.5 and flux > 0.01 and (now - last_onset) > min_interval:
            self._onset_times.append(now)
            
            # Re-align beat grid to this onset
            if self._grid_period > 0:
                self._grid_phase = now
        
        # Prune old onsets
        cutoff = now - ONSET_HISTORY
        self._onset_times = [t for t in self._onset_times if t > cutoff]
        
        # Compute BPM from onset intervals
        if len(self._onset_times) >= 4:
            intervals = np.diff(self._onset_times)
            # Filter to musically reasonable intervals (60-200 BPM → 0.3-1.0 sec)
            valid = intervals[(intervals > 60.0/BPM_MAX) & (intervals < 60.0/BPM_MIN)]
            
            if len(valid) >= 3:
                median_interval = np.median(valid)
                bpm = 60.0 / median_interval
                
                # Confidence: how consistent are the intervals?
                std = np.std(valid)
                self.confidence = max(0, 1.0 - (std / median_interval))
                
                # Only update if confidence is reasonable
                if self.confidence > 0.3:
                    # Smooth BPM updates
                    if self.current_bpm == 0:
                        self.current_bpm = bpm
                    else:
                        self.current_bpm = 0.8 * self.current_bpm + 0.2 * bpm
                    self._grid_period = 60.0 / self.current_bpm
    
    def _beat_loop(self):
        """Fires light commands precisely on the beat grid."""
        beat_count = 0
        
        while not self._stop_event.is_set():
            if self.current_bpm < BPM_MIN or not self.active_ips:
                time.sleep(0.05)
                continue
            
            now = time.monotonic()
            period = self._grid_period
            
            # Calculate time until next beat
            if self._grid_phase > 0:
                elapsed = now - self._grid_phase
                beats_elapsed = elapsed / period
                next_beat_frac = 1.0 - (beats_elapsed % 1.0)
                sleep_time = next_beat_frac * period
                
                if sleep_time > 0.001:
                    # Sleep in tiny increments for precision
                    end = now + sleep_time
                    while time.monotonic() < end and not self._stop_event.is_set():
                        remaining = end - time.monotonic()
                        if remaining > 0.005:
                            time.sleep(0.002)
                        elif remaining > 0:
                            pass  # busy-wait for sub-5ms precision
                        else:
                            break
            else:
                time.sleep(0.05)
                continue
            
            if self._stop_event.is_set():
                break
            
            # === FIRE THE BEAT ===
            r, g, b = self.beat_color
            
            if self.distribution == "dynamic" and len(self.active_ips) > 1:
                # Ping-pong: one bulb flashes per beat
                active_idx = beat_count % len(self.active_ips)
                for i, ip in enumerate(self.active_ips):
                    if i == active_idx:
                        self.wiz.set_color_sync(ip, r, g, b, 100)
                    else:
                        self.wiz.set_color_sync(ip, r, g, b, 10)
            else:
                # Uniform: all flash together
                for ip in self.active_ips:
                    self.wiz.set_color_sync(ip, r, g, b, 100)
            
            # Hold bright for 15% of beat, then dim
            hold = period * 0.15
            time.sleep(max(0.01, hold))
            
            for ip in self.active_ips:
                self.wiz.set_color_sync(ip, r, g, b, 10)
            
            beat_count += 1
