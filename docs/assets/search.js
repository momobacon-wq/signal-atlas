/* Signal Atlas — 搜尋頁 #/?q=&c=：names/<CTRL>.json 前端子字串搜尋，排序 exact name > name prefix > alias > name substring > alias substring > desc */
'use strict';
(function () {
  const D = window.DC;
  const MAX = 500;
  const lc = new Map(); // ctrl -> [[name, alias, desc], ...] 小寫索引

  function lowerIndex(ctrl, rows) {
    if (!lc.has(ctrl)) lc.set(ctrl, rows.map((r) => [r[0].toLowerCase(), (r[3] || '').toLowerCase(), (r[1] || '').toLowerCase()]));
    return lc.get(ctrl);
  }

  const SUB = 256; // names flags bit：警報子變數（<訊號>.<後綴>），搜尋時摺到母訊號下
  function search(sets, q, mask) {
    let tokens = q.toLowerCase().split(/\s+/).filter(Boolean);
    if (!tokens.length) return { hits: [], total: 0, folded: 0 };
    const dotted = tokens[0].includes('.'); // 查詢本身帶 '.'（例如 X.HH_SP）才直接列子變數
    let onlyCtrl = null;
    const m = /^([a-z0-9]+)\.(.+)$/.exec(tokens[0]);
    if (m && D.ctrls.some((c) => c.toLowerCase() === m[1])) { onlyCtrl = m[1]; tokens[0] = m[2]; }
    const t0 = tokens[0];
    const rest = tokens.slice(1);
    const hits = [];
    let total = 0;
    for (const [ctrl, rows] of sets) {
      if (onlyCtrl && ctrl.toLowerCase() !== onlyCtrl) continue;
      const ix = lowerIndex(ctrl, rows);
      for (let i = 0; i < ix.length; i++) {
        const [name, alias, desc] = ix[i];
        const flags = rows[i][2] || 0;
        if (mask && (flags & mask) !== mask) continue;
        let rank;
        if (name === t0) rank = 0;
        else if (name.startsWith(t0)) rank = 1;
        else if (alias && (alias === t0 || alias.startsWith(t0))) rank = 2;
        else if (name.includes(t0)) rank = 3;
        else if (alias && alias.includes(t0)) rank = 4;
        else if (desc.includes(t0)) rank = 5;
        else continue;
        if (rest.length) {
          let ok = true;
          for (const t of rest) if (!(name.includes(t) || alias.includes(t) || desc.includes(t))) { ok = false; break; }
          if (!ok) continue;
        }
        total++;
        if (hits.length < 6000 || rank < 2) hits.push([rank, ctrl, rows[i]]);
      }
    }
    // 摺疊：子變數的母訊號也在結果裡 → 併到母列（+N 警報屬性）；查詢帶 '.' 時不摺
    let folded = 0;
    if (!dotted) {
      const parents = new Map();
      for (const h of hits) if (!((h[2][2] || 0) & SUB)) parents.set(h[1] + '|' + h[2][0].toLowerCase(), h);
      const kept = [];
      for (const h of hits) {
        if ((h[2][2] || 0) & SUB) {
          const nm = h[2][0]; const k = nm.lastIndexOf('.');
          const par = k > 0 ? parents.get(h[1] + '|' + nm.slice(0, k).toLowerCase()) : null;
          if (par) { (par.subs || (par.subs = [])).push(h[2]); folded++; total--; continue; }
        }
        kept.push(h);
      }
      hits.length = 0; hits.push(...kept);
    }
    hits.sort((a, b) => a[0] - b[0] || a[2][0].length - b[2][0].length || (a[2][0] < b[2][0] ? -1 : a[2][0] > b[2][0] ? 1 : 0) || (a[1] < b[1] ? -1 : 1));
    return { hits: hits.slice(0, MAX), total, folded };
  }
  const FILTERS = [[1, '有寫入者'], [2, 'I/O'], [4, 'EGD'], [8, 'HMI'], [16, '警報'], [32, '常數'], [128, '加密']];

  function browseRow() {
    const rows = D.ctrls.map((c) => [D.mono(c), D.link(D.hrefP(c), '程式'), D.link(D.hrefIO(c), 'I/O'), D.link(D.hrefA(c), '警報')]);
    return D.h('details', { class: 'sec browse' },
      D.h('summary', null, D.h('span', { class: 'sec-title', text: '瀏覽' }), D.h('span', { class: 'sec-note', text: '程式 / Task / 方塊、I/O 機櫃、警報清單、HMI 畫面' })),
      D.h('div', { class: 'sec-body' },
        D.h('p', null, D.link('#/s', 'HMI 畫面清單（screens.json）', 'btn sm')),
        D.h('div', { class: 'browse-grid' }, D.ctrls.map((c) => D.h('div', { class: 'browse-cell' }, D.mono(c, 'b'), ' ', D.link(D.hrefP(c), '程式'), ' · ', D.link(D.hrefIO(c), 'I/O'), ' · ', D.link(D.hrefA(c), '警報'))))));
  }

  D.page('', async ({ route, view, alive }) => {
    D.setTitle('');
    const q0 = route.query.q || '';
    let ctrl = route.query.c || D.store.get('ctrl', 'ALL');
    if (ctrl !== 'ALL' && !D.ctrls.includes(ctrl)) ctrl = 'ALL';

    const input = D.h('input', { id: 'q', type: 'search', autocomplete: 'off', spellcheck: 'false', autofocus: true, value: q0,
      placeholder: '訊號名 / KKS 別名 / DeviceTag / 說明…（例：L27QE1_A、G11.L4、lube oil）', 'aria-label': '搜尋訊號' });
    const chips = D.h('div', { class: 'chips', role: 'group', 'aria-label': '控制器' });
    const fchips = D.h('div', { class: 'chips', role: 'group', 'aria-label': '篩選' });
    let mask = Number(D.store.get('fmask', 0)) || 0;
    const prog = D.h('div', { class: 'lb-bar', hidden: true }, D.h('div'));
    const status = D.h('div', { class: 'status muted small' });
    const results = D.h('div', { class: 'results' });
    const hint = D.h('p', { class: 'muted small hint' }, '排序：名稱完全相同 › 名稱開頭 › 別名 › 名稱含 › 別名含 › 說明含；多個字以空白分隔（全部都要符合）；輸入 CTRL.NAME 只查該控制器。');
    D.set(view, 
      D.h('div', { class: 'home-wrap' },
        D.h('h1', { class: 'home-title', text: 'Signal Atlas 訊號邏輯查詢' }),
        D.h('p', { class: 'muted home-sub', text: '查一個訊號在哪裡被寫、被讀、接在哪個端子、送到哪個控制器、出現在哪張畫面。' }),
        D.h('div', { class: 'bigsearch' }, input),
        chips, fchips, prog, status, results, hint, browseRow()));

    let sets = null;
    let loadSeq = 0;
    async function loadSets() {
      const seq = ++loadSeq;
      const list = ctrl === 'ALL' ? D.ctrls : [ctrl];
      const need = list.filter((c) => !D.namesLoaded(c));
      if (need.length) { prog.hidden = false; prog.firstElementChild.style.width = '0'; status.textContent = '載入索引 0 / ' + list.length + '…'; }
      const r = await D.names(list, (done, total) => {
        if (seq !== loadSeq) return;
        prog.firstElementChild.style.width = Math.round((done / total) * 100) + '%';
        status.textContent = '載入索引 ' + done + ' / ' + total + '…';
        D.progress(done / total);
      });
      if (seq !== loadSeq || !alive()) return;
      prog.hidden = true; D.progress(null);
      sets = r;
      const n = sets.reduce((a, s) => a + s[1].length, 0);
      status.textContent = (ctrl === 'ALL' ? '全部控制器' : ctrl) + '：' + D.int(n) + ' 個訊號';
      run();
    }
    function renderChips() {
      D.set(chips, 
        D.h('button', { type: 'button', class: 'chip-btn' + (ctrl === 'ALL' ? ' on' : ''), 'aria-pressed': ctrl === 'ALL' ? 'true' : 'false', text: '全部', onclick: () => pick('ALL') }),
        D.ctrls.map((c) => D.h('button', { type: 'button', class: 'chip-btn' + (ctrl === c ? ' on' : ''), 'aria-pressed': ctrl === c ? 'true' : 'false', text: c, onclick: () => pick(c) })));
    }
    function pick(c) { ctrl = c; D.store.set('ctrl', c); renderChips(); syncHash(); loadSets(); input.focus(); }
    function renderFilters() {
      D.set(fchips, D.h('span', { class: 'muted small', text: '篩選：' }),
        FILTERS.map(([bit, label]) => D.h('button', { type: 'button', class: 'chip-btn' + ((mask & bit) ? ' on' : ''), 'aria-pressed': (mask & bit) ? 'true' : 'false', text: label,
          title: '只列有此屬性的訊號（可複選，全部都要符合）', onclick: () => { mask ^= bit; D.store.set('fmask', mask); renderFilters(); run(); } })));
    }
    function syncHash() {
      const q = input.value.trim();
      const h = '#/' + (q ? '?q=' + encodeURIComponent(q) + (ctrl !== 'ALL' ? '&c=' + encodeURIComponent(ctrl) : '') : '');
      if (location.hash !== h) history.replaceState(null, '', h);
    }
    function run() {
      if (!alive()) return;
      const q = input.value.trim();
      syncHash();
      if (!sets) return;
      if (!q) { D.set(results); return; }
      const { hits, total, folded } = search(sets, q, mask);
      if (!hits.length) { D.set(results, D.empty('找不到符合「' + q + '」的訊號' + (ctrl === 'ALL' ? '' : '（目前只查 ' + ctrl + '，可改選「全部」）') + (mask ? '；試試取消篩選' : ''))); return; }
      const rowOf = (c, r, cls) => D.h('tr', { class: cls },
        D.h('td', { class: 'nowrap' }, D.mono(c)),
        D.h('td', { class: 'nowrap' }, D.h('a', { href: D.hrefV(c + '.' + r[0]), class: 'lk mono b', text: r[0] }), ' ', D.flagIcons(r[2])),
        D.h('td', null, r[1] || ''),
        D.h('td', { class: 'nowrap' }, r[3] ? D.mono(r[3]) : ''),
        D.h('td', { class: 'nowrap muted small' }, r[4] || ''));
      const rows = hits.map((h) => {
        const [rank, c, r] = h;
        const tr = rowOf(c, r, 'rk' + rank);
        if (h.subs && h.subs.length) { // 母訊號列：+N 警報屬性，點了在下面展開／收起子變數列
          const subs = h.subs.slice().sort((x, y) => (x[0] < y[0] ? -1 : 1));
          let open = false; let inserted = [];
          const btn = D.h('button', { type: 'button', class: 'chip-btn sm', text: '+' + subs.length + ' 警報屬性', title: '設定值／延時／遲滯／旗標（' + subs.map((x) => x[0].slice(x[0].lastIndexOf('.'))).join(' ') + '）',
            onclick: () => { open = !open; if (open) { inserted = subs.map((x) => rowOf(c, x, 'sub')); let after = tr; for (const s of inserted) { after.insertAdjacentElement('afterend', s); after = s; } } else { inserted.forEach((s) => s.remove()); inserted = []; } } });
          tr.children[1].append(' ', btn);
        }
        return tr;
      });
      D.set(results,
        D.h('div', { class: 'muted small rcount', text: (total > MAX ? '共 ' + D.int(total) + ' 筆，顯示前 ' + MAX + ' 筆（請輸入更精確的字）' : '共 ' + D.int(total) + ' 筆') + (folded ? '；' + D.int(folded) + ' 個警報子變數已摺到母訊號下' : '') }),
        D.table(['控制器', '訊號名', '說明', '別名', '型別'], rows, 'results-t'));
    }
    input.addEventListener('input', D.debounce(run, 150));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { const a = results.querySelector('tr.rk0 a, tr.rk1 a'); if (a && e.ctrlKey) D.go(a.getAttribute('href')); else run(); }
    });
    renderChips();
    renderFilters();
    setTimeout(() => input.focus(), 0);
    await loadSets();
  });
})();
