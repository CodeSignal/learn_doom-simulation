import io
import os
import threading
import uuid
from typing import Optional

import numpy as np
import vizdoom as vzd
from PIL import Image

# Map keyboard keys to ViZDoom button indices
# Button order: MOVE_FORWARD, MOVE_BACKWARD, MOVE_LEFT, MOVE_RIGHT,
#               TURN_LEFT, TURN_RIGHT, ATTACK, USE, JUMP,
#               TURN_LEFT_RIGHT_DELTA, LOOK_UP_DOWN_DELTA
KEY_TO_BUTTON = {
    "w": 0,   # MOVE_FORWARD
    "s": 1,   # MOVE_BACKWARD
    "a": 2,   # MOVE_LEFT
    "d": 3,   # MOVE_RIGHT
    "arrowleft": 4,   # TURN_LEFT
    "arrowright": 5,   # TURN_RIGHT
    " ": 7,   # USE (space / activate)
    "e": 7,   # USE
    "control": 8,   # JUMP
    "shift": 18,  # SPEED (run)
    "1": 11,  # SELECT_WEAPON_1 (fist/chainsaw)
    "2": 12,  # SELECT_WEAPON_2 (pistol)
    "3": 13,  # SELECT_WEAPON_3 (shotgun)
    "4": 14,  # SELECT_WEAPON_4 (chaingun)
    "5": 15,  # SELECT_WEAPON_5 (rocket launcher)
    "6": 16,  # SELECT_WEAPON_6 (plasma rifle)
    "7": 17,  # SELECT_WEAPON_7 (BFG)
}

# 9 binary movement/action + 2 delta axes + 7 weapon select + 1 speed
NUM_BUTTONS = 19

SCENARIOS_DIR = os.path.join(os.path.dirname(vzd.__file__), "scenarios")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Scenarios that crash in single-player headless mode
# (multiplayer or no WAD specified)
_BLOCKED_SCENARIOS = {
    "multi.cfg",
    "multi_duel.cfg",
    "cig.cfg",
    "oblige.cfg",
}

# ZDoom class names for things with MF_COUNTKILL (count toward kill total)
# Stored lowercase for case-insensitive matching (ViZDoom may vary casing)
_MONSTER_NAMES = {n.lower() for n in (
    "ZombieMan", "ShotgunGuy", "ChaingunGuy", "DoomImp", "Demon", "Spectre",
    "LostSoul", "Cacodemon", "HellKnight", "BaronOfHell", "Arachnotron",
    "PainElemental", "Revenant", "Mancubus", "Archvile", "SpiderMastermind",
    "Cyberdemon", "WolfensteinSS", "CommanderKeen",
)}

# ZDoom class names for things with MF_COUNTITEM (count toward item total)
_COUNTITEM_NAMES = {n.lower() for n in (
    # Health
    "HealthBonus", "Stimpack", "Medikit", "Soulsphere", "MegaSphere",
    # Armor
    "ArmorBonus", "GreenArmor", "BlueArmor",
    # Ammo
    "Clip", "ClipBox", "Shell", "ShellBox",
    "RocketAmmo", "RocketBox", "Cell", "CellPack",
    # Weapons
    "Chainsaw", "Shotgun", "SuperShotgun", "Chaingun",
    "RocketLauncher", "PlasmaRifle", "BFG9000",
    # Powerups
    "BlurSphere", "InvulnerabilitySphere", "Berserk", "RadSuit",
    "ComputerAreaMap", "Infrared", "Backpack",
)}

