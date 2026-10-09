# Audio Preset Analysis

This document provides a complete technical breakdown of all 13 presets implemented in `audio_engine.py`. 

## Global Context & Derived Signals

Before analyzing individual presets, it's important to understand the derived signals they rely on, calculated globally in `_process()` for every frame (max 25Hz output rate). None of the preset functions maintain internal state between calls (except `time.monotonic()` for hue shifting); all state decay/smoothing is handled in `_process()`.

### Derived Signal: Transients (`_trig_fast`)
The engine detects rapid increases in volume relative to the recent volume history (AGC 92nd percentile). 
```python
delta = max(0.0, raw - self._prev_raw[i])
self._prev_raw[i] = raw

ref = float(np.percentile(self._agcs[i].buf, 92)) if len(self._agcs[i].buf) >= 3 else 1.0
delta_n = (delta / ref) if ref > 0 else 0.0

s = max(self.sensitivity, 0.1)
trigger_thresh = 0.15 / s

if (i < 2 or i > 6) and delta_n > trigger_thresh:
    self._trig_fast[i] = 1.0
else:
    self._trig_fast[i] *= 0.85
```
**Important Note:** Sensitivity is applied here by dividing the baseline `0.15` threshold by `s`. When triggered, `_trig_fast[i]` goes to exactly `1.0` and decays by 15% (`* 0.85`) every frame thereafter.

---

## 1. `_club_kick`

**1. NAME & PURPOSE**
- Intended to pulse intensely on kick drum hits and drop to a dim purple glow in between.
- Controls both channels (bulbs) identically. 

**2. INPUTS**
- Reads `_trig_fast[0]` (Sub-Bass, 20-60Hz).
- Reads `self.sensitivity` directly.

**3. THRESHOLDS & MAGIC NUMBERS**
```python
def _club_kick(self) -> dict:
    s = max(self.sensitivity, 0.1)
    trig = self._trig_fast[0]
    c = (255, _clamp_int(trig*100, 0, 255), _clamp_int(trig*100, 0, 255), 100) if trig > (0.3 / s) else (10, 0, 20, 20)
    return {"ch1": c, "ch2": c}
```
- `trig > (0.3 / s)`: Arbitrarily chosen threshold to gate the flash.

**4. OUTPUT LOGIC**
- Instantaneous output (no post-smoothing).
- If triggered: Outputs `(255, trig*100, trig*100, 100)`. 
- If not: Outputs `(10, 0, 20, 20)`.

**5. KNOWN BEHAVIOR ISSUES**
- **Sensitivity Double-Dip Bug:** Since `_trig_fast[0]` is capped at `1.0`, if `s < 0.3`, then `(0.3 / s)` is greater than `1.0`. **This means if sensitivity is set below 0.3, the preset will physically never trigger.**
- **Color Bug:** When triggered, `trig` is at most 1.0. This means `trig*100` maxes out at 100. The resulting RGB color is `(255, 100, 100)`, which is a light reddish-pink, not pure white as might be expected for a strobe.

---

## 2. `_drum_kit`

**1. NAME & PURPOSE**
- Intended to flash red on kicks and cyan on snares.
- Controls both channels identically.

**2. INPUTS**
- Reads `_trig_fast[0]` (Sub-Bass, 20-60Hz).
- Reads `_trig_fast[2]` (Low Mid, 150-300Hz).
- Reads `self.sensitivity`.

**3. THRESHOLDS & MAGIC NUMBERS**
```python
def _drum_kit(self) -> dict:
    s = max(self.sensitivity, 0.1)
    tk = self._trig_fast[0]
    ts = self._trig_fast[2]
    c = (255, 0, 0, 100) if (tk > ts and tk > (0.2 / s)) else (0, 255, 255, 100) if ts > (0.2 / s) else (10, 0, 20, 20)
    return {"ch1": c, "ch2": c}
```
- `tk > (0.2 / s)` and `ts > (0.2 / s)`: Arbitrary thresholds for kick/snare triggering.

**4. OUTPUT LOGIC**
- Instantaneous output. Kick outputs Red, Snare outputs Cyan, default is dim purple.

**5. KNOWN BEHAVIOR ISSUES**
- **Sensitivity Double-Dip Bug:** Same as above. If `s < 0.2`, it will never trigger.
- **Preemption:** If both kick and snare trigger simultaneously with high values (`tk > ts`), the kick completely overrides the snare. There's no color blending.

---

## 3. `_hat_sparkle`

