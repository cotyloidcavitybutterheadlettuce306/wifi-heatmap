// ============================================================
//  wifi-heatmap frontend
//
//  Single global state object, no framework. All DOM updates
//  flow through render() so we never get out of sync.
// ============================================================

const state = {
  authenticated: false,
  hasFloorplan: false,
  fpWidth: 0,
  fpHeight: 0,
  points: [],
  bssids: [],
  selectedBssid: '',
  showFloorplan: true,
  showHeatmap: false,
  alpha: 0.6,
  scanning: false,
  rssiMin: -85,
  rssiMax: -30,
  snrMin: 5,
  snrMax: 50,
  heatmapMode: 'rssi',
  forceRoam: false,
  speedTest: false,
  accessPoints: [],
  placingAP: false,
  pendingAPCoords: null,
  // Pan / zoom
  scale: 1,
  panX: 0,
  panY: 0,
  // Scanner mode
  scanMode: 'local',  // 'local' or 'remote'
  agentUrl: '',
  // Live monitor
  monitoring: false,
  // Insights + snapshots
  activeTab: 'tab-heatmap',
  findings: [],
  snapshots: [],
  // Rooms
  rooms: [],
  drawingRoom: false,
  roomCorner1: null,  // first click {x, y} in image coords
};

// ----- DOM refs -----
const $ = id => document.getElementById(id);
const on = (el, evt, fn, opts) => { if (el) el.addEventListener(evt, fn, opts); };
const els = {
  authOverlay: $('auth-overlay'),
  authPassword: $('auth-password'),
  authSubmit: $('auth-submit'),
  authError: $('auth-error'),

  app: $('app'),
  statusText: $('status-text'),
  uploadInput: $('floorplan-upload'),

  canvasWrap: $('canvas-wrap'),
  canvasEmpty: $('canvas-empty'),
  canvasStage: $('canvas-stage'),
  floorplanImg: $('floorplan-img'),
  heatmapOverlay: $('heatmap-overlay'),
  markersLayer: $('markers-layer'),
  scanOverlay: $('scan-overlay'),

  floorplanToggle: $('floorplan-toggle'),
  heatmapToggle: $('heatmap-toggle'),
  forceRoamToggle: $('force-roam-toggle'),
  speedTestToggle: $('speed-test-toggle'),
  bssidFilter: $('bssid-filter'),
  alphaSlider: $('alpha-slider'),
  alphaValue: $('alpha-value'),

  pointsList: $('points-list'),
  clearAll: $('clear-all'),

  drawRoomBtn: $('draw-room-btn'),
  roomList: $('room-list'),
  roomDialog: $('room-dialog'),
  roomName: $('room-name'),
  roomDialogCancel: $('room-dialog-cancel'),
  roomDialogSave: $('room-dialog-save'),

  clearAllAp: $('clear-all-ap'),
  placeApBtn: $('place-ap-btn'),
  apList: $('ap-list'),
  apDialog: $('ap-dialog'),
  apName: $('ap-name'),
  apBssid: $('ap-bssid'),
  apBssidList: $('ap-bssid-list'),
  apDialogCancel: $('ap-dialog-cancel'),
  apDialogSave: $('ap-dialog-save'),

  canvasHint: $('canvas-hint'),
  zoomIndicator: $('zoom-indicator'),
  zoomLevel: $('zoom-level'),
  resetView: $('reset-view'),

  monitorBtn: $('monitor-btn'),
  liveMonitor: $('live-monitor'),
  monitorClose: $('monitor-close'),
  monitorSsid: $('monitor-ssid'),
  monitorBssid: $('monitor-bssid'),
  monitorApRow: $('monitor-ap-row'),
  monitorApName: $('monitor-ap-name'),
  monitorRssiValue: $('monitor-rssi-value'),
  monitorBar: $('monitor-bar'),
  monitorSnr: $('monitor-snr'),
  monitorChannel: $('monitor-channel'),
  monitorTxRate: $('monitor-tx-rate'),
  monitorTime: $('monitor-time'),

  newSurveyBtn: $('new-survey-btn'),
  exportBtn: $('export-btn'),

  insightsList: $('insights-list'),

  snapshotBtn: $('snapshot-btn'),
  snapshotDialog: $('snapshot-dialog'),
  snapshotName: $('snapshot-name'),
  snapshotCancel: $('snapshot-cancel'),
  snapshotSave: $('snapshot-save'),

  compareSelect: $('compare-select'),
  compareModal: $('compare-modal'),
  compareClose: $('compare-close'),
  compareCurrentImg: $('compare-current-img'),
  compareSnapshotImg: $('compare-snapshot-img'),
  compareSnapshotLabel: $('compare-snapshot-label'),
  compareDiff: $('compare-diff'),

  toast: $('toast'),
};

// ----- API helpers -----

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body ? { 'Content-Type': 'application/json' } : {},
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || res.statusText);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res;
}

function showToast(msg, isError = false) {
  els.toast.textContent = msg;
  els.toast.classList.toggle('error', isError);
  els.toast.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { els.toast.hidden = true; }, 3000);
}

// ----- UI settings persistence (localStorage) -----

const SETTINGS_KEY = 'wifi-heatmap-settings';

function saveSettings() {
  const s = {
    activeTab: state.activeTab,
    showFloorplan: state.showFloorplan,
    showHeatmap: state.showHeatmap,
    heatmapMode: state.heatmapMode,
    alpha: state.alpha,
    forceRoam: state.forceRoam,
    speedTest: state.speedTest,
    selectedBssid: state.selectedBssid,
  };
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch {}
}

function loadSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return;
    const s = JSON.parse(raw);
    if (s.activeTab) { state.activeTab = s.activeTab; switchToTab(s.activeTab); }
    if (s.showFloorplan != null) {
      state.showFloorplan = s.showFloorplan;
      if (els.floorplanToggle) els.floorplanToggle.classList.toggle('on', s.showFloorplan);
      if (els.floorplanImg) els.floorplanImg.style.opacity = s.showFloorplan ? '1' : '0';
    }
    if (s.showHeatmap != null) {
      state.showHeatmap = s.showHeatmap;
      if (els.heatmapToggle) els.heatmapToggle.classList.toggle('on', s.showHeatmap);
    }
    if (s.heatmapMode) {
      state.heatmapMode = s.heatmapMode;
      // Update segmented control
      document.querySelectorAll('#metric-seg button').forEach(b => {
        b.classList.toggle('on', b.dataset.metric === s.heatmapMode);
      });
      updateLegend();
    }
    if (s.alpha != null) {
      state.alpha = s.alpha;
      if (els.alphaSlider) els.alphaSlider.value = Math.round(s.alpha * 100);
      if (els.alphaValue) els.alphaValue.textContent = Math.round(s.alpha * 100) + '%';
    }
    if (s.forceRoam != null) {
      state.forceRoam = s.forceRoam;
      if (els.forceRoamToggle) els.forceRoamToggle.classList.toggle('on', s.forceRoam);
    }
    if (s.speedTest != null) {
      state.speedTest = s.speedTest;
      if (els.speedTestToggle) els.speedTestToggle.classList.toggle('on', s.speedTest);
    }
    if (s.selectedBssid != null) {
      state.selectedBssid = s.selectedBssid;
      // BSSID filter dropdown is rebuilt after points load, so we defer
    }
  } catch {}
}

function restoreBssidFilter() {
  if (state.selectedBssid && els.bssidFilter) {
    // Check the saved BSSID still exists in the dropdown
    const exists = [...els.bssidFilter.options].some(o => o.value === state.selectedBssid);
    if (exists) {
      els.bssidFilter.value = state.selectedBssid;
      renderPoints();
    } else {
      state.selectedBssid = '';
    }
  }
}

