'use strict';

// ── Config ────────────────────────────────────────────────────
const VIDEO_DEFAULT_VOLUME = 0.5; // 0.0 ~ 1.0

/** Sprint 15: HttpOnly 세션 쿠키(동일 출처 자동 전송) + 레거시 Bearer + API_KEY */
function authHeaders() {
  const h = {};
  const jwt = localStorage.getItem('GALLERY_JWT') || '';
  if (jwt) h['Authorization'] = 'Bearer ' + jwt;
  const meta = document.querySelector('meta[name="gallery-api-key"]');
  const fromMeta = meta && meta.getAttribute('content');
  const k = (fromMeta && fromMeta.trim()) || localStorage.getItem('GALLERY_API_KEY') || '';
  if (k) h['X-API-Key'] = k;
  return h;
}
const apiKeyHeaders = authHeaders;

function fetchOpts(extra) {
  return Object.assign({ credentials: 'include' }, extra || {});
}

function currentUser() {
  try { return JSON.parse(localStorage.getItem('GALLERY_USER') || 'null'); }
  catch (_) { return null; }
}

function logoutAndRedirect() {
  fetch('/auth/logout', fetchOpts({ method: 'POST', headers: { ...authHeaders() } }))
    .catch(() => {});
  localStorage.removeItem('GALLERY_JWT');
  localStorage.removeItem('GALLERY_USER');
  location.replace('/login.html');
}

async function ensureSession() {
  const res = await fetch('/auth/whoami', fetchOpts({ headers: { ...authHeaders() } }));
  const data = await res.json().catch(() => ({}));
  if (!data.authenticated) return false;
  localStorage.setItem(
    'GALLERY_USER',
    JSON.stringify({ id: data.id, email: data.email, role: data.role })
  );
  return true;
}

// ── DOM refs ──────────────────────────────────────────────────
const qOcr  = document.getElementById('q-ocr');
const qWd14 = document.getElementById('q-wd14');
const qRam  = document.getElementById('q-ram');
const qStt  = document.getElementById('q-stt');
const ddWd14    = document.getElementById('dd-wd14');
const ddRam     = document.getElementById('dd-ram');
const btnSearch  = document.getElementById('btn-search');
const btnShuffle = document.getElementById('btn-shuffle');
const btnClear   = document.getElementById('btn-clear');
/** 검색/랜덤 결과 요약(개수·시간) — status-bar 와 분리해 사용자 스트립을 덮어쓰지 않음 */
const galleryStatusEl = document.getElementById('gallery-status');
const gridEl    = document.getElementById('grid');
const loaderEl  = document.getElementById('loader');
const emptyEl   = document.getElementById('empty');
const infoModal = document.getElementById('info-modal');
const infoClose = document.getElementById('info-close');
const infoOcrEl     = document.getElementById('info-ocr');
const infoWd14El    = document.getElementById('info-wd14');
const infoRamEl     = document.getElementById('info-ram');
const infoSttEl     = document.getElementById('info-stt');
const infoSttSection= document.getElementById('info-stt-section');

// ── State ─────────────────────────────────────────────────────
let activeFilter  = '';    // '' | 'image' | 'gif' | 'video'
let _state = {
  mode:        'browse',   // 'browse' | 'search'
  page:        1,
  totalPages:  1,
  searchParams: null,      // URLSearchParams for current search
};

// ── Utility ───────────────────────────────────────────────────
function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function setLoading(on) {
  loaderEl.style.display = on ? 'block' : 'none';
  emptyEl.style.display  = 'none';
  if (on) { gridEl.textContent = ''; paginationEl.textContent = ''; }
}