**1. NAME & PURPOSE**
- Flashes white on high-frequency percussive hits (hi-hats/cymbals). Both channels identical.

**2. INPUTS**
- `_trig_fast[8]` (Brilliance 2, 8000-12000Hz).

**3. THRESHOLDS & OUTPUT LOGIC**
```python
def _hat_sparkle(self) -> dict:
    s = max(self.sensitivity, 0.1)
    th = self._trig_fast[8]
    c = (255, 255, 255, 100) if th > (0.3 / s) else (40, 0, 60, 20)
    return {"ch1": c, "ch2": c}
```
- White `(255, 255, 255)` at full brightness if triggered, else magenta.

**5. KNOWN BEHAVIOR ISSUES**
- **Sensitivity Bug:** Fails entirely if `s < 0.3`.

---

## 4. `_synth_lead`

**1. NAME & PURPOSE**
- Continuous, smooth shifting hue mapped to mid-range melodic elements.

**2. INPUTS & THRESHOLDS**
- `bands_smooth[4]` (High Mid, 600-1200Hz).
- No discrete `if` thresholds, purely continuous math.

```python
def _synth_lead(self) -> dict:
    s = self.sensitivity
    v = min(1.0, self.bands_smooth[4] * s)
    hue = (v * 0.5 + time.monotonic() * 0.1) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 0.4 + v * 0.6)
    c = (int(r * 255), int(g * 255), int(b * 255), _clamp_int(10 + v * 90, 10, 100))
    return {"ch1": c, "ch2": c}
```

**4. OUTPUT LOGIC**
- Instantaneous frame output, but naturally smooth because it relies on `bands_smooth` (which uses Exponential Moving Average).

**5. KNOWN BEHAVIOR ISSUES**
- The hue shift relies on `time.monotonic() * 0.1`. This means the color inherently drifts independently of the music, and the music just adds a jump of up to `0.5` to the hue wheel.

---

## 5. `_bass_groove`

**1. NAME & PURPOSE**
- Warm, smooth colors (reds/oranges/yellows) mapped to sustained bass lines.

**2. INPUTS & OUTPUT LOGIC**
- `bands_smooth[1]` (Bass, 60-150Hz).
```python
def _bass_groove(self) -> dict:
    s = self.sensitivity
    k = min(1.0, self.bands_smooth[1] * s)
    hue = 0.6 + (k * 0.15)
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 0.2 + k * 0.8)
```
- The hue ranges from 0.6 (blue) to 0.75 (purple/pink). Wait, the description says "warm lows" but a hue of `0.6` is Blue. `0.75` is Purple. 

**5. KNOWN BEHAVIOR ISSUES**
- **Color Mismatch:** The HSV hue wheel goes from 0 (Red) to 1.0 (Red). 0.6 is Blue. This preset produces cool colors, contradicting its intended "warm" vibe.

---

## 6. `_thunderstorm`

**1. NAME & PURPOSE**
- Simulates a storm: rolling dark blue/grey clouds on sustained bass, flashing white lightning on kicks.

**2. INPUTS & THRESHOLDS**
```python
def _thunderstorm(self) -> dict:
    s = self.sensitivity
    tk = min(1.0, self._trig_fast[0] * s)
    k = min(1.0, self.bands_smooth[0] * s)
    c = (255, 255, 255, 100) if tk > 0.4 else (20, 30, 50, _clamp_int(15 + k * 30, 10, 100))
```
- `tk > 0.4`: Threshold for lightning. 

**5. KNOWN BEHAVIOR ISSUES**
- Because `tk` is multiplied by `s` *before* the check (`tk = min(1.0, self._trig_fast[0] * s)`), if `s` is low (e.g. 0.3), `tk` maxes out at 0.3, meaning `tk > 0.4` can never trigger.

---

## 7. `_firefly`

**1. NAME & PURPOSE**
- Orange/yellow continuous glow on melodic presence, white flashes on hi-hats.

**2. INPUTS & LOGIC**
```python
def _firefly(self) -> dict:
    s = self.sensitivity
    v = min(1.0, self.bands_smooth[5] * s)
    th = min(1.0, self._trig_fast[8] * s)
    if th > 0.3:
        c = (255, 255, 100, 100)
    else:
        c = (255, _clamp_int(v * 120, 30, 120), 0, _clamp_int(20 + v * 60, 10, 100))
```
- `th > 0.3`: Threshold for sparks. Subject to the same `< 0.3` sensitivity failure bug.

---

