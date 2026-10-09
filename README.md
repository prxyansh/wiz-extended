<div align="center">
  
# Philips WiZ Extended
**The Open-Source, Cross-Platform Desktop Client for Philips WiZ Lights**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Platform: Windows | macOS | Linux](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgray.svg)]()
[![Built with: Python & Electron](https://img.shields.io/badge/Built%20with-Python%20%26%20Electron-success.svg)]()

</div>

---

## Why This Exists (vs. Official App)

The official Philips WiZ app is great for basic mobile control, but it lacks the robust, real-time desktop integrations needed by power users, gamers, and audiophiles. 

**Philips WiZ Extended** was built from the ground up as a native desktop application to unlock capabilities the official app simply doesn't offer. By communicating directly with the lights over your local UDP network (bypassing the cloud entirely), we achieve ultra-low latency perfect for real-time synchronization.

### Exclusive Features

* **Zero-Latency 16-Band Audio Sync:** A custom Digital Signal Processing (DSP) engine that captures your computer's internal audio in real-time. It separates audio into 16 distinct frequency bands (from Sub-Bass to Brilliance) and maps them to your lights instantly.
* **Spotify Album Art Sync:** Automatically detects what you are listening to on Spotify, extracts the dominant color palette from the album art, and dynamically paints your room to match the vibe.
* **Pomodoro Productivity Mode:** A built-in focus timer that automatically shifts your room lighting from "hyper-focus" cool white during work sessions, to warm relaxing hues during breaks.
* **Screen Color Averaging:** Captures your active display and projects the average screen color onto your lights in real-time—perfect for immersive movie watching and gaming.
* **Cross-Platform Native Desktop UI:** No more reaching for your phone. A sleek, borderless desktop app that runs flawlessly on Windows, macOS, and Linux.
* **100% Local Processing:** Your data, audio, and screen grabs never leave your computer. Everything runs locally for maximum privacy and zero network delay.

---

## Installation

### 1. Prerequisites
You will need **Python 3.10+** and **Node.js / npm** installed on your system.

### 2. Setup the Python Backend
Clone the repository and install the required Python dependencies into a virtual environment:

```bash
git clone https://github.com/yourusername/philips-wiz-extended.git
cd philips-wiz-extended

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Setup the Electron Frontend
Install the desktop application dependencies:

```bash
npm install
```

---

## Running the Application

Once your environments are set up, you only need one command to launch the full native application:

```bash
npm start
```
*The app will automatically start the background server, discover the lights on your local network, and open the native window.*

---

## Setting Up Audio Sync

To use the real-time music visualizer, the app needs to "hear" what your computer is playing. It will automatically scan for internal loopback drivers:

- **macOS:** Install [BlackHole](https://existential.audio/blackhole/) (2ch).
- **Windows:** Enable "Stereo Mix" in your Sound Control Panel, or install VoiceMeeter / VB-Cable.
- **Linux:** PulseAudio or PipeWire monitor interfaces are automatically detected.

---

## Open Source & Contributing

This project is 100% free and open-source. We believe that smart home hardware shouldn't be locked behind restrictive proprietary apps. 

Contributions, pull requests, and bug reports are highly welcome! Whether you want to add new visualizer presets, improve the UI, or optimize the UDP networking, feel free to fork the repo and submit a PR.

**License:** MIT
