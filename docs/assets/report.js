/* Signal Atlas — 加密報告載入器（reports/*.html）
 *
 * 報告本文只以密文放在 reports/<name>.bin（12-byte IV || AES-256-GCM(gzip(html))，AAD = "reports/<name>"），
 * 金鑰與站台相同：PBKDF2-HMAC-SHA-256（salt/iter 記在 reports/<name>.json）。
 *  1. 先過登入閘門（auth.js，員工代號＋登入紀錄）。
 *  2. 試 localStorage 'atlas.key'（主站記住的金鑰）；解不開才顯示密語視窗。
 *  3. 解密 → 解壓 → 以 DOMParser 取 <style> 與 <body>，換掉載入畫面；站台 app.css 停用（報告自帶樣式）。
 * 產生密文：py tools/seal_report.py <report.html> <name>
 */
'use strict';
(function () {
  const KEY_STORE = 'atlas.key';
  const NAME = (document.querySelector('meta[name="atlas-report"]') || {}).content;
  const utf8 = (s) => new TextEncoder().encode(s);
  const b64dec = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64enc = (u) => { let s = ''; for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); return btoa(s); };
  const $ = (s) => document.querySelector(s);

  function status(text, bad) {
    const m = $('#rep-status');
    if (m) { m.textContent = text; m.className = 'auth-msg' + (bad ? ' bad' : ''); }
  }

  async function tryDecrypt(key, blob) {
    try {
      const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: blob.subarray(0, 12), additionalData: utf8('reports/' + NAME) }, key, blob.subarray(12));
      const ds = new Response(new Blob([pt]).stream().pipeThrough(new DecompressionStream('gzip')));
      return await ds.text();
    } catch (e) { return null; }
  }

  async function deriveKey(pass, kdf) {
    const km = await crypto.subtle.importKey('raw', utf8(pass), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey({ name: 'PBKDF2', hash: kdf.hash || 'SHA-256', salt: b64dec(kdf.salt), iterations: Number(kdf.iter) || 200000 }, km,
      { name: 'AES-GCM', length: 256 }, true, ['decrypt']);
  }

  function askPass(kdf, blob, siteSalt) {
    return new Promise((resolve) => {
      const form = $('#rep-form');
      const input = $('#rep-pass');
      const remember = $('#rep-remember');
      form.hidden = false;
      input.focus();
      let busy = false;
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (busy) return;
        if (!input.value) { status('請輸入密語。', true); input.focus(); return; }
        busy = true; status('驗證中…');
        try {
          const key = await deriveKey(input.value, kdf);
          const html = await tryDecrypt(key, blob);
          if (html) {
            if (remember.checked && siteSalt === kdf.salt) {
              try { localStorage.setItem(KEY_STORE, b64enc(new Uint8Array(await crypto.subtle.exportKey('raw', key)))); } catch (e2) { /* 私密模式 */ }
            }
            resolve(html);
            return;
          }
          status('密語不正確', true); input.select();
        } catch (err) {
          status('無法驗證：' + ((err && err.message) || err), true);
        } finally { busy = false; }
      });
    });
  }

  function render(html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const t = doc.querySelector('title');
    if (t) document.title = t.textContent;
    const css = $('link[data-site-css]');
    if (css) css.disabled = true;
    const chip = $('#auth-chip');
    if (chip) chip.remove();
    doc.querySelectorAll('style').forEach((s) => document.head.appendChild(document.importNode(s, true)));
    document.body.className = '';
    document.body.replaceChildren(...[...doc.body.childNodes].map((n) => document.importNode(n, true)));
    initFilter();
  }

  function initFilter() {
    const q = $('#q'), hits = $('#hits');
    if (!q) return;
    const items = [...document.querySelectorAll('#register details.claim')];
    function run() {
      const t = q.value.trim().toLowerCase();
      let n = 0;
      items.forEach((el) => {
        const ok = !t || (el.getAttribute('data-s') || '').indexOf(t) >= 0 || el.textContent.toLowerCase().indexOf(t) >= 0;
        el.hidden = !ok; if (ok) n++;
      });
      document.querySelectorAll('#register section.dom').forEach((s) => { s.hidden = !s.querySelector('details.claim:not([hidden])'); });
      hits.textContent = t ? ('符合 ' + n + ' / ' + items.length + ' 條') : ('共 ' + items.length + ' 條');
    }
    q.addEventListener('input', run); run();
  }

  async function main() {
    if (window.AMSAuth && window.AMSAuth.ready) await window.AMSAuth.ready();
    status('載入中…');
    const [meta, bin, site] = await Promise.all([
      fetch(NAME + '.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)),
      fetch(NAME + '.bin', { cache: 'no-cache' }).then((r) => (r.ok ? r.arrayBuffer() : null)),
      fetch('../data/meta.json', { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]);
    if (!meta || !bin) { status('找不到報告檔案。', true); return; }
    const blob = new Uint8Array(bin);
    let html = null;
    let stored = null;
    try { stored = localStorage.getItem(KEY_STORE); } catch (e) { /* 私密模式 */ }
    if (stored) {
      try {
        const key = await crypto.subtle.importKey('raw', b64dec(stored), { name: 'AES-GCM' }, false, ['decrypt']);
        html = await tryDecrypt(key, blob);
      } catch (e) { /* 壞掉的儲存值 */ }
    }
    if (!html) { status(''); html = await askPass(meta.kdf, blob, site && site.kdf && site.kdf.salt); }
    render(html);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', main); else main();
})();