## 8. `_fire` & `_ocean` (Legacy)

**1. PURPOSE**
- Simple 1-to-1 mappings of smoothed band amplitudes to RGB values. Continuous interpolation.

**2. INPUTS & LOGIC**
```python
def _fire(self) -> dict:
    b = min(1.0, (self.bands_smooth[0] + self.bands_smooth[2]) * self.sensitivity)
    t = min(1.0, self.bands_smooth[8] * self.sensitivity)
    c = (255, _clamp_int(b * 210, 0, 210), _clamp_int(t * 60, 0, 60), _clamp_int(10 + b * 90, 10, 100))
```
- No discrete thresholds. Uses Sub-Bass, Low-Mid, and Brilliance 2.

---

## 9. `_spatial_stage`

**1. NAME & PURPOSE**
- Multi-bulb orchestration. `ch1` acts as the rhythm section (drums), `ch2` acts as the singer/melody.

**2. INPUTS & THRESHOLDS**
```python
def _spatial_stage(self) -> dict:
    s = max(self.sensitivity, 0.1)
    # CH1: The Drummer (Kick + Snare)
    tk = self._trig_fast[0]
    ts = self._trig_fast[2]
    ch1 = (255, 0, 0, 100) if (tk > ts and tk > (0.2 / s)) else (0, 255, 255, 100) if ts > (0.2 / s) else (0, 0, 0, 10)
    
    # CH2: The Singer (Vocals / Synths)
    v = min(1.0, self.bands_smooth[4] * self.sensitivity)
    hue = (v * 0.5 + time.monotonic() * 0.1) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 0.4 + v * 0.6)
    ch2 = (int(r * 255), int(g * 255), int(b * 255), _clamp_int(10 + v * 90, 10, 100))
    return {"ch1": ch1, "ch2": ch2}
```
- Relies on identical thresholds to `_drum_kit` and `_synth_lead`.
- **Known Issue**: Subject to the sensitivity `< 0.2` failure bug on the drum channel.

---

## 10. `_spatial_pingpong`

**1. NAME & PURPOSE**
- Sub-bass (kick) triggers `ch1`, Low-mid (snare) triggers `ch2`.

**2. THRESHOLDS & LOGIC**
```python
def _spatial_pingpong(self) -> dict:
    s = max(self.sensitivity, 0.1)
    tk = self._trig_fast[0]
    ts = self._trig_fast[2]
    
    ch1 = (255, 255, 255, 100) if tk > (0.3 / s) else (0, 0, 0, 10)
    ch2 = (255, 255, 255, 100) if ts > (0.3 / s) else (0, 0, 0, 10)
    return {"ch1": ch1, "ch2": ch2}
```
- **Known Issue**: Subject to sensitivity `< 0.3` failure bug.

---

## 11. `_spatial_storm`

**1. NAME & PURPOSE**
- Clouds on `ch1` (continuous), lightning on `ch2` (transient flashes).

**2. THRESHOLDS & LOGIC**
```python
def _spatial_storm(self) -> dict:
    s = max(self.sensitivity, 0.1)
    tk = self._trig_fast[0]
    k = min(1.0, self.bands_smooth[0] * self.sensitivity)
    
    ch1 = (20, 30, 50, _clamp_int(15 + k * 30, 10, 100))
    ch2 = (255, 255, 255, 100) if tk > (0.4 / s) else (0, 0, 0, 10)
    return {"ch1": ch1, "ch2": ch2}
```
- **Known Issue**: Subject to sensitivity `< 0.4` failure bug on the lightning channel.

---

## 12. `_spatial_bassair`

**1. NAME & PURPOSE**
- `ch1` maps to continuous Sub-Bass. `ch2` maps to High frequencies.

**2. INPUTS & LOGIC**
```python
def _spatial_bassair(self) -> dict:
    s = self.sensitivity
    k = min(1.0, self.bands_smooth[0] * s)
    th = min(1.0, self._trig_fast[8] * s)
    v = min(1.0, self.bands_smooth[5] * s)
    
    ch1 = (255, _clamp_int(k * 100, 0, 100), 0, _clamp_int(15 + k * 85, 10, 100))
    air_val = max(th, v)
    ch2 = (0, _clamp_int(air_val * 200, 100, 255), 255, _clamp_int(15 + air_val * 85, 10, 100))
    return {"ch1": ch1, "ch2": ch2}
```
- Continuous math mapping with no discrete thresholds, sidestepping the sensitivity-threshold failures present in the other presets.
