'use strict';

const form     = document.getElementById('login-form');
const emailEl  = document.getElementById('email');
const passEl   = document.getElementById('password');
const submitEl = document.getElementById('submit');
const msgEl    = document.getElementById('msg');

function authHeaders() {
  const j = localStorage.getItem('GALLERY_JWT');
  return j ? { Authorization: 'Bearer ' + j } : {};
}

(async () => {
  const r = await fetch('/auth/whoami', { credentials: 'include', headers: authHeaders() });
  const d = await r.json().catch(() => ({}));
  if (d.authenticated) location.replace('/');
})();

function setMsg(text, kind) {
  msgEl.textContent = text;
  msgEl.className = 'auth-msg' + (kind ? ' ' + kind : '');
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = emailEl.value.trim();
  const password = passEl.value;
  if (!email || !password) {
    setMsg('이메일과 비밀번호를 입력하세요', 'err');
    return;
  }
  submitEl.disabled = true;
  setMsg('로그인 중...', '');
  try {
    const res = await fetch('/auth/login', {
      method:  'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ email, password }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMsg(data.detail || '로그인 실패', 'err');
      submitEl.disabled = false;
      return;
    }
    localStorage.setItem('GALLERY_USER', JSON.stringify(data.user));
    if (data.access_token) {
      localStorage.setItem('GALLERY_JWT', data.access_token);
    }
    location.replace('/');
  } catch (err) {
    setMsg('네트워크 오류: ' + (err && err.message ? err.message : err), 'err');
    submitEl.disabled = false;
  }
});
