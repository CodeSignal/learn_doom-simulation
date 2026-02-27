import Modal from './design-system/components/modal/modal.js';
import DoomClient from './doom-client.js';

let helpModal = null;
let doomClient = null;

// Game UI state: 'menu' | 'playing' | 'dead' | 'won'
let uiState = 'menu';
let clickToPlayTimer = null;

// Game mode: 'campaign' | 'test' | null
let gameMode = null;

// Campaign selection state — accumulated during menu navigation
let pendingCampaign = null; // { scenario: 'doom.cfg', map: 'E1M1' }

// --- Overlay management ---

const OVERLAYS = ['overlay-disconnected', 'overlay-menu', 'overlay-test-menu', 'overlay-episode', 'overlay-skill', 'overlay-click', 'overlay-pause', 'overlay-death', 'overlay-win'];

function showOnly(id) {
  for (const oid of OVERLAYS) {
    const el = document.getElementById(oid);
    if (el) el.style.display = oid === id ? 'flex' : 'none';
  }
}

function hideAll() {
  clearTimeout(clickToPlayTimer);
  for (const oid of OVERLAYS) {
    const el = document.getElementById(oid);
    if (el) el.style.display = 'none';
  }
}

// --- Title management ---

const DEFAULT_TITLE = 'Doom Simulation';

function updateTitle(text) {
  document.title = text;
  const el = document.getElementById('page-title');
  if (el) el.textContent = text;
}

// --- State transitions ---

function setHudVisible(visible) {
  const bar = document.querySelector('.hud-bar');
  if (bar) bar.style.display = visible ? '' : 'none';
}

function goToMenu() {
  uiState = 'menu';
  gameMode = null;
  pendingCampaign = null;
  setHudVisible(true);
  updateTitle(DEFAULT_TITLE);
  doomClient.lockVerticalMouse = false;
  if (document.pointerLockElement) document.exitPointerLock();
  doomClient.setPaused(true);
  showOnly('overlay-menu');
}

function startCampaign(scenario, map, skill) {
  gameMode = 'campaign';
  uiState = 'playing';
  hideAll();
  setHudVisible(false);
  doomClient.lockVerticalMouse = true;
  doomClient.enableAudio();
  doomClient.setScenario(scenario, map, skill);
  doomClient.setPaused(false);
  doomClient.requestPointerLock();
}

function startScenario(name) {
  gameMode = 'test';
  uiState = 'playing';
  hideAll();
  setHudVisible(true);
  updateTitle(name.replace('.cfg', ''));
  doomClient.lockVerticalMouse = false;
  doomClient.enableAudio();  // activate on user gesture
  doomClient.setScenario(name);
  doomClient.setPaused(false);
  doomClient.requestPointerLock();
}

function restartLevel() {
  uiState = 'playing';
  hideAll();
  doomClient.reset();
  doomClient.setPaused(false);
  doomClient.requestPointerLock();
}

function nextLevel() {
  uiState = 'playing';
  hideAll();
  doomClient.nextLevel();
  doomClient.setPaused(false);
  doomClient.requestPointerLock();
}

// --- HUD ---

function formatStat(count, total) {
  const c = Math.round(count);
  if (total != null && total > 0) {
    const pct = Math.round((c / total) * 100);
    return `${c}/${Math.round(total)} (${pct}%)`;
  }
  return String(c);
}

function updateHud(state) {
  const hp = document.getElementById('hud-hp');
  const armor = document.getElementById('hud-armor');
  const ammo = document.getElementById('hud-ammo');
  const kills = document.getElementById('hud-kills');

  if (hp) hp.textContent = `HP: ${Math.round(state.health)}`;
  if (armor) armor.textContent = `Armor: ${Math.round(state.armor)}`;
  if (ammo) ammo.textContent = `Ammo: ${Math.round(state.ammo)}`;
  if (kills) kills.textContent = `Kills: ${Math.round(state.kill_count)}`;

  // Update title with current map name
  if (state.current_map && uiState === 'playing') {
    updateTitle(state.current_map);
  }

  // Only react to episode end while actively playing
  if (uiState !== 'playing') return;

  if (state.dead) {
    uiState = 'dead';
    doomClient.setPaused(true);
    if (document.pointerLockElement) document.exitPointerLock();
    showOnly('overlay-death');
  } else if (state.episode_finished) {
    uiState = 'won';
    doomClient.setPaused(true);
    if (document.pointerLockElement) document.exitPointerLock();
    // Populate stats
    const sk = document.getElementById('stat-kills');
    const si = document.getElementById('stat-items');
    const ss = document.getElementById('stat-secrets');
    if (sk) sk.textContent = formatStat(state.kill_count, state.total_kills);
    if (si) si.textContent = formatStat(state.item_count, state.total_items);
    if (ss) ss.textContent = formatStat(state.secret_count, state.total_secrets);
    // Show/hide Next Level button based on game mode
    const nextBtn = document.getElementById('btn-next-level');
    if (nextBtn) nextBtn.style.display = gameMode === 'campaign' ? '' : 'none';
    showOnly('overlay-win');
  }
}

function updateFps(fps) {
  const el = document.getElementById('hud-fps');
  if (el) el.textContent = `FPS: ${fps}`;
}

// --- Pointer lock changes ---

