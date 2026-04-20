'use strict';

const STORAGE_KEY = 'mediark_hubs';

function loadServers() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); }
  catch { return []; }
}

function saveServers(list) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

function normalizeUrl(raw) {
  const s = raw.trim().replace(/\/$/, '');
  return s.startsWith('http') ? s : 'http://' + s;
}

async function pingServer(url) {
  try {
    const r = await fetch(url + '/healthz', { signal: AbortSignal.timeout(5000) });
    return r.ok;
  } catch { return false; }
}

async function fetchProfile(url) {
  try {
    const r = await fetch(url + '/profile/public', { signal: AbortSignal.timeout(5000) });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

function setMsg(el, text, type) {
  el.textContent = text;
  el.className = 'msg' + (type ? ' msg-' + type : '');
}

// ── DOM refs ──────────────────────────────────────────────────

const form      = document.getElementById('add-server-form');
const urlInput  = document.getElementById('server-url-input');
const addBtn    = document.getElementById('add-btn');
const addMsg    = document.getElementById('add-msg');
const grid      = document.getElementById('server-grid');
const emptyHint = document.getElementById('empty-hint');
const cardTpl   = document.getElementById('card-template');

// ── Card rendering ────────────────────────────────────────────

function renderCard(server) {
  const node = cardTpl.content.cloneNode(true);
  const card = node.querySelector('.hub-card');
  card.dataset.id = server.id;

  const img      = card.querySelector('.hub-card-img');
  const initials = card.querySelector('.hub-card-initials');
  const nameEl   = card.querySelector('.hub-card-name');
  const urlEl    = card.querySelector('.hub-card-url');
  const descEl   = card.querySelector('.hub-card-desc');
  const badge    = card.querySelector('.hub-status-badge');

  nameEl.textContent = server.name || server.url;
  urlEl.textContent  = server.url;
  descEl.textContent = server.description || '';

  if (server.icon_url) {
    img.src = server.icon_url;
    img.alt = server.name || '';
    img.style.display = '';
    initials.style.display = 'none';
  } else {
    img.style.display = 'none';
    initials.style.display = '';
    initials.textContent = (server.name || server.url).slice(0, 2).toUpperCase();
  }

  const online = server.status === 'online';
  badge.textContent = online ? '온라인' : '오프라인';
  badge.className   = 'hub-status-badge ' + (online ? 'badge-online' : 'badge-offline');

  card.querySelector('.btn-open').addEventListener('click', () => {
    window.open(server.url, '_blank', 'noopener');
  });

  card.querySelector('.btn-refresh').addEventListener('click', () =>
    refreshServer(server.id)
  );

  card.querySelector('.btn-remove').addEventListener('click', () => {
    if (!confirm(`"${server.name || server.url}" 을 목록에서 제거하시겠습니까?`)) return;
    saveServers(loadServers().filter(s => s.id !== server.id));
    renderAll();
  });

  return card;
}

function renderAll() {
  grid.querySelectorAll('.hub-card').forEach(el => el.remove());
  const list = loadServers();
  emptyHint.style.display = list.length ? 'none' : '';
  list.forEach(s => grid.appendChild(renderCard(s)));
}

// ── Status refresh ────────────────────────────────────────────

async function refreshServer(id) {
  const list = loadServers();
  const idx  = list.findIndex(s => s.id === id);
  if (idx === -1) return;

  const card = grid.querySelector(`.hub-card[data-id="${id}"]`);
  if (card) {
    const badge = card.querySelector('.hub-status-badge');
    badge.textContent = '확인 중…';
    badge.className = 'hub-status-badge badge-checking';
  }

  const server  = list[idx];
  const alive   = await pingServer(server.url);
  const profile = alive ? await fetchProfile(server.url) : null;

  list[idx] = {
    ...server,
    status:        alive ? 'online' : 'offline',
    lastCheckedAt: new Date().toISOString(),
    name:        (profile?.name)        || server.name,
    description: (profile?.description) || server.description,
    icon_url:    (profile?.icon_url)    || server.icon_url,
  };
  saveServers(list);
  renderAll();
}

// ── Add server ────────────────────────────────────────────────

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url  = normalizeUrl(urlInput.value);
  const list = loadServers();

  if (list.some(s => s.url === url)) {
    setMsg(addMsg, '이미 추가된 서버입니다.', 'err');
    return;
  }

  addBtn.disabled = true;
  setMsg(addMsg, '연결 확인 중…', '');

  const alive = await pingServer(url);
  if (!alive) {
    setMsg(addMsg, '서버에 연결할 수 없습니다. URL을 확인해 주세요.', 'err');
    addBtn.disabled = false;
    return;
  }

  const profile = await fetchProfile(url);
  list.push({
    id:            crypto.randomUUID(),
    url,
    name:          profile?.name        || '',
    description:   profile?.description || '',
    icon_url:      profile?.icon_url    || '',
    status:        'online',
    addedAt:       new Date().toISOString(),
    lastCheckedAt: new Date().toISOString(),
  });

  saveServers(list);
  urlInput.value = '';
  setMsg(addMsg, '추가했습니다.', 'ok');
  addBtn.disabled = false;
  renderAll();
});

// ── Init ──────────────────────────────────────────────────────

renderAll();