// ── Pagination ────────────────────────────────────────────────
function renderPagination(page, totalPages) {
  paginationEl.textContent = '';
  if (totalPages <= 1) return;

  const mkBtn = (label, targetPage, isActive, disabled) => {
    const b = document.createElement('button');
    b.className   = 'pg-btn' + (isActive ? ' active' : '');
    b.textContent = String(label);
    b.disabled    = disabled || false;
    if (!disabled && !isActive) {
      b.addEventListener('click', () => gotoPage(targetPage));
    }
    return b;
  };

  const ellipsis = () => {
    const s = document.createElement('span');
    s.className   = 'pg-ellipsis';
    s.textContent = '…';
    return s;
  };

  // Prev
  paginationEl.appendChild(mkBtn('‹', page - 1, false, page <= 1));

  // Page number strategy: always show 1, always show totalPages,
  // show current ± 1, fill with ellipsis
  const visible = new Set();
  visible.add(1);
  visible.add(totalPages);
  for (let p = Math.max(1, page - 1); p <= Math.min(totalPages, page + 1); p++) {
    visible.add(p);
  }
  const sorted = Array.from(visible).sort((a, b) => a - b);

  let prev = 0;
  sorted.forEach(p => {
    if (p - prev > 1) paginationEl.appendChild(ellipsis());
    paginationEl.appendChild(mkBtn(p, p, p === page, false));
    prev = p;
  });

  // Next
  paginationEl.appendChild(mkBtn('›', page + 1, false, page >= totalPages));
}

function gotoPage(p) {
  doSearch(p);
}

function setStatus(text) {
  if (galleryStatusEl) galleryStatusEl.textContent = text;
}

// ── API ───────────────────────────────────────────────────────
async function apiFetch(url) {
  const res = await fetch(url, fetchOpts({ headers: { ...authHeaders() } }));
  if (res.status === 401) {
    localStorage.removeItem('GALLERY_JWT');
    localStorage.removeItem('GALLERY_USER');
    location.replace('/login.html');
    throw new Error('인증이 만료되었습니다');
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || res.statusText);
  }
  return res.json();
}

// ── Render grid ───────────────────────────────────────────────
function makeCard(item) {
  // normalize: search returns media_id, random returns id
  const id   = item.id ?? item.media_id;
  const type = item.media_type || 'image';
  if (!id) return null;

  const card = document.createElement('div');
  card.className  = 'card';
  card.dataset.id = String(id);

  const img = document.createElement('img');
  img.src     = '/thumb/' + id;
  img.alt     = '';
  img.loading = 'lazy';
  img.onerror = () => { img.style.display = 'none'; };
  card.appendChild(img);

  if (type === 'video') {
    const badge = document.createElement('div');
    badge.className   = 'play-badge';
    badge.textContent = '▶';
    card.appendChild(badge);
  }
  if (type === 'gif') {
    const badge = document.createElement('div');
    badge.className   = 'type-badge';
    badge.textContent = 'GIF';
    card.appendChild(badge);
  }

  card.addEventListener('click', () => doInfo(id));
  return card;
}

function renderGrid(items) {
  gridEl.textContent = '';
  emptyEl.style.display = items.length === 0 ? 'block' : 'none';
  items.forEach(raw => {
    const card = makeCard(raw);
    if (card) gridEl.appendChild(card);
  });
}

