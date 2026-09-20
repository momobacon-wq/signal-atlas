/* Signal Atlas — 方塊頁 #/b/<CTRL>/<block_path>：task/<sha1('CTRL|Program/Task')[:3]>.json 內查 key；task 節點另列該 task 的方塊清單 */
'use strict';
(function () {
  const D = window.DC;
  const CK = { V: '變數', L: '同 task 方塊腳', P: '介面腳（外層巨集）', D: 'device pin', N: '常數 / RUNG', E: '列舉', A: '只有位址', AV: '宣告於腳位', '-': '空' };
  /** 連線種類文字：A 且有變數 = 宣告於腳位的全域變數（索引已連結） */
  const ckLabel = (ck, varFull) => (ck === 'A' && varFull ? CK.AV : CK[ck] || '');

  /** 腳位值鏡像：task entry 的 vm = {mirror_full_name: pin_name} → Map('CTRL|block_path#pin' → [mirror_full_name…])。
   *  鏡像名慣例 CTRL.<Block>.<Pin>（GlobalNamePrefix=Block）→ 先以「方塊名.腳位名」對到方塊；對不到時，腳位名在 task 內唯一者才採用（腳位名即變數名的情形）。
   *  舊資料無 vm → null。 */
  D.mirrorMap = function (t, ctrl) {
    const vm = t && t.vm;
    if (!vm || !t.b) return null;
    const byName = new Map(), byPin = new Map();
    const push = (m, k, v) => { if (!m.has(k)) m.set(k, []); m.get(k).push(v); };
    for (const k in t.b) {
      const r = t.b[k];
      const nm = r.name || k.slice(k.lastIndexOf('/') + 1);
      for (const p of r.pins || []) { push(byName, nm + '.' + p[0], k); push(byPin, p[0], k); }
    }
    const out = new Map();
    const pre = (ctrl || String(Object.keys(t.b)[0] || '').split('|')[0]) + '.';
    for (const m in vm) {
      const pin = vm[m];
      if (!pin) continue;
      const short = m.startsWith(pre) ? m.slice(pre.length) : m;
      let ks = short.endsWith('.' + pin) ? byName.get(short) : null;
      if (!ks) { const only = byPin.get(pin); ks = only && only.length === 1 ? only : null; }
      if (!ks) continue;
      for (const k of ks) push(out, k + '#' + pin, m);
    }
    return out;
  };
  /** 「發佈為」欄：鏡像變數連結（多個以換行列出） */
  const mirrorCell = (mirrors) => (mirrors && mirrors.length
    ? D.h('div', { class: 'mirror' }, mirrors.map((m, i) => D.frag(i ? D.h('br') : null, D.h('a', { href: D.hrefV(m), class: 'lk mono small', text: m, title: '此腳位的值發佈為變數 ' + m }))))
    : '—');

  function pinRow(p, mirrors) {
    const [name, dir, src, ck, conn, varFull, tgtKey, tgtPin, addr, alias] = p;
    let connCell;
    if (ck === 'V' && varFull) connCell = D.h('a', { href: D.hrefV(varFull), class: 'lk mono', text: conn || varFull });
    else if (ck === 'L' && tgtKey) connCell = D.h('a', { href: D.hrefBKey(tgtKey), class: 'lk mono', text: conn || tgtKey });
    else if (varFull) connCell = D.h('a', { href: D.hrefV(varFull), class: 'lk mono', text: conn || varFull, title: ck === 'A' ? '宣告於腳位的變數' : undefined });
    else connCell = D.mono(D.val(conn), ck === 'N' || ck === 'E' ? 'const' : '');
    return D.h('tr', null,
      D.h('td', { class: 'nowrap' }, D.mono(name, 'b')),
      D.h('td', { class: 'nowrap' }, D.dirBadge(dir), ' ', D.srcBadge(src)),
      D.h('td', { class: 'nowrap' }, D.tag(ck || '-'), ' ', D.h('span', { class: 'muted small', text: ckLabel(ck, varFull) })),
      D.h('td', null, connCell),
      D.h('td', null, varFull ? D.h('a', { href: D.hrefV(varFull), class: 'lk mono small', text: varFull }) : '—'),
      D.h('td', null, tgtKey ? D.frag(D.h('a', { href: D.hrefBKey(tgtKey), class: 'lk mono small', text: tgtKey.slice(tgtKey.indexOf('|') + 1) }), tgtPin ? D.mono('.' + tgtPin, 'small') : null) : '—'),
      D.h('td', { class: 'nowrap' }, addr ? D.mono(addr) : '—'),
      D.h('td', null, alias ? D.mono(alias) : '—'),
      D.h('td', null, mirrorCell(mirrors)));
  }

  D.pinRow = pinRow; // 邏輯圖側欄（diagram.js）重用；第二參數 = 該腳位的鏡像變數名陣列（選用）
  D.PIN_HEADS = ['腳位', '方向 / 來源', '連線種類', '連線', '變數', '目標方塊', '位址', '別名', '發佈為'];

  /** 程式樹中的 Task/UserBlock 節點（block_path 第二段）；供 task 頁列方塊、一般方塊頁補邏輯圖號 */
  async function taskEntry(ctrl, program, taskName) {
    const tree = await D.program(ctrl);
    if (!tree) return null;
    for (const p of tree.programs || []) {
      if (program && p.name !== program) continue;
      for (const t of p.tasks || []) if (t.name === taskName) return { prog: p, task: t };
    }
    return null;
  }

  D.page('b', async ({ route, view, signal }) => {
    const ctrl = route.segs[0] || '';
    const path = route.segs.slice(1).join('/');
    const key = ctrl + '|' + path;
    D.setTitle(path + ' (' + ctrl + ')');
    D.set(view, D.loading('載入方塊 ' + path + '…'));
    const b = await D.block(key, signal);
    if (!b) {
      D.set(view, D.errorBox('找不到此方塊', key), D.h('p', null, D.link(D.hrefP(ctrl), '瀏覽 ' + ctrl + ' 的程式', 'btn'), ' ', D.link('#/', '回到搜尋', 'btn')));
      return;
    }
    // block_path = Program/Task/…/Block；第一段是程式（連到程式瀏覽）、第二段是 Task
    const segs = path.split('/');
    const program = b.program || segs[0];
    const taskPath = segs.length > 1 ? segs.slice(0, 2).join('/') : null;
    const isTask = b.kind === 'task' || segs.length <= 2;
    const parent = segs.length > 1 ? segs.slice(0, -1).join('/') : null;
    const crumb = D.h('div', { class: 'crumb mono' }, D.mono(ctrl), ' / ',
      segs.map((s, i) => D.frag(i ? ' / ' : null,
        i === segs.length - 1 ? D.h('b', { text: s })
          : i === 0 ? D.h('a', { href: D.hrefP(ctrl, s), class: 'lk', text: s, title: '程式瀏覽' })
            : D.h('a', { href: D.hrefB(ctrl, segs.slice(0, i + 1).join('/')), class: 'lk', text: s }))));
    const head = D.h('div', { class: 'ph' },
      D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › ', D.mono(ctrl), ' › 方塊'),
      D.h('h1', { class: 'ph-title mono' }, b.name || segs[segs.length - 1], ' ', b.type ? D.tag(b.type, 'lg') : null, ' ', D.tag(b.kind === 'task' ? 'Task' : (b.kind || 'block')), b.opaque ? D.frag(' ', D.tag('不透明 opaque', 'warn')) : null),
      crumb,
      b.desc ? D.h('p', { class: 'ph-sub', text: b.desc }) : null,
      D.h('div', { class: 'ph-actions' },
        parent && segs.length > 2 ? D.link(D.hrefB(ctrl, parent), '上一層', 'btn') : null,
        taskPath && segs.length > 2 ? D.link(D.hrefB(ctrl, taskPath), '所屬 Task ' + segs[1], 'btn') : null,
        D.link(D.hrefP(ctrl, program), '程式 ' + program, 'btn'),
        segs.length > 1 ? D.link(D.hrefD(ctrl, program, segs[1], segs.length > 2 ? { b: key } : null), '邏輯圖', 'btn primary') : null,
        D.h('button', { type: 'button', class: 'btn', text: '複製深連結', onclick: () => D.copy(location.href.split('#')[0] + D.hrefB(ctrl, path), '連結') })));
    const entry = segs.length > 1 ? await taskEntry(ctrl, program, segs[1]).catch(() => null) : null;
    const meta = D.h('table', { class: 'kv' }, D.h('tbody', null,
      D.kv('原始檔', D.mono((b.file || '?') + ':' + (b.line == null ? '?' : b.line))),
      b.ver ? D.kv('版本', D.mono(b.ver)) : null,
      b.drg || b.pid ? D.kv('邏輯圖 / P&ID', D.frag(D.mono(D.val(b.drg)), '  ', D.mono(D.val(b.pid)))) : null,
      entry && entry.task.drg && entry.task.drg !== b.drg ? D.kv('所屬 Task 邏輯圖', D.frag(D.mono(entry.task.drg), ' ', D.h('span', { class: 'muted small', text: '（' + segs[1] + (entry.task.type ? ' · ' + entry.task.type : '') + '）' }))) : null,
      b.device ? D.kv('裝置', D.mono(b.device)) : null,
      b.hmi ? D.kv('HMI', typeof b.hmi === 'string' ? D.h('a', { href: D.hrefS(b.hmi), class: 'lk mono', text: b.hmi }) : D.mono(JSON.stringify(b.hmi))) : null));
    const attrs = Object.entries(b.attrs || {});
    const pins = b.pins || [];
    // 腳位值鏡像（task entry 的 vm；D.block 已載入同一 task 檔，這裡只是再查一次快取）
    const tEntry = await D.task(D.taskKeyOf(key), signal).catch(() => null);
    const mm = D.mirrorMap(tEntry, ctrl);
    const nMirror = mm ? pins.filter((p) => mm.has(key + '#' + p[0])).length : 0;
    const secs = [
      D.section('基本資料', meta),
      D.section('屬性（attrs）', attrs.length ? D.h('table', { class: 'kv' }, D.h('tbody', null, attrs.map(([k, v]) => D.kv(k, D.mono(v == null ? '—' : String(v)))))) : D.empty('無屬性'), { count: attrs.length, open: attrs.length > 0 }),
      D.section('腳位（pins）', pins.length ? D.frag(
        nMirror ? D.h('p', { class: 'muted small', text: '「發佈為」= 該腳位的值被組態工具發佈成的全域變數（腳位值鏡像）；共 ' + nMirror + ' 腳。' }) : null,
        D.table(D.PIN_HEADS, pins.map((p) => pinRow(p, mm ? mm.get(key + '#' + p[0]) : null)), 'pins')) : D.empty('沒有腳位'), { count: pins.length }),
    ];
    if (isTask && entry) {
      const rows = (entry.task.blocks || []).filter((r) => r[3] !== 'task' && r[0] !== key)
        .map(([k, name, type, kind, opaque]) => [D.h('a', { href: D.hrefBKey(k), class: 'lk mono b', text: name }), D.mono(type || ''), kind || '', opaque ? D.tag('不透明', 'warn') : '']);
      secs.push(D.section('Task 內的方塊', D.frag(D.h('p', { class: 'muted small' }, D.link(D.hrefD(ctrl, program, segs[1]), '開啟邏輯方塊圖', 'lk'), entry.task.drg ? D.frag(' · 邏輯圖號 ', D.mono(entry.task.drg)) : null), rows.length ? D.table(['方塊', '型式', '種類', ''], rows) : D.empty('無')), { count: rows.length }));
    }
    D.set(view, head, D.h('div', { class: 'secs' }, secs));
  });
})();