// ----- Stable BSSID color from MAC hash -----

function bssidColor(bssid) {
  if (!bssid) return '#666';
  let h = 0;
  for (let i = 0; i < bssid.length; i++) {
    h = ((h << 5) - h) + bssid.charCodeAt(i);
    h |= 0;
  }
  return `hsl(${Math.abs(h) % 360}, 70%, 55%)`;
}

// ----- Auth flow -----

async function tryAuth() {
  const pw = els.authPassword.value;
  if (!pw) return;
  els.authSubmit.disabled = true;
  els.authError.hidden = true;
  try {
    const authRes = await api('/api/auth', {
      method: 'POST',
      body: JSON.stringify({ password: pw }),
    });
    state.authenticated = true;
    state.scanMode = authRes.mode || 'local';
    els.authPassword.value = '';
    els.authOverlay.hidden = true;
    els.app.hidden = false;
    await loadStatus();
    await loadPoints();
    await loadAccessPoints();
    await loadRooms();
    loadSettings();
    restoreBssidFilter();
    if (state.showHeatmap) refreshHeatmap();
    loadInsights();
    loadSnapshots();
  } catch (e) {
    els.authError.textContent = e.message;
    els.authError.hidden = false;
  } finally {
    els.authSubmit.disabled = false;
  }
}

on(els.authSubmit, 'click', tryAuth);
on(els.authPassword, 'keydown', e => {
  if (e.key === 'Enter') tryAuth();
});

// Auth tab switching
document.querySelectorAll('.auth-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.auth-pane').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    const pane = $('auth-' + tab.dataset.auth);
    if (pane) pane.classList.add('active');
    // Focus the right input
    if (tab.dataset.auth === 'local' && els.authPassword) els.authPassword.focus();
    if (tab.dataset.auth === 'remote') {
      const urlInput = $('auth-agent-url');
      if (urlInput) urlInput.focus();
    }
  });
});

// Remote agent auth
const authAgentUrl = $('auth-agent-url');
const authRemoteSubmit = $('auth-remote-submit');
on(authRemoteSubmit, 'click', tryRemoteAuth);
on(authAgentUrl, 'keydown', e => { if (e.key === 'Enter') tryRemoteAuth(); });

async function tryRemoteAuth() {
  const url = authAgentUrl ? authAgentUrl.value.trim() : '';
  if (!url) return;
  if (authRemoteSubmit) authRemoteSubmit.disabled = true;
  els.authError.hidden = true;
  try {
    await api('/api/auth', {
      method: 'POST',
      body: JSON.stringify({ agent_url: url }),
    });
    state.authenticated = true;
    els.authOverlay.hidden = true;
    els.app.hidden = false;
    await loadStatus();
    await loadPoints();
    await loadAccessPoints();
    await loadRooms();
    loadInsights();
    loadSnapshots();
    state.scanMode = 'remote';
    state.agentUrl = url;
    updateStatusText();
    showToast('Connected to remote agent');
  } catch (e) {
    els.authError.textContent = e.message;
    els.authError.hidden = false;
  } finally {
    if (authRemoteSubmit) authRemoteSubmit.disabled = false;
  }
}

// ----- Status / floor plan loading -----

async function loadStatus() {
  const s = await api('/api/status');
  state.hasFloorplan = s.has_floorplan;
  state.fpWidth = s.floorplan_width || 0;
  state.fpHeight = s.floorplan_height || 0;
  state.rssiMin = s.rssi_min;
  state.rssiMax = s.rssi_max;
  state.snrMin = s.snr_min || 5;
  state.snrMax = s.snr_max || 50;

  if (state.hasFloorplan) {
    els.floorplanImg.src = '/api/floorplan?t=' + Date.now();
    els.canvasEmpty.hidden = true;
    els.canvasStage.hidden = false;
    els.zoomIndicator.hidden = false;
  } else {
    els.canvasEmpty.hidden = false;
    els.canvasStage.hidden = true;
    els.zoomIndicator.hidden = true;
  }
  updateStatusText();
}

function updateStatusText() {
  if (!state.hasFloorplan) {
    els.statusText.textContent = '\u2014 upload a floor plan to begin \u2014';
    return;
  }
  const n = state.points.length;
  const mode = state.scanMode === 'remote'
    ? ` \u00b7 \ud83d\udce1 remote`
    : ' \u00b7 local';
  els.statusText.textContent =
    `${state.fpWidth}\u00d7${state.fpHeight}px \u00b7 ${n} measurement${n === 1 ? '' : 's'}${mode}`;
}

function updateTabLabels() {
  const ptCount = state.points.length;
  const warnCount = state.findings.filter(f => f.severity === 'warn' || f.severity === 'bad').length;
  const ptTab = document.querySelector('.tab[data-tab="tab-points"]');
  const insTab = document.querySelector('.tab[data-tab="tab-insights"]');
  if (ptTab) ptTab.textContent = ptCount > 0 ? `Points (${ptCount})` : 'Points';
  if (insTab) insTab.textContent = warnCount > 0 ? `Insights (${warnCount})` : 'Insights';
}

// ----- Floor plan upload -----

on(els.uploadInput, 'change', async e => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    await fetch('/api/floorplan', { method: 'POST', body: fd })
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j)));
    await loadStatus();
    await loadPoints();
    await loadAccessPoints();
    await loadRooms();
    loadInsights();
    showToast('Floor plan uploaded \u2014 previous measurements cleared');
  } catch (err) {
    showToast(err.error || 'Upload failed', true);
  }
  els.uploadInput.value = '';
});

// ----- Pan and Zoom -----

let _isPanning = false;
let _panStartX = 0;
let _panStartY = 0;
let _dragDist = 0;

function applyTransform() {
  els.canvasStage.style.transform =
    `translate(${state.panX}px, ${state.panY}px) scale(${state.scale})`;
  els.zoomLevel.textContent = Math.round(state.scale * 100) + '%';
}

function resetView() {
  if (!state.fpWidth) return;
  const wrap = els.canvasWrap.getBoundingClientRect();
  const sx = wrap.width / state.fpWidth;
  const sy = wrap.height / state.fpHeight;
  state.scale = Math.min(1, sx, sy) * 0.95;
  state.panX = (wrap.width - state.fpWidth * state.scale) / 2;
  state.panY = (wrap.height - state.fpHeight * state.scale) / 2;
  applyTransform();
}

on(els.floorplanImg, 'load', resetView);

on(els.canvasWrap, 'wheel', e => {
  if (!state.hasFloorplan) return;
  e.preventDefault();
  const factor = e.deltaY > 0 ? 0.9 : 1.1;
  const newScale = Math.max(0.05, Math.min(20, state.scale * factor));
  const rect = els.canvasWrap.getBoundingClientRect();
  const cx = e.clientX - rect.left;
  const cy = e.clientY - rect.top;
  // Point under cursor in image space
  const ix = (cx - state.panX) / state.scale;
  const iy = (cy - state.panY) / state.scale;
  state.scale = newScale;
  state.panX = cx - ix * newScale;
  state.panY = cy - iy * newScale;
  applyTransform();
}, { passive: false });

on(els.canvasWrap, 'mousedown', e => {
  if (e.button !== 0) return;
  _isPanning = true;
  _panStartX = e.clientX - state.panX;
  _panStartY = e.clientY - state.panY;
  _dragDist = 0;
  els.canvasWrap.classList.add('panning');
});

window.addEventListener('mousemove', e => {
  if (!_isPanning) return;
  const nx = e.clientX - _panStartX;
  const ny = e.clientY - _panStartY;
  _dragDist += Math.abs(nx - state.panX) + Math.abs(ny - state.panY);
  state.panX = nx;
  state.panY = ny;
  applyTransform();
});

