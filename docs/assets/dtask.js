/* Signal Atlas — Task 邏輯圖 #/d/<CTRL>/<Program>/<Task>?ub=<block_path>&f=&pins=1&cm=0|1&sel=<CTRL.VAR>&b=<key>&page=k&all=1
 * 由 task/<hhh>.json 的整份 task 記錄建圖（buildGraph，規則見 plan §二），交給 D.dg（diagram.js）排版／渲染／互動。
 * 規模分級：≤300 方塊全畫；301–1000 依連通群組分頁；>1000 拒絕並提供篩選／型別 chips／分頁。 */
'use strict';
(function () {
  const D = window.DC;
  /** 規模分級門檻（可由 DC.dtask 覆寫；每次進頁讀取） */
  const LIM = (D.dtask = D.dtask || { PAGE_MAX: 300, REFUSE: 1000, CM_HIDE: 40 });
  let PAGE_MAX = 300, REFUSE = 1000, CM_HIDE = 40;
  const readLimits = () => { PAGE_MAX = LIM.PAGE_MAX; REFUSE = LIM.REFUSE; CM_HIDE = LIM.CM_HIDE; };

  const shortVar = (full) => { const i = full.indexOf('.'); return i < 0 ? full : full.slice(i + 1); };
  const isComment = (rec) => rec.type === '_COMMENT' || /^_COMMENT(_\d+)?$/.test(rec.name || '');
  const stripK = (s) => String(s == null ? '' : s).replace(/^[NEL]:/, '');

  /** 建圖：t = task 記錄 {n,b}；rootKey = task key 或 ?ub= 的 userblock key；q = {pins, cm, f} */
  function buildGraph(t, ctrl, rootKey, q) {
    const b = t.b;
    const root = b[rootKey] || {};
    const prefix = rootKey + '/';
    const members = [];
    let idx = 0;
    for (const k in b) if (k.startsWith(prefix) && k.indexOf('/', prefix.length) < 0) members.push([k, b[k], idx++]);
    const memberSet = new Set(members.map((m) => m[0]));
    const nodes = new Map(), edges = [], tags = [], warn = [];
    const meta = { title: rootKey.slice(rootKey.indexOf('|') + 1), warn, nComment: 0, nHiddenComment: 0, nHiddenPins: 0 };
    // 被同 scope 的 L 腳指到的目標腳（即使只有位址也要畫出來）
    const needed = new Map();
    for (const [, rec] of members) for (const p of rec.pins || []) if (p[3] === 'L' && p[6] && memberSet.has(p[6]) && p[7]) { if (!needed.has(p[6])) needed.set(p[6], new Set()); needed.get(p[6]).add(p[7]); }
    const rootVar = new Map();
    for (const p of root.pins || []) if (p[5]) rootVar.set(p[0], p[5]);
    const comments = [];
    for (const [k, rec, i] of members) {
      const ord = rec.lay != null ? rec.lay : 100000 + i;
      const name = rec.name || k.slice(k.lastIndexOf('/') + 1);
      if (isComment(rec)) {
        if (!(rec.desc || '').trim()) { meta.nEmptyComment = (meta.nEmptyComment || 0) + 1; continue; } // 空白註解不畫
        meta.nComment++;
        const node = { id: k, kind: 'comment', key: k, name, type: '_COMMENT', desc: rec.desc || '', ord, left: [], right: [], body: D.dg.wrapText(rec.desc, 300, 8), pins: rec.pins, attrs: rec.attrs };
        nodes.set(k, node); comments.push(node);
        continue;
      }
      const node = { id: k, kind: rec.kind === 'userblock' ? 'ub' : 'block', key: k, name, type: rec.type || (rec.kind === 'userblock' ? 'UserBlock' : ''), desc: rec.desc || '', attrs: rec.attrs, pins: rec.pins || [], ord, left: [], right: [], body: [], opaque: !!rec.opaque, cls: rec.opaque ? 'opaque' : '', kindLabel: rec.kind };
      const need = needed.get(k);
      for (const p of rec.pins || []) {
        const [pin, dir, src, ck, conn, varFull, tgtKey, tgtPin] = p;
        const shown = ck === 'V' || ck === 'L' || ck === 'P' || ck === 'N' || ck === 'E' || q.pins || (need && need.has(pin));
        if (!shown) { meta.nHiddenPins++; continue; }
        const port = { id: k + '#' + pin, pin, dir: dir || '?', src, ck, conn, varFull: varFull || null, tgtKey, tgtPin, inline: null };
        if (ck === 'N' || ck === 'E') {
          const txt = stripK(conn);
          if ((rec.type === 'RUNG' || rec.type === 'CALC') && (pin === 'EQN' || pin === 'EQUAT')) node.body = D.dg.wrapText(pin + ' = ' + txt, 240, 3);
          else port.inline = txt;
        }
        (dir === 'O' ? node.right : node.left).push(port);
      }
      nodes.set(k, node);
    }
    // 註解黏到下一個非註解方塊（依 ord）
    const ordered = Array.from(nodes.values()).sort((a, b) => a.ord - b.ord);
    for (let i = 0; i < ordered.length; i++) {
      if (ordered[i].kind !== 'comment') continue;
      for (let j = i + 1; j < ordered.length; j++) if (ordered[j].kind !== 'comment') { edges.push({ id: 's:' + ordered[i].id, from: ordered[i].id, to: ordered[j].id, kind: 'sticky' }); ordered[i].anchor = ordered[j].id; break; }
    }
    // L：同 scope 走線；目標＝scope 本身 → 介面腳；scope 外 → 標籤；未解析 → 警告
    const edgeIds = new Set();
    const addEdge = (e) => { if (edgeIds.has(e.id)) return; edgeIds.add(e.id); edges.push(e); };
    const covered = new Set();
    const portOf = (nodeId, pin) => { const n = nodes.get(nodeId); if (!n) return null; return n.left.find((p) => p.pin === pin) || n.right.find((p) => p.pin === pin) || null; };
    const ifaceTag = (node, port, tgtPin) => {
      const v = rootVar.get(tgtPin) || null;
      tags.push({ port: port.id, side: port.dir === 'O' ? 'R' : 'L', text: '⟨' + tgtPin + '⟩' + (v ? ' = ' + shortVar(v) : ''), varFull: v, cls: 'iface', title: '介面腳 ' + tgtPin + (v ? '（' + v + '）' : '') });
      covered.add(port.id);
    };
    for (const n of nodes.values()) {
      if (n.kind === 'comment') continue;
      for (const port of n.left.concat(n.right)) {
        if (port.ck === 'P') { if (port.tgtPin) ifaceTag(n, port, port.tgtPin); else { tags.push({ port: port.id, side: port.dir === 'O' ? 'R' : 'L', text: stripK(port.conn) || '介面腳', cls: 'iface' }); covered.add(port.id); } continue; }
        if (port.ck !== 'L') continue;
        const tk = port.tgtKey, tp = port.tgtPin;
        if (tk && tk === rootKey && tp) { ifaceTag(n, port, tp); continue; }
        if (tk && memberSet.has(tk)) {
          let other = portOf(tk, tp);
          if (!other && tp) { // 目標記錄沒列這支腳（不透明 userblock 無 pins）→ 依持有者方向補一支
            const tn = nodes.get(tk);
            other = { id: tk + '#' + tp, pin: tp, dir: port.dir === 'O' ? 'I' : 'O', src: '-', ck: '-', conn: null, varFull: null, inline: null, synth: true };
            (other.dir === 'O' ? tn.right : tn.left).push(other);
          }
          if (!other) { tags.push({ port: port.id, side: port.dir === 'O' ? 'R' : 'L', text: stripK(port.conn) + ' ?', cls: 'warn', title: '目標腳不存在：' + tp }); warn.push(n.name + '.' + port.pin + ' → ' + tp + ' 不存在'); continue; }
          const holderOut = port.dir === 'O' && other.dir !== 'O';
          const from = holderOut ? port : other, to = holderOut ? other : port;
          addEdge({ id: from.id + '>' + to.id, from: from.id, to: to.id, kind: 'L', cls: port.dir === '?' || other.dir === '?' ? 'dir-q' : '' });
          covered.add(port.id); covered.add(other.id);
          continue;
        }
        if (tk && b[tk]) { tags.push({ port: port.id, side: port.dir === 'O' ? 'R' : 'L', text: stripK(port.conn), cls: 'ext-l', title: 'scope 外的方塊腳：' + tk + '.' + tp }); covered.add(port.id); continue; }
        tags.push({ port: port.id, side: port.dir === 'O' ? 'R' : 'L', text: '? ' + stripK(port.conn), cls: 'warn', title: '未解析：' + (port.conn || '') });
        warn.push(n.name + '.' + port.pin + ' 未解析 ' + (port.conn || ''));
        covered.add(port.id);
      }
    }
    // V：同變數在 scope 內 |W| 1..2 且 |Rd| 1..4 → 內部走線；否則標籤
    const byVar = new Map();
    for (const n of nodes.values()) for (const port of n.left.concat(n.right)) if (port.ck === 'V' && port.varFull) { if (!byVar.has(port.varFull)) byVar.set(port.varFull, { W: [], R: [] }); byVar.get(port.varFull)[port.dir === 'O' ? 'W' : 'R'].push(port); }
    const portNode = (p) => p.id.slice(0, p.id.indexOf('#'));
    for (const [v, g] of byVar) {
      const wired = new Set();
      if (g.W.length >= 1 && g.W.length <= 2 && g.R.length >= 1 && g.R.length <= 4) {
        for (const w of g.W) for (const r of g.R) {
          if (portNode(w) === portNode(r)) continue; // 自寫自讀 → 標籤
          addEdge({ id: w.id + '>' + r.id, from: w.id, to: r.id, kind: 'V', varFull: v, label: shortVar(v), multi: g.W.length > 1 });
          wired.add(w.id); wired.add(r.id);
        }
      }
      for (const p of g.W) if (!wired.has(p.id)) tags.push({ port: p.id, side: 'R', text: shortVar(v), varFull: v, cls: 'out', title: v });
      for (const p of g.R) if (!wired.has(p.id)) tags.push({ port: p.id, side: 'L', text: shortVar(v), varFull: v, cls: 'in', title: v });
    }
    // 註解顯示規則
    const cmOn = q.cm === '1' ? true : q.cm === '0' ? false : comments.length <= CM_HIDE;
    if (!cmOn) { for (const c of comments) nodes.delete(c.id); meta.nHiddenComment = comments.length; }
    meta.cmOn = cmOn;
    let graph = { nodes, edges, tags, meta };
    D.dg.prepare(graph); // 過濾掉指向已刪節點的邊／標籤
    // 篩選：名稱／型別／說明／腳位變數含關鍵字 + 一跳鄰居
    if (q.f) {
      const kw = q.f.toLowerCase();
      const hit = (n) => [n.name, n.type, n.desc].some((s) => s && String(s).toLowerCase().includes(kw)) || n.left.concat(n.right).some((p) => (p.varFull && p.varFull.toLowerCase().includes(kw)) || (p.conn && String(p.conn).toLowerCase().includes(kw)));
      const keep = new Set();
      for (const n of nodes.values()) if (hit(n)) keep.add(n.id);
      const nodeOf = (pid) => { const p = graph.ports.get(pid); return p ? p.node : pid; };
      const nb = new Set();
      for (const e of edges) { const a = nodeOf(e.from), c = nodeOf(e.to); if (keep.has(a)) nb.add(c); if (keep.has(c)) nb.add(a); }
      for (const x of nb) keep.add(x);
      for (const id of Array.from(nodes.keys())) if (!keep.has(id)) nodes.delete(id);
      meta.filter = q.f; meta.nMatched = keep.size;
      D.dg.prepare(graph);
    }
    return graph;
  }

  /** 依連通群組切成 ≤PAGE_MAX 個非註解節點的頁；回傳 [[nodeId…]…] */
  function pagesOf(graph) {
    const comps = D.dg.components(graph);
    const pages = [];
    let cur = [], cnt = 0;
    for (const c of comps) {
      const n = c.filter((id) => graph.nodes.get(id).kind !== 'comment').length;
      if (cnt + n > PAGE_MAX && cur.length) { pages.push(cur); cur = []; cnt = 0; }
      cur.push(...c); cnt += n;
    }
    if (cur.length) pages.push(cur);
    return pages;
  }
  function subgraph(graph, ids) {
    const set = new Set(ids);
    const nodes = new Map();
    for (const id of ids) nodes.set(id, graph.nodes.get(id));
    const g = { nodes, edges: graph.edges, tags: graph.tags, meta: graph.meta };
    return D.dg.prepare(g);
  }
  const nBlocks = (graph) => Array.from(graph.nodes.values()).filter((n) => n.kind !== 'comment').length;

  /** 雙擊變數標籤：輸入 → 寫入者所在 task；輸出 → 讀取者 task 清單 */
  async function expandVar(varFull, ctx, inst, cur) {
    const side = ctx && ctx.port ? ctx.port.side : (ctx && ctx.el && ctx.el.classList.contains('out') ? 'R' : 'L');
    let rec = inst.lastCard && inst.lastCard.varFull === varFull ? inst.lastCard.rec : null;
    if (!rec) { try { rec = await D.varCard(varFull); } catch (e) { rec = null; } }
    if (!rec) { D.toast('找不到訊號卡'); return; }
    const refs = (side === 'L' ? rec.w : rec.r) || [];
    const groups = new Map();
    for (const r of refs) { const tk = D.taskKeyOf(r[0] + '|' + r[2]); if (tk === cur.tkey) continue; if (!groups.has(tk)) groups.set(tk, []); groups.get(tk).push(r); }
    if (side === 'L') {
      if (groups.size === 1) { const tk = groups.keys().next().value; const [c, pt] = tk.split('|'); const [pg, tk2] = pt.split('/'); D.go(D.hrefD(c, pg, tk2, { sel: varFull })); return; }
      if (!groups.size) {
        if (refs.length) { D.toast('寫入者就在本 task 內'); inst.centerVar(varFull); return; }
        const ioIn = (rec.io || []).filter((x) => x.dir === 'I');
        if (ioIn.length) D.toast('現場 I/O 輸入：' + (ioIn[0].module || '') + ' ' + (ioIn[0].point || '') + (ioIn[0].tag ? ' ' + ioIn[0].tag : ''));
        else if (rec.egd && rec.egd.src) D.go(D.hrefV(rec.egd.src.ctrl + '.' + rec.egd.src.var));
        else if (rec.enc && rec.enc.length) D.toast('加密 — 無法追蹤（' + rec.enc.join('、') + '）');
        else if (rec.d && rec.d.const) D.toast('常數（無寫入者）');
        else D.toast('無寫入者（可能為 HMI／外部寫入或方向未推斷）');
        return;
      }
    } else if (groups.size === 1) { const tk = groups.keys().next().value; const [c, pt] = tk.split('|'); const [pg, tk2] = pt.split('/'); D.go(D.hrefD(c, pg, tk2, { sel: varFull })); return; }
    else if (!groups.size) {
      const cons = (rec.egd && rec.egd.c) || [];
      if (cons.length) D.toast('EGD 消費者：' + cons.map((c) => c.ctrl + '.' + c.local).join('、'));
      else if ((rec.io || []).some((x) => x.dir === 'O')) D.toast('現場 I/O 輸出');
      else D.toast(refs.length ? '讀取者都在本 task 內' : '無讀取者');
      return;
    }
    // 多個 task：面板列連結
    const list = D.h('div', { class: 'dg-expand' }, D.h('h4', { text: (side === 'L' ? '寫入者' : '讀取者') + '所在 task（' + groups.size + '）' }),
      D.h('ul', { class: 'plain' }, Array.from(groups, ([tk, rs]) => { const [c, pt] = tk.split('|'); const [pg, t2] = pt.split('/'); return D.h('li', null, D.h('a', { href: D.hrefD(c, pg, t2, { sel: varFull }), class: 'lk mono', text: c + '/' + pt }), ' ', D.h('span', { class: 'sec-n', text: String(rs.length) })); })));
    const old = inst.sideBody.querySelector('.dg-expand');
    if (old) old.remove();
    inst.sideBody.prepend(list);
    inst.side.classList.add('open');
  }

  /* ------------------------------------------------------------------ 頁面 */
  D.page('d', async ({ route, view, signal, alive }) => {
    const [ctrl, program, task] = route.segs;
    const q = route.query;
    readLimits();
    if (!ctrl || !program || !task) { D.set(view, D.errorBox('路徑不完整', '需要 #/d/<控制器>/<程式>/<Task>')); return; }
    const tkey = ctrl + '|' + program + '/' + task;
    const rootKey = q.ub ? ctrl + '|' + q.ub : tkey;
    const rootPath = rootKey.slice(rootKey.indexOf('|') + 1);
    const cur = { ctrl, program, task, tkey, rootKey };
    const href = (patch) => D.hrefD(ctrl, program, task, Object.assign({ ub: q.ub, f: q.f, pins: q.pins, cm: q.cm, page: q.page, all: q.all }, patch));
    D.setTitle('邏輯圖 ' + rootPath + ' (' + ctrl + ')');
    const crumbs = [D.link('#/', '搜尋'), ' › ', D.mono(ctrl), ' › ', D.link(D.hrefP(ctrl, program), program, 'lk mono'), ' › ', D.link(D.hrefB(ctrl, program + '/' + task), task, 'lk mono'), ' › 邏輯圖'];
    if (q.ub) crumbs.push(' › ', D.mono(q.ub.split('/').slice(2).join('/'), 'b'));
    const inst = D.dg.create(view, {
      crumbs, title: rootPath, fileName: ctrl + '_' + rootPath.replace(/\//g, '_'),
      varHint: '雙擊變數標籤：輸入 → 展開到寫入者所在 task；輸出 → 讀取者所在 task。',
      onDblVar: (v, ctx, i) => { expandVar(v, ctx, i, cur).catch((e) => console.warn(e)); },
      nodeActions: (n) => (n.kind === 'ub' && !n.opaque ? D.link(href({ ub: n.key.slice(n.key.indexOf('|') + 1), page: null, f: null }), '展開內部', 'btn sm') : null),
    });
    inst.setLoading('載入 task 檔與排版引擎…');
    const f0 = D.fetchCount;
    const [t] = await Promise.all([D.task(tkey, signal), D.loadScript('dagre.min.js')]);
    if (!alive()) return;
    if (!t) {
      const tree = await D.program(ctrl).catch(() => null);
      if (!alive()) return;
      const prog = tree && (tree.programs || []).find((p) => p.name === program);
      if (prog && prog.enc) { inst.setMessage(D.errorBox('加密 — 無法追蹤', '程式 ' + program + ' 內容加密，只索引變數宣告與 EGD；無法繪製 ' + task + ' 的邏輯圖。'), D.h('p', null, D.link(D.hrefP(ctrl, program), '程式瀏覽', 'btn'))); inst.status('加密程式'); return; }
      inst.setMessage(D.errorBox('找不到此 Task', tkey + (tree ? (prog ? '（程式存在，但沒有這個 task）' : '（沒有這個程式）') : '（沒有控制器 ' + ctrl + ' 的程式資料）')), D.h('p', null, D.link(D.hrefP(ctrl, program), '瀏覽 ' + ctrl + ' 的程式', 'btn'), ' ', D.link('#/', '回到搜尋', 'btn')));
      inst.status('無此 task');
      return;
    }
    if (!t.b[rootKey]) { inst.setMessage(D.errorBox('找不到此 UserBlock', rootKey), D.h('p', null, D.link(href({ ub: null }), '回到 Task 圖', 'btn'))); return; }
    const full = buildGraph(t, ctrl, rootKey, q);
    const N = nBlocks(full);
    // 工具列
    inst.addTool(D.dg.tool('pins', '全腳位', () => D.go(href({ pins: q.pins ? null : 1 })), { pressed: !!q.pins, title: '顯示只有位址／device 的腳（?pins=1）' }));
    inst.addTool(D.dg.tool('cm', '註解', () => D.go(href({ cm: full.meta.cmOn ? 0 : 1 })), { pressed: full.meta.cmOn, title: '顯示／隱藏 _COMMENT（' + full.meta.nComment + '）' }));
    inst.addTool(D.dg.tool('block', '方塊頁', null, { href: D.hrefB(ctrl, rootPath), title: '開此 ' + (q.ub ? 'UserBlock' : 'Task') + ' 的方塊頁' }));
    if (q.ub) inst.addTool(D.dg.tool(null, '回 Task', null, { href: href({ ub: null, page: null }), title: '回到 Task 圖' }));
    if (q.f) inst.addTool(D.dg.tool('close', '清除篩選 ' + q.f, () => D.go(href({ f: null, page: null })), { title: '清除 ?f= 篩選' }));
    const rootRec = t.b[rootKey];
    if (rootRec && rootRec.desc) inst.setTitle(rootPath + ' — ' + D.dg.trunc(rootRec.desc.split('\n')[0], 60));

    // 規模分級
    let graph = full, banner = null;
    const pages = N > PAGE_MAX ? pagesOf(full) : null;
    const pageNo = Math.max(1, parseInt(q.page, 10) || 0);
    const pageLinks = () => D.h('div', { class: 'dg-pages' }, pages.map((p, i) => D.link(href({ page: i + 1, all: null }), String(i + 1), 'chip-btn' + (i + 1 === pageNo && q.page ? ' on' : ''))));
    if (N > REFUSE && !q.page && !q.all && !q.f) {
      const types = new Map();
      for (const n of full.nodes.values()) if (n.kind !== 'comment') types.set(n.type || '?', (types.get(n.type || '?') || 0) + 1);
      const chips = Array.from(types).sort((a, b) => b[1] - a[1]).slice(0, 24).map(([ty, c]) => D.link(href({ f: ty, page: null }), ty + ' ' + c, 'chip-btn'));
      const fin = D.h('input', { type: 'search', class: 'filter', placeholder: '篩選：方塊名／型別／說明／變數…', 'aria-label': '篩選', onkeydown: (e) => { if (e.key === 'Enter' && fin.value.trim()) D.go(href({ f: fin.value.trim(), page: null })); } });
      inst.setMessage(D.h('div', { class: 'error-box' }, D.h('h2', { text: '此 Task 有 ' + D.int(N) + ' 個方塊 — 超過 ' + D.int(REFUSE) + '，不會一次全畫' }),
        D.h('p', { text: '請先篩選（關鍵字＋一跳鄰居）、點型別 chips，或依連通群組分頁（每頁 ≤ ' + PAGE_MAX + ' 個方塊）。' })),
        D.h('div', { class: 'dg-refuse' }, D.h('p', null, fin), D.h('h4', { text: '型別' }), D.h('div', { class: 'chips' }, chips),
          D.h('h4', { text: '分頁（' + pages.length + '）' }), pageLinks(), D.h('p', { class: 'muted small' }, D.link(href({ all: 1 }), '仍要全部繪製（可能很慢）', 'lk'))));
      inst.status('抓取 ' + (D.fetchCount - f0) + ' 個分片 · ' + D.int(N) + ' 個方塊 · 未繪製');
      return;
    }
    if (pages && !q.all) {
      const k = Math.min(pageNo, pages.length);
      graph = subgraph(full, pages[k - 1]);
      banner = D.h('div', { class: 'dg-banner' }, D.h('span', null, D.int(N) + ' 個方塊，依連通群組分 ' + pages.length + ' 頁（本頁 ' + nBlocks(graph) + '）：'), pageLinks(), pages.length > 1 && N <= REFUSE ? D.link(href({ all: 1, page: null }), '全部', 'chip-btn') : null);
    } else if (N > REFUSE) {
      banner = D.h('div', { class: 'dg-banner warn' }, D.int(N) + ' 個方塊全部繪製 — 可能很慢；', D.link(href({ all: null, page: 1 }), '改用分頁', 'lk'));
    }
    if (!graph.nodes.size) {
      inst.setMessage(D.errorBox('沒有可畫的方塊', q.f ? '篩選「' + q.f + '」沒有符合的方塊。' : '此 ' + (q.ub ? 'UserBlock' : 'Task') + ' 沒有子方塊。'), q.f ? D.h('p', null, D.link(href({ f: null }), '清除篩選', 'btn')) : null);
      inst.status('抓取 ' + (D.fetchCount - f0) + ' 個分片 · 0 個方塊');
      return;
    }
    inst.setLoading('排版 ' + nBlocks(graph) + ' 個方塊…');
    await D.yieldMain();
    if (!alive()) return;
    const tm = inst.setGraph(graph);
    if (banner) inst.canvas.prepend(banner);
    const nEdges = graph.edges.filter((e) => e.kind !== 'sticky').length;
    const st = ['抓取 ' + (D.fetchCount - f0) + ' 個分片', '方塊 ' + D.int(nBlocks(graph)) + (full.meta.nHiddenComment ? '（隱藏 ' + full.meta.nHiddenComment + ' 個註解）' : ''), '連線 ' + nEdges, '標籤 ' + graph.tags.length, '群組 ' + tm.comps, '排版 ' + Math.round(tm.layout) + ' ms'];
    if (full.meta.filter) st.push('篩選「' + full.meta.filter + '」' + full.meta.nMatched + ' 個');
    if (full.meta.warn.length) st.push('警告 ' + full.meta.warn.length);
    inst.status(st.join(' · '));
    if (full.meta.warn.length) inst.statusEl.title = full.meta.warn.slice(0, 20).join('\n');
    if (q.sel) { if (inst.highlight(q.sel)) inst.centerVar(q.sel); else D.toast('圖中沒有 ' + q.sel); }
    else if (q.b) { if (inst.select(q.b)) inst.centerNode(q.b); else D.toast('圖中沒有這個方塊'); }
  });
})();
