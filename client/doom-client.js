/**
 * DoomClient — canvas rendering + input + WebSocket communication for ViZDoom.
 * Overlay / pointer-lock state is managed by app.js, not here.
 */

// Binary message tags (must match server)
const TAG_VIDEO = 0x01;
const TAG_AUDIO = 0x02;

// Audio config (must match server: 22050 Hz stereo int16)
const AUDIO_SAMPLE_RATE = 22050;
const AUDIO_CHANNELS = 2;

export default class DoomClient {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');

    this.ws = null;
    this.connected = false;

    // FPS tracking
    this._frameCount = 0;
    this._lastFpsTime = performance.now();
    this._frameSeq = 0;
    this.fps = 0;

    // Callbacks
    this.onStateUpdate = null;   // (stateObj) => {}
    this.onFpsUpdate = null;     // (fps) => {}
    this.onScenarios = null;     // (scenarioNames[]) => {}
    this.onConnect = null;       // () => {}
    this.onDisconnect = null;    // () => {}
    this.onPointerLockChange = null; // (locked: boolean) => {}

    // Mouse accumulation
    this._mouseDx = 0;
    this._mouseDy = 0;
    this._mouseFlushInterval = null;

    // When true, vertical mouse movement is ignored (original Doom behavior)
    this.lockVerticalMouse = false;

    // Audio playback
    this._audioCtx = null;
    this._audioNextTime = 0;
    this._audioEnabled = false;

    this._setupInput();
  }

  // --- Connection ---

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    const wsUrl = `${protocol}//${host}/ws`;

    this.ws = new WebSocket(wsUrl);
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      this.connected = true;
      if (this.onConnect) this.onConnect();
    };

    this.ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        const view = new Uint8Array(event.data);
        if (view.length < 2) return;
        const tag = view[0];
        const payload = event.data.slice(1);
        if (tag === TAG_VIDEO) {
          this._renderFrame(payload);
        } else if (tag === TAG_AUDIO) {
          this._playAudio(payload);
        }
      } else {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'state' && this.onStateUpdate) {
            this.onStateUpdate(msg);
          } else if (msg.type === 'scenarios' && this.onScenarios) {
            this.onScenarios(msg.scenarios);
          }
        } catch (e) {
          console.error('Failed to parse WS text message', e);
        }
      }
    };

    this.ws.onclose = () => {
      this.connected = false;
      if (this.onDisconnect) this.onDisconnect();
      setTimeout(() => this.connect(), 2000);
    };

    this.ws.onerror = (err) => {
      console.error('WebSocket error', err);
    };
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  // --- Frame rendering ---

  _renderFrame(buffer) {
    // Bump sequence so stale frames get dropped
    const seq = ++this._frameSeq;
    const blob = new Blob([buffer], { type: 'image/jpeg' });
    createImageBitmap(blob).then((bmp) => {
      // Drop if a newer frame arrived while decoding
      if (seq !== this._frameSeq) { bmp.close(); return; }
      this.ctx.drawImage(bmp, 0, 0, this.canvas.width, this.canvas.height);
      bmp.close();

      this._frameCount++;
      const now = performance.now();
      if (now - this._lastFpsTime >= 1000) {
        this.fps = this._frameCount;
        this._frameCount = 0;
        this._lastFpsTime = now;
        if (this.onFpsUpdate) this.onFpsUpdate(this.fps);
      }
    });
  }

  // --- Input ---

  _setupInput() {
    // Keyboard (only send when pointer is locked = actively playing)
    document.addEventListener('keydown', (e) => {
      if (document.pointerLockElement !== this.canvas) return;
      if (e.repeat) return;
      e.preventDefault();
      this._send({ type: 'keydown', key: e.key.toLowerCase() });
    });

    document.addEventListener('keyup', (e) => {
      if (document.pointerLockElement !== this.canvas) return;
      e.preventDefault();
      this._send({ type: 'keyup', key: e.key.toLowerCase() });
    });

    // Mouse move (only while pointer locked)
    document.addEventListener('mousemove', (e) => {
      if (document.pointerLockElement !== this.canvas) return;
      this._mouseDx += e.movementX;
      if (!this.lockVerticalMouse) {
        this._mouseDy += e.movementY;
      }
    });

    // Mouse button hold = continuous fire
    document.addEventListener('mousedown', (e) => {
      if (document.pointerLockElement !== this.canvas) return;
      if (e.button === 0) {
        this._send({ type: 'mousedown' });
      }
    });

    document.addEventListener('mouseup', (e) => {
      if (e.button === 0) {
        this._send({ type: 'mouseup' });
      }
    });

    // Flush mouse deltas at 60Hz
    this._mouseFlushInterval = setInterval(() => {
      if (this._mouseDx !== 0 || this._mouseDy !== 0) {
        this._send({ type: 'mouse', dx: this._mouseDx, dy: this._mouseDy });
        this._mouseDx = 0;
        this._mouseDy = 0;
      }
    }, 1000 / 60);

    // Notify app.js of pointer lock changes
    document.addEventListener('pointerlockchange', () => {
      if (this.onPointerLockChange) {
        this.onPointerLockChange(!!document.pointerLockElement);
      }
    });
  }

  // --- Audio playback ---

  enableAudio() {
    if (this._audioCtx) return;
    try {
      this._audioCtx = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: AUDIO_SAMPLE_RATE,
      });
      this._audioNextTime = 0;
      this._audioEnabled = true;
    } catch (e) {
      console.warn('Web Audio API not available:', e);
    }
  }

  _playAudio(buffer) {
    if (!this._audioCtx || !this._audioEnabled) return;
    if (this._audioCtx.state === 'suspended') {
      this._audioCtx.resume();
    }

    // Decode int16 stereo PCM from ArrayBuffer
    const int16 = new Int16Array(buffer);
    const numSamples = int16.length / AUDIO_CHANNELS;
    if (numSamples === 0) return;

    const audioBuffer = this._audioCtx.createBuffer(
      AUDIO_CHANNELS, numSamples, AUDIO_SAMPLE_RATE
    );

    // Convert int16 to float32 [-1, 1] for each channel
    for (let ch = 0; ch < AUDIO_CHANNELS; ch++) {
      const channelData = audioBuffer.getChannelData(ch);
      for (let i = 0; i < numSamples; i++) {
        channelData[i] = int16[i * AUDIO_CHANNELS + ch] / 32768;
      }
    }

    const source = this._audioCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(this._audioCtx.destination);

    // Schedule seamlessly after the previous chunk
    const now = this._audioCtx.currentTime;
    const startTime = Math.max(now, this._audioNextTime);
    source.start(startTime);
    this._audioNextTime = startTime + audioBuffer.duration;
  }

  // --- Pointer lock ---

  requestPointerLock() {
    this.canvas.requestPointerLock();
  }

  // --- Pause ---

  setPaused(paused) {
    this._send({ type: paused ? 'pause' : 'unpause' });
    // Reset audio scheduling on pause to avoid queued stale audio
    if (paused) {
      this._audioNextTime = 0;
    }
  }

  // --- Game commands ---

  reset() {
    this._send({ type: 'reset' });
  }

  nextLevel() {
    this._send({ type: 'next_level' });
  }

  setScenario(name, map, skill) {
    const msg = { type: 'scenario', name };
    if (map) msg.map = map;
    if (skill) msg.skill = skill;
    this._send(msg);
  }

  destroy() {
    clearInterval(this._mouseFlushInterval);
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
    }
    if (this._audioCtx) {
      this._audioCtx.close();
      this._audioCtx = null;
    }
  }
}