window.addEventListener('mouseup', () => {
  if (_isPanning) {
    _isPanning = false;
    els.canvasWrap.classList.remove('panning');
  }
});

on(els.resetView, 'click', resetView);

function zoomToPoint(x, y) {
  const wrap = els.canvasWrap.getBoundingClientRect();
  // Ensure minimum zoom so the point is visible
  if (state.scale < 0.5) state.scale = 0.5;
  state.panX = wrap.width / 2 - x * state.scale;
  state.panY = wrap.height / 2 - y * state.scale;
  applyTransform();
}

function switchToTab(tabId) {
  document.querySelectorAll('.tab-strip .tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector(`.tab[data-tab="${tabId}"]`);
  const panel = $(tabId);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');
  state.activeTab = tabId;
}

function scrollToRowInList(container, id) {
  const row = container.querySelector(`.point-row[data-id="${id}"]`);
  if (row) {
    row.scrollIntoView({ block: 'nearest' });
    row.classList.add('highlighted');
    setTimeout(() => row.classList.remove('highlighted'), 2000);
  }
}

// ----- Room drawing mode -----

on(els.drawRoomBtn, 'click', () => {
  state.drawingRoom = !state.drawingRoom;
  state.roomCorner1 = null;
  els.drawRoomBtn.classList.toggle('active', state.drawingRoom);
  els.drawRoomBtn.textContent = state.drawingRoom ? 'Cancel' : 'Draw Room';
  if (state.drawingRoom) {
    els.canvasStage.classList.add('drawing-room');
    showCanvasHint('Click the first corner of the room, then the opposite corner');
  } else {
    els.canvasStage.classList.remove('drawing-room');
    els.canvasHint.hidden = true;
  }
});

function handleRoomClick(x, y) {
  if (!state.roomCorner1) {
    state.roomCorner1 = { x, y };
    showCanvasHint('Now click the opposite corner');
    return;
  }
  // Got both corners — ask for name
  const c1 = state.roomCorner1;
  state.roomCorner1 = null;
  state.drawingRoom = false;
  els.drawRoomBtn.classList.remove('active');
  els.drawRoomBtn.textContent = 'Draw Room';
  els.canvasStage.classList.remove('drawing-room');
  els.canvasHint.hidden = true;

  // Store corners temporarily and open dialog
  state._pendingRoom = { x1: c1.x, y1: c1.y, x2: x, y2: y };
  els.roomName.value = '';
  els.roomDialog.hidden = false;
  els.roomName.focus();
}

on(els.roomDialogCancel, 'click', () => {
  els.roomDialog.hidden = true;
  state._pendingRoom = null;
});

on(els.roomDialogSave, 'click', saveRoom);
on(els.roomName, 'keydown', e => { if (e.key === 'Enter') saveRoom(); });

async function saveRoom() {
  const name = els.roomName.value.trim();
  if (!name) { els.roomName.focus(); return; }
  const coords = state._pendingRoom;
  if (!coords) return;

  els.roomDialog.hidden = true;
  try {
    const res = await api('/api/rooms', {
      method: 'POST',
      body: JSON.stringify({ name, ...coords }),
    });
    state.rooms.push(res.room);
    renderRooms();
    loadInsights();
    showToast(`Room "${name}" added`);
  } catch (err) {
    showToast(err.message, true);
  }
  state._pendingRoom = null;
}

async function deleteRoom(id) {
  try {
    await api(`/api/rooms/${id}`, { method: 'DELETE' });
    state.rooms = state.rooms.filter(r => r.id !== id);
    renderRooms();
    loadInsights();
  } catch (err) {
    showToast(err.message, true);
  }
}

function renderRooms() {
  // Canvas rectangles
  els.markersLayer.querySelectorAll('.room-rect').forEach(r => r.remove());
  if (state.fpWidth) {
    for (const r of state.rooms) {
      const div = document.createElement('div');
      div.className = 'room-rect';
      div.dataset.id = r.id;
      div.style.left = (r.x1 / state.fpWidth * 100) + '%';
      div.style.top = (r.y1 / state.fpHeight * 100) + '%';
      div.style.width = ((r.x2 - r.x1) / state.fpWidth * 100) + '%';
      div.style.height = ((r.y2 - r.y1) / state.fpHeight * 100) + '%';
      const label = document.createElement('span');
      label.className = 'room-label';
      label.textContent = r.name;
      div.appendChild(label);
      els.markersLayer.appendChild(div);
    }
  }

  // Sidebar list
  const list = els.roomList;
  list.innerHTML = '';
  if (state.rooms.length === 0) {
    list.innerHTML = '<p class="hint empty-hint">Draw room boundaries to get per-room insights.</p>';
    return;
  }
  for (const r of state.rooms) {
    const row = document.createElement('div');
    row.className = 'point-row';

    const dot = document.createElement('div');
    dot.className = 'point-dot';
    dot.style.background = 'var(--accent)';
    dot.style.borderColor = 'var(--accent)';

    const info = document.createElement('div');
    info.className = 'point-info';
    info.innerHTML = `<div><span class="point-rssi">${esc(r.name)}</span></div>`;

    const del = document.createElement('button');
    del.className = 'point-delete';
    del.textContent = '\u00d7';
    del.title = 'Remove room';
    del.addEventListener('click', () => deleteRoom(r.id));

    row.appendChild(dot);
    row.appendChild(info);
    row.appendChild(del);
    row.addEventListener('mouseenter', () => highlightRoom(r.id, true));
    row.addEventListener('mouseleave', () => highlightRoom(r.id, false));
    row.addEventListener('click', e => {
      if (e.target.closest('.point-delete')) return;
      const cx = (r.x1 + r.x2) / 2;
      const cy = (r.y1 + r.y2) / 2;
      zoomToPoint(cx, cy);
    });
    list.appendChild(row);
  }
}

function highlightRoom(id, on) {
  const rect = els.markersLayer.querySelector(`.room-rect[data-id="${id}"]`);
  if (rect) rect.classList.toggle('highlighted', on);
}

async function loadRooms() {
  try {
    const res = await api('/api/rooms');
    state.rooms = res.rooms || [];
  } catch {
    state.rooms = [];
  }
  renderRooms();
}

// ----- Access point placement mode -----

on(els.placeApBtn, 'click', () => {
  state.placingAP = !state.placingAP;
  els.placeApBtn.classList.toggle('active', state.placingAP);
  els.placeApBtn.textContent = state.placingAP ? 'Cancel placement' : 'Place AP';
  if (state.placingAP) {
    els.canvasStage.classList.add('placing-ap');
    showCanvasHint('Click on the floor plan to mark where this access point is located');
  } else {
    els.canvasStage.classList.remove('placing-ap');
    els.canvasHint.hidden = true;
  }
});

function showCanvasHint(text) {
  els.canvasHint.textContent = text;
  els.canvasHint.hidden = false;
  clearTimeout(showCanvasHint._t);
  showCanvasHint._t = setTimeout(() => { els.canvasHint.hidden = true; }, 8000);
}

function openAPDialog(x, y) {
  state.pendingAPCoords = { x, y };
  els.apName.value = '';
  els.apBssid.value = '';
  els.apBssidList.innerHTML = '';
  state.bssids.forEach(b => {
    const opt = document.createElement('option');
    opt.value = b.bssid;
    opt.textContent = `${b.ssid || '?'} \u2013 ${shortBssid(b.bssid)}`;
    els.apBssidList.appendChild(opt);
  });
  els.apDialog.hidden = false;
  els.apName.focus();
}

on(els.apDialogCancel, 'click', () => {
  els.apDialog.hidden = true;
  state.pendingAPCoords = null;
});

on(els.apDialogSave, 'click', saveAP);
on(els.apName, 'keydown', e => { if (e.key === 'Enter') saveAP(); });

async function saveAP() {
  const name = els.apName.value.trim();
  if (!name) { els.apName.focus(); return; }
  const coords = state.pendingAPCoords;
  if (!coords) return;

  els.apDialog.hidden = true;
  try {
    const res = await api('/api/access_points', {
      method: 'POST',
      body: JSON.stringify({ x: coords.x, y: coords.y, name, bssid: els.apBssid.value }),
    });
    state.accessPoints.push(res.access_point);
    renderAccessPoints();
    showToast(`AP "${name}" placed`);
  } catch (err) {
    showToast(err.message, true);
  }
  state.pendingAPCoords = null;
  state.placingAP = false;
  els.placeApBtn.classList.remove('active');
  els.placeApBtn.textContent = 'Place AP';
  els.canvasStage.classList.remove('placing-ap');
  els.canvasHint.hidden = true;
}

async function deleteAP(id) {
  try {
    await api(`/api/access_points/${id}`, { method: 'DELETE' });
    state.accessPoints = state.accessPoints.filter(a => a.id !== id);
    renderAccessPoints();
  } catch (err) {
    showToast(err.message, true);
  }
}

on(els.clearAllAp, 'click', async () => {
  if (state.accessPoints.length === 0) return;
  if (!confirm(`Remove all ${state.accessPoints.length} access points?`)) return;
  try {
    for (const ap of [...state.accessPoints]) {
      await api(`/api/access_points/${ap.id}`, { method: 'DELETE' });
    }
    state.accessPoints = [];
    renderAccessPoints();
  } catch (err) {
    showToast(err.message, true);
  }
});

function renderAccessPoints() {
  els.markersLayer.querySelectorAll('.ap-marker').forEach(m => m.remove());
  if (!state.fpWidth) return;

  for (const ap of state.accessPoints) {
    const m = document.createElement('div');
    m.className = 'ap-marker';
    m.dataset.id = ap.id;
    m.style.left = (ap.x / state.fpWidth * 100) + '%';
    m.style.top = (ap.y / state.fpHeight * 100) + '%';
    m.title = ap.name + (ap.bssid ? ` (${ap.bssid})` : '');
    m.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 4h2v2h-2V6zm3 10H10v-1l1-.5V11h-1v-1h3v4.5l1 .5v1z"/></svg>';
    m.addEventListener('click', e => { e.stopPropagation(); showAPInfo(ap); });
    els.markersLayer.appendChild(m);
  }

  const list = els.apList;
  list.innerHTML = '';
  if (state.accessPoints.length === 0) {
    list.innerHTML = '<p class="hint empty-hint">Mark where your routers are on the floor plan.</p>';
    return;
  }
  for (const ap of state.accessPoints) {
    const row = document.createElement('div');
    row.className = 'point-row';

    const dot = document.createElement('div');
    dot.className = 'point-dot ap-dot';

    const info = document.createElement('div');
    info.className = 'point-info';
    info.innerHTML = `
      <div><span class="point-rssi">${esc(ap.name)}</span></div>
      ${ap.bssid ? `<div class="point-meta">${shortBssid(ap.bssid)}</div>` : ''}
    `;

    const del = document.createElement('button');
    del.className = 'point-delete';
    del.textContent = '\u00d7';
    del.title = 'Remove AP';
    del.addEventListener('click', () => deleteAP(ap.id));

    row.appendChild(dot);
    row.appendChild(info);
    row.appendChild(del);
    row.addEventListener('mouseenter', () => highlightAPMarker(ap.id, true));    row.addEventListener('mouseleave', () => highlightAPMarker(ap.id, false));
    row.addEventListener('click', e => {
      if (e.target.closest('.point-delete')) return;
      zoomToPoint(ap.x, ap.y);
      showAPInfo(ap);
    });
    list.appendChild(row);
  }
}

function highlightAPMarker(id, on) {
  const m = els.markersLayer.querySelector(`.ap-marker[data-id="${id}"]`);
  if (m) m.classList.toggle('highlighted', on);
}

function showAPInfo(ap) {
  const radius = 80;
  const nearby = state.points.filter(p => {
    const dx = p.x - ap.x, dy = p.y - ap.y;
    return Math.sqrt(dx * dx + dy * dy) <= radius;
  });

  const matching = ap.bssid
    ? state.points.filter(p => p.sample.bssid === ap.bssid)
    : [];

  let html = `<strong>${esc(ap.name)}</strong>`;
  if (ap.bssid) {
    html += `<br>BSSID: <span style="color:${bssidColor(ap.bssid)}">${esc(ap.bssid)}</span>`;
    html += `<br>Measurements on this AP: ${matching.length}`;
    if (matching.length > 0) {
      const rssis = matching.map(p => p.sample.rssi);
      const avg = Math.round(rssis.reduce((a, b) => a + b, 0) / rssis.length);
      html += `<br>RSSI: best ${Math.max(...rssis)}, avg ${avg}, worst ${Math.min(...rssis)} dBm`;
    }
  }
  if (nearby.length > 0) {
    html += `<br>Nearby measurements: ${nearby.length}`;
  }

  // BSSID edit field
  const knownBssids = state.bssids.map(b =>
    `<option value="${esc(b.bssid)}">${esc(b.ssid || '?')} \u2013 ${shortBssid(b.bssid)}</option>`
  ).join('');
  html += `<br><input type="text" id="toast-ap-bssid" list="toast-ap-bssid-list" ` +
    `value="${esc(ap.bssid || '')}" placeholder="Set BSSID" ` +
    `style="margin-top:6px;width:100%;background:var(--bg);border:1px solid var(--border-strong);` +
    `border-radius:3px;padding:4px 8px;font-size:11px;color:var(--text);font-family:var(--font-mono);">` +
    `<datalist id="toast-ap-bssid-list">${knownBssids}</datalist>`;

  els.toast.innerHTML = html;
  els.toast.classList.remove('error');
  els.toast.hidden = false;
  clearTimeout(showToast._t);
  // No auto-dismiss — toast has an input field, user controls when to close

  const bssidInput = document.getElementById('toast-ap-bssid');
  if (bssidInput) {
    bssidInput.addEventListener('focus', () => clearTimeout(showToast._t));
    bssidInput.addEventListener('keydown', async e => {
      e.stopPropagation();
      if (e.key === 'Escape') { els.toast.hidden = true; return; }
      if (e.key === 'Enter') {
        const newBssid = bssidInput.value.trim();
        try {
          const res = await api(`/api/access_points/${ap.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ bssid: newBssid }),
          });
          // Update local state
          const local = state.accessPoints.find(a => a.id === ap.id);
          if (local) local.bssid = newBssid;
          renderAccessPoints();
          renderPoints();
          loadInsights();
          els.toast.hidden = true;
          showToast(`BSSID updated for ${ap.name}`);
        } catch (err) {
          showToast(err.message, true);
        }
      }
    });
    bssidInput.addEventListener('click', e => e.stopPropagation());
  }
}

async function loadAccessPoints() {
  const res = await api('/api/access_points');
  state.accessPoints = res.access_points;
  renderAccessPoints();
}

// ----- Click on floor plan = take measurement or place AP -----

on(els.canvasStage, 'click', async e => {
  if (_dragDist > 5) return; // was a pan drag, not a click
  if (state.scanning) return;
  if (e.target.closest('.marker') || e.target.closest('.ap-marker')) return;

  const rect = els.floorplanImg.getBoundingClientRect();
  const scaleX = state.fpWidth / rect.width;
  const scaleY = state.fpHeight / rect.height;
  const x = Math.round((e.clientX - rect.left) * scaleX);
  const y = Math.round((e.clientY - rect.top) * scaleY);

  // Room drawing mode
  if (state.drawingRoom) {
    handleRoomClick(x, y);
    return;
  }

  // AP placement mode
  if (state.placingAP) {
    openAPDialog(x, y);
    return;
  }

  state.scanning = true;
  els.scanOverlay.hidden = false;
  const scanText = els.scanOverlay.querySelector('.scan-text');
  if (scanText) {
    let msg = state.forceRoam ? 'Reconnecting WiFi + ' : '';
    msg += 'Taking 3 samples';
    if (state.speedTest) msg += ' + speed test';
    msg += '\u2026';
    scanText.textContent = msg;
  }
  try {
    const res = await api('/api/measure', {
      method: 'POST',
      body: JSON.stringify({ x, y, force_roam: state.forceRoam, speed_test: state.speedTest }),
    });
    state.points.push(res.point);
    rebuildBssidList();
    renderPoints();
    updateTabLabels();
    if (state.showHeatmap) refreshHeatmap();
    updateStatusText();
    loadInsights();
    // Pulse the new marker
    const newMarker = els.markersLayer.querySelector(`.marker[data-id="${res.point.id}"]`);
    if (newMarker) {
      newMarker.classList.add('pulse');
      setTimeout(() => newMarker.classList.remove('pulse'), 1500);
    }
    playBeep(800, 150);
    let msg = `${res.point.sample.rssi} dBm`;
    if (res.point.download_mbps != null) msg += ` \u00b7 ${res.point.download_mbps} Mbps`;
    if (res.point.bssid_changed) msg += ' (BSSID roamed)';
    showToast(msg);
  } catch (err) {
    playBeep(300, 300);
    showToast(err.message, true);
  } finally {
    state.scanning = false;
    els.scanOverlay.hidden = true;
  }
});

// ----- Points loading & rendering -----

async function loadPoints() {
  const res = await api('/api/points');
  state.points = res.points;
  rebuildBssidList();
  renderPoints();
  updateStatusText();
  updateTabLabels();
}

function rebuildBssidList() {
  const seen = new Map();
  for (const p of state.points) {
    const b = p.sample.bssid;
    if (!b) continue;
    if (!seen.has(b)) seen.set(b, { bssid: b, ssid: p.sample.ssid, count: 0 });
    seen.get(b).count++;
  }
  state.bssids = [...seen.values()].sort((a, b) => b.count - a.count);

  const sel = els.bssidFilter;
  const prev = state.selectedBssid;
  sel.innerHTML = '<option value="">All access points combined</option>';
  state.bssids.forEach(b => {
    const apName = apNameForBssid(b.bssid);
    const apPrefix = apName ? `${apName} \u2013 ` : '';
    const label = `${apPrefix}${b.ssid || '?'} \u00b7 ${shortBssid(b.bssid)} (${b.count})`;
    const opt = document.createElement('option');
    opt.value = b.bssid;
    opt.textContent = label;
    opt.style.color = bssidColor(b.bssid);
    sel.appendChild(opt);
  });
  if (prev && state.bssids.find(b => b.bssid === prev)) {
    sel.value = prev;
  } else {
    state.selectedBssid = '';
    sel.value = '';
  }
}

function shortBssid(b) {
  if (!b) return '?';
  const parts = b.split(':');
  return parts.slice(-3).join(':');
}

function bssidMatchesAp(bssid, apBssid) {
  if (!bssid || !apBssid) return false;
  // Normalize: lowercase, strip dashes
  const a = bssid.toLowerCase().replace(/-/g, ':');
  const b = apBssid.toLowerCase().replace(/-/g, ':');
  if (a === b) return true;
  // Fuzzy: match if first 14 chars match (same device, different radio)
  // e.g. 5c:62:8b:2a:21:7a vs 5c:62:8b:2a:21:78
  return a.length >= 14 && b.length >= 14 && a.slice(0, 14) === b.slice(0, 14);
}

function apNameForBssid(bssid) {
  if (!bssid) return null;
  const ap = state.accessPoints.find(a => bssidMatchesAp(bssid, a.bssid));
  return ap ? ap.name : null;
}

function renderPoints() {
  // Clear only measurement markers (preserve AP markers)
  els.markersLayer.querySelectorAll('.marker').forEach(m => m.remove());
  if (!state.fpWidth) return;

  const filtered = state.selectedBssid
    ? state.points.filter(p => p.sample.bssid === state.selectedBssid)
    : state.points;

  for (const p of filtered) {
    const m = document.createElement('div');
    m.className = 'marker';
    m.dataset.id = p.id;
    m.style.left = (p.x / state.fpWidth * 100) + '%';
    m.style.top = (p.y / state.fpHeight * 100) + '%';
    m.style.color = p.color;
    m.style.borderColor = bssidColor(p.sample.bssid);
    if (p.bssid_changed) m.classList.add('bssid-changed');
    m.title = formatPointTooltip(p);
    m.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
      'stroke="currentColor" stroke-width="2.5" stroke-linecap="round">' +
      '<path d="M5 12.5a10 10 0 0 1 14 0"/>' +
      '<path d="M8.5 15a5.5 5.5 0 0 1 7 0"/>' +
      '<circle cx="12" cy="18" r="1.5" fill="currentColor" stroke="none"/>' +
      '</svg>';
    m.addEventListener('click', e => {
      e.stopPropagation();
      showPointInfo(p);
      switchToTab('tab-points');
      scrollToRowInList(els.pointsList, p.id);
    });
    els.markersLayer.appendChild(m);
  }

  // -- Side list --
  const list = els.pointsList;
  list.innerHTML = '';
  if (state.points.length === 0) {
    list.innerHTML = '<p class="hint empty-hint">Click anywhere on the floor plan to take a measurement.</p>';
    return;
  }

  let sorted = [...state.points].sort((a, b) => b.timestamp - a.timestamp);
  const searchQ = (pointSearchInput ? pointSearchInput.value : '').toLowerCase();
  if (searchQ) {
    sorted = sorted.filter(p =>
      (p.sample.bssid || '').toLowerCase().includes(searchQ) ||
      (p.sample.ssid || '').toLowerCase().includes(searchQ) ||
      (p.note || '').toLowerCase().includes(searchQ) ||
      String(p.sample.rssi).includes(searchQ)
    );
  }
  for (const p of sorted) {
    const row = document.createElement('div');
    row.className = 'point-row';
    row.dataset.id = p.id;

    const dot = document.createElement('div');
    dot.className = 'point-dot';
    dot.style.background = p.color;
    dot.style.boxShadow = `0 0 0 2px ${bssidColor(p.sample.bssid)}`;

    const info = document.createElement('div');
    info.className = 'point-info';
    const roamBadge = p.bssid_changed
      ? ' <span class="roam-badge" title="BSSID changed during measurement">R</span>'
      : '';
    const apName = apNameForBssid(p.sample.bssid);
    const apTag = apName
      ? `<span style="color:var(--accent)">${esc(apName)}</span> \u00b7 `
      : '';
    const speedTag = p.download_mbps != null
      ? ` \u00b7 ${p.download_mbps} Mbps`
      : '';
    const noteTag = p.note
      ? `<div class="point-meta" style="color:var(--text-dim);font-style:italic;">${esc(p.note)}</div>`
      : '';
    info.innerHTML = `
      <div><span class="point-rssi">${p.sample.rssi} dBm${speedTag}</span>${roamBadge}</div>
      <div class="point-meta" title="${esc(p.sample.ssid || '')} ${esc(p.sample.bssid || '')}">
        <span style="color:${bssidColor(p.sample.bssid)}">\u25cf</span>
        ${apTag}${esc(p.sample.ssid || '?')} \u00b7 ${shortBssid(p.sample.bssid)}
      </div>
      ${noteTag}
    `;

    const del = document.createElement('button');
    del.className = 'point-delete';
    del.textContent = '\u00d7';
    del.title = 'Remove point';
    del.addEventListener('click', () => deletePoint(p.id));

    row.appendChild(dot);
    row.appendChild(info);
    row.appendChild(del);

    row.addEventListener('mouseenter', () => highlightMarker(p.id, true));
    row.addEventListener('mouseleave', () => highlightMarker(p.id, false));
    row.addEventListener('click', e => {
      if (e.target.closest('.point-delete')) return; // don't zoom when clicking delete
      zoomToPoint(p.x, p.y);
      highlightMarker(p.id, true);
      showPointInfo(p);
      setTimeout(() => highlightMarker(p.id, false), 2000);
    });

    list.appendChild(row);
  }
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[c]);
}

function formatPointTooltip(p) {
  const s = p.sample;
  const apName = apNameForBssid(s.bssid);
  const lines = [
    `${s.ssid} (${s.bssid})`,
    `RSSI: ${s.rssi} dBm  Noise: ${s.noise} dBm`,
    `${s.phy_mode} \u00b7 ${s.channel} \u00b7 ${s.tx_rate ?? '?'} Mbps`,
  ];
  if (apName) lines.unshift(`AP: ${apName}`);
  if (p.bssid_changed && p.all_samples && p.all_samples.length > 0) {
    lines.push('', 'Multi-scan readings:');
    p.all_samples.forEach((r, i) => {
      lines.push(`  #${i + 1}: ${r.rssi} dBm @ ${r.bssid || '?'}`);
    });
  }
  return lines.join('\n');
}

function showPointInfo(p) {
  const s = p.sample;
  const apName = apNameForBssid(s.bssid);

  let html = `<strong>${s.rssi} dBm</strong> <span style="color:var(--text-faint)">(${rssiLabel(s.rssi)})</span>`;
  if (apName) {
    html += `<br>AP: <span style="color:var(--accent)">${esc(apName)}</span>`;
  }
  html += `<br>SSID: ${esc(s.ssid || '?')}`;
  html += `<br>BSSID: <span style="color:${bssidColor(s.bssid)}">${esc(s.bssid || '?')}</span>`;
  if (s.noise != null) html += `<br>Noise: ${s.noise} dBm \u00b7 SNR: ${s.rssi - s.noise} dB`;
  if (s.channel) html += `<br>Channel: ${esc(s.channel)}`;
  if (s.phy_mode) html += `<br>PHY: ${esc(s.phy_mode)}`;
  if (s.tx_rate != null) html += ` \u00b7 Tx: ${s.tx_rate} Mbps`;
  if (p.download_mbps != null) {
    html += `<br>Download: <strong>${p.download_mbps} Mbps</strong>`;
  }
  if (p.bssid_changed) {
    html += `<br><span style="color:var(--accent)">BSSID changed during scan</span>`;
  }
  if (p.all_samples && p.all_samples.length > 1) {
    html += `<br><span style="color:var(--text-faint)">Samples: `;
    html += p.all_samples.map(r => `${r.rssi}`).join(', ');
    html += ` dBm</span>`;
  }

  // Note editor
  html += `<br><input type="text" id="toast-note-input" value="${esc(p.note || '')}" ` +
    `placeholder="Add a note (e.g. kitchen)" ` +
    `style="margin-top:6px;width:100%;background:var(--bg);border:1px solid var(--border-strong);` +
    `border-radius:3px;padding:4px 8px;font-size:11px;color:var(--text);font-family:var(--font-mono);">`;

  els.toast.innerHTML = html;
  els.toast.classList.remove('error');
  els.toast.hidden = false;
  clearTimeout(showToast._t);
  // No auto-dismiss — toast has an input field

  // Wire up note input
  const noteInput = document.getElementById('toast-note-input');
  if (noteInput) {
    noteInput.addEventListener('focus', () => clearTimeout(showToast._t));
    noteInput.addEventListener('keydown', e => {
      e.stopPropagation();
      if (e.key === 'Escape') { els.toast.hidden = true; return; }
      if (e.key === 'Enter') {
        saveNote(p.id, noteInput.value.trim());
        els.toast.hidden = true;
      }
    });
    noteInput.addEventListener('click', e => e.stopPropagation()); // prevent toast dismiss
  }
}

function rssiLabel(rssi) {
  if (rssi >= -50) return 'excellent';
  if (rssi >= -60) return 'good';
  if (rssi >= -70) return 'usable';
  if (rssi >= -75) return 'poor';
  return 'dead';
}

function highlightMarker(id, on) {
  const m = els.markersLayer.querySelector(`.marker[data-id="${id}"]`);
  if (m) m.classList.toggle('highlighted', on);
}

async function deletePoint(id) {
  try {
    await api(`/api/points/${id}`, { method: 'DELETE' });
    state.points = state.points.filter(p => p.id !== id);
    rebuildBssidList();
    renderPoints();
    if (state.showHeatmap) refreshHeatmap();
    updateStatusText();
    loadInsights();
  } catch (err) {
    showToast(err.message, true);
  }
}

on(els.clearAll, 'click', async () => {
  if (state.points.length === 0) return;
  if (!confirm(`Remove all ${state.points.length} measurements?`)) return;
  try {
    await api('/api/clear', { method: 'POST' });
    state.points = [];
    rebuildBssidList();
    renderPoints();
    if (state.showHeatmap) {
      els.heatmapOverlay.hidden = true;
      els.heatmapToggle.checked = false;
      state.showHeatmap = false;
    }
    updateStatusText();
    loadInsights();
  } catch (err) {
    showToast(err.message, true);
  }
});

// ----- Point search -----

const pointSearchInput = $('point-search');
if (pointSearchInput) {
  pointSearchInput.addEventListener('input', renderPoints);
}

// ----- Heatmap controls -----

// Toggle switches (button-based)
function setupToggle(el, stateKey, onChange) {
  if (!el) return;
  el.addEventListener('click', () => {
    state[stateKey] = !state[stateKey];
    el.classList.toggle('on', state[stateKey]);
    el.setAttribute('aria-pressed', state[stateKey]);
    if (onChange) onChange(state[stateKey]);
    saveSettings();
  });
}

setupToggle(els.floorplanToggle, 'showFloorplan', on => {
  if (els.floorplanImg) els.floorplanImg.style.opacity = on ? '1' : '0';
});

setupToggle(els.heatmapToggle, 'showHeatmap', on => {
  if (on) refreshHeatmap();
  else els.heatmapOverlay.hidden = true;
});
setupToggle(els.forceRoamToggle, 'forceRoam');
setupToggle(els.speedTestToggle, 'speedTest');

// Metric segmented control
document.querySelectorAll('#metric-seg button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#metric-seg button').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
    state.heatmapMode = btn.dataset.metric;
    updateLegend();
    if (state.showHeatmap) refreshHeatmap();
    saveSettings();
  });
});

