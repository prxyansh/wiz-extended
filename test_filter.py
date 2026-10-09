import numpy as np
from audio_engine import AudioEngine

engine = AudioEngine()

# Simulate White noise
np.random.seed(42)
white_noise = np.random.normal(0, 0.1, 8192).astype(np.float32)

# Simulate Sine wave at 50Hz (Sub Bass)
t = np.linspace(0, 8192/44100, 8192, endpoint=False)
sine = (np.sin(2 * np.pi * 50 * t) * 0.5).astype(np.float32)

print("Processing white noise:")
for _ in range(50):
    engine._process(white_noise)
print("mel_energy (approx):", engine.band_energy)
print("spectral flux:", engine.band_transient)

engine = AudioEngine()
print("\nProcessing 50Hz sine:")
for _ in range(50):
    engine._process(sine)
print("mel_energy (approx):", engine.band_energy)
