/* Signal Atlas — 瀏覽頁：#/p/<CTRL>?prog= 程式/Task/方塊樹、#/s 畫面清單、#/s/<screen.cim> 畫面點位、#/a/<CTRL> 警報清單 */
'use strict';
(function () {
  const D = window.DC;

  /* ------------------------------------------------------------------ 程式瀏覽 */
  D.page('p', async ({ route, view }) => {
    const ctrl = route.segs[0] || '';
    const want = route.query.prog || '';
    D.setTitle('程式 ' + ctrl + (want ? ' ' + want : ''));
    D.set(view, D.loading('載入程式樹…'));
    const tree = await D.program(ctrl);
    if (!tree) { D.set(view, D.errorBox('找不到程式資料', ctrl), D.h('p', null, D.link('#/', '回到搜尋', 'btn'))); return; }
    const progs = tree.programs || [];
    const filter = D.h('input', { type: 'search', class: 'filter', placeholder: '篩選程式名…', 'aria-label': '篩選程式', value: want && !progs.some((p) => p.name === want) ? want : '' });
    const list = D.h('div', { class: 'secs' });
    function progSection(p) {
      const tasks = p.tasks || [];
      const body = tasks.length ? tasks.map((t) => {
        const blocks = t.blocks || [];
        return D.h('details', { class: 'grp task', open: tasks.length === 1 },
          D.h('summary', null, D.h('span', { text: (t.is_task ? 'Task ' : (t.type || 'UserBlock') + ' ') }), D.h('a', { href: D.hrefB(ctrl, p.name + '/' + t.name), class: 'lk mono b', text: t.name, onclick: (e) => e.stopPropagation() }),
            t.drg ? D.frag(' ', D.tag(t.drg)) : null, D.h('span', { class: 'sec-n', text: String(blocks.length) }), ' ', D.h('a', { href: D.hrefD(ctrl, p.name, t.name), class: 'lk small', text: '圖', title: '邏輯方塊圖', onclick: (e) => e.stopPropagation() }), D.h('span', { class: 'muted small mono', text: ' :' + (t.line == null ? '?' : t.line) })),
          D.h('div', { class: 'grp-body' }, blocks.length ? D.table(['方塊', '型式', '種類', ''], blocks.filter((r) => r[3] !== 'task').map(([k, name, type, kind, opaque]) => [D.h('a', { href: D.hrefBKey(k), class: 'lk mono', text: name }), D.mono(type || ''), kind || '', opaque ? D.tag('不透明', 'warn') : ''])) : D.empty('無方塊')));
      }) : D.h('p', { class: 'muted' }, p.enc ? D.h('span', { class: 'warn-text', text: '加密 — 無法追蹤：此程式內容加密，只索引變數宣告與 EGD。' }) : '沒有 task。');
      const det = D.section(p.name, D.frag(
        D.h('p', { class: 'muted small' }, '檔案 ', D.mono(D.val(p.file)), p.lib ? D.frag(' · 函式庫 ', D.mono(p.lib)) : null, ' · ', D.int(p.n_tasks || 0), ' 個 task · ', D.int(p.n_blocks || 0), ' 個方塊', p.help ? D.frag(' · 說明檔 ', D.mono(p.help)) : null),
        body), { count: p.n_blocks || 0, open: p.name === want, note: p.enc ? '加密' : (p.lib ? '函式庫 ' + p.lib : ''), cls: p.enc ? 'enc' : '' });
      det.id = 'prog-' + p.name;
      return det;
    }
    function draw() {
      const q = filter.value.trim().toLowerCase();
      const items = progs.filter((p) => !q || p.name.toLowerCase().includes(q));
      D.set(list, items.length ? items.map(progSection) : D.empty(progs.length ? '沒有符合的程式' : '此控制器沒有程式資料（可能尚未匯出）'));
    }
    filter.addEventListener('input', D.debounce(draw, 120));
    const nEnc = progs.filter((p) => p.enc).length;
    D.set(view, 
      D.h('div', { class: 'ph' }, D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › ', D.mono(ctrl), ' › 程式'),
        D.h('h1', { class: 'ph-title' }, '程式瀏覽 ', D.mono(ctrl, 'b')),
        D.h('p', { class: 'ph-sub' }, D.int(progs.length), ' 個程式', nEnc ? D.frag('（', D.h('span', { class: 'warn-text', text: nEnc + ' 個加密' }), '）') : null, ' · 點程式展開 task 與方塊'),
        D.h('div', { class: 'ph-actions' }, filter)),
      want && !progs.some((p) => p.name === want) ? D.h('p', { class: 'warn-text' }, '程式清單中沒有「' + want + '」（可能是 EGD / DistributedIO / P2P 等非程式的參照，或程式尚未匯出）。') : null,
      list);
    draw();
    if (want) { const el = document.getElementById('prog-' + want); if (el) setTimeout(() => el.scrollIntoView({ block: 'start' }), 0); }
  });

  /* ------------------------------------------------------------------ 畫面 */
  D.page('s', async ({ route, view }) => {
    const name = route.segs.join('/');
    if (!name) {
      D.setTitle('HMI 畫面清單');
      D.set(view, D.loading('載入畫面清單…'));
      const s = await D.screens();
      const rows = (s && s.rows) || [];
      const filter = D.h('input', { type: 'search', class: 'filter', placeholder: '篩選畫面名 / 選單路徑…', 'aria-label': '篩選畫面' });
      const list = D.h('div');
      function draw() {
        const q = filter.value.trim().toLowerCase();
        const items = rows.filter((r) => !q || (r[0] + ' ' + (r[2] || '')).toLowerCase().includes(q));
        D.set(list, items.length ? D.table(['畫面', '點數', '選單路徑'], items.map((r) => [D.h('a', { href: D.hrefS(r[0]), class: 'lk mono', text: r[0] }), D.int(r[1]), r[2] || '—'])) : D.empty(rows.length ? '沒有符合的畫面' : '沒有畫面資料（可能尚未匯出）'));
      }
      filter.addEventListener('input', D.debounce(draw, 120));
      D.set(view, D.h('div', { class: 'ph' }, D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › HMI 畫面'), D.h('h1', { class: 'ph-title', text: 'HMI 畫面清單' }),
        D.h('p', { class: 'ph-sub' }, D.int(rows.length), ' 張畫面（CIMPLICITY .cim）'), D.h('div', { class: 'ph-actions' }, filter)), list);
      draw();
      return;
    }
    D.setTitle('畫面 ' + name);
    D.set(view, D.loading('載入畫面 ' + name + '…'));
    let sc = await D.screen(name);
    if (!sc) {
      // screen names are case-insensitive in the HMI; the export keeps the menu spelling — retry via the screen list
      const s = await D.screens();
      const hit = ((s && s.rows) || []).find((r) => r[0].toLowerCase() === name.toLowerCase());
      if (hit && hit[0] !== name) { location.replace(D.hrefS(hit[0])); return; }
    }
    if (!sc) { D.set(view, D.errorBox('找不到此畫面', name), D.h('p', null, D.link('#/s', '回到畫面清單', 'btn'))); return; }
    const pts = sc.points || [];
    D.set(view, 
      D.h('div', { class: 'ph' }, D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › ', D.link('#/s', 'HMI 畫面'), ' › 畫面'),
        D.h('h1', { class: 'ph-title mono', text: name }),
        D.h('div', { class: 'ph-sub' }, '選單路徑：', (sc.menu || []).length ? (sc.menu || []).map((m, i) => D.frag(i ? '；' : null, D.mono(m))) : '—')),
      D.h('div', { class: 'secs' }, D.section('畫面上的訊號', pts.length ? D.table(['訊號', '來源'], pts.map((p) => [D.h('a', { href: D.hrefV(p[0]), class: 'lk mono', text: p[0] }), p[1] || '—'])) : D.empty('無'), { count: pts.length })));
  });

  /* ------------------------------------------------------------------ 警報清單 */
  D.page('a', async ({ route, view }) => {
    const ctrl = route.segs[0] || '';
    D.setTitle('警報 ' + ctrl);
    D.set(view, D.loading('載入警報清單…'));
    const a = await D.alarm(ctrl);
    if (!a) { D.set(view, D.errorBox('找不到警報資料', ctrl), D.h('p', null, D.link('#/', '回到搜尋', 'btn'))); return; }
    const rows = a.rows || [];
    const MAX = 1000;
    const filter = D.h('input', { type: 'search', class: 'filter', placeholder: '篩選：名稱 / 說明 / 等級 / 區域 / 緊急度…', 'aria-label': '篩選警報', value: route.query.q || '' });
    const count = D.h('span', { class: 'muted small' });
    const list = D.h('div');
    const lc = rows.map((r) => r.map((x) => String(x == null ? '' : x).toLowerCase()).join(' \u0001 '));
    function draw() {
      const q = filter.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const items = [];
      for (let i = 0; i < rows.length; i++) { let ok = true; for (const t of q) if (!lc[i].includes(t)) { ok = false; break; } if (ok) items.push(rows[i]); }
      count.textContent = items.length > MAX ? '共 ' + D.int(items.length) + ' 筆，顯示前 ' + MAX + ' 筆' : '共 ' + D.int(items.length) + ' 筆';
      D.set(list, items.length ? D.table(['訊號', '說明', '等級', '定義', '區域', '緊急度'], items.slice(0, MAX).map((r) => [
        D.h('a', { href: D.hrefV(ctrl + '.' + r[0]), class: 'lk mono', text: r[0] }), r[1] || '—', r[2] ? D.tag(r[2]) : '—', r[3] || '—', D.h('span', { class: 'small', text: r[4] || '—' }), r[5] || '—']), 'alarms') : D.empty('沒有符合的警報'));
    }
    filter.addEventListener('input', D.debounce(draw, 150));
    D.set(view, 
      D.h('div', { class: 'ph' }, D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › ', D.mono(ctrl), ' › 警報'),
        D.h('h1', { class: 'ph-title' }, '警報清單 ', D.mono(ctrl, 'b')), D.h('p', { class: 'ph-sub' }, D.int(rows.length), ' 個有警報屬性的訊號'),
        D.h('div', { class: 'ph-actions' }, filter, count)),
      list);
    draw();
  });
})();