on(els.bssidFilter, 'change', e => {
  state.selectedBssid = e.target.value;
  renderPoints();
  if (state.showHeatmap) refreshHeatmap();
  loadInsights();
  saveSettings();
});

on(els.alphaSlider, 'input', e => {
  state.alpha = e.target.value / 100;
  els.alphaValue.textContent = e.target.value + '%';
  if (state.showHeatmap) {
    clearTimeout(els.alphaSlider._t);
    els.alphaSlider._t = setTimeout(refreshHeatmap, 200);
  }
  saveSettings();
});

function refreshHeatmap() {
  const params = new URLSearchParams();
  if (state.selectedBssid) params.set('bssid', state.selectedBssid);
  params.set('alpha', state.alpha);
  params.set('mode', state.heatmapMode);
  params.set('t', Date.now());

  const url = '/api/heatmap?' + params.toString();
  const img = new Image();
  img.onload = () => {
    els.heatmapOverlay.src = url;
    els.heatmapOverlay.hidden = false;
  };
  img.onerror = async () => {
    try {
      const r = await fetch(url);
      const j = await r.json().catch(() => ({}));
      showToast(j.error || 'Heatmap unavailable', true);
    } catch {
      showToast('Heatmap unavailable', true);
    }
    els.heatmapOverlay.hidden = true;
    els.heatmapToggle.checked = false;
    state.showHeatmap = false;
  };
  img.src = url;
}

