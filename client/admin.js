'use strict';

function authHeaders(extra) {
  const h = {};
  const j = localStorage.getItem('GALLERY_JWT');
  if (j) h['Authorization'] = 'Bearer ' + j;
  return Object.assign(h, extra || {});
}

async function api(method, url, body) {
  const opts = { method, headers: authHeaders(), credentials: 'include' };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (res.status === 401) {
    localStorage.removeItem('GALLERY_JWT');
    localStorage.removeItem('GALLERY_USER');
    location.replace('/login.html');
    throw new Error('인증 만료');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

// ── Permission UI (한글 라벨 + 그룹) ─────────────────────────
const PERM_LABELS = {
  report_review: '신고 검토',
  media_hide: '미디어 숨김',
  media_delete: '미디어 삭제',
  comment_delete: '댓글 삭제',
  user_list_view: '사용자 목록 조회',
  tag_edit: '태그 편집',
  ingest_trigger: '인제스트 수동 실행',
  transfer_approve: '이전(전송) 승인',
};

const PERM_GROUPS = [
  { title: '콘텐츠 · 신고', keys: ['report_review', 'media_hide', 'media_delete', 'comment_delete'] },
  { title: '메타 · 인제스트', keys: ['tag_edit', 'ingest_trigger'] },
  { title: '사용자 · 이전', keys: ['user_list_view', 'transfer_approve'] },
];

// ── Logout ────────────────────────────────────────────────────
document.getElementById('btn-logout').addEventListener('click', async () => {
  try { await api('POST', '/auth/logout'); } catch (_) {}
  localStorage.removeItem('GALLERY_JWT');
  localStorage.removeItem('GALLERY_USER');
  location.replace('/login.html');
});

// ── 공개 가입 (OPEN_REGISTRATION) ─────────────────────────────
const regSettingsForm = document.getElementById('reg-settings-form');
const regOpenEl = document.getElementById('reg-open');
const regRoleEl = document.getElementById('reg-role');
const regSettingsMsg = document.getElementById('reg-settings-msg');
const regEnvHint = document.getElementById('reg-env-hint');

async function loadRegistrationSettings() {
  try {
    const data = await api('GET', '/admin/registration-settings');
    regOpenEl.checked = !!data.open_registration;
    regRoleEl.value = data.open_registration_role || 'viewer';
    const eff = !!data.open_registration;
    const envO = data.env_open_registration;
    const envR = data.env_open_registration_role;
    const overridden = data.database_overrides;
    regEnvHint.textContent =
      '현재 서버 적용: 공개가입 ' + (eff ? '켜짐' : '꺼짐') +
      ', 가입 시 역할 ' + (data.open_registration_role || 'viewer') +
      ' · 참고(.env 기본값): 공개가입 ' + (envO ? '켜짐' : '꺼짐') + ', 역할 ' + envR +
      (overridden ? ' — 위 적용값은 DB에 저장된 설정입니다.' : ' — 저장 시 DB에 기록되며 그때부터 적용됩니다.');
  } catch (err) {
    regEnvHint.textContent = '설정 조회 실패: ' + err.message;
  }
}

regSettingsForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  setMsg(regSettingsMsg, '저장 중…', '');
  try {
    await api('POST', '/admin/registration-settings', {
      open_registration:      regOpenEl.checked,
      open_registration_role: regRoleEl.value,
    });
    setMsg(regSettingsMsg, '저장했습니다.', 'ok');
    await loadRegistrationSettings();
  } catch (err) {
    setMsg(regSettingsMsg, '저장 실패: ' + err.message, 'err');
  }
});

// ── Anon access (Sprint 15B) ──────────────────────────────────
const anonAccessForm = document.getElementById('anon-access-form');
const anonRoleEl = document.getElementById('anon-role');
const anonAccessMsg = document.getElementById('anon-access-msg');

async function loadAnonAccess() {
  try {
    const data = await api('GET', '/admin/anon-access');
    anonRoleEl.value = data.default_anon_role || 'none';
  } catch (err) {
    setMsg(anonAccessMsg, '설정 조회 실패: ' + err.message, 'err');
  }
}

anonAccessForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  setMsg(anonAccessMsg, '저장 중…', '');
  try {
    await api('PUT', '/admin/anon-access', { default_anon_role: anonRoleEl.value });
    setMsg(anonAccessMsg, '저장했습니다.', 'ok');
  } catch (err) {
    setMsg(anonAccessMsg, '저장 실패: ' + err.message, 'err');
  }
});

// ── Invite codes ──────────────────────────────────────────────
const inviteForm  = document.getElementById('invite-form');
const inviteMsg   = document.getElementById('invite-msg');
const inviteTbody = document.querySelector('#invite-table tbody');
const inviteRoleEl = document.getElementById('invite-role');
const inviteMaxModeEl = document.getElementById('invite-max-mode');
const inviteMaxNWrap = document.getElementById('invite-max-n-wrap');
const inviteMaxNEl = document.getElementById('invite-max-n');

function inviteMaxUsesPayload() {
  const mode = inviteMaxModeEl.value;
  if (mode === '1') return 1;
  if (mode === 'inf') return null;
  const n = parseInt(String(inviteMaxNEl.value || '2'), 10);
  return Number.isFinite(n) && n >= 2 ? n : 2;
}

inviteMaxModeEl.addEventListener('change', () => {
  const mode = inviteMaxModeEl.value;
  inviteMaxNWrap.hidden = mode !== 'n';
});

function formatInviteLimit(row) {
  const uc = row.use_count != null ? Number(row.use_count) : 0;
  if (row.max_uses == null) return uc + ' / ∞';
  return uc + ' / ' + row.max_uses;
}

function setMsg(el, text, kind) {
  el.textContent = text;
  el.className = 'auth-msg' + (kind ? ' ' + kind : '');
}

async function loadInvites() {
  try {
    const data = await api('GET', '/admin/invite-codes');
    inviteTbody.textContent = '';
    (data.results || []).forEach(row => {
      const tr = document.createElement('tr');
      tr.appendChild(td(spanCode(row.code)));
      tr.appendChild(td(row.role));
      tr.appendChild(td(formatInviteLimit(row)));
      const st = document.createElement('span');
      st.className = 'status-' + row.status;
      st.textContent = row.status;
      tr.appendChild(td(st));
      tr.appendChild(td(String(row.created_by ?? '')));
      tr.appendChild(td(row.used_by ? String(row.used_by) : ''));

      const actions = document.createElement('td');
      actions.className = 'row-actions';
      if (row.status === 'active') {
        const b = document.createElement('button');
        b.textContent = '회수';
        b.addEventListener('click', () => revokeInvite(row.code));
        actions.appendChild(b);
      }
      tr.appendChild(actions);
      inviteTbody.appendChild(tr);
    });
  } catch (err) {
    setMsg(inviteMsg, '조회 실패: ' + err.message, 'err');
  }
}

function td(content) {
  const el = document.createElement('td');
  if (content instanceof Node) el.appendChild(content);
  else el.textContent = String(content);
  return el;
}

function spanCode(text) {
  const s = document.createElement('span');
  s.className = 'code-pill';
  s.textContent = text;
  return s;
}

async function revokeInvite(code) {
  if (!confirm('초대 코드 ' + code + ' 를 회수하시겠습니까?')) return;
  try {
    await api('DELETE', '/admin/invite-codes/' + encodeURIComponent(code));
    loadInvites();
  } catch (err) {
    setMsg(inviteMsg, '회수 실패: ' + err.message, 'err');
  }
}

inviteForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  setMsg(inviteMsg, '생성 중...', '');
  try {
    const maxUses = inviteMaxUsesPayload();
    const res = await api('POST', '/admin/invite-codes', {
      role:     inviteRoleEl.value,
      max_uses: maxUses,
    });
    setMsg(inviteMsg, '생성됨: ' + res.code + ' (한도: ' + (maxUses == null ? '무제한' : String(maxUses)) + ')', 'ok');
    loadInvites();
  } catch (err) {
    setMsg(inviteMsg, '생성 실패: ' + err.message, 'err');
  }
});

