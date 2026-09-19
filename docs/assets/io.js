/* Signal Atlas — I/O 頁 #/io/<CTRL>[/<module>]：io/<CTRL>.json 依機櫃分組的模組清單；模組頁列端子板 → 點位表 */
'use strict';
(function () {
  const D = window.DC;

  function pointRows(pts, ctrl) {
    return (pts || []).map((p) => {
      const [name, dir, conn, tag, addr, type, lo, hi, screws, varFull] = p;
      const scr = screws && screws.length ? D.h('div', { class: 'screws' }, screws.map((s) => D.h('span', { class: 'screw mono', text: [s[0], s[1], s[2], s[3]].filter((x) => !D.blank(x)).join(' · ') }))) : '—';
      return D.h('tr', null,
        D.h('td', { class: 'nowrap' }, D.mono(name, 'b')),
        D.h('td', null, D.dirBadge(dir)),
        D.h('td', null, varFull ? D.h('a', { href: D.hrefV(varFull), class: 'lk mono', text: conn || varFull }) : (conn ? D.h('a', { href: D.hrefV(ctrl + '.' + conn), class: 'lk mono', text: conn }) : '—')),
        D.h('td', null, tag ? D.mono(tag) : '—'),
        D.h('td', { class: 'nowrap' }, addr ? D.mono(addr) : '—'),
        D.h('td', { class: 'nowrap' }, D.mono(D.val(type))),
        D.h('td', { class: 'nowrap' }, D.blank(lo) && D.blank(hi) ? '—' : D.mono(D.val(lo) + ' – ' + D.val(hi))),
        D.h('td', null, scr));
    });
  }

  D.page('io', async ({ route, view }) => {
    const ctrl = route.segs[0] || '';
    const mod = route.segs.slice(1).join('/');
    D.setTitle('I/O ' + ctrl + (mod ? ' ' + mod : ''));
    D.set(view, D.loading('載入 I/O 機櫃樹…'));
    const io = await D.io(ctrl);
    if (!io) { D.set(view, D.errorBox('找不到 I/O 資料', ctrl), D.h('p', null, D.link('#/', '回到搜尋', 'btn'))); return; }
    const modules = io.modules || [];
    const kicker = D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › ', D.mono(ctrl), ' › I/O', mod ? D.frag(' › ', D.link(D.hrefIO(ctrl), '模組清單')) : null);

    if (mod) {
      const m = modules.find((x) => x.name === mod);
      if (!m) { D.set(view, D.errorBox('找不到此模組', ctrl + ' / ' + mod), D.h('p', null, D.link(D.hrefIO(ctrl), '回到 ' + ctrl + ' 模組清單', 'btn'))); return; }
      const nPts = (m.boards || []).reduce((a, b) => a + (b.points || []).length, 0);
      const secs = (m.boards || []).map((b) => D.section('端子板 ' + (b.name || '?') + (b.hw ? '（' + b.hw + (b.pos ? ' ' + b.pos : '') + '）' : ''),
        D.table(['點', '方向', '連接變數', 'DeviceTag', '位址', '型式', '量程', '端子（名 · 號 · 電纜 · 線號）'], pointRows(b.points, ctrl), 'pts'), { count: (b.points || []).length }));
      if (m.internal && m.internal.length) {
        secs.push(D.section('內部訊號（診斷等）', D.table(['名稱', '連接', '位址', '變數'], m.internal.map((r) => [D.mono(r[0]), D.mono(D.val(r[1])), D.mono(D.val(r[2])), r[3] ? D.h('a', { href: D.hrefV(r[3]), class: 'lk mono', text: r[3] }) : '—'])), { count: m.internal.length, open: false }));
      }
      D.set(view, 
        D.h('div', { class: 'ph' }, kicker,
          D.h('h1', { class: 'ph-title mono' }, m.name, ' ', m.red ? D.tag(m.red) : null),
          D.h('p', { class: 'ph-sub' }, '機櫃 ', D.mono(D.val(m.cabinet)), ' · ID ', D.mono(D.val(m.id)), ' · IP ', D.mono(D.val(m.ip)), ' · ', D.int((m.boards || []).length), ' 個端子板 · ', D.int(nPts), ' 點')),
        D.h('div', { class: 'secs' }, secs.length ? secs : D.empty('此模組沒有端子板')));
      return;
    }

    const byCab = new Map();
    for (const m of modules) { const c = m.cabinet || '（未指定機櫃）'; if (!byCab.has(c)) byCab.set(c, []); byCab.get(c).push(m); }
    const filter = D.h('input', { type: 'search', class: 'filter', placeholder: '篩選模組 / 機櫃 / IP…', 'aria-label': '篩選模組' });
    const list = D.h('div', { class: 'secs' });
    function draw() {
      const q = filter.value.trim().toLowerCase();
      D.set(list);
      for (const [cab, mods] of byCab) {
        const rows = mods.filter((m) => !q || (m.name + ' ' + cab + ' ' + (m.id || '') + ' ' + (m.ip || '')).toLowerCase().includes(q))
          .map((m) => [D.h('a', { href: D.hrefIO(ctrl, m.name), class: 'lk mono b', text: m.name }), D.mono(D.val(m.id)), m.red || '—', D.mono(D.val(m.ip)),
            D.int((m.boards || []).length), D.int((m.boards || []).reduce((a, b) => a + (b.points || []).length, 0))]);
        if (!rows.length) continue;
        list.append(D.section('機櫃 ' + cab, D.table(['模組', 'ID', '冗餘', 'IP', '端子板', '點數'], rows), { count: rows.length }));
      }
      if (!list.childElementCount) list.append(D.empty(modules.length ? '沒有符合的模組' : '此控制器沒有 I/O 資料（可能尚未匯出或非 Mark VIe）'));
    }
    filter.addEventListener('input', D.debounce(draw, 120));
    D.set(view, 
      D.h('div', { class: 'ph' }, kicker, D.h('h1', { class: 'ph-title' }, 'I/O 機櫃樹 ', D.mono(ctrl, 'b')),
        D.h('p', { class: 'ph-sub' }, D.int(byCab.size), ' 個機櫃 · ', D.int(modules.length), ' 個模組'), D.h('div', { class: 'ph-actions' }, filter)),
      list);
    draw();
  });
})();