# Secret counts per map for standard Doom 1 / Doom 2 (sector-based, not
# exposed by ViZDoom API, so we use a static lookup table).
_SECRET_TOTALS = {
    # Doom 1
    "E1M1": 3, "E1M2": 6, "E1M3": 7, "E1M4": 3, "E1M5": 9,
    "E1M6": 4, "E1M7": 4, "E1M8": 1, "E1M9": 2,
    "E2M1": 4, "E2M2": 12, "E2M3": 6, "E2M4": 10, "E2M5": 10,
    "E2M6": 3, "E2M7": 6, "E2M8": 0, "E2M9": 1,
    "E3M1": 1, "E3M2": 3, "E3M3": 6, "E3M4": 4, "E3M5": 10,
    "E3M6": 4, "E3M7": 4, "E3M8": 0, "E3M9": 1,
    "E4M1": 2, "E4M2": 3, "E4M3": 22, "E4M4": 2, "E4M5": 2,
    "E4M6": 3, "E4M7": 4, "E4M8": 1, "E4M9": 2,
    # Doom 2
    "MAP01": 5, "MAP02": 1, "MAP03": 1, "MAP04": 3, "MAP05": 3,
    "MAP06": 3, "MAP07": 1, "MAP08": 7, "MAP09": 6, "MAP10": 18,
    "MAP11": 3, "MAP12": 4, "MAP13": 8, "MAP14": 0, "MAP15": 11,
    "MAP16": 4, "MAP17": 3, "MAP18": 4, "MAP19": 9, "MAP20": 7,
    "MAP21": 0, "MAP22": 3, "MAP23": 2, "MAP24": 4, "MAP25": 2,
    "MAP26": 4, "MAP27": 8, "MAP28": 7, "MAP29": 0, "MAP30": 0,
    "MAP31": 4, "MAP32": 6,
}

# Substitute free WADs for missing commercial ones
_WAD_FALLBACKS = {
    "doom.wad": "freedoom1.wad",
    "doom2.wad": "freedoom2.wad",
}


def list_scenarios() -> list[dict]:
    """List available ViZDoom scenario .cfg files (single-player safe only)."""
    scenarios = []
    if os.path.isdir(SCENARIOS_DIR):
        for f in sorted(os.listdir(SCENARIOS_DIR)):
            if f.endswith(".cfg") and f not in _BLOCKED_SCENARIOS:
                scenarios.append({"name": f, "path": os.path.join(SCENARIOS_DIR, f)})
    return scenarios


def _find_scenario_path(name: str) -> Optional[str]:
    """Resolve a scenario name to its full path."""
    for s in list_scenarios():
        if s["name"] == name:
            return s["path"]
    return None


