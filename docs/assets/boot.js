/* 在第一次繪製前套用主題，避免閃爍（原本是 index.html 的行內 script；CSP script-src 'self' 不允許行內程式） */
(function () {
  var t = null;
  try { t = localStorage.getItem('ams.theme'); } catch (e) { /* 私密模式 */ }
  if (t !== 'light' && t !== 'dark') {
    t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  document.documentElement.setAttribute('data-theme', t);
})();