function onPointerLockChange(locked) {
  clearTimeout(clickToPlayTimer);

  if (locked) {
    hideAll();
    if (uiState === 'playing') {
      doomClient.setPaused(false);
    }
  } else if (uiState === 'playing') {
    // User pressed Escape — show pause overlay immediately, then
    // add "Click to Play" after browser cooldown (1.5s)
    doomClient.setPaused(true);
    showOnly('overlay-pause');
    const sub = document.getElementById('pause-subtitle');
    if (sub) sub.style.visibility = 'hidden';

    clickToPlayTimer = setTimeout(() => {
      if (uiState === 'playing' && !document.pointerLockElement) {
        if (sub) sub.style.visibility = 'visible';
      }
    }, 1500);
  }
}

// --- Menu ---

const CAMPAIGN_CONFIGS = ['doom.cfg', 'doom2.cfg', 'freedoom1.cfg', 'freedoom2.cfg'];

function populateMenu(scenarios) {
  const grid = document.getElementById('menu-scenarios');
  if (!grid) return;
  grid.innerHTML = '';
  const testScenarios = scenarios.filter((name) => !CAMPAIGN_CONFIGS.includes(name));
  testScenarios.forEach((name) => {
    const card = document.createElement('div');
    card.className = 'menu-card';
    card.innerHTML = `<div class="menu-card-name">${name.replace('.cfg', '')}</div>`;
    card.addEventListener('click', () => startScenario(name));
    grid.appendChild(card);
  });
}

// --- Initialization ---

function initDoom() {
  const canvas = document.getElementById('game-canvas');
  if (!canvas) return;

  doomClient = new DoomClient(canvas);
  doomClient.onStateUpdate = updateHud;
  doomClient.onFpsUpdate = updateFps;
  doomClient.onScenarios = populateMenu;
  doomClient.onPointerLockChange = onPointerLockChange;
  doomClient.onConnect = () => {
    showOnly('overlay-menu');
    uiState = 'menu';
  };
  doomClient.onDisconnect = () => {
    uiState = 'menu';
    if (document.pointerLockElement) document.exitPointerLock();
    showOnly('overlay-disconnected');
  };

  // Click anywhere in game area requests pointer lock when paused mid-game
  const resumePlay = () => {
    if (uiState === 'playing' && !document.pointerLockElement) {
      doomClient.requestPointerLock();
    }
  };
  canvas.addEventListener('click', resumePlay);
  document.getElementById('overlay-click')?.addEventListener('click', resumePlay);
  document.getElementById('overlay-pause')?.addEventListener('click', resumePlay);

  doomClient.connect();

  // Header menu button
  document.getElementById('btn-menu')?.addEventListener('click', goToMenu);

  // Campaign buttons — open episode/skill menus
  document.getElementById('btn-play-doom')?.addEventListener('click', () => {
    pendingCampaign = { scenario: 'doom.cfg' };
    showOnly('overlay-episode');
  });
  document.getElementById('btn-play-doom2')?.addEventListener('click', () => {
    pendingCampaign = { scenario: 'doom2.cfg', map: 'MAP01' };
    showOnly('overlay-skill');
  });

  // Episode select buttons (Doom 1)
  document.querySelectorAll('.menu-episode-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (pendingCampaign) pendingCampaign.map = btn.dataset.map;
      showOnly('overlay-skill');
    });
  });

  // Skill select buttons (shared)
  document.querySelectorAll('.menu-skill-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (pendingCampaign) {
        startCampaign(pendingCampaign.scenario, pendingCampaign.map, parseInt(btn.dataset.skill, 10));
      }
    });
  });

  // Episode/skill back buttons
  document.getElementById('btn-episode-back')?.addEventListener('click', () => showOnly('overlay-menu'));
  document.getElementById('btn-skill-back')?.addEventListener('click', () => {
    if (pendingCampaign && pendingCampaign.scenario === 'doom.cfg') {
      showOnly('overlay-episode');
    } else {
      showOnly('overlay-menu');
    }
  });

  // Test levels navigation
  document.getElementById('btn-test-levels')?.addEventListener('click', () => showOnly('overlay-test-menu'));
  document.getElementById('btn-test-back')?.addEventListener('click', () => showOnly('overlay-menu'));

  // Death overlay buttons
  document.getElementById('btn-respawn')?.addEventListener('click', restartLevel);
  document.getElementById('btn-death-menu')?.addEventListener('click', goToMenu);

  // Win overlay buttons
  document.getElementById('btn-next-level')?.addEventListener('click', nextLevel);
  document.getElementById('btn-restart-win')?.addEventListener('click', restartLevel);
  document.getElementById('btn-win-menu')?.addEventListener('click', goToMenu);
}

// Load help content and initialize modal
async function initializeHelpModal() {
  try {
    const response = await fetch('./help-content.html');
    const helpContent = await response.text();

    helpModal = Modal.createHelpModal({
      title: 'Help / User Guide',
      content: helpContent
    });

    const helpButton = document.getElementById('btn-help');
    if (helpButton) {
      helpButton.addEventListener('click', () => {
        helpModal.open();
      });
    }
  } catch (error) {
    console.error('Failed to load help content:', error);
    helpModal = Modal.createHelpModal({
      title: 'Help / User Guide',
      content: '<p>Help content could not be loaded.</p>'
    });
    const helpButton = document.getElementById('btn-help');
    if (helpButton) {
      helpButton.addEventListener('click', () => helpModal.open());
    }
  }
}

async function initialize() {
  await initializeHelpModal();
  initDoom();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initialize);
} else {
  initialize();
}