class GameSession:
    """Wraps a single ViZDoom DoomGame instance."""

    def __init__(self, scenario: str = "basic.cfg"):
        self._lock = threading.Lock()
        self.game = vzd.DoomGame()
        self._scenario = scenario
        self._configure(scenario)
        self.game.init()
        self._current_map = self._normalize_map(self.game.get_doom_map())

        # Track last known state (get_state() returns None after episode ends)
        self._last_health: float = 100.0
        # Saved inventory for carry-over between levels
        self._saved_inventory: dict | None = None

        # Level totals (counted from objects at level start)
        self._level_total_kills: int | None = None
        self._level_total_items: int | None = None
        self._count_level_totals()

        # Input state for browser play (keys currently held)
        self._keys_held: set[str] = set()
        self._mouse_dx: float = 0.0
        self._mouse_dy: float = 0.0
        self._mouse_held: bool = False

    # Doom 1 scenarios use ExMy map format, but doom.cfg defaults to "map01"
    _DOOM1_SCENARIOS = {"doom.cfg", "freedoom1.cfg"}

    def _normalize_map(self, map_name: str) -> str:
        """Convert default 'map01' to 'E1M1' for Doom 1 WADs."""
        if self._scenario in self._DOOM1_SCENARIOS and map_name.lower() == "map01":
            return "E1M1"
        return map_name

    def _count_level_totals(self):
        """Count total monsters and items on the current level using objects info.

        objects_info must be enabled in _configure() before init().
        Secrets use a static lookup table (sector-based, not exposed by ViZDoom).
        """
        try:
            state = self.game.get_state()
            if state and state.objects:
                kills = 0
                items = 0
                for obj in state.objects:
                    name = obj.name.lower()
                    if name in _MONSTER_NAMES:
                        kills += 1
                    elif name in _COUNTITEM_NAMES:
                        items += 1
                self._level_total_kills = kills
                self._level_total_items = items
            else:
                self._level_total_kills = None
                self._level_total_items = None
        except Exception:
            self._level_total_kills = None
            self._level_total_items = None
        # Secrets: look up from static table by map name
        self._level_total_secrets = _SECRET_TOTALS.get(self._current_map.upper())

    def _configure(self, scenario: str):
        """Configure the DoomGame instance."""
        scenario_path = _find_scenario_path(scenario)
        if scenario_path:
            self.game.load_config(scenario_path)

        # Substitute WADs for missing commercial ones
        # Look in project dir first, then fall back to freedoom WADs
        try:
            game_wad = os.path.basename(self.game.get_doom_game_path()).lower()
            original = self.game.get_doom_game_path()
            if not os.path.isfile(original):
                # Check project directory for the WAD
                local = os.path.join(PROJECT_DIR, game_wad)
                if os.path.isfile(local):
                    self.game.set_doom_game_path(local)
                elif game_wad in _WAD_FALLBACKS:
                    fallback = os.path.join(SCENARIOS_DIR, _WAD_FALLBACKS[game_wad])
                    if os.path.isfile(fallback):
                        self.game.set_doom_game_path(fallback)
        except Exception:
            pass

        self.game.set_window_visible(False)
        self.game.set_mode(vzd.Mode.PLAYER)
        self.game.set_screen_format(vzd.ScreenFormat.RGB24)
        self.game.set_screen_resolution(vzd.ScreenResolution.RES_640X480)

        # Clear any buttons from config and set our own
        self.game.clear_available_buttons()
        self.game.add_available_button(vzd.Button.MOVE_FORWARD)
        self.game.add_available_button(vzd.Button.MOVE_BACKWARD)
        self.game.add_available_button(vzd.Button.MOVE_LEFT)
        self.game.add_available_button(vzd.Button.MOVE_RIGHT)
        self.game.add_available_button(vzd.Button.TURN_LEFT)
        self.game.add_available_button(vzd.Button.TURN_RIGHT)
        self.game.add_available_button(vzd.Button.ATTACK)
        self.game.add_available_button(vzd.Button.USE)
        self.game.add_available_button(vzd.Button.JUMP)
        self.game.add_available_button(vzd.Button.TURN_LEFT_RIGHT_DELTA)
        self.game.add_available_button(vzd.Button.LOOK_UP_DOWN_DELTA)
        self.game.add_available_button(vzd.Button.SELECT_WEAPON1)
        self.game.add_available_button(vzd.Button.SELECT_WEAPON2)
        self.game.add_available_button(vzd.Button.SELECT_WEAPON3)
        self.game.add_available_button(vzd.Button.SELECT_WEAPON4)
        self.game.add_available_button(vzd.Button.SELECT_WEAPON5)
        self.game.add_available_button(vzd.Button.SELECT_WEAPON6)
        self.game.add_available_button(vzd.Button.SELECT_WEAPON7)
        self.game.add_available_button(vzd.Button.SPEED)

        # Override game variables so indices are always consistent
        self.game.clear_available_game_variables()
        self.game.add_available_game_variable(vzd.GameVariable.HEALTH)
        self.game.add_available_game_variable(vzd.GameVariable.ARMOR)
        self.game.add_available_game_variable(vzd.GameVariable.SELECTED_WEAPON_AMMO)
        self.game.add_available_game_variable(vzd.GameVariable.KILLCOUNT)
        self.game.add_available_game_variable(vzd.GameVariable.ITEMCOUNT)
        self.game.add_available_game_variable(vzd.GameVariable.SECRETCOUNT)

        self.game.set_episode_timeout(0)  # no timeout

        # Enable objects info for level totals counting
        self.game.set_objects_info_enabled(True)

        # Enable audio buffer (requires ViZDoom built with sound support)
        try:
            self.game.set_sound_enabled(True)
            self.game.set_audio_buffer_enabled(True)
            self.game.set_audio_sampling_rate(vzd.SamplingRate.SR_22050)
            self.game.set_audio_buffer_size(4)  # 4 tics of audio per step
        except Exception:
            pass

    # --- Input handling (browser play) ---

    def key_down(self, key: str):
        self._keys_held.add(key.lower())

    def key_up(self, key: str):
        self._keys_held.discard(key.lower())

    def mouse_move(self, dx: float, dy: float):
        self._mouse_dx += dx
        self._mouse_dy += dy

    def mouse_button_down(self):
        self._mouse_held = True

    def mouse_button_up(self):
        self._mouse_held = False

    def _build_action(self) -> list[float]:
        """Build action vector from current input state."""
        action = [0.0] * NUM_BUTTONS

        for key, idx in KEY_TO_BUTTON.items():
            if key in self._keys_held:
                action[idx] = 1.0

        # Attack while mouse button is held
        if self._mouse_held:
            action[6] = 1.0  # ATTACK

        # Mouse delta axes (negative dy = look up in Doom)
        action[9] = self._mouse_dx * 0.25    # TURN_LEFT_RIGHT_DELTA
        action[10] = -self._mouse_dy * 0.25  # LOOK_UP_DOWN_DELTA
        self._mouse_dx = 0.0
        self._mouse_dy = 0.0

        return action

    def _capture_inventory(self):
        """Capture full player inventory for carry-over between levels."""
        gv = self.game.get_game_variable
        self._saved_inventory = {
            "health": gv(vzd.GameVariable.HEALTH),
            "armor": gv(vzd.GameVariable.ARMOR),
            "selected_weapon": gv(vzd.GameVariable.SELECTED_WEAPON),
            "weapons": [gv(getattr(vzd.GameVariable, f"WEAPON{i}")) for i in range(10)],
            "ammo": [gv(getattr(vzd.GameVariable, f"AMMO{i}")) for i in range(10)],
            "kill_count": gv(vzd.GameVariable.KILLCOUNT),
            "item_count": gv(vzd.GameVariable.ITEMCOUNT),
            "secret_count": gv(vzd.GameVariable.SECRETCOUNT),
        }

    def _restore_inventory(self):
        """Restore player inventory on a new map using console commands."""
        inv = self._saved_inventory
        if not inv:
            return

        g = self.game
        # Doom ammo type names by slot index
        ammo_names = {2: "Clip", 3: "Shell", 4: "Cell", 5: "RocketAmmo"}
        # Doom weapon names by slot index
        weapon_names = {
            1: "Fist", 2: "Pistol", 3: "Shotgun", 4: "Chaingun",
            5: "RocketLauncher", 6: "PlasmaRifle", 7: "BFG9000", 8: "Chainsaw",
            9: "SuperShotgun",
        }

        # Give weapons the player had
        for i, has in enumerate(inv["weapons"]):
            if has > 0 and i in weapon_names:
                g.send_game_command(f"give {weapon_names[i]}")

        # Set ammo: first clear default ammo, then give exact amounts
        for slot, name in ammo_names.items():
            g.send_game_command(f"take {name} 9999")
        # Tick to process take commands
        g.make_action([0.0] * NUM_BUTTONS)

        for slot, name in ammo_names.items():
            amount = int(inv["ammo"][slot])
            if amount > 0:
                g.send_game_command(f"give {name} {amount}")

        # Set health: use god mode to prevent death from take
        target_hp = int(inv["health"])
        if target_hp < 100:
            g.send_game_command("god")
            g.make_action([0.0] * NUM_BUTTONS)
            g.send_game_command(f"take health {100 - target_hp}")
            g.make_action([0.0] * NUM_BUTTONS)
            g.send_game_command("god")
        elif target_hp > 100:
            g.send_game_command(f"give health {target_hp - 100}")

        # Set armor
        target_armor = int(inv["armor"])
        if target_armor > 0:
            # Give full armor then take excess
            g.send_game_command("give armor")
            g.make_action([0.0] * NUM_BUTTONS)
            current_armor = int(g.get_game_variable(vzd.GameVariable.ARMOR))
            if current_armor > target_armor:
                g.send_game_command(f"take armor {current_armor - target_armor}")

        # Tick to apply all commands
        g.make_action([0.0] * NUM_BUTTONS)

    def _get_state_dict(self) -> dict:
        """Extract game state as a dictionary."""
        state = self.game.get_state()
        game_vars = state.game_variables if state and state.game_variables is not None else []

        if len(game_vars) > 0:
            # Live state available — read and track
            # Order: HEALTH, ARMOR, SELECTED_WEAPON_AMMO, KILLCOUNT, ITEMCOUNT, SECRETCOUNT
            health = float(game_vars[0])
            armor = float(game_vars[1]) if len(game_vars) > 1 else 0
            ammo = float(game_vars[2]) if len(game_vars) > 2 else 0
            kill_count = float(game_vars[3]) if len(game_vars) > 3 else 0
            item_count = float(game_vars[4]) if len(game_vars) > 4 else 0
            secret_count = float(game_vars[5]) if len(game_vars) > 5 else 0
            self._last_health = health
            # Save inventory snapshot every tick (used if episode ends suddenly)
            self._capture_inventory()
        else:
            # State is None (episode just ended) — use saved inventory
            inv = self._saved_inventory or {}
            health = inv.get("health", self._last_health)
            armor = inv.get("armor", 0)
            ammo = 0
            kill_count = inv.get("kill_count", 0)
            item_count = inv.get("item_count", 0)
            secret_count = inv.get("secret_count", 0)

        finished = self.game.is_episode_finished()
        dead = finished and self.game.is_player_dead()

        result = {
            "health": health,
            "armor": armor,
            "ammo": ammo,
            "kill_count": kill_count,
            "item_count": item_count,
            "secret_count": secret_count,
            "episode_finished": finished,
            "dead": dead,
            "total_reward": self.game.get_total_reward(),
            "current_map": self._current_map,
        }
        if self._level_total_kills is not None:
            result["total_kills"] = self._level_total_kills
        if self._level_total_items is not None:
            result["total_items"] = self._level_total_items
        if self._level_total_secrets is not None:
            result["total_secrets"] = self._level_total_secrets
        return result

    def _compress_frame(self, screen: np.ndarray) -> bytes:
        """Compress RGB screen buffer to JPEG bytes."""
        img = Image.fromarray(screen)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=20)
        return buf.getvalue()

    def _extract_audio(self, state) -> bytes | None:
        """Extract audio PCM bytes from state, or None if unavailable/silent."""
        if state is None or state.audio_buffer is None:
            return None
        ab = state.audio_buffer
        if ab.size == 0 or np.count_nonzero(ab) == 0:
            return None
        # int16 stereo PCM, little-endian
        return ab.astype("<i2").tobytes()

    def tick(self) -> tuple[bytes, dict, bytes | None]:
        """Advance one game tick using current input state (browser play).
        Returns (jpeg_bytes, state_dict, audio_pcm_or_none).
        """
        with self._lock:
            if self.game.is_episode_finished():
                state = self.game.get_state()
                if state and state.screen_buffer is not None:
                    jpeg = self._compress_frame(state.screen_buffer)
                else:
                    jpeg = self._compress_frame(np.zeros((480, 640, 3), dtype=np.uint8))
                return jpeg, self._get_state_dict(), None

            action = self._build_action()
            reward = self.game.make_action(action)

            state = self.game.get_state()
            if state and state.screen_buffer is not None:
                jpeg = self._compress_frame(state.screen_buffer)
            else:
                jpeg = self._compress_frame(np.zeros((480, 640, 3), dtype=np.uint8))

            audio = self._extract_audio(state)

            state_dict = self._get_state_dict()
            state_dict["last_reward"] = reward

            return jpeg, state_dict, audio

    def step(self, actions: list[float]) -> tuple[bytes, dict, float]:
        """API-driven step: takes explicit action vector, returns (jpeg, state, reward)."""
        with self._lock:
            if self.game.is_episode_finished():
                jpeg = self._compress_frame(np.zeros((480, 640, 3), dtype=np.uint8))
                return jpeg, self._get_state_dict(), 0.0

            # Pad or truncate to NUM_BUTTONS
            padded = (actions + [0.0] * NUM_BUTTONS)[:NUM_BUTTONS]
            reward = self.game.make_action(padded)

            state = self.game.get_state()
            if state and state.screen_buffer is not None:
                jpeg = self._compress_frame(state.screen_buffer)
            else:
                jpeg = self._compress_frame(np.zeros((480, 640, 3), dtype=np.uint8))

            state_dict = self._get_state_dict()
            state_dict["last_reward"] = reward

            return jpeg, state_dict, reward

    @staticmethod
    def _next_map(current: str) -> Optional[str]:
        """Compute the next map name, or None if no obvious successor."""
        m = current.strip().upper()
        # Doom 1 style: E1M1 → E1M2, E1M9 → E2M1
        if len(m) == 4 and m[0] == "E" and m[2] == "M" and m[1].isdigit() and m[3].isdigit():
            ep, lev = int(m[1]), int(m[3])
            if lev < 9:
                return f"E{ep}M{lev + 1}"
            else:
                return f"E{ep + 1}M1"
        # Doom 2 style: MAP01 → MAP02
        if m.startswith("MAP") and m[3:].isdigit():
            num = int(m[3:]) + 1
            return f"MAP{num:02d}"
        return None

    def next_level(self):
        """Advance to the next map, carrying over player inventory."""
        with self._lock:
            nxt = self._next_map(self._current_map)
            if not nxt:
                return
            carry = self._saved_inventory
            try:
                self.game.close()
            except Exception:
                pass
            self.game = vzd.DoomGame()
            self._configure(self._scenario)
            self.game.set_doom_map(nxt)
            self.game.init()
            self._current_map = nxt
            # Restore player state from previous level
            if carry:
                self._saved_inventory = carry
                self._restore_inventory()
            self._last_health = self.game.get_game_variable(vzd.GameVariable.HEALTH)
            self._count_level_totals()
            self._keys_held.clear()
            self._mouse_dx = 0.0
            self._mouse_dy = 0.0
            self._mouse_held = False

    def reset(self, scenario: Optional[str] = None, start_map: Optional[str] = None, skill: Optional[int] = None):
        """Reset the current episode. Optionally change scenario, starting map, and skill."""
        with self._lock:
            if scenario:
                self._scenario = scenario
                try:
                    self.game.close()
                except Exception:
                    pass
                self.game = vzd.DoomGame()
                self._configure(scenario)
                if start_map:
                    self.game.set_doom_map(start_map)
                if skill:
                    self.game.set_doom_skill(skill)
                self.game.init()
                self._current_map = start_map if start_map else self._normalize_map(self.game.get_doom_map())
            else:
                self.game.new_episode()
            self._last_health = 100.0
            self._saved_inventory = None
            self._count_level_totals()
            self._keys_held.clear()
            self._mouse_dx = 0.0
            self._mouse_dy = 0.0
            self._mouse_held = False

    def close(self):
        """Close the game instance."""
        try:
            self.game.close()
        except Exception:
            pass


class GameManager:
    """Manages multiple game sessions by ID. Thread-safe."""

    def __init__(self):
        self._sessions: dict[str, GameSession] = {}
        self._lock = threading.Lock()

    def create_session(self, scenario: str = "basic.cfg") -> str:
        """Create a new game session, return its ID."""
        session_id = str(uuid.uuid4())
        session = GameSession(scenario)
        with self._lock:
            self._sessions[session_id] = session
        return session_id

    def get_session(self, session_id: str) -> Optional[GameSession]:
        """Get a session by ID."""
        with self._lock:
            return self._sessions.get(session_id)

    def destroy_session(self, session_id: str) -> bool:
        """Destroy a session by ID. Returns True if it existed."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            session.close()
            return True
        return False

    def destroy_all(self):
        """Destroy all sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            s.close()
