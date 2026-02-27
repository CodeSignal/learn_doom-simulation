import asyncio
import base64
import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from game_manager import GameManager, GameSession, list_scenarios

IS_PRODUCTION = os.environ.get("IS_PRODUCTION", "").lower() == "true"
PORT = 3000 if IS_PRODUCTION else int(os.environ.get("PORT", "3000"))
DIST_DIR = os.path.join(os.path.dirname(__file__), "dist")

TICK_RATE = 1 / 35  # 35 fps (original Doom tic rate)

# Binary message tags (first byte)
TAG_VIDEO = b"\x01"
TAG_AUDIO = b"\x02"

app = FastAPI()
manager = GameManager()


# --- Pydantic models ---

class StartRequest(BaseModel):
    scenario: str = "basic.cfg"

class ActionRequest(BaseModel):
    actions: list[float]

class ResetRequest(BaseModel):
    scenario: str | None = None


# --- WebSocket for browser play ---

@app.websocket("/ws")
async def websocket_play(ws: WebSocket):
    await ws.accept()

    # Create a dedicated game session for this WebSocket connection
    session_id = await asyncio.to_thread(manager.create_session)
    session = manager.get_session(session_id)
    if not session:
        await ws.close()
        return

    # Send initial scenario list
    scenarios = await asyncio.to_thread(list_scenarios)
    await ws.send_text(json.dumps({
        "type": "scenarios",
        "scenarios": [s["name"] for s in scenarios],
    }))

    # Background task: game loop streaming frames
    stop_event = asyncio.Event()
    paused = True  # Start paused until player picks a level

    async def game_loop():
        nonlocal paused
        loop = asyncio.get_event_loop()
        next_tick = loop.time()
        while not stop_event.is_set():
            if paused:
                await asyncio.sleep(TICK_RATE)
                next_tick = loop.time()
                continue
            # Separate game errors (skip frame) from WS errors (stop loop)
            try:
                jpeg, state, audio = await asyncio.to_thread(session.tick)
            except Exception:
                await asyncio.sleep(TICK_RATE)
                next_tick = loop.time()
                continue
            try:
                await ws.send_bytes(TAG_VIDEO + jpeg)
                await ws.send_text(json.dumps({"type": "state", **state}))
                if audio:
                    await ws.send_bytes(TAG_AUDIO + audio)
            except Exception:
                break
            next_tick += TICK_RATE
            sleep_for = next_tick - loop.time()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                next_tick = loop.time()

    loop_task = asyncio.create_task(game_loop())

    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == "keydown":
                session.key_down(msg.get("key", ""))
            elif msg_type == "keyup":
                session.key_up(msg.get("key", ""))
            elif msg_type == "mouse":
                session.mouse_move(msg.get("dx", 0), msg.get("dy", 0))
            elif msg_type == "mousedown":
                session.mouse_button_down()
            elif msg_type == "mouseup":
                session.mouse_button_up()
            elif msg_type == "reset":
                await asyncio.to_thread(session.reset)
            elif msg_type == "scenario":
                scenario_name = msg.get("name", "basic.cfg")
                start_map = msg.get("map")
                skill = msg.get("skill")
                await asyncio.to_thread(session.reset, scenario_name, start_map, skill)
            elif msg_type == "next_level":
                await asyncio.to_thread(session.next_level)
            elif msg_type == "pause":
                paused = True
            elif msg_type == "unpause":
                paused = False
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        stop_event.set()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        manager.destroy_session(session_id)


# --- REST API for AI agents ---

@app.get("/api/game/scenarios")
async def get_scenarios():
    scenarios = await asyncio.to_thread(list_scenarios)
    return {"scenarios": [s["name"] for s in scenarios]}


@app.post("/api/game/start")
async def start_game(req: StartRequest):
    session_id = await asyncio.to_thread(manager.create_session, req.scenario)
    return {"session_id": session_id}


@app.post("/api/game/{session_id}/action")
async def game_action(session_id: str, req: ActionRequest):
    session = manager.get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    jpeg, state, reward = await asyncio.to_thread(session.step, req.actions)
    frame_b64 = base64.b64encode(jpeg).decode("ascii")

    return {
        "frame": frame_b64,
        "state": state,
        "reward": reward,
    }


@app.get("/api/game/{session_id}/state")
async def game_state(session_id: str):
    session = manager.get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    state = await asyncio.to_thread(session._get_state_dict)
    return {"state": state}


@app.post("/api/game/{session_id}/reset")
async def game_reset(session_id: str, req: ResetRequest):
    session = manager.get_session(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "Session not found"})

    await asyncio.to_thread(session.reset, req.scenario)
    return {"status": "ok"}


@app.delete("/api/game/{session_id}")
async def game_destroy(session_id: str):
    destroyed = manager.destroy_session(session_id)
    if not destroyed:
        return JSONResponse(status_code=404, content={"error": "Session not found"})
    return {"status": "ok"}


# --- Static file serving (production) ---

if IS_PRODUCTION:
    if not os.path.isdir(DIST_DIR):
        raise RuntimeError(f"Production mode but dist/ not found: {DIST_DIR}")
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="static")


# --- Entry point ---

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