// ----- Live signal monitor -----

let _monitorInterval = null;
let _monitorTimeInterval = null;
let _lastScanTime = null;

on(els.monitorBtn, 'click', () => {
  if (state.monitoring) stopMonitor();
  else startMonitor();
});

on(els.monitorClose, 'click', stopMonitor);

function startMonitor() {
  state.monitoring = true;
  els.monitorBtn.textContent = 'Stop Monitor';
  els.monitorBtn.classList.add('active');
  els.liveMonitor.hidden = false;
  doMonitorScan();
  _monitorInterval = setInterval(doMonitorScan, 2000);
  _monitorTimeInterval = setInterval(updateMonitorTime, 1000);
}

function stopMonitor() {
  state.monitoring = false;
  els.monitorBtn.textContent = 'Monitor';
  els.monitorBtn.classList.remove('active');
  els.liveMonitor.hidden = true;
  clearInterval(_monitorInterval);
  clearInterval(_monitorTimeInterval);
  _monitorInterval = null;
  _monitorTimeInterval = null;
}

async function doMonitorScan() {
  try {
    const data = await api('/api/scan');
    _lastScanTime = Date.now();
    els.monitorSsid.textContent = data.ssid || '\u2014';
    els.monitorBssid.textContent = data.bssid ? shortBssid(data.bssid) : '\u2014';
    els.monitorBssid.style.color = bssidColor(data.bssid);

    // AP name
    const apName = apNameForBssid(data.bssid);
    if (apName) {
      els.monitorApName.textContent = apName;
      els.monitorApRow.hidden = false;
    } else {
      els.monitorApRow.hidden = true;
    }

    // RSSI + bar
    els.monitorRssiValue.textContent = data.rssi != null ? `${data.rssi} dBm` : '\u2014';
    els.monitorRssiValue.style.color = data.color || 'inherit';
    if (data.rssi != null) {
      const pct = Math.max(0, Math.min(100,
        (data.rssi - state.rssiMin) / (state.rssiMax - state.rssiMin) * 100));
      els.monitorBar.style.width = pct + '%';
      els.monitorBar.style.background = data.color;
    } else {
      els.monitorBar.style.width = '0%';
    }

    // SNR
    if (data.rssi != null && data.noise != null) {
      const snr = data.rssi - data.noise;
      els.monitorSnr.textContent = `${snr} dB`;
    } else {
      els.monitorSnr.textContent = '\u2014';
    }

    // Channel + band
    if (data.channel) {
      const ch = data.channel;
      // Channel numbers: 1-14 = 2.4 GHz, 36+ = 5 GHz, 1-233 for 6 GHz
      const chNum = parseInt(ch);
      const band = ch.includes('5g') ? '5 GHz'
        : ch.includes('2g') || ch.includes('2.4') ? '2.4 GHz'
        : (chNum >= 36 && chNum <= 177) ? '5 GHz'
        : (chNum >= 1 && chNum <= 14) ? '2.4 GHz'
        : '';
      els.monitorChannel.textContent = band ? `${ch} (${band})` : ch;
    } else {
      els.monitorChannel.textContent = '\u2014';
    }

    // Tx rate
    els.monitorTxRate.textContent = data.tx_rate != null ? `${data.tx_rate} Mbps` : '\u2014';

    updateMonitorTime();
  } catch {
    els.monitorRssiValue.textContent = 'error';
  }
}

