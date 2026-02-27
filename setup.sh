#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/CodeSignal/learn_doom-simulation.git"
DIR="learn_doom-simulation"

# Install system dependencies for ViZDoom audio (OpenAL + FluidSynth)
if command -v apt-get &>/dev/null; then
  echo "==> Installing system audio libraries..."
  apt-get update -qq && apt-get install -y -qq libopenal1 libfluidsynth2 fluid-soundfont-gm timidity 2>/dev/null || true
fi

echo "==> Cloning $DIR"
git -c advice.detachedHead=false clone -q --depth 1 "$REPO" "$DIR"
cd "$DIR"
git submodule update -q --init --recursive

# Ensure pip is available
if ! python3 -m pip --version &>/dev/null; then
  echo "==> Installing pip..."
  curl -sS https://bootstrap.pypa.io/get-pip.py | python3 - -q 2>/dev/null
fi

# Python dependencies (try venv, fall back to direct install)
if python3 -m venv .venv 2>/dev/null && [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi
echo "==> Installing Python dependencies..."
python3 -m pip install -q -r requirements.txt 2>/dev/null

# Node dependencies + production build
echo "==> Installing Node dependencies..."
npm install --silent 2>/dev/null
echo "==> Building client..."
npx vite build --outDir ../dist 2>/dev/null

# Locate the General MIDI soundfont for FluidSynth MIDI synthesis
SOUNDFONT=""
for sf in /usr/share/sounds/sf2/FluidR3_GM.sf2 \
          /usr/share/soundfonts/FluidR3_GM.sf2 \
          /usr/share/sounds/sf2/default-GM.sf2; do
  if [ -f "$sf" ]; then
    SOUNDFONT="$sf"
    break
  fi
done
if [ -n "$SOUNDFONT" ]; then
  echo "==> Using soundfont: $SOUNDFONT"
  export SDL_SOUNDFONTS="$SOUNDFONT"
fi

# Start production server (single process, no proxy overhead)
# ALSOFT_DRIVERS=null lets OpenAL render audio in headless containers without real audio hardware
echo "==> Starting server on port 3000..."
ALSOFT_DRIVERS=null IS_PRODUCTION=true python3 server.py