// ── Card actions ──────────────────────────────────────────────
function doDownload(id) {
  const a = document.createElement('a');
  a.href     = '/media/' + id;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

async function doShare(id) {
  const url = location.origin + '/media/' + id;
  try {
    await navigator.clipboard.writeText(url);
    alert('링크가 클립보드에 복사되었습니다');
  } catch (_) {
    alert(url);
  }
}

function renderTags(container, tagStr) {
  container.textContent = '';
  const tags = tagStr ? tagStr.split(',').map(t => t.trim()).filter(Boolean) : [];
  if (!tags.length) {
    const em = document.createElement('span');
    em.className   = 'empty-val';
    em.textContent = '없음';
    container.appendChild(em);
    return;
  }
  tags.forEach(tag => {
    const pill = document.createElement('span');
    pill.className   = 'tag-pill';
    pill.textContent = tag;
    container.appendChild(pill);
  });
}

const infoMediaEl  = document.getElementById('info-media');
const paginationEl = document.getElementById('pagination');
const infoBtnDownload = document.getElementById('info-btn-download');
const infoBtnShare    = document.getElementById('info-btn-share');

let currentInfoId = null;

function closeInfoModal() {
  infoModal.classList.remove('open');
  const video = infoMediaEl.querySelector('video');
  if (video) { video.pause(); video.src = ''; }
  infoMediaEl.replaceChildren();
  currentInfoId = null;
}

async function doInfo(id) {
  try {
    const data = await apiFetch('/info/' + id);

    // media pane
    infoMediaEl.replaceChildren();
    if (data.media_type === 'video') {
      const video = document.createElement('video');
      video.src         = '/media/' + id;
      video.autoplay    = true;
      video.muted       = false;
      video.volume      = VIDEO_DEFAULT_VOLUME;
      video.loop        = true;
      video.controls    = true;
      video.playsInline = true;
      infoMediaEl.appendChild(video);
    } else {
      // image or gif — img handles both; GIF animates natively
      const img = document.createElement('img');
      img.src = '/media/' + id;
      img.alt = '';
      infoMediaEl.appendChild(img);
    }

    // text pane
    if (data.ocr_text) {
      infoOcrEl.textContent = data.ocr_text;
    } else {
      infoOcrEl.replaceChildren();
      const em = document.createElement('span');
      em.className = 'empty-val';
      em.textContent = '없음';
      infoOcrEl.appendChild(em);
    }
    renderTags(infoWd14El, data.tags);
    renderTags(infoRamEl,  data.ram_tags);

    // STT — 영상만 표시
    if (data.media_type === 'video') {
      infoSttSection.style.display = '';
      if (data.audio_text) {
        infoSttEl.textContent = data.audio_text;
      } else {
        infoSttEl.replaceChildren();
        const em = document.createElement('span');
        em.className = 'empty-val';
        em.textContent = '없음';
        infoSttEl.appendChild(em);
      }
    } else {
      infoSttSection.style.display = 'none';
    }

    currentInfoId = id;
    infoModal.classList.add('open');
  } catch (err) {
    alert('상세정보 로드 실패: ' + err.message);
  }
}

infoBtnDownload.addEventListener('click', () => { if (currentInfoId) doDownload(currentInfoId); });
infoBtnShare.addEventListener('click',    () => { if (currentInfoId) doShare(currentInfoId); });
infoClose.addEventListener('click', closeInfoModal);
infoModal.addEventListener('click', e => {
  if (e.target === infoModal) closeInfoModal();
});

// ── Search / browse ───────────────────────────────────────────
const PER_PAGE = 50;

function allEmpty() {
  return !qOcr.value.trim() && !qWd14.value.trim()
      && !qRam.value.trim()  && !qStt.value.trim();
}

async function doSearch(page) {
  closeAllDropdowns();
  if (allEmpty()) { loadRandom(); return; }

  page = page || 1;

  const params = new URLSearchParams();
  if (qOcr.value.trim())  params.set('ocr_q',  qOcr.value.trim());
  if (qWd14.value.trim()) params.set('wd14_q', qWd14.value.trim());
  if (qRam.value.trim())  params.set('ram_q',  qRam.value.trim());
  if (qStt.value.trim())  params.set('stt_q',  qStt.value.trim());
  if (activeFilter)       params.set('media_type', activeFilter);
  params.set('page',     String(page));
  params.set('per_page', String(PER_PAGE));

  _state.mode         = 'search';
  _state.searchParams = params;

  setLoading(true);
  try {
    const data = await apiFetch('/search?' + params.toString());
    const total      = data.total      ?? data.count ?? 0;
    const totalPages = data.total_pages ?? 0;
    _state.page       = page;
    _state.totalPages = totalPages;
    setLoading(false);
    renderGrid(data.results || []);
    renderPagination(page, totalPages);
    setStatus(total + '개 · ' + Math.round(data.elapsed_ms) + 'ms');
  } catch (err) {
    setLoading(false);
    setStatus('오류: ' + err.message);
  }
}

async function loadRandom() {
  _state.mode = 'browse';

  const params = new URLSearchParams();
  params.set('limit', String(PER_PAGE));
  if (activeFilter) params.set('media_type', activeFilter);

  setLoading(true);
  try {
    const data = await apiFetch('/random?' + params.toString());
    const count = data.count ?? 0;
    setLoading(false);
    renderGrid(data.results || []);
    paginationEl.textContent = '';
    setStatus('무작위 ' + count + '개');
  } catch (err) {
    setLoading(false);
    setStatus('로드 오류: ' + err.message);
  }
}


function clearAll() {
  qOcr.value = qWd14.value = qRam.value = qStt.value = '';
  closeAllDropdowns();
  setStatus('');
  loadRandom();
}

btnSearch.addEventListener('click', () => doSearch(1));
btnShuffle.addEventListener('click', loadRandom);
btnClear.addEventListener('click', clearAll);

// ── Upload ────────────────────────────────────────────
const btnUpload      = document.getElementById('btn-upload');
const fileInput      = document.getElementById('file-input');
const uploadModal    = document.getElementById('upload-modal');
const uploadFilename = document.getElementById('upload-filename');
const uploadBar      = document.getElementById('upload-progress-bar');
const uploadCounter  = document.getElementById('upload-counter');
const uploadLog      = document.getElementById('upload-log');

btnUpload.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', () => {
  if (fileInput.files && fileInput.files.length > 0) {
    startUpload(Array.from(fileInput.files));
    fileInput.value = '';
  }
});

