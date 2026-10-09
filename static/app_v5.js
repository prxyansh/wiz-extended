/**
 * WiZ Controller — Frontend v2.1
 * Includes performance optimizations, layout thrashing fixes, modal UX improvements.
 */
'use strict';

// ── Global error trap ────────────────────────────────────────────────────────
window.addEventListener('error', e => {
  console.error('CRASH:', e.message, 'at', e.filename, e.lineno);
});

// ── State ────────────────────────────────────────────────────────────────────
const S = {
  lights:      {},
  presets:     [],
  selectedIp:  null,
  selectedMac: null,
  hue:         15,
  sat:         1.0,
  val:         1.0,
  r:           255,
  g:           80,
  b:           0,
  brightness:  75,
  temp:        3000,
  mode:        'color',
  sceneId:     1,
  sceneSpeed:  100,
  isOn:        true,
  syncRunning:  false,
  pomodoroTimer: null,
  syncDevIdx:   null,
  syncMode:    'spectral_prism',
  sensitivity:  1.0,
  syncIps:      [],
  audioDevices: [],
  ws:           null,
  draggingSV:   false,
  draggingHue:  false,
  _pingTimer:   null,
};

// ── DOM helpers ──────────────────────────────────────────────────────────────
const $   = id  => document.getElementById(id);
const $$  = sel => document.querySelectorAll(sel);

function setText(id, val) { const e = $(id); if (e) e.textContent = String(val); }
function setVal(id, val)  { const e = $(id); if (e) e.value = val; }
function show(id)         { const e = $(id); if (e) e.classList.remove('hidden'); }
function hide(id)         { const e = $(id); if (e) e.classList.add('hidden'); }
function on(id, evt, fn)  { const e = $(id); if (e) e.addEventListener(evt, fn); }