// ── Users list + moderator panel ────────────────────────────
const userSearchEl   = document.getElementById('user-search');
const userSearchBtn  = document.getElementById('user-search-btn');
const userListMeta   = document.getElementById('user-list-meta');
const userTbody      = document.getElementById('user-tbody');
const userPagination = document.getElementById('user-pagination');
const modDetail      = document.getElementById('mod-detail');
const modUserLine    = document.getElementById('mod-user-line');
const modPermGroups  = document.getElementById('mod-perm-groups');
const modPermHint    = document.getElementById('mod-perm-hint');
const btnSavePerms   = document.getElementById('btn-save-perms');
const roleBtnRow     = document.getElementById('role-btn-row');
const modPermSection = document.getElementById('mod-perm-section');

let page = 1;
const perPage = 20;
let searchQ = '';
let activeAdminCount = 0;
let allPermsFromApi = [];
let selectedUser = null;
let currentAdminId = null;

const btnDeactivateUser = document.getElementById('btn-deactivate-user');

function canDeactivateUser(u) {
  if (!u || !u.is_active) return false;
  if (u.id === currentAdminId) return false;
  if (u.role === 'admin' && activeAdminCount <= 1) return false;
  return true;
}

function updateDeactivateControls() {
  const show = canDeactivateUser(selectedUser);
  btnDeactivateUser.hidden = !show;
  btnDeactivateUser.disabled = !show;
}

async function deactivateUser(uid, email) {
  const label = email ? '(' + email + ')' : '';
  if (!confirm('이 사용자 ' + label + ' 를 탈퇴(비활성) 처리할까요? JWT가 즉시 무효화됩니다.')) return;
  try {
    await api('POST', '/admin/users/' + uid + '/active', { active: false });
    if (selectedUser && selectedUser.id === uid) {
      selectedUser = null;
      modDetail.style.display = 'none';
    }
    await loadUsers(page);
  } catch (err) {
    alert(err.message || String(err));
  }
}

btnDeactivateUser.addEventListener('click', () => {
  if (!selectedUser || !canDeactivateUser(selectedUser)) return;
  deactivateUser(selectedUser.id, selectedUser.email);
});

function permKeysUngrouped() {
  const inGroup = new Set();
  PERM_GROUPS.forEach(g => g.keys.forEach(k => inGroup.add(k)));
  return (allPermsFromApi || []).filter(k => !inGroup.has(k));
}

function renderPermGroups(enabled) {
  modPermGroups.textContent = '';
  const set = new Set(enabled || []);
  const canEdit = selectedUser && selectedUser.role === 'moderator';

  function addGroup(title, keys) {
    if (!keys.length) return;
    const wrap = document.createElement('div');
    wrap.className = 'perm-group';
    const h = document.createElement('div');
    h.className = 'perm-group-title';
    h.textContent = title;
    wrap.appendChild(h);
    const grid = document.createElement('div');
    grid.className = 'perm-grid perm-grid-tight';
    keys.forEach(p => {
      const label = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = p;
      cb.checked = set.has(p);
      cb.disabled = !canEdit;
      label.appendChild(cb);
      const cap = document.createElement('span');
      cap.className = 'perm-cap';
      cap.textContent = (PERM_LABELS[p] || p);
      label.appendChild(cap);
      grid.appendChild(label);
    });
    wrap.appendChild(grid);
    modPermGroups.appendChild(wrap);
  }

  PERM_GROUPS.forEach(g => addGroup(g.title, g.keys.filter(k => allPermsFromApi.includes(k))));
  const rest = permKeysUngrouped();
  if (rest.length) addGroup('기타', rest);

  modPermHint.textContent = canEdit
    ? '체크한 권한만 저장됩니다.'
    : '권한은 moderator 역할에서만 저장할 수 있습니다. 먼저 아래에서 역할을 moderator 로 바꾸세요.';
  modPermSection.style.opacity = canEdit ? '1' : '0.85';
  btnSavePerms.disabled = !canEdit;
}

