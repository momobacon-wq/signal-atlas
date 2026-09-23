/* Signal Atlas — 核心：工具、SHA-1、資料載入（分片快取）、hash 路由、標頭／頁尾、共用的 ref／badge 渲染
 * vanilla ES2018，無 build step；所有模組掛在 window.DC 之下。各頁面模組以 DC.page('v', fn) 註冊。 */
'use strict';
(function () {
  const D = (window.DC = window.DC || {});

  /* ------------------------------------------------------------------ 基本工具 */
  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  D.esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ESC[c]);
  D.$ = (sel, root) => (root || document).querySelector(sel);
  D.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  /** h(tag, attrs, ...kids)：attrs 的 text 走 textContent，事件用 onclick 等函式；永遠不用 innerHTML 放資料 */
  D.h = function (tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (v == null || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    D.append(el, kids);
    return el;
  };
  D.append = function (el, kids) {
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.appendChild(typeof kid === 'string' || typeof kid === 'number' ? document.createTextNode(String(kid)) : kid);
    }
    return el;
  };
  D.frag = (...kids) => D.append(document.createDocumentFragment(), kids);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  /** svg(tag, attrs, ...kids)：D.h 的 SVG 版（createElementNS）；class/text/on* 規則相同，其餘屬性 setAttribute */
  D.svg = function (tag, attrs, ...kids) {
    const el = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (const k in attrs) {
        const v = attrs[k];
        if (v == null || v === false) continue;
        if (k === 'text') el.textContent = v;
        else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : v);
      }
    }
    D.append(el, kids);
    return el;
  };
  /** 清空 el 再放入 kids（可含陣列／null） */
  D.set = (el, ...kids) => { el.replaceChildren(); return D.append(el, kids); };
  D.debounce = function (fn, ms) {
    let t = 0;
    return function (...a) { clearTimeout(t); t = setTimeout(() => fn.apply(this, a), ms); };
  };
  D.store = {
    get(k, d) { try { const v = localStorage.getItem('atlas.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('atlas.' + k, JSON.stringify(v)); } catch (e) { /* 私密模式 */ } },
  };
  D.int = (n) => (n == null || n === '' ? '' : Number(n).toLocaleString('en-US'));
  D.blank = (v) => v === null || v === undefined || v === '';
  D.val = (v) => (D.blank(v) ? '—' : String(v));
  D.yieldMain = () => new Promise((r) => setTimeout(r, 0));

  /* ------------------------------------------------------------------ hash 編碼 */
  // 路徑片段只跳脫會破壞 hash 解析的字元（% ? # & 空白），訊號名保持可讀
  D.enc = (s) => String(s).replace(/[%?#& ]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase());
  D.dec = (s) => { try { return decodeURIComponent(s); } catch (e) { return s; } };
  D.hrefV = (full) => '#/v/' + D.enc(full);
  D.hrefT = (full, dir, hops) => '#/t/' + D.enc(full) + '?dir=' + (dir || 'up') + '&hops=' + (hops || 3);
  D.hrefB = (ctrl, path) => '#/b/' + D.enc(ctrl) + '/' + path.split('/').map(D.enc).join('/');
  D.hrefBKey = (key) => { const i = key.indexOf('|'); return D.hrefB(key.slice(0, i), key.slice(i + 1)); };
  D.hrefP = (ctrl, prog) => '#/p/' + D.enc(ctrl) + (prog ? '?prog=' + encodeURIComponent(prog) : '');
  D.hrefIO = (ctrl, mod) => '#/io/' + D.enc(ctrl) + (mod ? '/' + D.enc(mod) : '');
  D.hrefS = (screen) => '#/s/' + D.enc(screen);
  D.hrefA = (ctrl) => '#/a/' + D.enc(ctrl);
  D.hrefQ = (q, ctrl) => '#/?q=' + encodeURIComponent(q) + (ctrl ? '&c=' + encodeURIComponent(ctrl) : '');
  /** Task 邏輯圖 #/d/<CTRL>/<Program>/<Task>?…（query 物件：值為 null/'' 者省略） */
  D.hrefD = function (ctrl, program, task, query) {
    let h = '#/d/' + D.enc(ctrl) + '/' + D.enc(program) + '/' + D.enc(task);
    const qs = [];
    if (query) for (const k in query) { const v = query[k]; if (v != null && v !== '' && v !== false) qs.push(k + '=' + encodeURIComponent(String(v))); }
    return qs.length ? h + '?' + qs.join('&') : h;
  };
  /** 訊號圖 #/g/<CTRL.NAME>?up=N&down=N */
  D.hrefG = (full, up, down) => '#/g/' + D.enc(full) + '?up=' + (up == null ? 2 : up) + '&down=' + (down == null ? 2 : down);
  /** block key 'CTRL|Program/Task/…/Block' → 所屬 task key 'CTRL|Program/Task'（前兩段） */
  D.taskKeyOf = function (key) {
    const i = String(key).indexOf('|');
    const path = i < 0 ? String(key) : key.slice(i + 1);
    return (i < 0 ? '' : key.slice(0, i + 1)) + path.split('/').slice(0, 2).join('/');
  };
  D.splitFull = (full) => { const i = full.indexOf('.'); return i < 0 ? [full, ''] : [full.slice(0, i), full.slice(i + 1)]; };
  /** block_path = 'Program/Task/…/Block'：Task 名（第二段；只有一段時就是那一段） */
  D.taskOf = (path) => { const s = String(path || '').split('/'); return (s.length > 1 ? s[1] : s[0]) || '?'; };

  /* ------------------------------------------------------------------ SHA-1（分片定位；優先 crypto.subtle，否則純 JS） */
  function sha1Pure(bytes) {
    const ml = bytes.length;
    const padLen = (((ml + 8) >> 6) + 1) << 6;
    const buf = new Uint8Array(padLen);
    buf.set(bytes);
    buf[ml] = 0x80;
    const dv = new DataView(buf.buffer);
    dv.setUint32(padLen - 4, (ml * 8) >>> 0, false);
    dv.setUint32(padLen - 8, Math.floor((ml * 8) / 0x100000000), false);
    let h0 = 0x67452301, h1 = 0xEFCDAB89, h2 = 0x98BADCFE, h3 = 0x10325476, h4 = 0xC3D2E1F0;
    const w = new Uint32Array(80);
    const rotl = (x, n) => ((x << n) | (x >>> (32 - n))) >>> 0;
    for (let off = 0; off < padLen; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4, false);
      for (let i = 16; i < 80; i++) w[i] = rotl(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
      let a = h0, b = h1, c = h2, d = h3, e = h4;
      for (let i = 0; i < 80; i++) {
        let f, k;
        if (i < 20) { f = (b & c) | (~b & d); k = 0x5A827999; }
        else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
        else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
        else { f = b ^ c ^ d; k = 0xCA62C1D6; }
        const t = (rotl(a, 5) + (f >>> 0) + e + k + w[i]) >>> 0;
        e = d; d = c; c = rotl(b, 30); b = a; a = t;
      }
      h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0; h4 = (h4 + e) >>> 0;
    }
    return [h0, h1, h2, h3, h4].map((x) => ('00000000' + x.toString(16)).slice(-8)).join('');
  }
  const hexOf = (ab) => Array.from(new Uint8Array(ab), (b) => ('0' + b.toString(16)).slice(-2)).join('');
  const shaCache = new Map();
  /** sha1(str) → Promise<hex>；結果快取（同一訊號重複查） */
  D.sha1 = async function (str) {
    if (shaCache.has(str)) return shaCache.get(str);
    const bytes = new TextEncoder().encode(str);
    let hex;
    if (window.crypto && crypto.subtle && crypto.subtle.digest) {
      try { hex = hexOf(await crypto.subtle.digest('SHA-1', bytes)); } catch (e) { hex = sha1Pure(bytes); }
    } else hex = sha1Pure(bytes);
    if (shaCache.size > 5000) shaCache.clear();
    shaCache.set(str, hex);
    return hex;
  };
  D.sha1Pure = (str) => sha1Pure(new TextEncoder().encode(str));

  /* ------------------------------------------------------------------ 資料載入 */
  D.man = null;           // manifest
  D.meta = null;          // data/meta.json（{enc,gzip,kdf,check,build}）；404 → {enc:0}（未加密的舊版匯出）
  D.key = null;           // AES-GCM CryptoKey（解鎖後）
  D.v = '';               // '?v=<build>'
  const KEY_STORE = 'atlas.key';
  const utf8 = (s) => new TextEncoder().encode(s);
  const b64enc = (u8) => { let s = ''; for (const c of u8) s += String.fromCharCode(c); return btoa(s); };
  const b64dec = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const stampBuild = () => { const m = D.$('meta[name="dcdas-build"], meta[name="atlas-build"]'); return (m && m.getAttribute('content')) || ''; };
  D.cryptoOK = () => !!(window.crypto && crypto.subtle && typeof crypto.subtle.deriveKey === 'function' && window.DecompressionStream && window.TextDecoder);

  const jsonCache = new Map(); // url -> Promise<obj|null>
  D.fetchCount = 0;
  D.decryptMs = 0;         // 解密＋解壓累計（效能觀察）
  async function fetchBin(rel, signal) {
    const r = await fetch('data/' + rel + '.bin' + D.v, { signal });
    D.fetchCount++;
    if (r.status === 404) return null;
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + rel);
    const buf = new Uint8Array(await r.arrayBuffer());
    const t0 = performance.now();
    let plain;
    try {
      plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.subarray(0, 12), additionalData: utf8(rel) }, D.key, buf.subarray(12));
    } catch (e) { throw new Error('解密失敗：' + rel + '（密語或資料版本不符，請「清除密語」後重新輸入）'); }
    let ab = plain;
    if (D.meta.gzip !== 0) ab = await new Response(new Blob([plain]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    const obj = JSON.parse(new TextDecoder().decode(ab));
    D.decryptMs += performance.now() - t0;
    return obj;
  }
  async function fetchPlain(rel, signal) {
    const r = await fetch('data/' + rel + D.v, { signal });
    D.fetchCount++;
    if (r.status === 404) return null;
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + rel);
    return r.json();
  }
  /** 取 JSON（加密匯出：<rel>.bin → AES-GCM(AAD=rel) → gunzip）；404 → null；其他錯誤 throw。signal 中止時不留在快取 */
  D.json = function (rel, signal) {
    const url = rel + D.v;
    if (jsonCache.has(url)) return jsonCache.get(url);
    const p = D.meta && D.meta.enc ? fetchBin(rel, signal) : fetchPlain(rel, signal);
    jsonCache.set(url, p);
    p.catch(() => jsonCache.delete(url));
    return p;
  };
  D.loadMeta = async function () {
    const b = stampBuild();
    const r = await fetch('data/meta.json' + (b ? '?v=' + b : ''));
    if (r.status === 404) { D.meta = { enc: 0 }; return D.meta; }
    if (!r.ok) throw new Error('meta.json HTTP ' + r.status);
    D.meta = await r.json();
    if (D.meta.enc && (!D.meta.kdf || !D.meta.check)) throw new Error('meta.json 缺少 kdf/check');
    return D.meta;
  };
  D.loadManifest = async function () {
    const b = (D.meta && D.meta.build) || stampBuild();
    D.v = b ? '?v=' + b : '';
    const man = await D.json('manifest.json');
    if (!man) throw new Error('找不到 manifest.json');
    D.man = man;
    D.v = man.build ? '?v=' + man.build : D.v;
    D.ctrls = (man.controllers || []).map((c) => c.name);
    return man;
  };

  /* ------------------------------------------------------------------ 密語 → 金鑰（PBKDF2-HMAC-SHA-256 → AES-256-GCM） */
  async function verifyKey(key) {
    try {
      const buf = b64dec(D.meta.check);
      const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: buf.subarray(0, 12), additionalData: utf8('check') }, key, buf.subarray(12));
      return new TextDecoder().decode(pt) === 'signal-atlas-ok';
    } catch (e) { return false; }
  }
  async function deriveKey(pass) {
    const kdf = D.meta.kdf;
    const km = await crypto.subtle.importKey('raw', utf8(pass), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey({ name: 'PBKDF2', hash: kdf.hash || 'SHA-256', salt: b64dec(kdf.salt), iterations: Number(kdf.iter) || 200000 }, km,
      { name: 'AES-GCM', length: 256 }, true, ['decrypt']);
  }
  D.forgetKey = function () { try { localStorage.removeItem(KEY_STORE); } catch (e) { /* ignore */ } location.reload(); };
  /** 解鎖：先試 localStorage 記住的金鑰，否則顯示密語視窗；resolve 後 D.key 可用 */
  D.unlock = async function () {
    let stored = null;
    try { stored = localStorage.getItem(KEY_STORE); } catch (e) { /* 私密模式 */ }
    if (stored) {
      try {
        const key = await crypto.subtle.importKey('raw', b64dec(stored), { name: 'AES-GCM' }, true, ['decrypt']);
        if (await verifyKey(key)) { D.key = key; return; }
      } catch (e) { /* 壞掉的儲存值 */ }
      try { localStorage.removeItem(KEY_STORE); } catch (e) { /* ignore */ }
    }
    await new Promise((resolve) => showKeyModal(resolve));
  };
  function showKeyModal(resolve) {
    const input = D.h('input', { id: 'key-pass', name: 'passphrase', type: 'password', autocomplete: 'current-password', autocapitalize: 'off', spellcheck: 'false', required: true, placeholder: '密語' });
    const remember = D.h('input', { id: 'key-remember', type: 'checkbox', checked: true });
    const msg = D.h('div', { class: 'auth-msg', role: 'status', 'aria-live': 'polite' });
    const btn = D.h('button', { id: 'key-submit', class: 'btn primary auth-btn', type: 'submit', text: '解鎖' });
    const form = D.h('form', { class: 'auth-card key-card', autocomplete: 'on', novalidate: true },
      D.h('div', { class: 'auth-brand' }, D.h('span', { class: 'brand-mark', 'aria-hidden': 'true', text: 'SA' }),
        D.h('div', null, D.h('h1', { id: 'key-title', text: '輸入密語' }), D.h('p', { class: 'auth-sub', text: '本站資料已加密，輸入密語後才會在你的裝置上解密顯示。' }))),
      D.h('label', { class: 'auth-field' }, D.h('span', { text: '密語' }), input),
      D.h('label', { class: 'key-remember' }, remember, ' 記住此裝置（金鑰存在此瀏覽器，不再詢問）'),
      msg, btn,
      D.h('p', { class: 'auth-foot', text: '密語請向站台管理者索取' }));
    const overlay = D.h('div', { id: 'key-gate', class: 'auth-gate key-gate', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'key-title' }, form);
    let busy = false;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (busy) return;
      const pass = input.value;
      if (!pass) { msg.textContent = '請輸入密語。'; msg.className = 'auth-msg bad'; input.focus(); return; }
      busy = true; btn.disabled = true; msg.textContent = '驗證中…'; msg.className = 'auth-msg';
      try {
        const key = await deriveKey(pass);
        if (await verifyKey(key)) {
          D.key = key;
          if (remember.checked) { try { localStorage.setItem(KEY_STORE, b64enc(new Uint8Array(await crypto.subtle.exportKey('raw', key)))); } catch (e2) { /* ignore */ } }
          overlay.remove(); document.body.classList.remove('auth-locked');
          resolve();
          return;
        }
        msg.textContent = '密語不正確'; msg.className = 'auth-msg bad'; input.select();
      } catch (err) {
        msg.textContent = '無法驗證：' + ((err && err.message) || err); msg.className = 'auth-msg bad';
      } finally { busy = false; btn.disabled = false; }
    });
    document.body.appendChild(overlay);
    document.body.classList.add('auth-locked');
    setTimeout(() => input.focus(), 0);
  }
  /** 訊號卡：var/<sha1(full)[:3]>.json → 該訊號的紀錄或 null */
  D.varCard = async function (full, signal) {
    const hh = (await D.sha1(full)).slice(0, 3);
    const obj = await D.json('var/' + hh + '.json', signal);
    return obj && obj.v && obj.v[full] ? obj.v[full] : null;
  };
  /** Task 檔：task/<sha1('CTRL|Program/Task')[:3]>.json → {n, b:{key:record,…}}（b 依文件順序，首鍵是 kind:"task" 根）或 null */
  D.task = async function (tkey, signal) {
    const hh = (await D.sha1(tkey)).slice(0, 3);
    const obj = await D.json('task/' + hh + '.json', signal);
    return obj && obj.t && obj.t[tkey] ? obj.t[tkey] : null;
  };
  /** 方塊：key = 'CTRL|block_path'；在所屬 task 檔內查（同 task 的方塊共用一次抓取） */
  D.block = async function (key, signal) {
    const t = await D.task(D.taskKeyOf(key), signal);
    return (t && t.b && t.b[key]) || null;
  };
  /** 延遲載入 assets/<name>（?v= 用 atlas-build 的 data-app）；同名只載一次 */
  const scriptCache = new Map();
  D.loadScript = function (name) {
    if (scriptCache.has(name)) return scriptCache.get(name);
    const meta = D.$('meta[name="dcdas-build"], meta[name="atlas-build"]');
    const app = (meta && meta.getAttribute('data-app')) || '';
    const p = new Promise((resolve, reject) => {
      const el = document.createElement('script');
      el.src = 'assets/' + name + (app ? '?v=' + app : '');
      el.async = true;
      el.onload = () => resolve();
      el.onerror = () => reject(new Error('無法載入 ' + name));
      document.head.appendChild(el);
    });
    scriptCache.set(name, p);
    p.catch(() => scriptCache.delete(name));
    return p;
  };
  D.screen = async function (name) {
    const hh = (await D.sha1(name)).slice(0, 2);
    const obj = await D.json('screen/' + hh + '.json');
    return obj && obj.s && obj.s[name] ? obj.s[name] : null;
  };
  D.program = (ctrl) => D.json('program/' + ctrl + '.json');
  D.io = (ctrl) => D.json('io/' + ctrl + '.json');
  D.alarm = (ctrl) => D.json('alarm/' + ctrl + '.json');
  D.screens = () => D.json('screens.json');
  /** 搜尋索引 names/<CTRL>.json（快取）；onProgress(done,total) */
  const namesCache = new Map();
  D.names = async function (ctrls, onProgress) {
    const out = [];
    let done = 0;
    for (const c of ctrls) {
      if (!namesCache.has(c)) {
        const obj = await D.json('names/' + c + '.json');
        namesCache.set(c, obj && obj.rows ? obj.rows : []);
      }
      out.push([c, namesCache.get(c)]);
      done++;
      if (onProgress) onProgress(done, ctrls.length);
      if (ctrls.length > 1) await D.yieldMain();
    }
    return out;
  };
  D.namesLoaded = (c) => namesCache.has(c);

  /* ------------------------------------------------------------------ UI 小件 */
  let toastT = 0;
  D.toast = function (msg) {
    const el = D.$('#toast');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
    clearTimeout(toastT);
    toastT = setTimeout(() => { el.hidden = true; }, 2200);
  };
  D.copy = async function (text, label) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(text);
      else {
        const ta = D.h('textarea', { style: { position: 'fixed', opacity: '0' } }); ta.value = text;
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      }
      D.toast('已複製' + (label ? '：' + label : ''));
    } catch (e) { D.toast('無法複製（瀏覽器不允許）'); }
  };
  D.progress = function (p) { // 0..1 或 null 關閉
    const bar = D.$('#topbar-progress');
    if (!bar) return;
    if (p == null) { bar.hidden = true; bar.firstElementChild.style.width = '0'; return; }
    bar.hidden = false; bar.firstElementChild.style.width = Math.round(p * 100) + '%';
  };
  D.setTitle = (t) => { document.title = (t ? t + ' — ' : '') + 'Signal Atlas — 訊號邏輯查詢'; };
  D.link = (href, text, cls) => D.h('a', { href, class: cls || 'lk', text });
  D.mono = (text, cls) => D.h('span', { class: 'mono' + (cls ? ' ' + cls : ''), text });
  D.tag = (text, cls) => D.h('span', { class: 'tag' + (cls ? ' ' + cls : ''), text });
  D.kv = (k, v) => D.h('tr', null, D.h('th', { text: k }), D.h('td', null, v == null ? '—' : v));
  /** 可摺疊區段：<details class="sec"><summary>標題 <count></summary>body</details> */
  D.section = function (title, body, opt) {
    opt = opt || {};
    const sum = D.h('summary', null, D.h('span', { class: 'sec-title', text: title }),
      opt.count != null ? D.h('span', { class: 'sec-n', text: String(opt.count) }) : null,
      opt.note ? D.h('span', { class: 'sec-note', text: opt.note }) : null);
    const det = D.h('details', { class: 'sec' + (opt.cls ? ' ' + opt.cls : ''), open: opt.open !== false }, sum, D.h('div', { class: 'sec-body' }, body));
    return det;
  };
  D.table = function (heads, rows, cls) {
    const thead = D.h('thead', null, D.h('tr', null, heads.map((t) => D.h('th', { text: t }))));
    const tbody = D.h('tbody', null, rows.map((r) => (r instanceof Node ? r : D.h('tr', null, r.map((c) => (c && c.nodeType === 1 && c.tagName === 'TD' ? c : D.h('td', null, c == null ? '—' : c)))))));
    return D.h('div', { class: 'tw' }, D.h('table', { class: 'mini' + (cls ? ' ' + cls : '') }, thead, tbody));
  };
  D.empty = (msg) => D.h('p', { class: 'muted empty', text: msg || '無' });
  D.errorBox = (title, msg) => D.h('div', { class: 'error-box' }, D.h('h2', { text: title }), msg ? D.h('p', { text: msg }) : null);
  D.loading = (msg) => D.h('div', { class: 'loading-box' }, D.h('div', { class: 'spinner' }), D.h('p', { text: msg || '載入中…' }));
  /** 多行文字（\n）→ 逐行 */
  D.lines = function (s) {
    const parts = String(s == null ? '' : s).split(/\r?\n/);
    return D.h('div', { class: 'lines' }, parts.map((p) => D.h('div', { text: p || ' ' })));
  };

  /* ------------------------------------------------------------------ 徽章：方向 / 來源 / 旗標 */
  D.DIR_LABEL = { I: '輸入', O: '輸出', S: '狀態/常數', C: '常數', '?': '方向未知' };
  D.SRC_LABEL = { U: '介面腳 Usage', T: '手冊表/人工覆寫', C: '常數規則', L: '連線投票', H: '命名慣例', R: '回推', '-': '無' };
  D.dirBadge = function (dir) {
    dir = dir || '?';
    return D.h('span', { class: 'bd dir dir-' + (dir === '?' ? 'q' : dir), text: dir, title: '方向：' + (D.DIR_LABEL[dir] || dir) });
  };
  D.srcBadge = function (src) {
    src = src || '-';
    const legend = (D.man && D.man.dir_legend && D.man.dir_legend[src]) || D.SRC_LABEL[src] || src;
    const inferred = src === 'L' || src === 'H' || src === 'R';
    return D.h('span', { class: 'bd src src-' + (src === '-' ? 'none' : src) + (inferred ? ' inferred' : ''), text: src, title: '來源：' + legend + (inferred ? '（推斷）' : '') });
  };
  /* ---- 不透明巨集（介面在加密區）：回推腳位 org（tuple 第 12 欄 'd' 宣告／'l' 連線／'p' 同編號配對／null 明文）、鎖頭圖示、介面說明行、程式庫目錄 */
  D.ORG_LABEL = { d: '宣告', l: '連線', p: '配對' };
  D.ORG_TITLE = { d: '由宣告變數回推', l: '由 L: 連線回推', p: '由同層同編號的 AI／FF_AI 方塊配對推斷（IN 讀其輸出；OUT／DEVICE_STATUS 依 ai_<名>／<名>_DS 命名與 ReferencedIn 推斷）' };
  D.orgOf = (p) => (p && p.length > 11 && p[11]) || null;
  D.orgLabel = (org) => (org ? D.ORG_LABEL[org] || org : '明文');
  /** 回推腳位小徽章「推」（class org）；title 說明回推依據 */
  D.orgBadge = (org) => D.h('span', { class: 'bd org', text: '推', title: D.ORG_TITLE[org] || '回推' });
  /** 鎖頭（10×11 viewBox；鎖環＋鎖身，只描邊） */
  D.LOCK_D = 'M3 5V3.5a2 2 0 0 1 4 0V5M1.5 5h7v5.5h-7z';
  D.lockIcon = (title) => D.svg('svg', { viewBox: '0 0 10 11', class: 'lock-ico', 'aria-hidden': title ? null : 'true', role: title ? 'img' : null }, title ? D.svg('title', { text: title }) : null, D.svg('path', { d: D.LOCK_D }));
  /** manifest.lib_iface[type]：不透明巨集型別的程式庫目錄 [[pin, dir]…]（只有名稱與方向，無接線）；無 → null */
  D.libIface = (type) => (D.man && D.man.lib_iface && type && D.man.lib_iface[type]) || null;
  /** 不透明方塊的介面說明：rc=[nDecl,nLink] → 回推；否則目錄；否則無腳位。回傳 {kind:'rc'|'cat'|'none', n, text, cat} */
  D.opaqueInfo = function (rec) {
    const rc = rec && rec.rc;
    if (Array.isArray(rc)) {
      const nd = Number(rc[0]) || 0, nl = Number(rc[1]) || 0, np = Number(rc[2]) || 0, n = nd + nl + np; // [宣告, 連線, 配對]
      if (n > 0) return { kind: 'rc', n, nd, nl, np, text: '介面回推 ' + n + ' 腳（可能不完整）' };
    }
    const cat = D.libIface(rec && rec.type);
    if (cat && cat.length) return { kind: 'cat', n: cat.length, cat, text: '目錄介面 ' + cat.length + ' 腳（無接線）' };
    return { kind: 'none', n: 0, text: '介面加密，無可見腳位' };
  };
  /** 介面說明行（鎖頭 + 文字）：側欄／方塊頁共用 */
  D.opaqueLine = (rec, cls) => { const oi = D.opaqueInfo(rec); return D.h('p', { class: 'opq-line muted small' + (cls ? ' ' + cls : ''), title: oi.kind === 'rc' ? '宣告 ' + oi.nd + '、連線 ' + oi.nl + '、配對 ' + oi.np : null }, D.lockIcon(), ' ', oi.text); };
  /** 程式庫目錄表（腳位／方向）＋說明 caption */
  D.catalogueTable = (cat) => D.frag(
    D.h('p', { class: 'cat-cap muted small', text: '程式庫目錄（只有名稱與方向，接線在加密區）' }),
    D.table(['腳位', '方向'], cat.map(([pn, d]) => [D.mono(pn, 'b'), D.dirBadge(d)]), 'cat compact'));
  D.FLAGS = [[2, 'io', 'I/O', '有 I/O 端子'], [4, 'egd', 'EGD', '跨控制器 EGD'], [8, 'hmi', 'HMI', '出現在 HMI 畫面'], [16, 'alm', '警報', '有警報屬性'],
    [32, 'const', '常數', '常數'], [64, 'copy', 'EGD副本', 'EGD 副本（由他站送來）'], [128, 'enc', '加密', '在加密程式內']];
  D.flagIcons = function (flags) {
    flags = Number(flags) || 0;
    const out = [];
    for (const [bit, cls, text, title] of D.FLAGS) if (flags & bit) out.push(D.h('span', { class: 'fi fi-' + cls, text, title }));
    return out;
  };

  /* ------------------------------------------------------------------ ref 列：CTRL/Program/Task/…/Block.Pin [Type] O/T  file:line */
  /** ref = [ctrl, program, block_path, block_type, pin, src, line]；dir 由所在清單決定（w→O、r→I、u→?） */
  D.refRow = function (ref, dir, opt) {
    opt = opt || {};
    const [ctrl, program, path, btype, pin, src, line] = ref;
    const file = ctrl + '/_' + program + '.xml';
    // block_path 已以 Program 開頭（Program/Task/…/Block）；舊格式（Task 開頭）才補上 Program
    const shown = String(path || '').startsWith(program + '/') || path === program ? path : program + '/' + path;
    return D.h('div', { class: 'ref' },
      D.h('a', { href: D.hrefB(ctrl, path), class: 'ref-path mono', text: ctrl + '/' + shown }),
      D.h('span', { class: 'ref-pin mono', text: '.' + pin }),
      D.h('span', { class: 'ref-type', text: '[' + (btype || '?') + ']' }),
      D.dirBadge(dir), D.srcBadge(src),
      D.h('span', { class: 'ref-file mono muted', text: file + ':' + (line == null ? '?' : line) }),
      opt.tail || null);
  };

  /* ------------------------------------------------------------------ 路由 */
  const pages = {};
  D.page = (name, fn) => { pages[name] = fn; };
  D.route = null; // {page, segs, query, hash}
  D.parseHash = function (hash) {
    let h = (hash || location.hash || '').replace(/^#/, '');
    if (!h.startsWith('/')) h = '/' + h;
    const qi = h.indexOf('?');
    const path = qi < 0 ? h : h.slice(0, qi);
    const qs = qi < 0 ? '' : h.slice(qi + 1);
    const query = {};
    if (qs) for (const kv of qs.split('&')) { const i = kv.indexOf('='); const k = D.dec(i < 0 ? kv : kv.slice(0, i)); query[k] = D.dec(i < 0 ? '' : kv.slice(i + 1)).replace(/\+/g, ' '); }
    const segs = path.split('/').slice(1).map(D.dec);
    const page = segs.shift() || '';
    return { page, segs, query, hash: h };
  };
  let routeSeq = 0;
  D.abortCurrent = null;
  async function render() {
    const seq = ++routeSeq;
    if (D.abortCurrent) { try { D.abortCurrent.abort(); } catch (e) { /* ignore */ } }
    const ctl = new AbortController();
    D.abortCurrent = ctl;
    const rt = (D.route = D.parseHash());
    const view = D.$('#view');
    const fn = pages[rt.page] || pages['404'];
    document.body.classList.toggle('home', rt.page === '');
    document.body.classList.remove('wide');
    D.progress(null);
    try {
      await fn({ route: rt, view, signal: ctl.signal, alive: () => seq === routeSeq });
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      console.error(e);
      if (seq === routeSeq) { D.set(view, D.errorBox('載入失敗', (e && e.message) || String(e))); }
    }
    if (seq === routeSeq) { const m = D.$('#main'); if (m && rt.page !== '') m.scrollTop = 0; }
  }
  D.page('404', ({ view }) => { D.set(view, D.errorBox('找不到此頁', '路徑：' + D.route.hash), D.h('p', null, D.link('#/', '回到搜尋'))); D.setTitle('找不到此頁'); });
  D.go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };

  /* ------------------------------------------------------------------ 標頭 / 頁尾 / 主題 */
  function initHeader() {
    const t = D.$('#btn-theme');
    if (t) t.addEventListener('click', () => {
      const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', cur);
      try { localStorage.setItem('ams.theme', cur); } catch (e) { /* ignore */ }
    });
    const k = D.$('#btn-key');
    if (k) k.addEventListener('click', () => { if (confirm('清除此裝置記住的密語並重新載入？')) D.forgetKey(); });
    const s = D.$('#global-search');
    if (s) s.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { const q = s.value.trim(); if (q) { D.go(D.hrefQ(q, D.store.get('ctrl', null) === 'ALL' ? '' : D.store.get('ctrl', ''))); s.blur(); } }
    });
    document.addEventListener('keydown', (e) => { // "/" 聚焦搜尋
      if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test((e.target && e.target.tagName) || '') && !e.ctrlKey && !e.metaKey) {
        const f = D.$('#q') || s; if (f) { e.preventDefault(); f.focus(); f.select(); }
      }
    });
  }
  function renderFooter() {
    const f = D.$('#foot');
    if (!f || !D.man) return;
    const src = D.man.source || {};
    const revs = src.controllers_minor_rev || {};
    const meta = D.$('meta[name="dcdas-build"], meta[name="atlas-build"]');
    const app = meta && meta.getAttribute('data-app');
    D.set(f, 
      D.h('div', { class: 'foot-row' },
        D.h('span', null, '工具版本 ', D.mono(src.toolbox_version || '?')),
        D.h('span', null, '索引時間 ', D.mono(src.indexed_at || '?')),
        D.h('span', null, '資料建置 ', D.mono(D.man.build || '?'), app ? D.frag(' · 程式 ', D.mono(app)) : null, D.meta && D.meta.enc ? D.frag(' · ', D.h('span', { class: 'tag', text: '已加密', title: '資料以 AES-256-GCM 加密，於瀏覽器內解密' })) : null),
        D.h('span', null, D.int(D.ctrls.length) + ' 個控制器 · ' + D.int((D.man.controllers || []).reduce((a, c) => a + (c.n_vars || 0), 0)) + ' 個訊號')),
      D.h('details', { class: 'foot-rev' }, D.h('summary', { text: '各控制器 MinorRev（匯出快照，不是現場即時狀態）' }),
        D.table(['控制器', '種類', '冗餘', '版本', 'MinorRev', '訊號', '方塊', '程式', '加密', 'I/O'],
          (D.man.controllers || []).map((c) => [c.name, c.kind, c.redundancy, c.product_version, revs[c.name] || '—', D.int(c.n_vars), D.int(c.n_blocks), D.int(c.n_programs), D.int(c.n_encrypted), D.int(c.n_io)]))),
      D.h('div', { class: 'foot-row muted small' },
        D.h('span', null, '腳位方向為推斷值（U/T/C/L/H，? 未知）；加密程式只索引宣告與 EGD；本站為匯出快照。'),
        (D.man.related || []).map((r) => D.h('a', { class: 'lk', href: r.href, target: '_blank', rel: 'noopener', text: r.label }))));
  }

  /* ------------------------------------------------------------------ 啟動 */
  async function boot() {
    initHeader();
    try {
      const metaP = D.loadMeta(); // 明文，與登入閘門並行（用到 <link rel=preload>）
      metaP.catch(() => {});
      if (window.AMSAuth && typeof window.AMSAuth.ready === 'function') await window.AMSAuth.ready(); // 先員工代號閘門
      await metaP;
      if (D.meta.enc) {
        if (!D.cryptoOK()) {
          D.set(D.$('#view'), D.errorBox('瀏覽器過舊或非安全連線', '本站資料需在瀏覽器內解密，需要 Web Crypto 與 DecompressionStream：iPad/iPhone Safari 16.4+、Chrome 80+、Edge 80+、Firefox 113+，且以 https 開啟。'));
          return;
        }
        await D.unlock(); // 再密語；之後才抓任何資料
        const k = D.$('#btn-key'); if (k) k.hidden = false;
      }
      await D.loadManifest();
    } catch (e) {
      console.error(e);
      D.set(D.$('#view'), D.errorBox('無法載入資料', (e && e.message) || String(e)));
      return;
    }
    renderFooter();
    window.addEventListener('hashchange', render);
    render();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
