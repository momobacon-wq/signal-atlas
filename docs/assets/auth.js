/* AMS 解析網頁 — 登入閘門（員工代號，對照 Google 試算表 Users 分頁；登入／造訪寫入 AMS_Log）
 *
 * 運作：index.html 在 core.js 之前載入本檔；app.js 的 boot() 會先 await window.AMSAuth.ready()。
 *  1. 讀 auth-config.json（<meta name="ams-auth-config" content="路徑" data-site="站名">；預設 auth-config.json）
 *     {"endpoint": "https://script.google.com/macros/s/.../exec", "sessionHours": 12, "title": "..."}
 *     endpoint 空白（或設定檔 404）＝ 閘門關閉，網站照舊（console 會提示）；設定檔存在但格式錯誤 ＝ 鎖住並顯示錯誤（fail closed）。
 *  2. localStorage 'ams.auth' 有未過期的工作階段 → 向 endpoint 送 resume（記一筆 VISIT；30 分鐘內驗證過就不再送）→ 通過。
 *     endpoint 連不上／逾時／回 5xx／回覆 transient 錯誤時，沿用快取的工作階段放行（軟性閘門）；伺服器明確回 ok:false 才重新登入。
 *  3. 否則顯示全螢幕登入表單：員工代號 → endpoint login → 成功記 LOGIN 並存工作階段（姓名由 Users 分頁帶出）。
 *  注意：這是「軟性」閘門——資料檔仍是公開的靜態檔案，閘門只擋一般瀏覽並留下登入紀錄，不是資安防線。
 *  端點以 text/plain 送 JSON（避免 CORS preflight，Apps Script 網頁應用程式的標準做法；302 到 script.googleusercontent.com 由 fetch 自動跟隨）。
 */
