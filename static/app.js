/**
 * SPHEREx Sky Viewer — Frontend Application Logic
 * No external CDNs, vanilla JS, accessible, progressive loading.
 */

(function () {
  'use strict';

  // ─── Application State ─────────────────────────────────────────────────────
  const state = {
    observations: [],
    currentIndex: 0,
    imageCache: new Map(), // frameId -> Image element or URL
    metaCache: new Map(),  // frameId -> metadata object
    isPlaying: false,
    playTimer: null,
    isBlinking: false,
    blinkTimer: null,
    blinkFrameState: 0, // 0 for A, 1 for B
    fps: 2,
    loadedImagesCount: 0,
    currentAbortController: null,
    isSampleMode: false,
  };

  // ─── DOM Elements ──────────────────────────────────────────────────────────
  const elements = {
    targetInput: document.getElementById('targetInput'),
    searchBtn: document.getElementById('searchBtn'),
    detectorSelect: document.getElementById('detectorSelect'),
    statusDot: document.getElementById('statusDot'),
    statusMessage: document.getElementById('statusMessage'),
    fallbackBanner: document.getElementById('fallbackBanner'),
    fallbackText: document.getElementById('fallbackText'),

    // Viewer
    mainImage: document.getElementById('mainImage'),
    blinkImage: document.getElementById('blinkImage'),
    imageSpinner: document.getElementById('imageSpinner'),
    spinnerText: document.getElementById('spinnerText'),
    emptyState: document.getElementById('emptyState'),
    errorState: document.getElementById('errorState'),
    errorMessage: document.getElementById('errorMessage'),
    errorTitle: document.getElementById('errorTitle'),
    retryBtn: document.getElementById('retryBtn'),
    blinkIndicator: document.getElementById('blinkIndicator'),

    // Badges & Info
    frameCounterBadge: document.getElementById('frameCounterBadge'),
    releaseBadge: document.getElementById('releaseBadge'),
    detectorBadge: document.getElementById('detectorBadge'),
    dateDisplay: document.getElementById('dateDisplay'),
    waveDisplay: document.getElementById('waveDisplay'),
    versionDisplay: document.getElementById('versionDisplay'),

    // Controls
    timeSlider: document.getElementById('timeSlider'),
    prevBtn: document.getElementById('prevBtn'),
    playBtn: document.getElementById('playBtn'),
    nextBtn: document.getElementById('nextBtn'),
    speedSelect: document.getElementById('speedSelect'),
    blinkBtn: document.getElementById('blinkBtn'),
    blinkFrameA: document.getElementById('blinkFrameA'),
    blinkFrameB: document.getElementById('blinkFrameB'),
    fullscreenBtn: document.getElementById('fullscreenBtn'),
    presetBtns: document.querySelectorAll('.btn-preset:not(#sampleModeBtn)'),
    sampleModeBtn: document.getElementById('sampleModeBtn'),
  };

  // ─── Preset Coordinates ───────────────────────────────────────────────────
  const PRESETS = {
    'M101': { ra: 210.80227, dec: 54.34895, band: 'SPHEREx-D2' },
    'NGC 6543': { ra: 269.639, dec: 66.633, band: 'SPHEREx-D3' },
  };

  // ─── Status Updates ────────────────────────────────────────────────────────
  function setStatus(msg, type = 'ready') {
    elements.statusMessage.textContent = msg;
    elements.statusDot.className = `status-indicator ${type}`;
  }

  // ─── Coordinate Parser ─────────────────────────────────────────────────────
  function parseCoordinates(input) {
    const trimmed = input.trim();
    // Matches "210.802, 54.348" or "210.802 54.348"
    const match = trimmed.match(/^([+-]?\d+(?:\.\d+)?)[,\s]+([+-]?\d+(?:\.\d+)?)$/);
    if (match) {
      const ra = parseFloat(match[1]);
      const dec = parseFloat(match[2]);
      if (ra >= 0 && ra < 360 && dec >= -90 && dec <= 90) {
        return { ra, dec };
      }
    }
    return null;
  }

  // ─── Fetch Bands List ──────────────────────────────────────────────────────
  async function loadBands() {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const resp = await fetch('/api/bands', { signal: controller.signal });
      clearTimeout(timer);
      if (resp.ok) {
        const data = await resp.json();
        if (data.bands && data.bands.length > 0) {
          const currentVal = elements.detectorSelect.value;
          elements.detectorSelect.innerHTML = '';
          data.bands.forEach((b) => {
            const opt = document.createElement('option');
            opt.value = b;
            opt.textContent = `${b}`;
            elements.detectorSelect.appendChild(opt);
          });
          if (data.bands.includes(currentVal)) {
            elements.detectorSelect.value = currentVal;
          }
        }
      }
    } catch (e) {
      clearTimeout(timer);
      console.warn('Using default detector band list:', e);
    }
  }

  // ─── Perform Search / Load Target ──────────────────────────────────────────
  async function executeSearch(targetText = null, bandOverride = null) {
    stopPlayback();
    stopBlink();

    const target = targetText || elements.targetInput.value.trim();
    const band = bandOverride || elements.detectorSelect.value;
    elements.detectorBadge.textContent = band;

    if (!target) {
      setStatus('Please enter an object name or celestial coordinates.', 'error');
      return;
    }

    if (state.currentAbortController) {
      state.currentAbortController.abort();
    }
    state.currentAbortController = new AbortController();
    const signal = state.currentAbortController.signal;

    // Reset UI view states
    showSpinner('Locating target & querying SPHEREx archive...');
    elements.emptyState.classList.add('hidden');
    elements.errorState.classList.add('hidden');
    elements.fallbackBanner.classList.add('hidden');
    disableControls(true);

    let ra, dec;
    const directCoords = parseCoordinates(target);

    try {
      if (directCoords) {
        ra = directCoords.ra;
        dec = directCoords.dec;
        setStatus(`Resolved coordinates: RA ${ra.toFixed(4)}°, Dec ${dec.toFixed(4)}°`, 'loading');
      } else {
        // Name resolution via /api/resolve
        setStatus(`Resolving object '${target}' via Simbad/NED...`, 'loading');
        const resolveResp = await fetch(`/api/resolve?name=${encodeURIComponent(target)}`, { signal });
        if (!resolveResp.ok) {
          const errData = await resolveResp.json().catch(() => ({}));
          throw new Error(errData.detail || `Target '${target}' could not be resolved.`);
        }
        const resolved = await resolveResp.json();
        ra = resolved.ra;
        dec = resolved.dec;
        setStatus(`Resolved ${target} to RA ${ra.toFixed(4)}°, Dec ${dec.toFixed(4)}°. Searching SPHEREx ${band}...`, 'loading');
      }

      // Fetch Observations
      const obsUrl = `/api/observations?ra=${ra}&dec=${dec}&band=${encodeURIComponent(band)}&size=0.1`;
      const obsResp = await fetch(obsUrl, { signal });

      if (!obsResp.ok) {
        const errData = await obsResp.json().catch(() => ({}));
        throw new Error(errData.detail || `Server returned HTTP ${obsResp.status}`);
      }

      const obsData = await obsResp.json();
      state.isSampleMode = !!obsData.sample_mode;

      if (state.isSampleMode) {
        elements.fallbackText.textContent = obsData.sample_reason || 'Showing offline sample dataset.';
        elements.fallbackBanner.classList.remove('hidden');
      }

      if (!obsData.observations || obsData.observations.length === 0) {
        showEmptyState(target, band);
        setStatus(`No SPHEREx observations found for ${target} in ${band}.`, 'ready');
        return;
      }

      handleObservationsLoaded(obsData.observations);

    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('Search error:', err);
      showErrorState('Query Failed', err.message);
      setStatus(`Search failed: ${err.message}`, 'error');
    }
  }

  // ─── Handle Loaded Observations ───────────────────────────────────────────
  function handleObservationsLoaded(observations) {
    state.observations = observations;
    state.currentIndex = 0;
    state.loadedImagesCount = 0;
    state.imageCache.clear();
    state.metaCache.clear();

    const count = observations.length;
    setStatus(`Found ${count} observation${count === 1 ? '' : 's'}. Loading first frame...`, 'loading');

    // Update slider
    elements.timeSlider.min = 0;
    elements.timeSlider.max = count - 1;
    elements.timeSlider.value = 0;
    elements.timeSlider.disabled = false;

    // Populate blink select options
    populateBlinkSelects();

    // Enable buttons
    disableControls(false);

    // Progressive loading: Load the first frame and concurrently load remaining frames
    loadAndDisplayFrame(0, true);
    loadRemainingFramesProgressively();
  }

  // ─── Populate Blink Select Dropdowns ──────────────────────────────────────
  function populateBlinkSelects() {
    elements.blinkFrameA.innerHTML = '';
    elements.blinkFrameB.innerHTML = '';

    state.observations.forEach((obs, idx) => {
      const optA = document.createElement('option');
      optA.value = idx;
      optA.textContent = `#${idx + 1} (${obs.time_iso.slice(0, 10)})`;
      elements.blinkFrameA.appendChild(optA);

      const optB = document.createElement('option');
      optB.value = idx;
      optB.textContent = `#${idx + 1} (${obs.time_iso.slice(0, 10)})`;
      elements.blinkFrameB.appendChild(optB);
    });

    elements.blinkFrameA.selectedIndex = 0;
    elements.blinkFrameB.selectedIndex = Math.min(1, state.observations.length - 1);
    elements.blinkFrameA.disabled = false;
    elements.blinkFrameB.disabled = false;
  }

  // ─── Load & Display Frame ──────────────────────────────────────────────────
  async function loadAndDisplayFrame(index, isFirst = false) {
    if (index < 0 || index >= state.observations.length) return;
    state.currentIndex = index;
    elements.timeSlider.value = index;

    const obs = state.observations[index];
    updateMetadataDisplay(obs);

    // If image is already cached in memory
    if (state.imageCache.has(obs.id)) {
      displayImage(state.imageCache.get(obs.id));
      hideSpinner();
      return;
    }

    showSpinner(isFirst ? 'Downloading and rendering initial frame...' : `Loading frame #${index + 1}...`);

    try {
      const imgUrl = `/api/image/${obs.id}.png`;
      const img = new Image();
      img.src = imgUrl;

      await new Promise((resolve, reject) => {
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error(`Frame ${obs.id} image failed to load`));
      });

      state.imageCache.set(obs.id, img);
      state.loadedImagesCount++;

      // If user is still viewing this frame, show it
      if (state.currentIndex === index) {
        displayImage(img);
        hideSpinner();
      }

      updateStatusCount();
      fetchMetadata(obs.id);

    } catch (e) {
      console.warn(`Error loading frame ${obs.id}:`, e);
      if (state.currentIndex === index) {
        hideSpinner();
        setStatus(`Frame #${index + 1} unavailable from IRSA archive: ${e.message}`, 'loading');
      }
    }
  }

  // ─── Display Image on Stage ────────────────────────────────────────────────
  function displayImage(imgElement) {
    elements.emptyState.classList.add('hidden');
    elements.errorState.classList.add('hidden');
    elements.mainImage.src = imgElement.src;
    elements.mainImage.style.display = 'block';
    elements.mainImage.alt = `SPHEREx frame at ${elements.dateDisplay.textContent}, wavelength ${elements.waveDisplay.textContent}`;
  }

  // ─── Fetch Metadata (Wavelength, Version) ──────────────────────────────────
  async function fetchMetadata(frameId) {
    if (state.metaCache.has(frameId)) {
      const m = state.metaCache.get(frameId);
      if (state.observations[state.currentIndex]?.id === frameId) {
        elements.waveDisplay.textContent = `${m.central_wavelength_um.toFixed(4)} μm`;
        elements.versionDisplay.textContent = `${m.version} (MJD ${state.observations[state.currentIndex].time_mjd.toFixed(2)})`;
      }
      return;
    }

    try {
      const resp = await fetch(`/api/meta/${frameId}`);
      if (resp.ok) {
        const m = await resp.json();
        state.metaCache.set(frameId, m);
        if (state.observations[state.currentIndex]?.id === frameId) {
          elements.waveDisplay.textContent = `${m.central_wavelength_um.toFixed(4)} μm`;
          elements.versionDisplay.textContent = `${m.version} (MJD ${state.observations[state.currentIndex].time_mjd.toFixed(2)})`;
        }
      }
    } catch (e) {
      console.warn('Metadata fetch error:', e);
    }
  }

  // ─── Progressive Background Loading ────────────────────────────────────────
  async function loadRemainingFramesProgressively() {
    const queue = state.observations
      .map((obs, idx) => ({ obs, idx }))
      .filter(({ obs }) => !state.imageCache.has(obs.id));

    const concurrency = 3;
    let inFlight = 0;
    let index = 0;

    async function processNext() {
      if (index >= queue.length) return;
      const item = queue[index++];
      inFlight++;

      try {
        const imgUrl = `/api/image/${item.obs.id}.png`;
        const img = new Image();
        img.src = imgUrl;

        await new Promise((resolve) => {
          img.onload = () => resolve(img);
          img.onerror = () => resolve(null);
        });

        if (img.naturalWidth > 0) {
          state.imageCache.set(item.obs.id, img);
          state.loadedImagesCount++;
          updateStatusCount();
        }
      } catch (e) {
        // Skip frame
      } finally {
        inFlight--;
        if (index < queue.length) {
          processNext();
        }
      }
    }

    for (let i = 0; i < Math.min(concurrency, queue.length); i++) {
      processNext();
    }
  }

  function updateStatusCount() {
    const total = state.observations.length;
    const loaded = state.loadedImagesCount;
    if (loaded < total) {
      setStatus(`Loaded ${loaded}/${total} frames...`, 'loading');
    } else {
      setStatus(`All ${total} observations loaded and ready.`, 'ready');
    }
  }

  // ─── Update Metadata Display ───────────────────────────────────────────────
  function updateMetadataDisplay(obs) {
    elements.frameCounterBadge.textContent = `Frame ${state.currentIndex + 1} / ${state.observations.length}`;
    elements.releaseBadge.textContent = obs.release || 'DR1';
    elements.detectorBadge.textContent = obs.detector || elements.detectorSelect.value;
    elements.dateDisplay.textContent = obs.time_iso ? obs.time_iso.replace('T', ' ').slice(0, 19) : '—';
    elements.versionDisplay.textContent = `${obs.version || 'v27'} (MJD ${obs.time_mjd ? obs.time_mjd.toFixed(3) : '—'})`;

    if (obs.central_wavelength_um) {
      elements.waveDisplay.textContent = `${obs.central_wavelength_um.toFixed(4)} μm`;
    } else if (state.metaCache.has(obs.id)) {
      elements.waveDisplay.textContent = `${state.metaCache.get(obs.id).central_wavelength_um.toFixed(4)} μm`;
    } else {
      elements.waveDisplay.textContent = 'Calculating...';
    }
  }

  // ─── Playback Controls ─────────────────────────────────────────────────────
  function togglePlayback() {
    if (state.isPlaying) {
      stopPlayback();
    } else {
      startPlayback();
    }
  }

  function startPlayback() {
    if (state.observations.length <= 1) return;
    stopBlink();
    state.isPlaying = true;
    elements.playBtn.textContent = '⏸ Pause';
    elements.playBtn.classList.add('active');

    const intervalMs = Math.round(1000 / state.fps);
    state.playTimer = setInterval(() => {
      let nextIndex = state.currentIndex + 1;
      if (nextIndex >= state.observations.length) {
        nextIndex = 0; // loop
      }
      loadAndDisplayFrame(nextIndex);
    }, intervalMs);
  }

  function stopPlayback() {
    state.isPlaying = false;
    if (state.playTimer) {
      clearInterval(state.playTimer);
      state.playTimer = null;
    }
    elements.playBtn.textContent = '▶ Play';
    elements.playBtn.classList.remove('active');
  }

  // ─── Blink Compare ─────────────────────────────────────────────────────────
  function toggleBlink() {
    if (state.isBlinking) {
      stopBlink();
    } else {
      startBlink();
    }
  }

  function startBlink() {
    if (state.observations.length < 2) {
      setStatus('At least two frames are required for blink comparison.', 'error');
      return;
    }
    stopPlayback();
    state.isBlinking = true;
    elements.blinkBtn.textContent = 'Stop Blink';
    elements.blinkBtn.classList.add('active');
    elements.blinkIndicator.classList.remove('hidden');

    const indexA = parseInt(elements.blinkFrameA.value, 10) || 0;
    const indexB = parseInt(elements.blinkFrameB.value, 10) || 1;

    state.blinkFrameState = 0;
    loadAndDisplayFrame(indexA);

    state.blinkTimer = setInterval(() => {
      state.blinkFrameState = 1 - state.blinkFrameState;
      const targetIdx = state.blinkFrameState === 0 ? indexA : indexB;
      loadAndDisplayFrame(targetIdx);
    }, 500); // 2 Hz blink rate
  }

  function stopBlink() {
    state.isBlinking = false;
    if (state.blinkTimer) {
      clearInterval(state.blinkTimer);
      state.blinkTimer = null;
    }
    elements.blinkBtn.textContent = 'Blink Compare';
    elements.blinkBtn.classList.remove('active');
    elements.blinkIndicator.classList.add('hidden');
  }

  // ─── Offline Demo / Sample Mode ────────────────────────────────────────────
  async function loadSampleMode() {
    stopPlayback();
    stopBlink();
    showSpinner('Loading bundled sample observations...');
    elements.emptyState.classList.add('hidden');
    elements.errorState.classList.add('hidden');

    try {
      const resp = await fetch('/api/sample');
      if (!resp.ok) throw new Error('Could not load sample dataset.');
      const sampleData = await resp.json();

      state.isSampleMode = true;
      elements.fallbackText.textContent = 'Showing offline demonstration sample dataset.';
      elements.fallbackBanner.classList.remove('hidden');

      // Populate meta cache from sample data
      if (sampleData.meta) {
        Object.entries(sampleData.meta).forEach(([id, m]) => {
          state.metaCache.set(id, m);
        });
      }

      handleObservationsLoaded(sampleData.observations);
      setStatus('Loaded offline demonstration dataset.', 'ready');
    } catch (e) {
      showErrorState('Sample Load Failed', e.message);
      setStatus(`Failed to load samples: ${e.message}`, 'error');
    }
  }

  // ─── UI State Helpers ──────────────────────────────────────────────────────
  function showSpinner(text) {
    elements.spinnerText.textContent = text;
    elements.imageSpinner.classList.remove('hidden');
  }

  function hideSpinner() {
    elements.imageSpinner.classList.add('hidden');
  }

  function showEmptyState(target, band) {
    hideSpinner();
    elements.mainImage.style.display = 'none';
    elements.emptyState.classList.remove('hidden');
    document.getElementById('emptyDetail').textContent =
      `No observations overlap '${target}' for detector ${band}. Try another detector (e.g. D1–D6) or a preset.`;
    disableControls(true);
  }

  function showErrorState(title, message) {
    hideSpinner();
    elements.mainImage.style.display = 'none';
    elements.errorState.classList.remove('hidden');
    elements.errorTitle.textContent = title;
    elements.errorMessage.textContent = message;
  }

  function disableControls(disabled) {
    elements.timeSlider.disabled = disabled;
    elements.prevBtn.disabled = disabled;
    elements.playBtn.disabled = disabled;
    elements.nextBtn.disabled = disabled;
    elements.blinkBtn.disabled = disabled;
  }

  // ─── Event Listeners ───────────────────────────────────────────────────────
  function initEvents() {
    // Search Button & Enter Key
    elements.searchBtn.addEventListener('click', () => executeSearch());
    elements.targetInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') executeSearch();
    });

    // Detector Change
    elements.detectorSelect.addEventListener('change', () => {
      executeSearch();
    });

    // Preset Buttons
    elements.presetBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        elements.presetBtns.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const targetName = btn.dataset.name;
        const targetBand = btn.dataset.band;
        elements.targetInput.value = targetName;
        elements.detectorSelect.value = targetBand;
        executeSearch(targetName, targetBand);
      });
    });

    // Sample Mode Button
    elements.sampleModeBtn.addEventListener('click', () => {
      elements.presetBtns.forEach((b) => b.classList.remove('active'));
      elements.sampleModeBtn.classList.add('active');
      loadSampleMode();
    });

    // Slider
    elements.timeSlider.addEventListener('input', (e) => {
      stopPlayback();
      stopBlink();
      const idx = parseInt(e.target.value, 10);
      loadAndDisplayFrame(idx);
    });

    // Playback Buttons
    elements.playBtn.addEventListener('click', togglePlayback);
    elements.prevBtn.addEventListener('click', () => {
      stopPlayback();
      stopBlink();
      const newIdx = Math.max(0, state.currentIndex - 1);
      loadAndDisplayFrame(newIdx);
    });
    elements.nextBtn.addEventListener('click', () => {
      stopPlayback();
      stopBlink();
      const newIdx = Math.min(state.observations.length - 1, state.currentIndex + 1);
      loadAndDisplayFrame(newIdx);
    });

    // Speed Select
    elements.speedSelect.addEventListener('change', (e) => {
      state.fps = parseInt(e.target.value, 10) || 2;
      if (state.isPlaying) {
        stopPlayback();
        startPlayback();
      }
    });

    // Blink Compare
    elements.blinkBtn.addEventListener('click', toggleBlink);

    // Fullscreen / Zoom
    elements.fullscreenBtn.addEventListener('click', () => {
      const stage = document.getElementById('imageStage');
      if (!document.fullscreenElement) {
        stage.requestFullscreen().catch(() => {});
      } else {
        document.exitFullscreen().catch(() => {});
      }
    });

    // Retry Button
    elements.retryBtn.addEventListener('click', () => executeSearch());

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      // Ignore if focus is in an input or select
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
        return;
      }

      if (e.code === 'Space') {
        e.preventDefault();
        togglePlayback();
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault();
        stopPlayback();
        stopBlink();
        const newIdx = Math.max(0, state.currentIndex - 1);
        loadAndDisplayFrame(newIdx);
      } else if (e.code === 'ArrowRight') {
        e.preventDefault();
        stopPlayback();
        stopBlink();
        const newIdx = Math.min(state.observations.length - 1, state.currentIndex + 1);
        loadAndDisplayFrame(newIdx);
      } else if (e.key === 'b' || e.key === 'B') {
        e.preventDefault();
        toggleBlink();
      }
    });
  }

  // ─── Initialization ────────────────────────────────────────────────────────
  async function init() {
    initEvents();
    loadBands(); // Non-blocking: fetch in background, don't stall initial search

    // Respect prefers-reduced-motion: never autoplay if user prefers reduced motion
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (prefersReducedMotion) {
      console.log('User prefers reduced motion; autoplay disabled.');
    }

    // Auto-search default target M101 on startup
    executeSearch('M101', 'SPHEREx-D2');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