function updateMonitorTime() {
  if (!_lastScanTime) { els.monitorTime.textContent = '\u2014'; return; }
  const ago = Math.round((Date.now() - _lastScanTime) / 1000);
  els.monitorTime.textContent = ago === 0 ? 'just now' : `${ago}s ago`;
}

// ----- New Survey -----

on(els.newSurveyBtn, 'click', async () => {
  if (!confirm('Start a new survey? This clears all points, rooms, and access points. The floor plan is kept.')) return;
  try {
    await api('/api/new-survey', { method: 'POST' });
    state.points = [];
    state.accessPoints = [];
    state.rooms = [];
    rebuildBssidList();
    renderPoints();
    renderAccessPoints();
    renderRooms();
    if (state.showHeatmap) {
      els.heatmapOverlay.hidden = true;
      els.heatmapToggle.checked = false;
      state.showHeatmap = false;
    }
    updateStatusText();
    updateTabLabels();
    loadInsights();
    showToast('New survey started');
  } catch (err) {
    showToast(err.message, true);
  }
});

// ----- Export PNG -----

on(els.exportBtn, 'click', async () => {
  const params = new URLSearchParams();
  if (state.selectedBssid) params.set('bssid', state.selectedBssid);
  params.set('alpha', state.alpha);
  params.set('heatmap', state.showHeatmap);
  showToast('Exporting\u2026');
  try {
    const res = await fetch('/api/export?' + params.toString());
    if (!res.ok) {
      const j = await res.json().catch(() => ({}));
      showToast(j.error || 'Export failed', true);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const cd = res.headers.get('content-disposition') || '';
    const match = cd.match(/filename="?(.+?)"?$/);
    a.download = match ? match[1] : 'wifi-survey.png';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    showToast('PNG exported');
  } catch (err) {
    showToast(err.message, true);
  }
});

// ----- Legend + audio helpers -----

function updateLegend() {
  const labels = document.querySelectorAll('.legend-labels span');
  if (labels.length >= 2) {
    if (state.heatmapMode === 'snr') {
      labels[0].textContent = state.snrMin + ' dB';
      labels[1].textContent = state.snrMax + ' dB';
    } else if (state.heatmapMode === 'txrate') {
      labels[0].textContent = '0 Mbps';
      labels[1].textContent = '866 Mbps';
    } else if (state.heatmapMode === 'speed') {
      labels[0].textContent = '0 Mbps';
      labels[1].textContent = '200 Mbps';
    } else {
      labels[0].textContent = state.rssiMin + ' dBm';
      labels[1].textContent = state.rssiMax + ' dBm';
    }
  }
}

function playBeep(freq, duration) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = freq || 800;
    gain.gain.value = 0.08;
    osc.start();
    osc.stop(ctx.currentTime + (duration || 150) / 1000);
  } catch {}
}

