#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════╗
# ║  WiZ Controller — Launcher                              ║
# ║  Usage: bash run.sh                                     ║
# ╚══════════════════════════════════════════════════════════╝

set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PY="python3"
VENV="$DIR/.venv"

# ── 1. Create virtual env if needed ────────────────────────
if [ ! -d "$VENV" ]; then
  echo "→ Creating virtual environment…"
  $PY -m venv "$VENV"
fi

source "$VENV/bin/activate"

# ── 2. Install / update dependencies ───────────────────────
echo "→ Installing dependencies…"
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ── 3. Launch ───────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════╗"
echo "║  🔥  WiZ Controller  v1.0       ║"
echo "╠══════════════════════════════════╣"
echo "║  http://localhost:8899           ║"
echo "║  http://$(ipconfig getifaddr en0 2>/dev/null || echo '<your-ip>'):8899  ║"
echo "╚══════════════════════════════════╝"
echo ""
echo "  Open the URL above in any browser on this network."
echo "  Press Ctrl+C to stop."
echo ""

python server.py