function addUploadLog(msg, type) {
  const line = document.createElement('div');
  line.className = type ? 'log-' + type : '';
  line.textContent = msg;
  uploadLog.appendChild(line);
  uploadLog.scrollTop = uploadLog.scrollHeight;
}

function uploadSingleFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/upload');
    xhr.withCredentials = true;
    const h = authHeaders();
    if (h['Authorization']) xhr.setRequestHeader('Authorization', h['Authorization']);
    if (h['X-API-Key'])     xhr.setRequestHeader('X-API-Key', h['X-API-Key']);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
    }
    xhr.onload = () => {
      let json = {};
      try { json = JSON.parse(xhr.responseText); } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(json);
      } else {
        reject(new Error(json.detail || xhr.statusText));
      }
    };
    xhr.onerror = () => reject(new Error('네트워크 오류'));
    xhr.send(form);
  });
}

function pollUploadStatus(mediaId, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || 180000);
  return new Promise((resolve) => {
    const tick = async () => {
      if (Date.now() > deadline) {
        resolve({ state: 'timeout', error: '처리 시간 초과' });
        return;
      }
      try {
        const r = await fetch('/upload/status/' + mediaId, fetchOpts({ headers: { ...apiKeyHeaders() } }));
        if (r.ok) {
          const j = await r.json();
          if (j.state === 'indexed' || j.state === 'error') {
            resolve(j);
            return;
          }
        }
      } catch (_) {}
      setTimeout(tick, 1500);
    };
    tick();
  });
}

async function startUpload(files) {
  uploadLog.textContent = '';
  uploadModal.classList.add('open');

  let done = 0;
  const total = files.length;

  for (const file of files) {
    done++;
    const base  = (done - 1) / total * 100;
    const share = 1 / total * 100;
    uploadBar.style.width = base + '%';
    uploadFilename.textContent = file.name;
    uploadCounter.textContent = `${done} / ${total} 파일`;

    try {
      const result = await uploadSingleFile(file, (pct) => {
        uploadBar.style.width = (base + pct * share) + '%';
      });
      addUploadLog(`⏳ ${result.filename} 업로드 접수 (id: ${result.media_id}) — 처리 중...`, '');

      const status = await pollUploadStatus(result.media_id);
      if (status.state === 'indexed') {
        addUploadLog(`✓ ${result.filename} 인덱싱 완료`, 'ok');
      } else if (status.state === 'error') {
        addUploadLog(`✗ ${result.filename}: 인덱싱 실패 (${status.error || 'unknown'})`, 'err');
      } else {
        addUploadLog(`⚠ ${result.filename}: ${status.error || '상태 확인 실패'}`, 'err');
      }
    } catch (err) {
      addUploadLog(`✗ ${file.name}: ${err.message}`, 'err');
    }
  }

  uploadBar.style.width = '100%';
  uploadFilename.textContent = '완료!';
  uploadCounter.textContent = `${total} / ${total} 파일`;

  setTimeout(() => {
    uploadModal.classList.remove('open');
    loadRandom();
  }, 1800);
}

[qOcr, qWd14, qRam, qStt].forEach(inp => {
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(1); });
});

[qOcr, qWd14, qRam, qStt].forEach(inp => {
  inp.addEventListener('input', () => { if (allEmpty()) loadRandom(); });
});

// ── Autocomplete ──────────────────────────────────────────────
function closeAllDropdowns() {
  ddWd14.classList.remove('open');
  ddRam.classList.remove('open');
}

function buildDropdownItem(tag, prefix, count) {
  const item = document.createElement('div');
  item.className   = 'dropdown-item';
  item.dataset.tag = tag;

  const nameSpan = document.createElement('span');
  nameSpan.className = 'tag-name';

  // bold prefix portion, plain text for rest
  if (prefix && tag.toLowerCase().startsWith(prefix.toLowerCase())) {
    const bold = document.createElement('b');
    bold.textContent = tag.slice(0, prefix.length);
    nameSpan.appendChild(bold);
    nameSpan.appendChild(document.createTextNode(tag.slice(prefix.length)));
  } else {
    nameSpan.textContent = tag;
  }

  const countSpan = document.createElement('span');
  countSpan.className   = 'tag-count';
  countSpan.textContent = String(count);

  item.appendChild(nameSpan);
  item.appendChild(countSpan);
  return item;
}