function renderRoleButtons() {
  roleBtnRow.textContent = '';
  if (!selectedUser) {
    updateDeactivateControls();
    return;
  }
  const r = selectedUser.role;
  const lastAdmin = r === 'admin' && activeAdminCount <= 1;

  function mk(label, newRole, danger) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn ' + (danger ? 'btn-danger' : 'btn-role');
    b.textContent = label;
    const demoteFromAdmin = r === 'admin' && newRole !== 'admin';
    b.disabled = demoteFromAdmin && lastAdmin;
    if (b.disabled) b.title = '마지막 admin 은 강등할 수 없습니다';
    b.addEventListener('click', () => changeRole(newRole));
    roleBtnRow.appendChild(b);
  }

  if (r === 'viewer') {
    mk('uploader로', 'uploader', false);
    mk('moderator로', 'moderator', false);
    mk('admin으로', 'admin', false);
  } else if (r === 'uploader') {
    mk('viewer로', 'viewer', false);
    mk('moderator로', 'moderator', false);
    mk('admin으로', 'admin', false);
  } else if (r === 'moderator') {
    mk('viewer로', 'viewer', false);
    mk('uploader로', 'uploader', false);
    mk('admin으로', 'admin', false);
  } else if (r === 'admin') {
    mk('moderator로 강등', 'moderator', true);
    mk('uploader로 강등', 'uploader', true);
    mk('viewer로 강등', 'viewer', true);
  }
  updateDeactivateControls();
}

async function changeRole(newRole) {
  if (!selectedUser) return;
  const msg = selectedUser.role === 'admin' && newRole !== 'admin'
    ? 'admin 에서 다른 역할로 바꿉니다. 계속할까요?'
    : '역할을 ' + newRole + '(으)로 변경할까요?';
  if (!confirm(msg)) return;
  try {
    await api('PUT', '/admin/users/' + selectedUser.id + '/role', { role: newRole });
    await loadUsers(page);
    const data = await api('GET', '/admin/users/' + selectedUser.id + '/permissions');
    selectedUser = {
      id:        data.user_id,
      email:     data.email,
      role:      data.role,
      is_active: data.is_active,
    };
    modUserLine.textContent =
      'ID ' + data.user_id + ' · ' + data.email + ' · 역할: ' + data.role +
      ' · 활성: ' + (data.is_active ? '예' : '아니오');
    renderPermGridFromData(data.permissions);
    renderRoleButtons();
    updateDeactivateControls();
  } catch (err) {
    alert(err.message || String(err));
  }
}

function renderPermGridFromData(perms) {
  renderPermGroups(perms);
}

async function selectUser(uid) {
  try {
    const data = await api('GET', '/admin/users/' + uid + '/permissions');
    selectedUser = {
      id:        data.user_id,
      email:     data.email,
      role:      data.role,
      is_active: data.is_active,
    };
    modUserLine.textContent =
      'ID ' + data.user_id + ' · ' + data.email + ' · 역할: ' + data.role +
      ' · 활성: ' + (data.is_active ? '예' : '아니오');
    renderPermGridFromData(data.permissions);
    modDetail.style.display = '';
    renderRoleButtons();
    userTbody.querySelectorAll('tr').forEach(tr => {
      tr.classList.toggle('row-selected', String(selectedUser.id) === tr.dataset.userId);
    });
    modDetail.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    alert('불러오기 실패: ' + err.message);
  }
}

btnSavePerms.addEventListener('click', async () => {
  if (!selectedUser || selectedUser.role !== 'moderator') return;
  const checked = Array.from(modPermGroups.querySelectorAll('input[type=checkbox]:checked'))
    .map(cb => cb.value);
  try {
    await api('PUT', '/admin/users/' + selectedUser.id + '/permissions',
              { permissions: checked });
    alert('권한이 저장되었습니다.');
    const data = await api('GET', '/admin/users/' + selectedUser.id + '/permissions');
    renderPermGridFromData(data.permissions);
  } catch (err) {
    alert('저장 실패: ' + err.message);
  }
});

