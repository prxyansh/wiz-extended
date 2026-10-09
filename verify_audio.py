import numpy as np
import time
import scipy.signal
from audio_engine import AudioEngine, BandAGC, BANDS

def run_test(engine, audio_data):
    # Reset engine state
    engine._agcs = [BandAGC() for _ in range(10)]
    engine.bands_smooth = [0.0] * 10
    engine._trig_fast = [0.0] * 10
    engine._prev_raw = [0.0] * 10
    engine._fft_buffer.fill(0)
    
    bands_history = []
    ch1_history = []
    
    chunk_size = 512
    for i in range(0, len(audio_data), chunk_size):
        chunk = audio_data[i:i+chunk_size]
        if len(chunk) < chunk_size:
            break
        engine._process(chunk)
        bands_history.append(list(engine.bands_smooth))
        
        c = engine._map_color()
        ch1_history.append(c["ch1"])
        
    avg_bands = np.mean(bands_history[-50:], axis=0) # steady state at the end
    avg_all = np.mean(bands_history, axis=0)
    return avg_bands, avg_all, ch1_history

def generate_sine(freq, duration=2.0, sr=44100):
    t = np.linspace(0, duration, int(sr*duration), False)
    return np.sin(freq * 2 * np.pi * t).astype(np.float32)

def generate_pink_noise(duration=10.0, sr=44100):
    n = int(duration * sr)
    white = np.random.randn(n).astype(np.float32)
    b, a = scipy.signal.butter(1, 0.05, btype='low')
    pink = scipy.signal.lfilter(b, a, white)
    return (pink / np.max(np.abs(pink))).astype(np.float32)

def generate_bass_heavy(duration=10.0, sr=44100):
    t = np.linspace(0, duration, int(sr*duration), False)
    audio = 0.5 * np.sin(45 * 2 * np.pi * t)
    kick_env = np.exp(-5 * (t % 0.5))
    audio += kick_env * np.sin(60 * 2 * np.pi * t)
    return (audio / np.max(np.abs(audio))).astype(np.float32)

def generate_treble_heavy(duration=10.0, sr=44100):
    t = np.linspace(0, duration, int(sr*duration), False)
    audio = 0.4 * np.sin(2000 * 2 * np.pi * t) + 0.4 * np.sin(3500 * 2 * np.pi * t)
    hat_env = np.exp(-30 * (t % 0.25))
    noise = np.random.randn(len(t))
    audio += 0.8 * hat_env * noise
    return (audio / np.max(np.abs(audio))).astype(np.float32)

print("=== VERIFICATION SCRIPT ===")
engine = AudioEngine()

print("\n1. SINE TONES")
test_freqs = [40, 100, 225, 450, 900, 1800, 3700, 6500, 10000, 16000]
for i, f in enumerate(test_freqs):
    audio = generate_sine(f, 2.0)
    avg_end, _, _ = run_test(engine, audio)
    target_val = avg_end[i]
    total_val = sum(avg_end)
    pct = (target_val / total_val * 100) if total_val > 0 else 0
    print(f"{f:5}Hz -> Band {i}: {target_val:.3f} | Total: {total_val:.3f} | Dominance: {pct:5.1f}%")
    print(f"  Full array: {[round(x,3) for x in avg_end]}")

print("\n2. PINK NOISE")
pink = generate_pink_noise(10.0)
_, avg_all_pink, _ = run_test(engine, pink)
print(f"Pink Noise 10s Avg: {[round(x,3) for x in avg_all_pink]}")

print("\n3. BASS-HEAVY SONG")
bass = generate_bass_heavy(10.0)
engine.color_mode = "club_kick"
_, avg_all_bass, ch1_hist = run_test(engine, bass)
print(f"Bass Song 10s Avg: {[round(x,3) for x in avg_all_bass]}")
red_flashes = sum(1 for c in ch1_hist if c[0] == 255 and c[1] < 50)
print(f"Club Kick Triggers (Red Flashes) detected: {red_flashes} frames out of {len(ch1_hist)}")

print("\n4. TREBLE-HEAVY SONG")
treble = generate_treble_heavy(10.0)
engine.color_mode = "spatial_stage"
_, avg_all_treble, ch1_hist_treble = run_test(engine, treble)
print(f"Treble Song 10s Avg: {[round(x,3) for x in avg_all_treble]}")
cyan_flashes = sum(1 for c in ch1_hist_treble if c[1] == 255 and c[2] == 255)
print(f"Spatial Stage Triggers (Cyan Flashes) detected: {cyan_flashes} frames out of {len(ch1_hist_treble)}")