// ── API ──────────────────────────────────────────────────────────────────────
async function api(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status} ${await res.text().catch(() => '')}`);
  return res.json();
}

// ── Toast ────────────────────────────────────────────────────────────────────
let _tt;
function toast(msg, ms = 2800) {
  const el = $('toast');
  if (!el) return;
  el.textContent = msg;
  show('toast');
  clearTimeout(_tt);
  _tt = setTimeout(() => hide('toast'), ms);
}

// ── Debounce ─────────────────────────────────────────────────────────────────
function debounce(fn, ms = 80) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ── Color math ───────────────────────────────────────────────────────────────
function hsvToRgb(h, s, v) {
  h = ((h % 360) + 360) % 360;
  const c = v * s, x = c * (1 - Math.abs((h / 60) % 2 - 1)), m = v - c;
  let r, g, b;
  if      (h < 60)  { r = c; g = x; b = 0; }
  else if (h < 120) { r = x; g = c; b = 0; }
  else if (h < 180) { r = 0; g = c; b = x; }
  else if (h < 240) { r = 0; g = x; b = c; }
  else if (h < 300) { r = x; g = 0; b = c; }
  else              { r = c; g = 0; b = x; }
  return [Math.round((r+m)*255), Math.round((g+m)*255), Math.round((b+m)*255)];
}

function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r,g,b), min = Math.min(r,g,b), d = max - min;
  let h = 0;
  const s = max === 0 ? 0 : d / max, v = max;
  if (d) {
    if      (max === r) h = ((g-b)/d) % 6;
    else if (max === g) h = (b-r)/d + 2;
    else                h = (r-g)/d + 4;
    h = ((h*60)+360) % 360;
  }
  return [h, s, v];
}

function rgbToHex(r, g, b) {
  return '#' + [r,g,b].map(v => Math.round(v).toString(16).padStart(2,'0')).join('');
}
function hexToRgb(hex) {
  const m = hex.replace('#','').match(/^([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  return m ? [parseInt(m[1],16), parseInt(m[2],16), parseInt(m[3],16)] : null;
}

function applyHsvToRgb() {
  [S.r, S.g, S.b] = hsvToRgb(S.hue, S.sat, S.val);
}

// ── Canvas helpers ───────────────────────────────────────────────────────────
function fillRoundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w/2, h/2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arcTo(x + w, y, x + w, y + r, r);
  ctx.lineTo(x + w, y + h - r);
  ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
  ctx.lineTo(x + r, y + h);
  ctx.arcTo(x, y + h, x, y + h - r, r);
  ctx.lineTo(x, y + r);
  ctx.arcTo(x, y, x + r, y, r);
  ctx.closePath();
  ctx.fill();
}

function sizeCanvas(canvas) {
  if (!canvas) return false;
  // Use clientWidth to avoid heavy getBoundingClientRect forced layout if possible
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cw * dpr);
  const h = Math.round(ch * dpr);
  if (w < 1 || h < 1) return false;
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width  = w;
    canvas.height = h;
    return true;
  }
  return false;
}

function sizeAllCanvases() {
  sizeCanvas($('sv-canvas'));
  sizeCanvas($('hue-canvas'));
  // temp canvas has custom size logic, handle separately
}

// ── SV picker ────────────────────────────────────────────────────────────────
function drawSVCanvas() {
  const canvas = $('sv-canvas');
  if (!canvas || canvas.width === 0) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;

  // Horizontal: white → pure hue
  const gH = ctx.createLinearGradient(0, 0, W, 0);
  gH.addColorStop(0, '#fff');
  gH.addColorStop(1, `hsl(${S.hue},100%,50%)`);
  ctx.fillStyle = gH;
  ctx.fillRect(0, 0, W, H);

  // Vertical: transparent → black (Value axis)
  const gV = ctx.createLinearGradient(0, 0, 0, H);
  gV.addColorStop(0, 'rgba(0,0,0,0)');
  gV.addColorStop(1, 'rgba(0,0,0,1)');
  ctx.fillStyle = gV;
  ctx.fillRect(0, 0, W, H);
}

function positionSVCursor() {
  const canvas = $('sv-canvas');
  const cursor = $('sv-cursor');
  if (!canvas || !cursor) return;
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  if (!cw || !ch) return;
  cursor.style.left = (S.sat * cw) + 'px';
  cursor.style.top  = ((1 - S.val) * ch) + 'px';
}

function svFromEvent(e, canvas) {
  const rect = canvas.getBoundingClientRect(); // This read is fast if we do it before writing layout
  const cx = e.touches ? e.touches[0].clientX : e.clientX;
  const cy = e.touches ? e.touches[0].clientY : e.clientY;
  S.sat = Math.max(0, Math.min(1, (cx - rect.left) / rect.width));
  S.val = Math.max(0, Math.min(1, 1 - (cy - rect.top) / rect.height));
  applyHsvToRgb();
}

// ── Hue bar ──────────────────────────────────────────────────────────────────
function drawHueCanvas() {
  const canvas = $('hue-canvas');
  if (!canvas || canvas.width === 0) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const grad = ctx.createLinearGradient(0, 0, W, 0);
  for (let i = 0; i <= 360; i += 30) {
    grad.addColorStop(i / 360, `hsl(${i},100%,50%)`);
  }
  ctx.fillStyle = grad;
  fillRoundRect(ctx, 0, 0, W, H, H / 2);
}

function positionHueCursor() {
  const canvas = $('hue-canvas');
  const cursor = $('hue-cursor');
  if (!canvas || !cursor) return;
  const cw = canvas.clientWidth;
  if (!cw) return;
  cursor.style.left = ((S.hue / 360) * cw) + 'px';
}

function hueFromEvent(e, canvas) {
  const rect = canvas.getBoundingClientRect();
  const cx = e.touches ? e.touches[0].clientX : e.clientX;
  S.hue = Math.max(0, Math.min(360, ((cx - rect.left) / rect.width) * 360));
  applyHsvToRgb();
}

// ── Gradient sliders ─────────────────────────────────────────────────────────
function updateSliderGradients() {
  const { r, g, b } = S;
  const slR  = $('sl-r'),  slG  = $('sl-g'),  slB = $('sl-b');
  const slBr = $('sl-bright'), hp = $('hex-preview');
  if (slR)  slR.style.background  = `linear-gradient(to right,rgb(0,${g},${b}),rgb(255,${g},${b}))`;
  if (slG)  slG.style.background  = `linear-gradient(to right,rgb(${r},0,${b}),rgb(${r},255,${b}))`;
  if (slB)  slB.style.background  = `linear-gradient(to right,rgb(${r},${g},0),rgb(${r},${g},255))`;
  if (slBr) slBr.style.background = `linear-gradient(to right,rgba(${r},${g},${b},0.12),rgb(${r},${g},${b}))`;
  if (hp)   hp.style.background   = `rgb(${r},${g},${b})`;
}

// ── Temp canvas ──────────────────────────────────────────────────────────────
function drawTempCanvas() {
  const canvas = $('temp-canvas');
  if (!canvas) return;
  const W = canvas.parentElement?.offsetWidth || canvas.offsetWidth || 400;
  const H = 22;
  canvas.width  = W * (window.devicePixelRatio || 1);
  canvas.height = H * (window.devicePixelRatio || 1);
  canvas.style.width  = W + 'px';
  canvas.style.height = H + 'px';
  const ctx = canvas.getContext('2d');
  ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
  const grad = ctx.createLinearGradient(0, 0, W, 0);
  grad.addColorStop(0.00, '#ff9329');
  grad.addColorStop(0.20, '#ffc58f');
  grad.addColorStop(0.45, '#fff4e5');
  grad.addColorStop(0.70, '#ffffff');
  grad.addColorStop(1.00, '#cde8ff');
  ctx.fillStyle = grad;
  fillRoundRect(ctx, 0, 0, W, H, H / 2);
}

// ── Sync all UI ──────────────────────────────────────────────────────────────
function updateColorUI() {
  setText('val-r', S.r);
  setText('val-g', S.g);
  setText('val-b', S.b);
  setVal('sl-r',  S.r);
  setVal('sl-g',  S.g);
  setVal('sl-b',  S.b);
  setVal('hex-in', rgbToHex(S.r, S.g, S.b));
  updateSliderGradients();
}

function syncUIAll() {
  updateColorUI();
  setText('val-bright', S.brightness + '%');
  setVal('sl-bright', S.brightness);
  setText('temp-val-label', S.temp + ' K');
  setVal('sl-temp', S.temp);
  drawSVCanvas();
  positionSVCursor();
  positionHueCursor();
  
  const panel = $('ctrl-panel');
  if (panel) {
    const isOff = !S.isOn;
    const isCirc = $('circadian-toggle')?.checked;
    const isSync = S.syncRunning;

    if (isOff) panel.classList.add('power-off');
    else panel.classList.remove('power-off');
    
    if (isCirc) panel.classList.add('circadian-active');
    else panel.classList.remove('circadian-active');
    
    if (isSync) panel.classList.add('sync-active');
    else panel.classList.remove('sync-active');
    
    const slBr = $('sl-bright');
    if (slBr) slBr.disabled = isOff || isCirc || isSync;
  }
}

// ── Send to light ────────────────────────────────────────────────────────────
const sendColor = debounce(async () => {
  if (!S.selectedIp || !S.isOn || S.mode !== 'color') return;
  api('POST', `/api/lights/${S.selectedIp}/color`,
      { r: S.r, g: S.g, b: S.b, brightness: S.brightness }).catch(console.warn);
}, 60);

const sendWhite = debounce(async () => {
  if (!S.selectedIp || !S.isOn || S.mode !== 'white') return;
  api('POST', `/api/lights/${S.selectedIp}/white`,
      { temp: S.temp, brightness: S.brightness }).catch(console.warn);
}, 60);

const sendScene = debounce(async () => {
  if (!S.selectedIp || !S.isOn || S.mode !== 'scene') return;
  api('POST', `/api/lights/${S.selectedIp}/scene`,
      { sceneId: S.sceneId, speed: S.sceneSpeed, brightness: S.brightness }).catch(console.warn);
}, 60);

function sendCurrent() {
  if (S.mode === 'color') sendColor();
  else if (S.mode === 'white') sendWhite();
  else if (S.mode === 'scene') sendScene();
}

// ── Lights ───────────────────────────────────────────────────────────────────
async function loadLights() {
  try {
    S.lights = await api('GET', '/api/lights');
    renderLights();
  } catch(e) { console.warn('loadLights:', e); }
}

// Background poller to keep UI in sync with backend's auto-discovery
setInterval(async () => {
  try {
    const res = await fetch('/api/lights');
    if (res.ok) {
      const data = await res.json();
      let structurallyChanged = false;
      for (const mac in data) {
         if (!S.lights[mac] || S.lights[mac].ip !== data[mac].ip) {
             structurallyChanged = true;
         }
      }
      for (const mac in S.lights) if (!data[mac]) structurallyChanged = true;
      
      S.lights = data;
      if (structurallyChanged) {
          renderLights();
      } else {
          // just safely update the dots in place without stealing UI focus
          for (const mac in S.lights) {
              const dot = $(`dot-${mac}`);
              if (dot) dot.className = `li-dot ${S.lights[mac].online ? 'online' : 'offline'}`;
          }
      }
    }
  } catch(e) {}
}, 5000);

function _renderItem(l) {
  const active = l.mac === S.selectedMac ? 'active' : '';
  const delBtn = l.is_group ? '' : `<button class="li-del" data-del="${l.mac}" title="Remove">✕</button>`;
  return `<div class="light-item ${active}" data-mac="${l.mac}" data-ip="${l.ip}">
    <span class="li-dot ${l.online ? 'online' : 'offline'}" id="dot-${l.mac}"></span>
    <div style="flex:1;min-width:0">
      <div class="li-name">${l.name}</div>
      <div class="li-ip">${l.is_group ? l.ips.length + ' bulbs' : l.ip}</div>
    </div>
    ${delBtn}
  </div>`;
}

function renderLights() {
  const el = $('lights-list');
  const grpEl = $('groups-list');
  if (!el || !grpEl) return;
  
  const allLights = Object.values(S.lights);
  const actualLights = allLights.filter(l => !l.is_group);
  const groups = allLights.filter(l => l.is_group);
  
  if (!actualLights.length) {
    el.innerHTML = '<div class="empty-hint">No lights found.<br/>Click Scan to discover.</div>';
  } else {
    el.innerHTML = actualLights.map(l => _renderItem(l)).join('');
  }
  
  if (!groups.length) {
    grpEl.innerHTML = '<div class="empty-hint" id="empty-group-hint">No groups yet.</div>';
  } else {
    grpEl.innerHTML = groups.map(l => _renderItem(l)).join('');
  }

  document.querySelectorAll('.light-item').forEach(item => {
    item.addEventListener('click', e => {
      if (e.target.dataset.del) return;
      selectLight(item.dataset.mac, item.dataset.ip);
    });
  });
  document.querySelectorAll('[data-del]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      deleteLight(btn.dataset.del);
    });
  });
  renderSyncCheckboxes();
}

function selectLight(mac, ip) {
  S.selectedMac = mac; S.selectedIp = ip;
  hide('no-selection');
  show('ctrl-panel');
  
  const isGroup = ip.startsWith('group:');
  const btnDel = $('btn-delete-group');
  if (btnDel) {
    if (isGroup) {
      btnDel.classList.remove('hidden');
    } else {
      btnDel.classList.add('hidden');
    }
  }
  
  setText('lh-name', S.lights[mac]?.name || mac);
  setText('lh-ip', isGroup ? S.lights[mac].ips.length + ' bulbs' : ip);
  document.querySelectorAll('.light-item').forEach(el =>
    el.classList.toggle('active', el.dataset.mac === mac));
  fetchLightState(ip);
  // Redraw pickers after panel becomes visible
  requestAnimationFrame(() => {
    setTimeout(() => {
      sizeAllCanvases();
      drawSVCanvas(); drawHueCanvas(); drawTempCanvas();
      positionSVCursor(); positionHueCursor();
    }, 30);
  });
}

function showNewGroupModal() {
  const actualLights = Object.values(S.lights).filter(l => !l.is_group);
  if (!actualLights.length) {
    toast('No lights available to group.');
    return;
  }
  
  const listHtml = actualLights.map(l => `
    <label style="display:flex;align-items:center;gap:10px;margin-bottom:8px;cursor:pointer;">
      <input type="checkbox" class="grp-check" value="${l.ip}" checked />
      <div>
        <strong>${l.name}</strong><br/>
        <small style="color:var(--text-dim)">${l.ip}</small>
      </div>
    </label>
  `).join('');

  showModal(`
    <h2 style="margin-bottom: 16px;">Create New Group</h2>
    <input type="text" id="grp-name-in" placeholder="Group Name (e.g. Living Room)" class="hex-input" style="width:100%;margin-bottom:16px;box-sizing:border-box;" />
    <div style="max-height: 200px; overflow-y: auto; background: var(--bg-1); padding: 12px; border-radius: 8px;">
      ${listHtml}
    </div>
  `, async () => {
    const name = $('grp-name-in').value.trim();
    if (!name) return toast('Name required');
    const checked = Array.from(document.querySelectorAll('.grp-check:checked')).map(cb => cb.value);
    if (!checked.length) return toast('Select at least one light');
    
    try {
      await api('POST', '/api/groups', { name, ips: checked });
      toast('Group created!');
      loadLights();
    } catch(e) { toast('Error: ' + e.message); }
  });
}

async function deleteGroup() {
  if (!S.selectedIp || !S.selectedIp.startsWith('group:')) return;
  if (!confirm("Delete this group?")) return;
  const groupId = S.selectedIp.split('group:')[1];
  try {
    await api('DELETE', `/api/groups/${groupId}`);
    toast('Group deleted');
    S.selectedMac = null;
    S.selectedIp = null;
    hide('ctrl-panel');
    show('no-selection');
    loadLights();
  } catch(e) { toast('Error: ' + e.message); }
}

async function fetchLightState(ip) {
  try {
    const st = await api('GET', `/api/lights/${ip}/state`);
    setOnlineStatus(true);
    S.isOn = st.state !== false;
    const toggle = $('pw-toggle');
    if (toggle) toggle.checked = S.isOn;
    if (st.r !== undefined || st.g !== undefined) {
      S.r = st.r ?? 255; S.g = st.g ?? 80; S.b = st.b ?? 0;
      S.brightness = st.dimming ?? 75;
      [S.hue, S.sat, S.val] = rgbToHsv(S.r, S.g, S.b);
      setMode('color');
    } else if (st.temp !== undefined) {
      S.temp = st.temp; S.brightness = st.dimming ?? 75;
      setMode('white');
    }
    syncUIAll();
  } catch { setOnlineStatus(false); }
}

function setOnlineStatus(online) {
  const dot  = $('online-dot');
  const txt  = $('online-text');
  if (dot) dot.className = 'online-dot ' + (online ? 'on' : 'off');
  if (txt) txt.textContent = online ? 'Online' : 'Offline';
}

async function deleteLight(mac) {
  if (!confirm(`Remove "${S.lights[mac]?.name}"?`)) return;
  try {
    await api('DELETE', `/api/lights/${mac}`);
    delete S.lights[mac];
    if (S.selectedMac === mac) {
      S.selectedMac = null; S.selectedIp = null;
      show('no-selection'); hide('ctrl-panel');
    }
    renderLights();
    toast('Light removed');
  } catch(e) { toast('Error: ' + e.message); }
}

// ── Rename ────────────────────────────────────────────────────────────────────
function renameLight() {
  if (!S.selectedMac) return;
  const current = S.lights[S.selectedMac]?.name || '';
  showModal(
    `<h2>Rename Light</h2>
     <p>Enter a new name for this light.</p>
     <input id="rename-input" class="modal-input" value="${current}" maxlength="30" />`,
    async () => {
      const inp = $('rename-input');
      const name = inp ? inp.value.trim() : '';
      if (!name) { toast('Name cannot be empty'); return; }
      try {
        await api('PATCH', `/api/lights/${S.selectedMac}`, { name });
        S.lights[S.selectedMac].name = name;
        setText('lh-name', name);
        renderLights();
        toast('Renamed!');
      } catch(e) { toast('Error: ' + e.message); }
    }
  );
  setTimeout(() => { const i = $('rename-input'); if (i) { i.focus(); i.select(); } }, 100);
}

// ── Discovery ─────────────────────────────────────────────────────────────────
async function scanLights() {
  const btn = $('btn-scan');
  if (btn) { btn.textContent = '⟳ Scanning…'; btn.disabled = true; }

  try {
    const discovery = await api('POST', '/api/discover');
    const found = discovery.lights || [];

    if (!found.length) {
      toast('No new lights found on WiFi.', 4000);
      return;
    }

    // Clear old list on server only if we found something
    await api('DELETE', '/api/lights');
    S.lights       = {};
    S.selectedMac  = null;
    S.selectedIp   = null;
    hide('ctrl-panel');
    show('no-selection');

    for (const l of found) {
      const name = `WiZ ${l.mac.slice(-5)}`;
      await api('POST', '/api/lights', { mac: l.mac, ip: l.ip, name });
      S.lights[l.mac] = { mac: l.mac, ip: l.ip, name };
    }

    renderLights();
    renderSyncCheckboxes();
    toast(`Found ${found.length} light${found.length !== 1 ? 's' : ''}! 🎉`);

  } catch(e) {
    toast('Scan failed: ' + e.message, 4000);
  } finally {
    if (btn) { btn.textContent = '⟳ Scan'; btn.disabled = false; }
  }
}

// ── Add by IP ─────────────────────────────────────────────────────────────────
function addByIP() {
  showModal(
    `<h2>Add Light by IP</h2>
     <p>Enter the light's local IP address (find it in your router's DHCP list or the WiZ app).</p>
     <input id="ip-input" class="modal-input" placeholder="192.168.1.100" />`,
    async () => {
      const inp = $('ip-input');
      const ip  = inp ? inp.value.trim() : '';
      if (!ip || !/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) { toast('Enter a valid IP'); return; }
      try {
        await api('GET', `/api/lights/${ip}/state`).catch(() => {});
        const mac  = 'manual-' + ip.replace(/\./g, '-');
        const name = `WiZ ${ip.split('.').pop()}`;
        await api('POST', '/api/lights', { mac, ip, name });
        S.lights[mac] = { mac, ip, name };
        renderLights();
        toast('Light added!');
      } catch(e) { toast('Could not reach that IP: ' + e.message); }
    }
  );
  setTimeout(() => { const i = $('ip-input'); if (i) i.focus(); }, 100);
}

// ── Presets ───────────────────────────────────────────────────────────────────
async function loadPresets() {
  try {
    S.presets = await api('GET', '/api/presets');
    renderPresets();
  } catch(e) { console.warn('loadPresets:', e); }
}

function renderPresets() {
  const el = $('presets-list');
  if (!el) return;
  if (!S.presets.length) { el.innerHTML = '<div class="empty-hint">No presets.</div>'; return; }
  el.innerHTML = S.presets.map(p => {
    const color = p.mode === 'color' ? `rgb(${p.r},${p.g},${p.b})` : kelvinToRgb(p.temp);
    return `<div class="preset-item" data-pid="${p.id}">
      <div class="pi-swatch" style="background:${color}"></div>
      <span class="pi-name">${p.name}</span>
      <button class="pi-del" data-pdel="${p.id}" title="Delete">✕</button>
    </div>`;
  }).join('');

  el.querySelectorAll('.preset-item').forEach(item => {
    item.addEventListener('click', e => {
      if (e.target.dataset.pdel) return;
      applyPreset(item.dataset.pid);
    });
  });
  el.querySelectorAll('[data-pdel]').forEach(btn => {
    btn.addEventListener('click', e => { e.stopPropagation(); deletePreset(btn.dataset.pdel); });
  });
}

async function applyPreset(pid) {
  const p = S.presets.find(x => x.id === pid);
  if (!p) return;
  if (p.mode === 'color') {
    S.r = p.r; S.g = p.g; S.b = p.b; S.brightness = p.brightness;
    [S.hue, S.sat, S.val] = rgbToHsv(S.r, S.g, S.b);
    setMode('color');
  } else {
    S.temp = p.temp; S.brightness = p.brightness;
    setMode('white');
  }
  syncUIAll(); sendCurrent();
  toast(`Applied "${p.name}"`);
}

async function deletePreset(pid) {
  await api('DELETE', `/api/presets/${pid}`).catch(() => {});
  S.presets = S.presets.filter(p => p.id !== pid);
  renderPresets();
  toast('Preset deleted');
}

function saveNewPreset() {
  showModal(
    `<h2>Save Preset</h2>
     <p>Save the current color${S.mode === 'white' ? ' temperature' : ''} as a named preset.</p>
     <input id="preset-name-input" class="modal-input" placeholder="My Preset" maxlength="24" />`,
    async () => {
      const inp = $('preset-name-input');
      const name = inp ? inp.value.trim() : '';
      if (!name) { toast('Enter a preset name'); return; }
      const body = S.mode === 'color'
        ? { name, mode:'color', r:S.r, g:S.g, b:S.b, brightness:S.brightness }
        : { name, mode:'white', temp:S.temp, brightness:S.brightness };
      try {
        const created = await api('POST', '/api/presets', body);
        S.presets.push(created); renderPresets(); toast('Preset saved!');
      } catch(e) { toast('Error: ' + e.message); }
    }
  );
  setTimeout(() => { const i = $('preset-name-input'); if (i) i.focus(); }, 100);
}

// ── Mode tabs ─────────────────────────────────────────────────────────────────
function setMode(mode) {
  S.mode = mode;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === mode));
  const tc = $('tab-color'), tw = $('tab-white'), ts = $('tab-scene');
  if (tc) tc.classList.toggle('hidden', mode !== 'color');
  if (tw) tw.classList.toggle('hidden', mode !== 'white');
  if (ts) ts.classList.toggle('hidden', mode !== 'scene');
  // Redraw temp canvas when switching to white
  if (mode === 'white') {
    requestAnimationFrame(() => setTimeout(drawTempCanvas, 30));
  }
}

// ── Power ─────────────────────────────────────────────────────────────────────
async function togglePower(on) {
  S.isOn = on;
  syncUIAll();
  try {
    await api('POST', `/api/lights/${S.selectedIp}/${on ? 'on' : 'off'}`);
    toast(on ? 'Light on' : 'Light off');
  } catch(e) { toast('Error: ' + e.message); }
}

// ── Music sync ────────────────────────────────────────────────────────────────
async function fetchAudioDevices() {
  try {
    const res = await api('GET', '/api/audio/devices');
    S.audioDevices = res.devices || [];
    S.syncDevIdx   = res.loopback_index;
    renderDeviceUI(res);
  } catch(e) { console.warn('fetchAudioDevices:', e); }

  api('GET', '/api/settings').then(s => {
    if (s.circadian_enabled) {
      const circ = $('circadian-toggle');
      if (circ) circ.checked = true;
      $('ctrl-panel').classList.add('circadian-active');
    }
  }).catch(() => {});
}

function renderDeviceUI(res) {
  const sel = $('dev-select');
  if (sel) {
    sel.innerHTML = S.audioDevices.map(d =>
      `<option value="${d.index}" ${d.index === res.loopback_index ? 'selected' : ''}>${d.name}</option>`
    ).join('');
    sel.addEventListener('change', () => { S.syncDevIdx = parseInt(sel.value); });
  }
  // Remove the static text warning if BlackHole exists
  if (res.blackhole_found) {
    hide('bh-warning');
  } else {
    show('bh-warning');
  }
  show('dev-row');
}

function renderSyncCheckboxes() {
  const el = $('sync-cbs');
  if (!el) return;
  const macs = Object.keys(S.lights);
  if (!macs.length) {
    el.innerHTML = '<span style="color:var(--txt-3);font-size:11px">No saved lights</span>';
    return;
  }
  el.innerHTML = macs.map(mac => {
    const l = S.lights[mac];
    const checked = S.syncIps.includes(l.ip) ? 'checked' : '';
    return `<label class="sync-cb-item"><input type="checkbox" data-ip="${l.ip}" ${checked}> ${l.name}</label>`;
  }).join('');
  el.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', () => {
      const ip = cb.dataset.ip;
      if (cb.checked) { if (!S.syncIps.includes(ip)) S.syncIps.push(ip); }
      else S.syncIps = S.syncIps.filter(x => x !== ip);
    });
  });
}

async function toggleAudioSync() {
  if (S.syncRunning) return stopSync();
  
  if (S.syncDevIdx === null && !S.audioDevices.length) {
    toast('No audio devices available');
    return;
  }
  
  if (S.syncDevIdx === null && S.audioDevices.length) {
    S.syncDevIdx = S.audioDevices[0].index;
  }
  if (!S.syncIps.length) {
    if (S.selectedIp) { S.syncIps = [S.selectedIp]; renderSyncCheckboxes(); }
    else { toast('Select lights to sync'); return; }
  }
  const btn = $('btn-sync-audio');
  if (btn) { btn.textContent = 'Starting…'; btn.disabled = true; }
  try {
    const res = await api('POST', '/api/audio/start', {
      device_index: S.syncDevIdx, mode: S.syncMode,
      sensitivity: S.sensitivity, light_ips: S.syncIps,
    });
    if (res.ok) {
      S.syncRunning = 'audio';
      if (btn) { btn.textContent = '■ Stop Sync'; btn.classList.add('running'); }
      show('live-badge-audio');
      show('eq-wrap');
      syncUIAll();
      toast('Music sync started 🔥');
    } else {
      toast('Error: ' + (res.error || 'Unknown'), 4000);
      if (btn) btn.textContent = 'Start Sync';
    }
  } catch(e) {
    toast('Failed: ' + e.message);
    if (btn) btn.textContent = 'Start Sync';
  } finally { if (btn) btn.disabled = false; }
}

async function toggleScreenSync() {
  if (S.syncRunning) return stopSync();
  
  if (!S.syncIps.length) {
    if (S.selectedIp) { S.syncIps = [S.selectedIp]; renderSyncCheckboxes(); }
    else { toast('Select lights to sync'); return; }
  }
  const btn = $('btn-sync-screen');
  const mode = $('screen-mode')?.value || 'average';
  
  if (btn) { btn.textContent = 'Starting…'; btn.disabled = true; }
  try {
    const res = await api('POST', '/api/screen/start', {
      light_ips: S.syncIps, mode: mode, brightness: S.brightness
    });
    if (res.ok) {
      S.syncRunning = 'screen';
      if (btn) { btn.textContent = '■ Stop Sync'; btn.classList.add('running'); }
      show('live-badge-screen');
      syncUIAll();
      toast('Ambilight started 🖥️');
    } else {
      toast('Error: ' + (res.error || 'Unknown'), 4000);
      if (btn) btn.textContent = 'Start Sync';
    }
  } catch(e) {
    toast('Failed: ' + e.message);
    if (btn) btn.textContent = 'Start Sync';
  } finally { if (btn) btn.disabled = false; }
}

async function stopSync() {
  const isAudio = S.syncRunning === 'audio';
  const isScreen = S.syncRunning === 'screen';
  const isWeather = S.syncRunning === 'weather';
  
  if (isAudio) {
    const btn = $('btn-sync-audio');
    try {
      await api('POST', '/api/audio/stop');
      S.syncRunning = false;
      hide('live-badge-audio');
      hide('eq-wrap');
      if (btn) { btn.textContent = 'Start Sync'; btn.classList.remove('running'); }
      syncUIAll();
    } catch(e) { toast('Error: ' + e.message); }
    updateEQ(new Array(16).fill(0));
  } else if (isScreen) {
    const btn = $('btn-sync-screen');
    try {
      await api('POST', '/api/screen/stop');
      S.syncRunning = false;
      hide('live-badge-screen');
      if (btn) { btn.textContent = 'Start Sync'; btn.classList.remove('running'); }
      syncUIAll();
    } catch(e) { toast('Error: ' + e.message); }
  } else if (isWeather) {
    const btn = $('btn-sync-weather');
    try {
      await api('POST', '/api/weather/stop');
      S.syncRunning = false;
      hide('live-badge-weather');
      if (btn) { btn.textContent = 'Start Sync'; btn.classList.remove('running'); }
      syncUIAll();
    } catch(e) { toast('Error: ' + e.message); }
  } else if (S.syncRunning === 'pomodoro') {
    const btn = $('btn-sync-pomodoro');
    try {
      await api('POST', '/api/pomodoro/stop');
      S.syncRunning = false;
      if(S.pomodoroTimer) { clearInterval(S.pomodoroTimer); S.pomodoroTimer = null; }
      hide('live-badge-pomodoro');
      if (btn) { btn.textContent = 'Start Focus'; btn.classList.remove('running'); }
      if($('pomodoro-timer')) $('pomodoro-timer').textContent = '25:00';
      if($('pomodoro-state')) $('pomodoro-state').textContent = 'Ready to Focus';
      syncUIAll();
    } catch(e) { toast('Error: ' + e.message); }
  }
  toast('Sync stopped');
}

let CURRENT_BAND_LABELS = [];

function updateEQ(bands) {
  const wrap = $('eq-wrap');
  if (!wrap) return;
  
  let expectedLabels;
  if (bands.length === 3) {
      expectedLabels = ["LOWS", "MIDS", "HIGHS"];
  } else {
      expectedLabels = ["SUB", "BASS1", "BASS2", "L-MID1", "L-MID2", "MID1", "MID2", "H-MID1", "H-MID2", "PRES1", "PRES2", "BRIL1", "BRIL2", "AIR1", "AIR2", "SPRKLE"];
  }

  // Rebuild UI if band count changed
  if (CURRENT_BAND_LABELS.length !== expectedLabels.length || wrap.children.length === 0) {
    CURRENT_BAND_LABELS = expectedLabels;
    let html = '';
    for (let i = 0; i < CURRENT_BAND_LABELS.length; i++) {
      html += `
        <div class="eq-row ${bands.length === 3 ? 'thick-row' : ''}">
          <span class="eq-lbl">${CURRENT_BAND_LABELS[i]}</span>
          <div class="eq-track"><div class="eq-bar" id="eq-bar-${i}"></div></div>
          <span class="eq-pct" id="pct-bar-${i}">0%</span>
        </div>
      `;
    }
    wrap.innerHTML = html;
  }
  
  for (let i = 0; i < CURRENT_BAND_LABELS.length; i++) {
    const b = Math.round((bands[i] || 0) * 100);
    const bar = $('eq-bar-' + i);
    const pct = $('pct-bar-' + i);
    if (bar) bar.style.width = b + '%';
    if (pct) pct.textContent = b + '%';
  }
}

// ── WebSocket (no more ping leak) ────────────────────────────────────────────
function connectWS() {
  if (S._pingTimer) { clearInterval(S._pingTimer); S._pingTimer = null; }

  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  S.ws = ws;
  ws.onopen = () => {
    const dot = $('ws-dot');
    if (dot) dot.className = 'ws-indicator connected';
    S._pingTimer = setInterval(() => { if (ws.readyState === 1) ws.send('ping'); }, 15000);
  };
  ws.onmessage = ({ data }) => {
    try {
      const msg = JSON.parse(data);
      if (msg.type === 'audio_sync') updateEQ(msg.bands || new Array(16).fill(0));
    } catch {}
  };
  ws.onclose = () => {
    const dot = $('ws-dot');
    if (dot) dot.className = 'ws-indicator disconnected';
    if (S._pingTimer) { clearInterval(S._pingTimer); S._pingTimer = null; }
    setTimeout(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}

// ── Kelvin → CSS color ────────────────────────────────────────────────────────
function kelvinToRgb(k) {
  const t = Math.max(0, Math.min(1, (k - 2200) / (6500 - 2200)));
  const r = Math.round(255 - t * 80);
  const g = Math.round(200 + t * 30);
  const b = Math.round(100 + t * 155);
  return `rgb(${r},${g},${b})`;
}

// ── Modal ────────────────────────────────────────────────────────────────────
let _modalOkFn = null;

function showModal(html, onOk) {
  const body = $('modal-body');
  if (body) body.innerHTML = html;
  show('modal-overlay');
  _modalOkFn = onOk || null;
  const okBtn = $('modal-ok');
  if (okBtn) okBtn.classList.toggle('hidden', !onOk);
  setText('modal-cancel', onOk ? 'Cancel' : 'Close');

  // Support Enter key submission
  const input = body.querySelector('input');
  if (input && onOk) {
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        $('modal-ok').click();
      }
    });
  }
}

function closeModal() {
  hide('modal-overlay');
  _modalOkFn = null;
}

function showSetupGuide() {
  showModal(`
    <h2>📻 BlackHole Setup Guide</h2>
    <p>BlackHole is a free virtual audio driver that captures system audio digitally — no microphone, no noise.</p>
    <ol>
      <li><strong>Install BlackHole</strong> (run in Terminal):<br/><code>brew install blackhole-2ch</code></li>
      <li><strong>Open Audio MIDI Setup</strong> (Cmd+Space → type "Audio MIDI Setup")</li>
      <li>Click <strong>+</strong> → <strong>Create Multi-Output Device</strong></li>
      <li>Tick both <strong>BlackHole 2ch</strong> and your speakers/headphones</li>
      <li>Enable <strong>Drift Correction</strong> on the speakers row</li>
      <li>Go to <strong>System Settings → Sound → Output</strong> → select the Multi-Output Device</li>
      <li>Come back here and click <strong>Refresh</strong></li>
    </ol>
    <p><em>🔊 You'll still hear audio normally — the multi-output device sends a clean copy to WiZ Controller.</em></p>
  `, null);
}

// ── Wire all events ───────────────────────────────────────────────────────────
function wireEvents() {
  // Sidebar
  on('btn-scan',       'click', scanLights);
  on('btn-add-ip',     'click', addByIP);
  on('btn-new-preset', 'click', saveNewPreset);
  on('btn-rename',     'click', renameLight);
  on('btn-new-group',  'click', showNewGroupModal);
  on('btn-delete-group', 'click', deleteGroup);

  // Mode tabs
  $$('.tab').forEach(tab => tab.addEventListener('click', () => { setMode(tab.dataset.tab); sendCurrent(); }));

  // Power and Circadian
  on('pw-toggle', 'change', e => togglePower(e.target.checked));
  
  on('circadian-toggle', 'change', e => {
    const isEnabled = e.target.checked;
    api('POST', '/api/settings', { circadian_enabled: isEnabled }).catch(console.warn);
    syncUIAll();
  });

  // SV picker
  const svCanvas = $('sv-canvas');
  if (svCanvas) {
    const onSV = e => {
      if (!S.draggingSV) return;
      e.preventDefault();
      svFromEvent(e, svCanvas);
      // Fast path: avoid redrawing SV Canvas layout!
      positionSVCursor();
      updateColorUI();
      sendColor();
    };
    svCanvas.addEventListener('mousedown', e => { S.draggingSV = true; svFromEvent(e, svCanvas); positionSVCursor(); updateColorUI(); sendColor(); });
    svCanvas.addEventListener('touchstart', e => { S.draggingSV = true; svFromEvent(e, svCanvas); positionSVCursor(); updateColorUI(); sendColor(); }, { passive: true });
    document.addEventListener('mousemove', onSV);
    document.addEventListener('touchmove', onSV, { passive: false });
    document.addEventListener('mouseup', () => S.draggingSV = false);
    document.addEventListener('touchend', () => S.draggingSV = false);
  }

  // Hue bar
  const hueCanvas = $('hue-canvas');
  if (hueCanvas) {
    const onHue = e => {
      if (!S.draggingHue) return;
      e.preventDefault();
      hueFromEvent(e, hueCanvas);
      drawSVCanvas(); positionHueCursor(); updateColorUI(); sendColor();
    };
    hueCanvas.addEventListener('mousedown', e => { S.draggingHue = true; hueFromEvent(e, hueCanvas); drawSVCanvas(); positionHueCursor(); updateColorUI(); sendColor(); });
    hueCanvas.addEventListener('touchstart', e => { S.draggingHue = true; hueFromEvent(e, hueCanvas); drawSVCanvas(); positionHueCursor(); updateColorUI(); sendColor(); }, { passive: true });
    document.addEventListener('mousemove', onHue);
    document.addEventListener('touchmove', onHue, { passive: false });
    document.addEventListener('mouseup', () => S.draggingHue = false);
    document.addEventListener('touchend', () => S.draggingHue = false);
  }

  // RGB sliders
  ['r', 'g', 'b'].forEach(ch => {
    on(`sl-${ch}`, 'input', e => {
      S[ch] = parseInt(e.target.value);
      setText(`val-${ch}`, S[ch]);
      [S.hue, S.sat, S.val] = rgbToHsv(S.r, S.g, S.b);
      drawSVCanvas(); positionSVCursor(); positionHueCursor();
      updateSliderGradients();
      setVal('hex-in', rgbToHex(S.r, S.g, S.b));
      const hp = $('hex-preview');
      if (hp) hp.style.background = `rgb(${S.r},${S.g},${S.b})`;
      sendColor();
    });
  });

  // Hex
  on('hex-in', 'input', e => {
    const rgb = hexToRgb(e.target.value);
    if (!rgb) return;
    [S.r, S.g, S.b] = rgb;
    [S.hue, S.sat, S.val] = rgbToHsv(S.r, S.g, S.b);
    syncUIAll(); sendColor();
  });

  // Brightness
  on('sl-bright', 'input', e => {
    S.brightness = parseInt(e.target.value);
    setText('val-bright', S.brightness + '%');
    updateSliderGradients(); sendCurrent();
  });

  // Temperature
  on('sl-temp', 'input', e => {
    S.temp = parseInt(e.target.value);
    setText('temp-val-label', S.temp + ' K');
    sendWhite();
  });

  // Scenes
  $$('.scene-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      $$('.scene-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      S.sceneId = parseInt(btn.dataset.scene);
      sendScene();
    });
  });

  on('sl-speed', 'input', e => {
    S.sceneSpeed = parseInt(e.target.value);
    setText('val-speed', S.sceneSpeed + '%');
    sendScene();
  });

  // Sync
  on('btn-sync-audio',  'click', toggleAudioSync);
  on('btn-sync-screen', 'click', toggleScreenSync);
  
  const allTabs = ['audio', 'screen', 'weather', 'pomodoro', 'alarm', 'bpm'];
  allTabs.forEach(t => {
    const btn = $(`tab-btn-${t}`);
    if (btn) {
      on(`tab-btn-${t}`, 'click', () => {
        allTabs.forEach(other => {
          const ob = $(`tab-btn-${other}`);
          const ov = $(`sync-view-${other}`);
          if (ob) {
             if (other === t) ob.classList.add('active');
             else ob.classList.remove('active');
          }
          if (ov) {
             if (other === t) ov.classList.remove('hidden');
             else ov.classList.add('hidden');
          }
        });
      });
    }
  });
  on('btn-refresh-dev', 'click',  fetchAudioDevices);
  on('btn-setup-guide', 'click',  showSetupGuide);
  on('sync-mode', 'change', async e => {
    S.syncMode = e.target.value;
    if (S.syncRunning === 'audio') api('PATCH', '/api/audio/mode', { mode: S.syncMode }).catch(() => {});
  });
  on('sl-sens', 'input', e => {
    S.sensitivity = parseInt(e.target.value, 10) / 10;
    $('val-sens').textContent = S.sensitivity.toFixed(1) + '×';
    if (S.syncRunning === 'audio') api('PATCH', '/api/audio/sensitivity', { value: S.sensitivity }).catch(() => {});
  });

  // Modal
  on('modal-ok', 'click', () => {
    const fn = _modalOkFn;
    closeModal();
    if (fn) fn();
  });
  on('modal-cancel', 'click', closeModal);
  on('modal-overlay', 'click', e => { if (e.target === $('modal-overlay')) closeModal(); });
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  console.log('WiZ Controller v2.1: init start');

  try { wireEvents(); } catch(e) { console.error('wireEvents FAILED:', e); }

  sizeAllCanvases();
  try { drawHueCanvas(); } catch(e) { console.warn('drawHueCanvas:', e); }
  try { drawSVCanvas(); }  catch(e) { console.warn('drawSVCanvas:', e); }
  try { drawTempCanvas(); } catch(e) { console.warn('drawTempCanvas:', e); }

  applyHsvToRgb();
  syncUIAll();

  loadLights().then(renderSyncCheckboxes).catch(console.warn);
  loadPresets().catch(console.warn);
  fetchAudioDevices().catch(console.warn);
  connectWS();

  const onResize = debounce(() => {
    sizeAllCanvases();
    drawSVCanvas(); drawHueCanvas(); drawTempCanvas();
    positionSVCursor(); positionHueCursor();
  }, 150);
  window.addEventListener('resize', onResize);

  console.log('WiZ Controller v2.1: init complete ✓');
});

// ─── Image Palette Feature ──────────────────────────────────────────────────────

let extractedPalette = [];

$('nav-palette').addEventListener('click', () => {
  hide('no-selection');
  hide('ctrl-panel');
  show('panel-palette');
  
  // Deselect any active presets
  document.querySelectorAll('.preset-item').forEach(el => el.classList.remove('active'));
  $('nav-palette').classList.add('active');
  
  // Deselect active light
  S.selectedIp = null;
  document.querySelectorAll('.light-item').forEach(el => el.classList.remove('active'));
});

// Hide palette when clicking a light
const originalSelectLight = selectLight;
window.selectLight = function(mac, ip) {
  hide('panel-palette');
  const navBtn = $('nav-palette');
  if (navBtn) navBtn.classList.remove('active');
  originalSelectLight(mac, ip);
};

// Drag and drop handling
const dropzone = $('palette-dropzone');
const fileInput = $('palette-file');
const swatchesContainer = $('palette-swatches');
const applyBtn = $('btn-apply-palette');
const spotifyBtn = $('btn-spotify-palette');
const spotifyArt = $('spotify-artwork');

spotifyBtn.addEventListener('click', async () => {
  spotifyBtn.textContent = 'Fetching...';
  spotifyBtn.disabled = true;
  try {
    const res = await api('GET', '/api/palette/spotify');
    if (res.palette) {
      extractedPalette = res.palette;
      renderSwatches(extractedPalette);
      
      if (res.artwork_url) {
        spotifyArt.src = res.artwork_url;
        show(spotifyArt.id);
      }
      
      show(applyBtn.id);
    } else {
      toast('Failed to get Spotify artwork');
    }
  } catch (err) {
    toast('Make sure Spotify is playing');
  }
  spotifyBtn.textContent = '🎵 Sync from Spotify';
  spotifyBtn.disabled = false;
});

dropzone.addEventListener('click', () => fileInput.click());

dropzone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropzone.classList.add('dragover');
});
dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
dropzone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (e.dataTransfer.files.length) {
    handlePaletteImage(e.dataTransfer.files[0]);
  }
});

fileInput.addEventListener('change', (e) => {
  if (e.target.files.length) handlePaletteImage(e.target.files[0]);
});

function handlePaletteImage(file) {
  if (!file.type.startsWith('image/')) {
    toast('Please upload an image file');
    return;
  }
  
  const reader = new FileReader();
  reader.onload = async (e) => {
    $('dropzone-text').textContent = 'Extracting palette...';
    try {
      const res = await api('POST', '/api/palette', { image_base64: e.target.result });
      if (res.palette) {
        extractedPalette = res.palette;
        renderSwatches(extractedPalette);
        $('dropzone-text').textContent = 'Palette Extracted! Drop another image to change.';
        show(applyBtn.id);
      } else {
        toast('Failed to extract palette');
        $('dropzone-text').textContent = 'Drop image here or click to upload';
      }
    } catch (err) {
      toast('Error parsing image');
      $('dropzone-text').textContent = 'Drop image here or click to upload';
    }
  };
  reader.readAsDataURL(file);
}

function renderSwatches(colors) {
  swatchesContainer.innerHTML = '';
  if (colors.length) {
    colors.forEach(hex => {
      const el = document.createElement('div');
      el.className = 'swatch';
      el.style.backgroundColor = hex;
      el.title = hex;
      swatchesContainer.appendChild(el);
    });
    show('palette-swatches');
  } else {
    hide('palette-swatches');
  }
}

applyBtn.addEventListener('click', async () => {
  if (!extractedPalette.length) return;
  
  // Get all active lights (not groups)
  const activeLights = Object.values(S.lights).filter(l => !l.is_group);
  if (!activeLights.length) {
    toast('No lights found');
    return;
  }
  
  applyBtn.textContent = 'Applying...';
  applyBtn.disabled = true;
  
  // Distribute colors for max contrast mix
  for (let i = 0; i < activeLights.length; i++) {
    const light = activeLights[i];
    // If multiple lights, stretch them across the palette (e.g. 0 and 4 for 2 lights)
    const colorIdx = activeLights.length > 1 
        ? Math.floor((i / (activeLights.length - 1)) * (extractedPalette.length - 1))
        : 0;
    
    const hex = extractedPalette[colorIdx];
    
    // Parse hex
    const r = parseInt(hex.substring(1,3), 16);
    const g = parseInt(hex.substring(3,5), 16);
    const b = parseInt(hex.substring(5,7), 16);
    
    api('POST', `/api/lights/${light.ip}/color`, { r, g, b, instant: true });
  }
  
  toast('Palette applied to room! 🎨');
  setTimeout(() => {
    applyBtn.textContent = '✨ Apply to Room';
    applyBtn.disabled = false;
  }, 1000);
});

// Spotify Auto-Sync
const toggleSpotifyAuto = $('toggle-spotify-auto');
let spotifyAutoInterval = null;
let lastSpotifyArtworkUrl = null;

toggleSpotifyAuto.addEventListener('change', (e) => {
  if (e.target.checked) {
    // Start auto-sync loop every 5 seconds
    spotifyAutoInterval = setInterval(async () => {
      try {
        const res = await api('GET', '/api/palette/spotify');
        if (res.palette && res.artwork_url && res.artwork_url !== lastSpotifyArtworkUrl) {
          lastSpotifyArtworkUrl = res.artwork_url;
          extractedPalette = res.palette;
          renderSwatches(extractedPalette);
          spotifyArt.src = res.artwork_url;
          show(spotifyArt.id);
          show(applyBtn.id);
          
          // Automatically apply to room!
          applyBtn.click();
        }
      } catch (err) {
        console.warn('Auto-sync Spotify failed:', err);
      }
    }, 5000);
    toast('Spotify Auto-Sync Enabled');
  } else {
    clearInterval(spotifyAutoInterval);
    toast('Spotify Auto-Sync Disabled');
  }
});

// ==========================================
// NEW FEATURE LOGIC (Pomodoro, AI, Alarm, Spotify)
// ==========================================

// --- Pomodoro ---
async function togglePomodoroSync() {
  if (!S.syncIps.length) {
    if (S.selectedIp) { S.syncIps = [S.selectedIp]; renderSyncCheckboxes(); }
    else { toast('Select lights to sync'); return; }
  }
  
  if (S.syncRunning === 'pomodoro') {
    // Stop
    const btn = $('btn-sync-pomodoro');
    if (btn) { btn.textContent = 'Stopping...'; btn.disabled = true; }
    try {
      await api('POST', '/api/pomodoro/stop');
      S.syncRunning = false;
      if(S.pomodoroTimer) { clearInterval(S.pomodoroTimer); S.pomodoroTimer = null; }
      hide('live-badge-pomodoro');
      if(btn) { btn.textContent = 'Start Focus'; btn.classList.remove('running'); }
      if($('pomodoro-timer')) $('pomodoro-timer').textContent = '25:00';
      if($('pomodoro-state')) $('pomodoro-state').textContent = 'Ready to Focus';
    } catch(e) {} finally { if(btn) btn.disabled = false; }
  } else {
    // Start
    const btn = $('btn-sync-pomodoro');
    if (btn) { btn.textContent = 'Starting...'; btn.disabled = true; }
    try {
      const res = await api('POST', '/api/pomodoro/start', { light_ips: S.syncIps });
      if (res.ok) {
        S.syncRunning = 'pomodoro';
        show('live-badge-pomodoro');
        if(btn) { btn.textContent = 'Stop Pomodoro'; btn.classList.add('running'); }
        S.pomodoroTimer = setInterval(pollPomodoro, 1000);
      }
    } catch(e) { toast('Failed: ' + e.message); } finally { if(btn) btn.disabled = false; }
  }
}

async function pollPomodoro() {
  if (S.syncRunning !== 'pomodoro') return;
  try {
    const res = await fetch('/api/pomodoro/status').then(r => r.json());
    if (res.state === 'stopped') {
      clearInterval(S.pomodoroTimer); S.pomodoroTimer = null;
      S.syncRunning = false;
      hide('live-badge-pomodoro');
      if($('btn-sync-pomodoro')) { $('btn-sync-pomodoro').textContent = 'Start Focus'; $('btn-sync-pomodoro').classList.remove('running'); }
      if($('pomodoro-timer')) $('pomodoro-timer').textContent = '25:00';
      if($('pomodoro-state')) $('pomodoro-state').textContent = 'Ready to Focus';
    } else {
      const m = Math.floor(res.time_remaining / 60).toString().padStart(2, '0');
      const s = (res.time_remaining % 60).toString().padStart(2, '0');
      if($('pomodoro-timer')) $('pomodoro-timer').textContent = `${m}:${s}`;
      if($('pomodoro-state')) {
        if(res.state === 'focus') $('pomodoro-state').textContent = 'Focusing...';
        else if(res.state === 'warning') $('pomodoro-state').textContent = 'Wrapping up...';
        else if(res.state === 'break') $('pomodoro-state').textContent = 'Take a break!';
      }
    }
  } catch(e) {}
}


// --- Alarm ---
async function setAlarm() {
  if (!S.syncIps.length) {
    if (S.selectedIp) { S.syncIps = [S.selectedIp]; renderSyncCheckboxes(); }
    else { toast('Select lights to sync'); return; }
  }
  const timeStr = $('alarm-time').value;
  if(!timeStr) return alert("Please select a time.");
  const btn = $('btn-sync-alarm');
  if (btn) { btn.textContent = 'Setting...'; btn.disabled = true; }
  
  try {
    const res = await api('POST', '/api/alarm/set', { light_ips: S.syncIps, time: timeStr });
    if (res.ok) {
      toast('Alarm armed ⏰');
      show('live-badge-alarm'); show('btn-clear-alarm');
      if(btn) { btn.textContent = 'Armed'; btn.classList.add('running'); }
    }
  } catch(e) { toast('Failed: ' + e.message); } finally { if(btn) btn.disabled = false; }
}

async function clearAlarm() {
  try {
    await api('POST', '/api/alarm/clear');
    toast('Alarm cleared');
    hide('live-badge-alarm'); hide('btn-clear-alarm');
    const btn = $('btn-sync-alarm');
    if(btn) { btn.textContent = 'Set Alarm'; btn.classList.remove('running'); }
  } catch(e) {}
}


// --- BPM Sync ---
let bpmPollInterval = null;

async function toggleBPMSync() {
  if (!S.syncIps.length) {
    if (S.selectedIp) { S.syncIps = [S.selectedIp]; renderSyncCheckboxes(); }
    else { toast('Select lights to sync'); return; }
  }
  
  if (S.bpmActive) {
    try {
      await api('POST', '/api/bpm/stop');
      S.bpmActive = false;
      clearInterval(bpmPollInterval);
      hide('live-badge-bpm');
      if($('btn-sync-bpm')) { $('btn-sync-bpm').textContent = 'Start BPM Sync'; $('btn-sync-bpm').classList.remove('running'); }
      if($('bpm-display')) $('bpm-display').textContent = '--';
      if($('bpm-confidence')) $('bpm-confidence').textContent = 'Stopped';
    } catch(e) {}
  } else {
    const btn = $('btn-sync-bpm');
    if(btn) { btn.textContent = 'Starting...'; btn.disabled = true; }
    const dist = document.querySelector('input[name="bpm-dist"]:checked').value;
    try {
      const res = await api('POST', '/api/bpm/start', { light_ips: S.syncIps, distribution: dist });
      if (res.ok) {
        S.bpmActive = true;
        show('live-badge-bpm');
        if(btn) { btn.textContent = 'Stop BPM Sync'; btn.classList.add('running'); }
        bpmPollInterval = setInterval(pollBPMStatus, 500);
      }
    } catch(e) { toast('Failed: ' + e.message); }
    finally { if(btn) btn.disabled = false; }
  }
}

async function pollBPMStatus() {
  try {
    const res = await fetch('/api/bpm/status').then(r => r.json());
    if ($('bpm-display')) $('bpm-display').textContent = res.bpm > 0 ? Math.round(res.bpm) : '--';
    if ($('bpm-confidence')) {
      if (res.bpm > 0) {
        const pct = Math.round(res.confidence * 100);
        $('bpm-confidence').textContent = `Confidence: ${pct}%`;
      } else {
        $('bpm-confidence').textContent = 'Listening...';
      }
    }
  } catch(e) {}
}

  if($('btn-sync-bpm')) on('btn-sync-bpm', 'click', toggleBPMSync);
  if($('btn-sync-pomodoro')) on('btn-sync-pomodoro', 'click', togglePomodoroSync);

  if($('btn-sync-alarm')) {
    on('btn-sync-alarm', 'click', setAlarm);
    on('btn-clear-alarm', 'click', clearAlarm);
    fetch('/api/alarm/status').then(r=>r.json()).then(res => {
      if(res && res.alarm_active) {
        if($('alarm-time')) $('alarm-time').value = res.alarm_time;
        show('live-badge-alarm'); show('btn-clear-alarm');
        if($('btn-sync-alarm')) { $('btn-sync-alarm').textContent = 'Armed'; $('btn-sync-alarm').classList.add('running'); }
      }
    }).catch(e=>{});
  }
