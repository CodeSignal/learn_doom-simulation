#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/CodeSignal/learn_doom-simulation.git"
DIR="learn_doom-simulation"

# Resolve latest tag
LATEST_TAG=$(git ls-remote --tags --sort=-v:refname "$REPO" 'refs/tags/v*' \
  | head -1 | sed 's/.*refs\/tags\///')
echo "==> Cloning $DIR @ $LATEST_TAG"

# Clone at that tag
git -c advice.detachedHead=false clone -q --branch "$LATEST_TAG" --depth 1 "$REPO" "$DIR"
cd "$DIR"
git checkout -q -b main
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

# Start production server (single process, no proxy overhead)
echo "==> Starting server on port 3000..."
IS_PRODUCTION=true python3 server.py