function setupAutocomplete(input, dropdown, source) {
  const fetch_suggestions = debounce(async (prefix) => {
    if (prefix.length < 1) { dropdown.classList.remove('open'); return; }
    try {
      const data = await apiFetch(
        '/tags/suggest?q=' + encodeURIComponent(prefix) +
        '&source=' + source + '&limit=12'
      );
      const items = data.results || [];
      dropdown.textContent = '';
      if (!items.length) { dropdown.classList.remove('open'); return; }

      items.forEach((item, idx) => {
        const el = buildDropdownItem(item.tag, prefix, item.count);
        el.dataset.idx = String(idx);
        el.addEventListener('mousedown', e => {
          e.preventDefault();
          input.value = item.tag;
          dropdown.classList.remove('open');
          input.focus();
        });
        dropdown.appendChild(el);
      });

      dropdown.classList.add('open');
    } catch (_) { dropdown.classList.remove('open'); }
  }, 200);

  input.addEventListener('input', () => fetch_suggestions(input.value));

  input.addEventListener('keydown', e => {
    if (!dropdown.classList.contains('open')) return;
    const items = Array.from(dropdown.querySelectorAll('.dropdown-item'));
    const activeEl = dropdown.querySelector('.dropdown-item.active');
    let idx = activeEl ? parseInt(activeEl.dataset.idx, 10) : -1;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      idx = Math.min(idx + 1, items.length - 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      idx = Math.max(idx - 1, 0);
    } else if (e.key === 'Tab' || e.key === 'Enter') {
      if (activeEl) {
        e.preventDefault();
        input.value = activeEl.dataset.tag;
        dropdown.classList.remove('open');
      }
      return;
    } else if (e.key === 'Escape') {
      dropdown.classList.remove('open');
      return;
    } else { return; }

    items.forEach(el => el.classList.remove('active'));
    if (idx >= 0) items[idx].classList.add('active');
  });

  input.addEventListener('blur', () => {
    setTimeout(() => dropdown.classList.remove('open'), 150);
  });
}

setupAutocomplete(qWd14, ddWd14, 'wd14');
setupAutocomplete(qRam,  ddRam,  'ram');

// ── Filter pills ──────────────────────────────────────────────
document.querySelectorAll('.filter-pill').forEach(pill => {
  pill.addEventListener('click', () => {
    document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    activeFilter = pill.dataset.type;
    if (_state.mode === 'search' && !allEmpty()) {
      doSearch(1);
    } else {
      loadRandom();
    }
  });
});

// ── User strip + logout (Sprint 15) ───────────────────────────
function renderUserStrip() {
  const u = currentUser();
  if (!u) return;
  const bar = document.getElementById('status-bar');
  if (!bar || bar.dataset.userWired) return;
  bar.dataset.userWired = '1';

  const who = document.createElement('span');
  who.className = 'user-who';
  who.textContent = u.email + ' · ' + u.role;
  bar.appendChild(who);

  if (u.role === 'admin') {
    const a = document.createElement('a');
    a.href = '/admin.html';
    a.textContent = '관리';
    a.className = 'user-link';
    bar.appendChild(a);
  }

  const out = document.createElement('button');
  out.className = 'user-link';
  out.textContent = '로그아웃';
  out.addEventListener('click', logoutAndRedirect);
  bar.appendChild(out);
}

// Uploader 이상이 아니면 업로드 버튼 숨김
function gateUploadButton() {
  const u = currentUser();
  const btn = document.getElementById('btn-upload');
  if (!btn) return;
  const level = { viewer: 0, uploader: 1, moderator: 2, admin: 3 };
  if (!u || (level[u.role] ?? 0) < 1) btn.style.display = 'none';
}

// ── Init ──────────────────────────────────────────────────────
(async function boot() {
  if (!(await ensureSession())) {
    location.replace('/login.html');
    return;
  }
  renderUserStrip();
  gateUploadButton();
  loadRandom();
})();