async function saveNote(pointId, note) {
  try {
    await api(`/api/points/${pointId}/note`, {
      method: 'POST',
      body: JSON.stringify({ note }),
    });
    // Update local state
    const pt = state.points.find(p => p.id === pointId);
    if (pt) pt.note = note;
    renderPoints();
  } catch (err) {
    showToast(err.message, true);
  }
}

// ----- Sidebar tabs -----

document.querySelectorAll('.tab-strip .tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab-strip .tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    const panel = $(tab.dataset.tab);
    if (panel) panel.classList.add('active');
    state.activeTab = tab.dataset.tab;
    saveSettings();
  });
});

// ----- Insights -----

async function loadInsights() {
  try {
    const params = new URLSearchParams();
    if (state.selectedBssid) params.set('bssid', state.selectedBssid);
    const res = await api('/api/insights?' + params.toString());
    state.findings = res.findings || [];
  } catch {
    state.findings = [];
  }
  renderInsights();
  updateTabLabels();
}

function renderInsights() {
  const list = els.insightsList;
  list.innerHTML = '';

  if (state.points.length < 3) {
    list.innerHTML = '<p class="hint empty-hint">Take more measurements to see insights.</p>';
    return;
  }

  if (state.findings.length === 0) {
    list.innerHTML = '<p class="hint empty-hint">No findings yet.</p>';
    return;
  }

  for (const f of state.findings) {
    const card = document.createElement('div');
    card.className = 'insight-card' + (f.locked ? ' locked' : '');

    const bar = document.createElement('div');
    bar.className = `insight-bar sev-${f.severity}`;

    const content = document.createElement('div');
    content.className = 'insight-content';
    content.innerHTML = `<div class="insight-title">${esc(f.title)}</div>` +
      `<div class="insight-body">${esc(f.body)}</div>`;

    card.appendChild(bar);
    card.appendChild(content);

    if (f.metric != null) {
      const metric = document.createElement('div');
      metric.className = 'insight-metric';
      metric.textContent = typeof f.metric === 'number'
        ? (Number.isInteger(f.metric) ? f.metric : f.metric.toFixed(1))
        : f.metric;
      card.appendChild(metric);
    }

    list.appendChild(card);
  }
}

