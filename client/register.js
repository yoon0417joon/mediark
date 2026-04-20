'use strict';

const form     = document.getElementById('register-form');
const emailEl  = document.getElementById('email');
const passEl   = document.getElementById('password');
const inviteEl = document.getElementById('invite');
const submitEl = document.getElementById('submit');
const msgEl    = document.getElementById('msg');
const titleEl     = document.getElementById('register-title');
const inviteHintEl = document.getElementById('invite-hint');

/** 서버 정책: 초대 코드 필수 여부 (기본 true, /auth/registration-options 로 갱신) */
let inviteRequired = true;

function setMsg(text, kind) {
  msgEl.textContent = text;
  msgEl.className = 'auth-msg' + (kind ? ' ' + kind : '');
}

async function loadRegistrationOptions() {
  try {
    const res = await fetch('/auth/registration-options', { credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return;
    inviteRequired = !!data.invite_required;
    if (data.open_registration) {
      if (titleEl) titleEl.textContent = '가입 (공개 가입 허용 — 초대 코드 생략 가능)';
      if (inviteHintEl) inviteHintEl.textContent = '(비워 두면 공개 가입)';
      inviteEl.removeAttribute('required');
      inviteEl.placeholder = '선택';
    } else {
      if (titleEl) titleEl.textContent = '가입 (초대 코드 필요)';
      if (inviteHintEl) inviteHintEl.textContent = '';
      inviteEl.setAttribute('required', 'required');
      inviteEl.placeholder = '';
    }
  } catch (_) {
    /* 폴백: 기존 동작(초대 필수) */
  }
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email    = emailEl.value.trim();
  const password = passEl.value;
  const inviteRaw = inviteEl.value.trim();
  if (!email || password.length < 8) {
    setMsg('이메일과 비밀번호(8자 이상)를 입력해 주세요', 'err');
    return;
  }
  if (inviteRequired && !inviteRaw) {
    setMsg('초대 코드가 필요합니다', 'err');
    return;
  }
  submitEl.disabled = true;
  setMsg('가입 중...', '');
  const body = { email, password };
  if (inviteRaw) body.invite_code = inviteRaw;
  try {
    const res = await fetch('/auth/register', {
      method:  'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMsg(data.detail || '가입 실패', 'err');
      submitEl.disabled = false;
      return;
    }
    setMsg('가입 완료 — 로그인 페이지로 이동합니다', 'ok');
    setTimeout(() => location.replace('/login.html'), 1200);
  } catch (err) {
    setMsg('네트워크 오류: ' + (err && err.message ? err.message : err), 'err');
    submitEl.disabled = false;
  }
});

loadRegistrationOptions();