function renderPagination(total, totalPages) {
  userPagination.textContent = '';
  if (totalPages <= 1) return;
  const prev = document.createElement('button');
  prev.type = 'button';
  prev.className = 'btn btn-page';
  prev.textContent = '이전';
  prev.disabled = page <= 1;
  prev.addEventListener('click', () => { if (page > 1) { page--; loadUsers(page); } });

  const lab = document.createElement('span');
  lab.className = 'page-indicator';
  lab.textContent = ' ' + page + ' / ' + totalPages + ' ';

  const next = document.createElement('button');
  next.type = 'button';
  next.className = 'btn btn-page';
  next.textContent = '다음';
  next.disabled = page >= totalPages;
  next.addEventListener('click', () => { if (page < totalPages) { page++; loadUsers(page); } });

  userPagination.appendChild(prev);
  userPagination.appendChild(lab);
  userPagination.appendChild(next);
}

async function loadUsers(p) {
  page = p || 1;
  const qs = new URLSearchParams();
  qs.set('page', String(page));
  qs.set('per_page', String(perPage));
  if (searchQ) qs.set('q', searchQ);

  const data = await api('GET', '/admin/users?' + qs.toString());
  activeAdminCount = data.active_admin_count ?? 0;
  userListMeta.textContent =
    '전체 ' + (data.total || 0) + '명 · 활성 admin ' + activeAdminCount + '명';

  userTbody.textContent = '';
  (data.results || []).forEach(row => {
    const tr = document.createElement('tr');
    tr.dataset.userId = String(row.id);
    if (selectedUser && selectedUser.id === row.id) tr.classList.add('row-selected');
    tr.appendChild(td(String(row.id)));
    tr.appendChild(td(row.email));
    tr.appendChild(td(row.role));
    tr.appendChild(td(row.is_active ? '예' : '아니오'));
    const act = document.createElement('td');
    act.className = 'row-actions';
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = '편집';
    b.addEventListener('click', () => selectUser(row.id));
    act.appendChild(b);
    const rowForDeactivate = {
      id:        row.id,
      email:     row.email,
      role:      row.role,
      is_active: row.is_active,
    };
    if (canDeactivateUser(rowForDeactivate)) {
      const b2 = document.createElement('button');
      b2.type = 'button';
      b2.className = 'btn btn-danger';
      b2.textContent = '탈퇴';
      b2.addEventListener('click', (e) => {
        e.stopPropagation();
        deactivateUser(row.id, row.email);
      });
      act.appendChild(b2);
    }
    tr.appendChild(act);
    userTbody.appendChild(tr);
  });

  renderPagination(data.total || 0, data.total_pages || 1);

  if (selectedUser) {
    const hit = (data.results || []).find(r => r.id === selectedUser.id);
    if (hit) {
      selectedUser.is_active = hit.is_active;
      selectedUser.role = hit.role;
    }
    renderRoleButtons();
  }
}

function runSearch() {
  searchQ = (userSearchEl.value || '').trim();
  page = 1;
  loadUsers(1).catch(err => alert(err.message));
}

userSearchBtn.addEventListener('click', runSearch);
userSearchEl.addEventListener('keydown', e => {
  if (e.key === 'Enter') { e.preventDefault(); runSearch(); }
});

async function loadPermKeys() {
  const data = await api('GET', '/admin/permissions');
  allPermsFromApi = data.permissions || [];
}

// ── Init ──────────────────────────────────────────────────────
(async function initAdmin() {
  const r = await fetch('/auth/whoami', { credentials: 'include', headers: authHeaders() });
  const d = await r.json().catch(() => ({}));
  if (!d.authenticated) {
    location.replace('/login.html');
    return;
  }
  if (d.role !== 'admin') {
    alert('관리자 권한이 필요합니다');
    location.replace('/');
    return;
  }
  currentAdminId = d.id;
  localStorage.setItem('GALLERY_USER', JSON.stringify({ id: d.id, email: d.email, role: d.role }));
  try {
    await loadRegistrationSettings();
    await loadAnonAccess();
    await loadInvites();
    await loadPermKeys();
    await loadUsers(1);
  } catch (err) {
    userListMeta.textContent = '목록 로드 실패: ' + err.message;
  }
})();