// ----- Snapshots -----

on(els.snapshotBtn, 'click', () => {
  const now = new Date();
  els.snapshotName.value = `Snapshot ${now.toISOString().slice(0, 16).replace('T', ' ')}`;
  els.snapshotDialog.hidden = false;
  els.snapshotName.focus();
  els.snapshotName.select();
});

on(els.snapshotCancel, 'click', () => {
  els.snapshotDialog.hidden = true;
});

on(els.snapshotSave, 'click', doSaveSnapshot);
on(els.snapshotName, 'keydown', e => { if (e.key === 'Enter') doSaveSnapshot(); });

async function doSaveSnapshot() {
  const name = els.snapshotName.value.trim();
  if (!name) return;
  els.snapshotDialog.hidden = true;
  try {
    await api('/api/snapshots', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    showToast(`Snapshot "${name}" saved`);
    await loadSnapshots();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadSnapshots() {
  try {
    const res = await api('/api/snapshots');
    state.snapshots = res.snapshots || [];
  } catch {
    state.snapshots = [];
  }
  renderSnapshotDropdown();
}

function renderSnapshotDropdown() {
  const sel = els.compareSelect;
  sel.innerHTML = '<option value="">Compare\u2026</option>';
  for (const s of state.snapshots) {
    const opt = document.createElement('option');
    opt.value = s.slug;
    opt.textContent = `${s.name} (${s.point_count} pts)`;
    sel.appendChild(opt);
  }
  // Add delete options
  if (state.snapshots.length > 0) {
    const sep = document.createElement('option');
    sep.disabled = true;
    sep.textContent = '\u2500\u2500 delete \u2500\u2500';
    sel.appendChild(sep);
    for (const s of state.snapshots) {
      const opt = document.createElement('option');
      opt.value = 'delete:' + s.slug;
      opt.textContent = `\u00d7 Delete "${s.name}"`;
      sel.appendChild(opt);
    }
  }
}

on(els.compareSelect, 'change', async e => {
  const val = e.target.value;
  els.compareSelect.value = '';  // reset dropdown
  if (!val) return;

  if (val.startsWith('delete:')) {
    const slug = val.slice(7);
    if (!confirm('Delete this snapshot?')) return;
    try {
      await api(`/api/snapshots/${slug}`, { method: 'DELETE' });
      showToast('Snapshot deleted');
      await loadSnapshots();
    } catch (err) {
      showToast(err.message, true);
    }
    return;
  }

  // Load snapshot and show comparison
  try {
    const data = await api(`/api/snapshots/${val}`);
    showComparison(data);
  } catch (err) {
    showToast(err.message, true);
  }
});

function showComparison(snapData) {
  els.compareSnapshotLabel.textContent = snapData.name || 'Snapshot';

  // Current: use export endpoint (floor plan + heatmap + markers composited)
  const params = new URLSearchParams();
  params.set('heatmap', state.showHeatmap);
  params.set('alpha', state.alpha);
  params.set('t', Date.now());
  els.compareCurrentImg.src = '/api/export?' + params.toString();
  els.compareCurrentImg.onerror = () => {
    els.compareCurrentImg.src = '/api/floorplan';
  };

  // Snapshot: use snapshot export endpoint (floor plan + heatmap + markers)
  els.compareSnapshotImg.src = `/api/snapshots/${snapData.slug}/export?t=${Date.now()}`;
  els.compareSnapshotImg.onerror = () => {
    els.compareSnapshotImg.src = '/api/floorplan';
  };

  // Render diff findings
  els.compareDiff.innerHTML = '';
  const diffs = snapData.diff || [];
  if (diffs.length === 0) {
    els.compareDiff.innerHTML = '<p class="hint">No significant differences found.</p>';
  } else {
    for (const f of diffs) {
      const card = document.createElement('div');
      card.className = 'insight-card';

      const bar = document.createElement('div');
      bar.className = `insight-bar sev-${f.severity}`;

      const content = document.createElement('div');
      content.className = 'insight-content';
      content.innerHTML = `<div class="insight-title">${esc(f.title)}</div>` +
        `<div class="insight-body">${esc(f.body)}</div>`;

      card.appendChild(bar);
      card.appendChild(content);

      if (f.metric != null) {
        const metric = document.createElement('div');
        metric.className = 'insight-metric';
        metric.textContent = typeof f.metric === 'number'
          ? (Number.isInteger(f.metric) ? String(f.metric) : f.metric.toFixed(1))
          : f.metric;
        card.appendChild(metric);
      }

      els.compareDiff.appendChild(card);
    }
  }

  els.compareModal.hidden = false;
}

on(els.compareClose, 'click', () => {
  els.compareModal.hidden = true;
});

// ----- Global keyboard + click handlers -----

window.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (!els.compareModal.hidden) { els.compareModal.hidden = true; return; }
    if (!els.snapshotDialog.hidden) { els.snapshotDialog.hidden = true; return; }
    if (!els.roomDialog.hidden) { els.roomDialog.hidden = true; state._pendingRoom = null; return; }
    if (!els.apDialog.hidden) { els.apDialog.hidden = true; state.pendingAPCoords = null; return; }
    if (state.drawingRoom) {
      state.drawingRoom = false; state.roomCorner1 = null;
      els.drawRoomBtn.classList.remove('active');
      els.drawRoomBtn.textContent = 'Draw Room';
      els.canvasStage.classList.remove('drawing-room');
      els.canvasHint.hidden = true;
      return;
    }
    if (state.placingAP) {
      state.placingAP = false;
      els.placeApBtn.classList.remove('active');
      els.placeApBtn.textContent = 'Place AP';
      els.canvasStage.classList.remove('placing-ap');
      return;
    }
    if (!els.toast.hidden) { els.toast.hidden = true; return; }
  }
});

// Click outside modal/dialog → close
[els.compareModal, els.snapshotDialog, els.apDialog, els.roomDialog].forEach(overlay => {
  on(overlay, 'click', e => {
    if (e.target === overlay) overlay.hidden = true;
  });
});

// Click toast → dismiss (but not if clicking an input inside it)
on(els.toast, 'click', e => {
  if (e.target.tagName === 'INPUT') return;
  els.toast.hidden = true;
});

// ----- Initial state check -----

(async () => {
  try {
    const s = await api('/api/status');
    if (s.authenticated) {
      state.authenticated = true;
      els.authOverlay.hidden = true;
      els.app.hidden = false;
      await loadStatus();
      await loadPoints();
      await loadAccessPoints();
      await loadRooms();
      loadSettings();
      restoreBssidFilter();
      if (state.showHeatmap) refreshHeatmap();
      loadInsights();
      loadSnapshots();
    }
  } catch {}
})();
