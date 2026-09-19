/* Signal Atlas — 訊號頁 #/v/<CTRL.NAME>：var/<sha1[:3]>.json 的訊號卡，區段依 CONTRACT 順序，全部可摺疊 */
'use strict';
(function () {
  const D = window.DC;

  /** 讀取者依 program → task 分組（block_path = Program/Task/…/Block，第二段是 task） */
  function grouped(refs, dir) {
    const byProg = new Map();
    for (const r of refs) {
      const prog = r[1] || '?';
      const task = D.taskOf(r[2]);
      if (!byProg.has(prog)) byProg.set(prog, new Map());
      const bt = byProg.get(prog);
      if (!bt.has(task)) bt.set(task, []);
      bt.get(task).push(r);
    }
    const openAll = refs.length <= 12;
    const out = [];
    for (const [prog, tasks] of byProg) {
      const n = Array.from(tasks.values()).reduce((a, l) => a + l.length, 0);
      const ctrl = refs[0][0];
      const body = [];
      for (const [task, list] of tasks) {
        body.push(D.h('details', { class: 'grp task', open: openAll || tasks.size === 1 },
          D.h('summary', null, D.h('span', { class: 'mono', text: 'Task ' }), D.h('a', { href: D.hrefB(ctrl, prog + '/' + task), class: 'lk mono', text: task, onclick: (e) => e.stopPropagation() }), D.h('span', { class: 'sec-n', text: String(list.length) })),
          D.h('div', { class: 'grp-body' }, list.map((r) => D.refRow(r, dir)))));
      }
      out.push(D.h('details', { class: 'grp prog', open: openAll || byProg.size === 1 },
        D.h('summary', null, D.h('span', { text: 'Program ' }), D.h('a', { href: D.hrefP(ctrl, prog), class: 'lk mono', text: prog, onclick: (e) => e.stopPropagation() }), D.h('span', { class: 'sec-n', text: String(n) })),
        D.h('div', { class: 'grp-body' }, body)));
    }
    return out;
  }

  function defSection(full, ctrl, d) {
    const rows = [];
    rows.push(D.kv('說明', d.desc || '—'));
    rows.push(D.kv('型別', D.mono(D.val(d.dt))));
    rows.push(D.kv('位址', D.mono(D.val(d.addr))));
    rows.push(D.kv('初始值', D.mono(D.val(d.val))));
    if (!D.blank(d.egd_page)) rows.push(D.kv('EGD 頁', D.mono(d.egd_page)));
    if (!D.blank(d.alias)) rows.push(D.kv('別名（KKS）', D.mono(d.alias)));
    if (!D.blank(d.units) || !D.blank(d.lo) || !D.blank(d.hi)) rows.push(D.kv('單位 / 量程', D.frag(D.val(d.units), '  ', D.mono(D.val(d.lo) + ' – ' + D.val(d.hi)))));
    if (!D.blank(d.fs)) rows.push(D.kv('格式（fs）', D.mono(d.fs)));
    rows.push(D.kv('常數 / 區域', D.frag(d.const ? D.tag('常數', 'warn') : '否', '  ', d.local ? D.tag('local') : null)));
    if (!D.blank(d.device_name)) rows.push(D.kv('裝置（device）', D.mono(d.device_name)));
    if (!D.blank(d.producer)) rows.push(D.kv('Producer', D.mono(d.producer)));
    if (d.decl) {
      const [prog, task, file, line] = d.decl;
      rows.push(D.kv('宣告', D.frag(
        prog ? D.h('a', { href: D.hrefP(ctrl, prog), class: 'lk mono', text: prog }) : D.mono('?'),
        task ? D.frag(D.mono('.'), D.mono(task)) : null, '  ',
        D.h('span', { class: 'mono muted', text: (file || '?') + ':' + (line == null ? '?' : line) }))));
    }
    if (d.ref && d.ref.length) rows.push(D.kv('出現於程式', D.h('div', { class: 'chips wrap' }, d.ref.map((p) => D.h('a', { href: D.hrefP(ctrl, p), class: 'chip-btn', text: p })))));
    if (!D.blank(d.screen)) rows.push(D.kv('畫面', D.h('a', { href: D.hrefS(d.screen), class: 'lk mono', text: d.screen })));
    return D.section('定義', D.h('table', { class: 'kv' }, D.h('tbody', null, rows)));
  }

  function sourceSection(rec) {
    const w = rec.w || [];
    const body = [];
    let note = '';
    if (w.length) {
      note = '邏輯寫入者';
      body.push(w.map((r) => D.refRow(r, 'O')));
      if (w.length > 1) body.push(D.h('p', { class: 'warn-text', text: '多個寫入者（' + w.length + '）— 可能是不同 task 交替寫入，或方向推斷有誤。' }));
      if (rec.w_more) body.push(D.h('p', { class: 'muted small', text: '另有 ' + D.int(rec.w_more) + ' 筆寫入者未列出（超過 400 筆）。' }));
    } else {
      const ioIn = (rec.io || []).filter((x) => x.dir === 'I');
      if (ioIn.length) {
        note = '現場 I/O';
        body.push(D.h('p', null, '由現場 I/O 輸入（無邏輯寫入者）：'),
          D.h('ul', { class: 'plain' }, ioIn.map((x) => D.h('li', null,
            D.h('a', { href: D.hrefIO(x.ctrl, x.module), class: 'lk mono', text: (x.cabinet ? x.cabinet + ' / ' : '') + x.module }),
            ' ', D.mono(x.board || ''), ' ', D.mono(x.point || ''), x.tag ? D.frag(' ', D.tag(x.tag)) : null))));
      } else if (rec.egd && rec.egd.src) {
        const s = rec.egd.src;
        note = 'EGD 來自 ' + s.ctrl;
        body.push(D.h('p', null, 'EGD 來自 ', D.h('a', { href: D.hrefV(s.ctrl + '.' + s.var), class: 'lk mono b', text: s.ctrl + '.' + s.var }),
          '（exchange ', D.mono(D.val(s.ex)), '，voffs ', D.mono(D.val(s.voffs)), '，比對 ', D.mono(D.val(s.match)), '）'));
      } else if (rec.enc && rec.enc.length) {
        note = '可能在加密程式內';
        body.push(D.h('p', { class: 'warn-text' }, '找不到寫入者；此訊號出現在加密程式 ', rec.enc.map((p, i) => D.frag(i ? '、' : null, D.mono(p))), ' 內 — 可能在加密程式內（不可追蹤）。'));
      } else {
        note = '無';
        body.push(D.h('p', { class: 'muted' }, rec.d && rec.d.const ? '常數（沒有寫入者）。' : '找不到寫入者：可能為 HMI/外部寫入、常數，或腳位方向未推斷出來（見「方向未知的腳」）。'));
      }
    }
    return D.section('來源（寫入者）', body, { count: w.length, note });
  }

  function egdSection(egd, full) {
    if (!egd) return null;
    const p = egd.p || [], c = egd.c || [];
    const body = [];
    if (p.length) {
      body.push(D.h('h4', { text: '本站送出（produced）' }),
        D.table(['EGD 頁', 'Exchange', 'voffs'], p.map((x) => [D.mono(D.val(x.page)), D.mono(D.val(x.ex)), D.mono(D.val(x.voffs))])));
      if (!c.length) body.push(D.h('p', { class: 'warn-text', text: 'consumer outside checkout — 有送出但站內找不到消費者（接收端控制器不在匯出範圍內）。' }));
    }
    if (c.length) {
      body.push(D.h('h4', { text: '消費者（其他控制器）' }),
        D.table(['控制器', '對方訊號', 'Exchange', 'voffs', '比對', 'EGD 頁'], c.map((x) => [D.mono(x.ctrl), D.h('a', { href: D.hrefV(x.ctrl + '.' + x.local), class: 'lk mono', text: x.local }), D.mono(D.val(x.ex)), D.mono(D.val(x.voffs)), D.mono(D.val(x.match)), D.mono(D.val(x.page))])));
    }
    if (egd.src) {
      const s = egd.src;
      body.push(D.h('h4', { text: '來源（本訊號是 EGD 副本）' }),
        D.h('p', null, D.h('a', { href: D.hrefV(s.ctrl + '.' + s.var), class: 'lk mono b', text: s.ctrl + '.' + s.var }), '  exchange ', D.mono(D.val(s.ex)), '  voffs ', D.mono(D.val(s.voffs)), '  比對 ', D.mono(D.val(s.match))));
    }
    if (!body.length) return null;
    return D.section('跨控制器 EGD', body, { count: p.length + c.length + (egd.src ? 1 : 0) });
  }

  function ioSection(io) {
    if (!io || !io.length) return null;
    const rows = io.map((x) => {
      const screws = x.screws && x.screws.length ? D.table(['端子名', '號', '電纜', '線號'], x.screws.map((s) => [D.mono(D.val(s[0])), D.mono(D.val(s[1])), D.mono(D.val(s[2])), D.mono(D.val(s[3]))]), 'sub') : null;
      return [
        x.cabinet || '—',
        D.h('a', { href: D.hrefIO(x.ctrl, x.module), class: 'lk mono', text: x.module || '?' }),
        D.mono(D.val(x.board)),
        D.mono(D.val(x.hw) + (x.pos ? ' ' + x.pos : '')),
        D.mono(D.val(x.point)),
        x.tag ? D.mono(x.tag) : '—',
        D.dirBadge(x.dir),
        D.mono(D.val(x.type) + (x.kind ? ' ' + x.kind : '')),
        D.blank(x.lo) && D.blank(x.hi) ? '—' : D.mono(D.val(x.lo) + ' – ' + D.val(x.hi)),
        screws || '—'];
    });
    return D.section('I/O 端子', D.table(['機櫃', '模組', '端子板', '型式 / 位置', '點', 'DeviceTag', '方向', '型式', '量程', '端子（螺絲）'], rows), { count: io.length });
  }

  function hmiSection(hmi) {
    if (!hmi || !hmi.length) return null;
    return D.section('HMI 畫面', D.table(['畫面', '選單路徑', '來源'], hmi.map((h) => [D.h('a', { href: D.hrefS(h[0]), class: 'lk mono', text: h[0] }), h[1] || '—', h[2] || '—'])), { count: hmi.length });
  }

  function almSection(a) {
    if (!a) return null;
    const rows = [];
    const add = (k, v, multi) => { if (!D.blank(v)) rows.push(D.kv(k, multi ? D.lines(v) : String(v))); };
    add('ID', a.id); add('等級（cls）', a.cls); add('定義（def）', a.def); add('區域（area）', a.area); add('緊急度', a.urg);
    add('可能原因', a.causes, true); add('處置', a.action, true); add('後果', a.conseq, true);
    return D.section('警報說明', D.h('table', { class: 'kv' }, D.h('tbody', null, rows)), { note: a.cls || a.def || '' });
  }

  D.page('v', async ({ route, view, signal }) => {
    const full = route.segs.join('/');
    if (!full) { D.set(view, D.errorBox('缺少訊號名')); return; }
    const [ctrl] = D.splitFull(full);
    D.setTitle(full);
    D.set(view, D.loading('載入訊號卡 ' + full + '…'));
    const rec = await D.varCard(full, signal);
    if (!rec) {
      D.set(view, D.errorBox('找不到此訊號', full), D.h('p', null, D.link(D.hrefQ(D.splitFull(full)[1] || full, D.ctrls.includes(ctrl) ? ctrl : ''), '用搜尋找找看', 'btn'), ' ', D.link('#/', '回到搜尋', 'btn')));
      return;
    }
    const d = rec.d || {};
    const head = D.h('div', { class: 'ph' },
      D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › ', D.mono(ctrl), ' › 訊號'),
      D.h('h1', { class: 'ph-title mono', text: full }),
      D.h('p', { class: 'ph-sub', text: d.desc || '' }),
      D.h('div', { class: 'ph-actions' },
        D.link(D.hrefT(full, 'up', 3), '追蹤上游', 'btn primary'),
        D.link(D.hrefT(full, 'down', 3), '追蹤下游', 'btn primary'),
        D.h('button', { type: 'button', class: 'btn', text: '複製深連結', onclick: () => D.copy(location.href.split('#')[0] + D.hrefV(full), '連結') }),
        D.h('button', { type: 'button', class: 'btn', text: '複製名稱', onclick: () => D.copy(full, full) })));

    const r = rec.r || [], u = rec.u || [];
    const secs = [
      defSection(full, ctrl, d),
      sourceSection(rec),
      D.section('去向（讀取者）', r.length ? D.frag(grouped(r, 'I'), rec.r_more ? D.h('p', { class: 'muted small', text: '另有 ' + D.int(rec.r_more) + ' 筆讀取者未列出（超過 400 筆）。' }) : null) : D.empty('沒有讀取者'), { count: r.length + (rec.r_more || 0) }),
      u.length ? D.section('方向未知的腳', D.frag(D.h('p', { class: 'muted small', text: '這些腳位的方向推不出來（?），可能是寫入也可能是讀取。' }), u.map((x) => D.refRow(x, '?'))), { count: u.length, open: u.length <= 20 }) : null,
      egdSection(rec.egd, full),
      ioSection(rec.io),
      hmiSection(rec.hmi),
      almSection(rec.alm),
      rec.watch && rec.watch.length ? D.section('Watch', D.h('ul', { class: 'plain' }, rec.watch.map((w) => D.h('li', null, D.mono(w[0]), ' ', D.mono(w[1], 'muted')))), { count: rec.watch.length }) : null,
      rec.drg && rec.drg.length ? D.section('邏輯圖號 / P&ID', D.table(['邏輯圖號', 'P&ID'], rec.drg.map((x) => [D.mono(D.val(x[0])), D.mono(D.val(x[1]))])), { count: rec.drg.length }) : null,
      rec.enc && rec.enc.length ? D.section('加密程式', D.frag(
        D.h('p', { class: 'warn-text b', text: '加密 — 無法追蹤：此訊號出現在下列加密程式內，內部邏輯無法索引。' }),
        D.h('div', { class: 'chips wrap' }, rec.enc.map((p) => D.h('a', { href: D.hrefP(ctrl, p), class: 'chip-btn enc', text: p })))), { count: rec.enc.length, cls: 'enc' }) : null,
    ];
    D.set(view, head, D.h('div', { class: 'secs' }, secs));
  });
})();
