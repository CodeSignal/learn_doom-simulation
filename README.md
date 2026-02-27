# Doom Simulation

Browser-playable Doom powered by [ViZDoom](https://vizdoom.cs.put.edu.pl/). Streams video and audio from a Python backend to a web client over WebSocket.

## Features

- **Campaign mode** — Play Doom 1 (4 episodes) and Doom 2 with difficulty selection
- **Test levels** — Run individual ViZDoom scenarios for experimentation
- **Full audio/video streaming** — 1920x1080 JPEG video + stereo PCM audio over WebSocket
- **Browser input** — WASD movement, mouse look, weapon selection, all in the browser
- **Level progression** — Inventory carry-over between levels, kill/item/secret stats
- **REST API** — Programmatic game control for AI agents

## Prerequisites

- Python 3.11+
- Node.js 22+
- ViZDoom (`pip install vizdoom`)
- `doom.wad` and `doom2.wad` in the project root

## Quick Start

```bash
# Install dependencies
git submodule update --init --recursive
pip install -r requirements.txt
npm install

# Start development servers (Vite on :3000, API on :3001)
npm run start:dev
```

Open http://localhost:3000 in your browser.

## Production

```bash
npm run build
IS_PRODUCTION=true python3 server.py
```

The production server serves the built frontend from `dist/` and runs on port 3000.

## Controls

| Key | Action |
|-----|--------|
| W/A/S/D | Move |
| Mouse | Look |
| Left Click | Attack |
| E / Space | Use / Interact |
| Shift | Run |
| 1-7 | Select weapon |
| Escape | Pause |

## Architecture

```
client/          → Browser frontend (vanilla JS, Vite)
  app.js         → Game UI state machine, menu navigation
  doom-client.js → WebSocket client, input capture, frame rendering
  doom.css       → Game-specific styles
server.py        → FastAPI/Uvicorn backend, WebSocket + REST API
game_manager.py  → ViZDoom wrapper, game sessions, input handling
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/game/scenarios` | List available scenarios |
| `POST` | `/api/game/start` | Create a new game session |
| `POST` | `/api/game/{id}/action` | Send action, get frame + state |
| `GET` | `/api/game/{id}/state` | Get current game state |
| `POST` | `/api/game/{id}/reset` | Reset episode |
| `DELETE` | `/api/game/{id}` | Destroy session |

## CI/CD

Pushing to `main` triggers the GitHub Actions workflow which builds the client, packages a release tarball (`dist/`, `server.py`, `game_manager.py`, `requirements.txt`, `package.json`), and creates a GitHub Release.

## Scripts

| Command | Description |
|---------|-------------|
| `npm run start:dev` | Vite dev server + Python API (concurrent) |
| `npm run build` | Production build to `dist/` |
| `npm run start:prod` | Production mode (Python serves `dist/`) |