'use strict';
(function () {
  const KEY = 'ams.auth';
  const LOGIN_TIMEOUT_MS = 15000;   // Apps Script 冷啟動可達 5–8 秒
  const RESUME_TIMEOUT_MS = 6000;   // resume 只是補紀錄，逾時就沿用快取放行
  const REVERIFY_MS = 30 * 60 * 1000;
  const meta = document.querySelector('meta[name="ams-auth-config"]');
  const CONFIG_URL = (meta && meta.getAttribute('content')) || 'auth-config.json';
  const SITE = (meta && meta.getAttribute('data-site')) || document.title || 'AMS';
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = (s) => { s = String(s == null ? '' : s); try { s = s.normalize('NFKC'); } catch (e) { /* old engines */ } return s.replace(/\s+/g, '').trim(); };
  const A = (window.AMSAuth = { user: null, config: null, enabled: false });

  function load() { try { const j = JSON.parse(localStorage.getItem(KEY) || 'null'); return j && j.id && j.token ? j : null; } catch (e) { return null; } }
  function save(s) { try { if (s) localStorage.setItem(KEY, JSON.stringify(s)); else localStorage.removeItem(KEY); } catch (e) { /* 私密模式 */ } }
  function payload(action, extra) {
    return JSON.stringify(Object.assign({ action, site: SITE, page: location.hash.slice(0, 200), ua: navigator.userAgent.slice(0, 200) }, extra || {}));
  }

  async function call(action, extra, timeoutMs) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs || LOGIN_TIMEOUT_MS);
    try {
      const r = await fetch(A.config.endpoint, {
        method: 'POST', mode: 'cors', redirect: 'follow', signal: ctl.signal,
        headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body: payload(action, extra),
      });
      if (!r.ok) throw new Error('端點錯誤 HTTP ' + r.status);
      const txt = await r.text();
      let j = null;
      try { j = JSON.parse(txt); } catch (e) { throw new Error('端點回應不是 JSON'); }
      if (!j || typeof j !== 'object') throw new Error('端點回應格式錯誤');
      if (j.transient) throw new Error(j.error || '端點暫時無法服務'); // 伺服器內部錯誤：視同連不上
      return j;
    } finally { clearTimeout(t); }
  }

  /* ---------------- 登入畫面 ---------------- */
  let overlay = null;
  const INERT_SEL = '.app-header, .app-body, #detail, #legend-pop, #toast, .skip-link, #topbar-progress';
  function setInert(on) {
    document.querySelectorAll(INERT_SEL).forEach((el) => {
      if ('inert' in el) el.inert = on;
      if (on) el.setAttribute('aria-hidden', 'true'); else el.removeAttribute('aria-hidden');
    });
  }
  function focusables() {
    return Array.from(overlay.querySelectorAll('input, button, a[href]')).filter((el) => !el.disabled && el.offsetParent !== null);
  }
  function trapTab(e) {
    if (e.key !== 'Tab' || !overlay) return;
    const f = focusables(); if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) { e.preventDefault(); first.focus(); }
  }
  function showLogin(msg, prefill, fatal) {
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'auth-gate';
      overlay.className = 'auth-gate';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-labelledby', 'auth-title');
      overlay.innerHTML = `
        <form class="auth-card" autocomplete="on" novalidate>
          <div class="auth-brand"><span class="brand-mark" aria-hidden="true">AMS</span><div><h1 id="auth-title">${esc(A.config.title || '請先登入')}</h1><p class="auth-sub">${esc(A.config.subtitle || '輸入員工代號後才能瀏覽本站；登入時間會記錄在登入紀錄中。')}</p></div></div>
          <label class="auth-field"><span>員工代號</span><input id="auth-id" name="id" type="text" inputmode="numeric" autocomplete="username" autocapitalize="off" spellcheck="false" required maxlength="20" placeholder="員工代號（半形數字）"></label>
          <div id="auth-msg" class="auth-msg" role="status" aria-live="polite"></div>
          <button id="auth-submit" class="btn primary auth-btn" type="submit">登入</button>
          <p class="auth-foot">${esc(A.config.footer || '站名：' + SITE)}</p>
        </form>`;
      document.body.appendChild(overlay);
      document.body.classList.add('auth-locked');
      setInert(true);
      document.addEventListener('keydown', trapTab, true);
      overlay.querySelector('form').addEventListener('submit', onSubmit);
    }
    const m = overlay.querySelector('#auth-msg');
    m.textContent = msg || '';
    m.className = 'auth-msg' + (msg ? ' bad' : '');
    if (fatal) { overlay.querySelector('#auth-submit').disabled = true; overlay.querySelectorAll('input').forEach((i) => { i.disabled = true; }); return; }
    if (prefill) overlay.querySelector('#auth-id').value = prefill.id || '';
    setTimeout(() => { const f = overlay.querySelector('#auth-id'); if (f) f.focus(); }, 0);
  }
  function hideLogin() {
    if (overlay) { overlay.remove(); overlay = null; }
    document.removeEventListener('keydown', trapTab, true);
    setInert(false);
    document.body.classList.remove('auth-locked');
  }
  let resolveReady = null;
  let submitting = false;
  async function onSubmit(e) {
    e.preventDefault();
    if (submitting) return;
    const id = norm(overlay.querySelector('#auth-id').value);
    const btn = overlay.querySelector('#auth-submit');
    const m = overlay.querySelector('#auth-msg');
    if (!id) { m.textContent = '請輸入員工代號。'; m.className = 'auth-msg bad'; return; }
    submitting = true; btn.disabled = true; m.textContent = '驗證中…'; m.className = 'auth-msg';
    try {
      const j = await call('login', { id });
      if (j.ok) {
        const s = { id: String(j.id || id), name: String(j.name || id), token: String(j.token || ''), exp: Number(j.exp) || (Date.now() + (A.config.sessionHours || 12) * 3600e3), site: SITE, verified: Date.now() };
        save(s); A.user = s; hideLogin(); renderChip(); if (resolveReady) resolveReady();
      } else {
        m.textContent = j.error || '員工代號不在使用者清單中，請再試一次。'; m.className = 'auth-msg bad';
      }
    } catch (err) {
      m.textContent = '無法連線到登入服務：' + (err && err.name === 'AbortError' ? '逾時' : (err && err.message) || err) + '。請確認網路後重試。';
      m.className = 'auth-msg bad';
    } finally { submitting = false; btn.disabled = false; }
  }

  /* ---------------- 標頭使用者籤 ---------------- */
  function renderChip() {
    const hdr = document.querySelector('.app-header');
    if (!hdr || !A.user) return;
    let chip = document.getElementById('auth-chip');
    if (!chip) {
      chip = document.createElement('button');
      chip.id = 'auth-chip'; chip.type = 'button'; chip.className = 'auth-chip';
      chip.addEventListener('click', () => { if (confirm('登出 ' + A.user.name + '？')) A.logout(); });
      const theme = document.getElementById('btn-theme');
      if (theme) hdr.insertBefore(chip, theme); else hdr.appendChild(chip);
    }
    chip.title = '已登入：' + A.user.name + '（' + A.user.id + '）。點擊登出';
    chip.setAttribute('aria-label', chip.title);
    chip.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/></svg><span class="auth-chip-name">${esc(A.user.name)}</span>`;
  }
  A.logout = function () {
    const s = A.user || load();
    A.user = null; save(null);
    // 登出紀錄用 fire-and-forget（sendBeacon 可送 text/plain），畫面立刻回到登入
    try {
      if (s && A.config && A.config.endpoint) {
        const body = new Blob([payload('logout', { id: s.id, token: s.token })], { type: 'text/plain;charset=utf-8' });
        if (!(navigator.sendBeacon && navigator.sendBeacon(A.config.endpoint, body))) fetch(A.config.endpoint, { method: 'POST', mode: 'cors', keepalive: true, headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body }).catch(() => {});
      }
    } catch (e) { /* ignore */ }
    location.reload();
  };

  /* ---------------- 主流程 ---------------- */
  A.ready = function () {
    if (A._ready) return A._ready;
    A._ready = (async () => {
      let cfg = null, status = 0, parseError = null;
      try {
        const r = await fetch(CONFIG_URL, { cache: 'no-cache' });
        status = r.status;
        if (r.ok) { try { cfg = await r.json(); } catch (e) { parseError = e; } }
      } catch (e) { status = -1; }
      if (parseError) { // 設定檔存在但壞掉：鎖住（fail closed），避免手滑把站台整個打開
        console.error('AMSAuth: auth-config.json 無法解析，閘門鎖定', CONFIG_URL, parseError);
        A.config = {}; A.enabled = true;
        showLogin('登入設定檔格式錯誤，請聯絡站台管理者。', null, true);
        await new Promise(() => {}); // 永不放行
      }
      A.config = cfg || {};
      if (!cfg || !cfg.endpoint || !/^https?:\/\//.test(String(cfg.endpoint))) {
        A.enabled = false;
        console.warn('AMSAuth: 未設定登入端點（' + CONFIG_URL + (status > 0 ? ' HTTP ' + status : ' 讀取失敗') + '），閘門關閉');
        return;
      }
      A.enabled = true;
      const s = load();
      if (s && s.exp > Date.now()) {
        if (s.verified && Date.now() - s.verified < REVERIFY_MS) { A.user = s; renderChip(); return; } // 30 分鐘內驗證過，不再打端點
        try {
          const j = await call('resume', { id: s.id, token: s.token }, RESUME_TIMEOUT_MS);
          if (j.ok) { A.user = Object.assign(s, { name: j.name || s.name, verified: Date.now() }); save(A.user); renderChip(); return; }
          save(null); // 伺服器判定工作階段無效 → 重新登入
          await new Promise((res) => { resolveReady = res; showLogin(j.error || '工作階段已失效，請重新登入。', { name: s.name, id: s.id }); });
          return;
        } catch (e) {
          // 連不上端點／逾時／5xx：沿用快取的工作階段放行，下次造訪再驗證
          console.warn('AMSAuth: 端點無法驗證，沿用快取工作階段', e && e.message);
          A.user = s; renderChip(); return;
        }
      }
      const expired = !!s;
      save(null);
      await new Promise((res) => { resolveReady = res; showLogin(expired ? '工作階段已逾期（超過 ' + (A.config.sessionHours || 12) + ' 小時），請重新登入。' : '', s ? { name: s.name, id: s.id } : null); });
    })();
    return A._ready;
  };
  // 提早開始（不等 app.js），使用者更快看到登入畫面
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => A.ready()); else A.ready();
})();
