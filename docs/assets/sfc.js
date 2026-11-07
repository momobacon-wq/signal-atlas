/* Signal Atlas — 序列頁（SFC task）：#/q?c=<CTRL>&ind=1 序列總覽（全部序列、直接啟動鏈 DAG、間接關聯表）、
 * #/q/<CTRL>/<Program>/<Task> 單一序列（介面變數、步驟／轉移圖、轉移表、步驟表、上游／下游序列與逐跳路徑）。
 * 資料：data/sfc.json（tools/dcdas/sfc.py；manifest.files.sfc，舊匯出沒有此鍵 →「此匯出沒有序列資料」，不抓檔）。
 * 啟動鏈邊：d 1 = 只經 MOVE／AND／OR／PULSE、介面腳、加密巨集與 EGD（直接）；d 0 = 經 RUNG／LATCH／計時器…（間接：可能是許可、重置或停止條件，
 *   本頁不替它下結論）；inf 1 = 路徑上有推斷方向的腳；op 1 = 經加密巨集（內部看不到）；w = 最後一個 MOVE 寫進 RUN 的字面常數（原文；
 *   只在直接邊、而且那個 MOVE 不在序列介面巨集裡時顯示——介面巨集內的 MOVE 是 ENABLE 接 RUN 腳、SRC 常數的固定寫法，不帶資訊）；
 *   cut = 走訪超過上限的來源（鏈路可能不完整；任何一個序列的上游清單都可能因此不完整）。用語只陳述索引看得到的路徑，不解讀步驟的製程意義。
 * 圖：vendored dagre（D.loadScript），SVG 文字一律 textContent；可捲動容器＋縮放按鈕（Ctrl＋滾輪縮放、滑鼠拖曳平移）。
 * 對外：D.sfcAvail()、D.sfcData()（解析後快取的 Promise）、D.hrefSeq(key)——browse.js／dtask.js／search.js／block.js／signal.js 延遲使用。 */
'use strict';
(function () {
  const D = window.DC;
  const IFACE = ['RUN', 'RESET', 'STATUS', 'COMPLETE', 'MODE', 'Steps', 'Transitions'];
  const PASS = new Set(['MOVE', 'AND', 'OR', 'PULSE']);
  const INFER = new Set(['L', 'H', 'R', 'M']);

  /* ------------------------------------------------------------------ 資料 */
  let cache = null;
  D.sfcAvail = () => !!(D.man && D.man.files && D.man.files.sfc);
  /** sfc.json → {sfc, edges, cut, out:Map(key→[edge]), inc:Map(key→[edge]), cutBy:Map(key→[source]), byCtrl:Map(ctrl→n),
   *  byVar:Map(變數全名→{key, role:介面腳名|'step'|'trans', n?})（介面變數與步驟／轉移的 Active_Flag；訊號頁連回序列頁）}；舊匯出 → null */
  D.sfcData = function () {
    if (!D.sfcAvail()) return Promise.resolve(null);
    if (!cache) {
      cache = D.json(D.man.files.sfc).then(prep);
      cache.catch(() => { cache = null; });
    }
    return cache;
  };
  function prep(d) {
    if (!d || !d.sfc) return null;
    const out = new Map(), inc = new Map(), cutBy = new Map(), byCtrl = new Map();
    const push = (m, k, v) => { if (!m.has(k)) m.set(k, []); m.get(k).push(v); };
    for (const e of d.edges || []) { push(out, e.f, e); push(inc, e.t, e); }
    for (const c of d.cut || []) push(cutBy, c[0], c[1]);
    const byVar = new Map();
    const note = (full, v) => { if (full && !byVar.has(full)) byVar.set(full, v); };
    for (const k of Object.keys(d.sfc)) {
      const c = splitKey(k).ctrl, s = d.sfc[k];
      byCtrl.set(c, (byCtrl.get(c) || 0) + 1);
      for (const p of Object.keys(s.iface || {})) note(s.iface[p], { key: k, role: p });
      for (const st of s.steps || []) note(st.flag, { key: k, role: 'step', n: st.n });
      for (const t of s.trans || []) note(t.flag, { key: k, role: 'trans', n: t.n });
    }
    return { sfc: d.sfc, edges: d.edges || [], cut: d.cut || [], out, inc, cutBy, byCtrl, byVar };
  }
  /** 'CTRL|Program/Task' → {ctrl, prog, task}（Program 是第一段） */
  function splitKey(key) {
    const i = key.indexOf('|');
    const path = key.slice(i + 1), j = path.indexOf('/');
    return { ctrl: key.slice(0, i), prog: j < 0 ? path : path.slice(0, j), task: j < 0 ? '' : path.slice(j + 1) };
  }
  D.hrefSeq = (key) => { const s = splitKey(key); return '#/q/' + D.enc(s.ctrl) + '/' + D.enc(s.prog) + '/' + D.enc(s.task); };
  const hrefOverview = (ctrl, ind) => '#/q' + (ctrl || ind ? '?' + [ctrl ? 'c=' + encodeURIComponent(ctrl) : null, ind ? 'ind=1' : null].filter(Boolean).join('&') : '');
  /** 方塊鍵 → Task 圖並選取該方塊（巢狀 UserBlock 內的方塊開父層的 ?ub=，同 dtask.js 的 extHref） */
  function hrefBlockD(k) {
    const i = k.indexOf('|'), ctrl = k.slice(0, i), segs = k.slice(i + 1).split('/');
    if (segs.length < 3) return D.hrefD(ctrl, segs[0], segs[1] || '');
    return D.hrefD(ctrl, segs[0], segs[1], segs.length > 3 ? { ub: segs.slice(0, -1).join('/'), b: k } : { b: k });
  }
  /** 'CTRL|Program/Task' 序列內某 UserBlock 的 Task 圖（?ub=） */
  const hrefUb = (key, rel) => { const s = splitKey(key); return D.hrefD(s.ctrl, s.prog, s.task, { ub: s.prog + '/' + s.task + '/' + rel }); };
  const shortVar = (full) => { const i = String(full).indexOf('.'); return i < 0 ? full : full.slice(i + 1); };
  const varLink = (full, short) => D.h('a', { href: D.hrefV(full), class: 'lk mono', text: short ? shortVar(full) : full, title: full });
  const seqName = (key) => { const s = splitKey(key); return s.ctrl + '/' + s.task; };
  const seqLink = (key, cls) => D.h('a', { href: D.hrefSeq(key), class: 'lk mono' + (cls ? ' ' + cls : ''), text: seqName(key), title: key.replace('|', '/') });
  const cite = (file, line) => D.mono((file || '?') + ':' + (line == null ? '?' : line), 'muted small');
  /** 路徑經過的方塊數（不含 task／巨集容器：進出介面腳的那一跳另計） */
  const nBlocks = (p) => new Set(p.filter((h) => h.k && !h.ub).map((h) => h.k)).size;
  const nIface = (p) => new Set(p.filter((h) => h.k && h.ub).map((h) => h.k)).size;
  const blkText = (p) => { const m = nIface(p); return nBlocks(p) + ' 個方塊' + (m ? '（另經 ' + m + ' 個 task／巨集介面）' : ''); };
  /** w 徽章只在直接邊顯示，而且寫入 RUN 的 MOVE 不在路徑進入的介面巨集裡（那是介面本身「ENABLE 接 RUN 腳、SRC 常數」的固定寫法） */
  function showW(e) {
    if (!e.d || e.w == null) return false;
    const bs = e.p.filter((h) => h.k), last = bs[bs.length - 1];
    return !(last && bs.some((h) => h.ub && last.k.startsWith(h.k + '/')));
  }
  /** 間接邊路徑上「不是單純傳遞」的方塊型別（RUNG、LATCH…） */
  const gateTypes = (p) => Array.from(new Set(p.filter((h) => h.k && !h.ub && !h.op && !PASS.has(h.type)).map((h) => h.type || '?')));
  const dsSplit = (ds) => { const s = String(ds || '?/-').split('/'); return [s[0] || '?', s[1] || '-']; };

  /* ------------------------------------------------------------------ 徽章 */
  const B = {
    sk: (sk) => D.tag(sk === 't' ? '轉移' : '步驟', 'sq-sk'),
    d: (d) => (d ? D.h('span', { class: 'tag sq-direct', text: '直接', title: '路徑只經過 MOVE／AND／OR／PULSE、介面腳、加密巨集與 EGD' })
      : D.h('span', { class: 'tag sq-indirect', text: '間接', title: '路徑經過 RUNG、LATCH、計時器、比較器等方塊：可能是許可、重置或停止條件，不一定代表啟動' })),
    inf: () => D.h('span', { class: 'tag sq-inf', text: '推斷', title: '路徑上有方向來源 L／H／R／M 或方向未知（?）的腳' }),
    op: () => D.h('span', { class: 'tag warn', text: '加密巨集，內部看不到', title: '路徑經過不透明（加密）巨集：照其回推／圖面腳位走，內部邏輯看不到' }),
    ub: () => D.h('span', { class: 'tag', text: '介面腳', title: 'task／巨集的介面腳：路徑由此進入或離開，邏輯在裡面' }),
    w: (w) => D.h('span', { class: 'tag sq-w', text: '寫入 RUN 的常數 ' + w, title: '路徑最後一個 MOVE 的 SRC 字面常數（原文）' }),
    sv: (v) => D.h('span', { class: 'tag ' + (/^(0|false)$/i.test(v) ? 'warn' : 'sq-w'), text: '來源寫入 ' + v,
      title: '來源步驟／轉移自己的 MOVE 把常數 ' + v + ' 寫進路徑起點的變數（原文）' + (/^(0|false)$/i.test(v) ? '：這一步是把它清掉' : '') }),
  };
  function edgeBadges(e) {
    return [B.sk(e.sk), ' ', B.d(e.d), e.inf ? [' ', B.inf()] : null, e.op ? [' ', B.op()] : null, e.sv != null ? [' ', B.sv(e.sv)] : null, showW(e) ? [' ', B.w(e.w)] : null];
  }

  /* ------------------------------------------------------------------ 逐跳路徑 */
  /** 路徑元素 → 顯示列：同一方塊連續的腳（入→出）併成一列 */
  function groupPath(p) {
    const out = [];
    for (const h of p) {
      if (h.k) {
        const last = out[out.length - 1];
        const pin = { pin: h.pin, ds: h.ds, at: h.at };
        if (last && last.kind === 'b' && last.k === h.k) { last.pins.push(pin); last.ub = last.ub || h.ub; last.op = last.op || h.op; }
        else out.push({ kind: 'b', k: h.k, type: h.type, pins: [pin], ub: h.ub, op: h.op });
      } else if (h.egd != null) out.push({ kind: 'egd', egd: h.egd, to: h.to });
      else if (h.v) out.push({ kind: 'v', v: h.v });
    }
    return out;
  }
  function pathList(p) {
    let lastTask = '';
    return D.h('ol', { class: 'sq-path' }, groupPath(p).map((g) => {
      if (g.kind === 'v') return D.h('li', { class: 'sq-hop v' }, D.h('span', { class: 'sq-hk', text: '變數' }), varLink(g.v));
      if (g.kind === 'egd') return D.h('li', { class: 'sq-hop egd' }, D.h('span', { class: 'sq-hk', text: 'EGD' }), 'EGD 交換 ' + g.egd + ' → ', D.mono(g.to, 'b'));
      const i = g.k.indexOf('|'), ctrl = g.k.slice(0, i), segs = g.k.slice(i + 1).split('/');
      const tk = ctrl + '/' + segs.slice(0, 2).join('/');
      const rel = segs.length > 2 ? segs.slice(2).join('/') : segs.join('/');
      const ctx = tk !== lastTask ? D.h('div', { class: 'sq-hctx muted small mono', text: tk }) : null;
      lastTask = tk;
      const file0 = String(g.pins[0].at || '').replace(/:\d+$/, '');
      const ats = g.pins.map((x, n) => { const at = String(x.at || '?'); return n && at.startsWith(file0 + ':') ? at.slice(file0.length) : at; });
      return D.h('li', { class: 'sq-hop b' + (g.op ? ' op' : '') },
        D.h('span', { class: 'sq-hk', text: '方塊' }),
        D.h('div', { class: 'sq-hb' }, ctx,
          D.h('div', null, D.h('a', { href: hrefBlockD(g.k), class: 'lk mono b', text: rel, title: '在 Task 邏輯圖中選取 ' + g.k.replace('|', '/') }), ' ', D.h('span', { class: 'ref-type', text: '[' + (g.type || '?') + ']' }),
            g.ub ? [' ', B.ub()] : null, g.op ? [' ', B.op()] : null),
          D.h('div', { class: 'sq-pins' }, g.pins.map((x, n) => { const [dir, src] = dsSplit(x.ds); return D.frag(n ? D.h('span', { class: 'muted', text: ' → ' }) : null, D.mono('.' + x.pin, 'b'), ' ', D.dirBadge(dir), D.srcBadge(src)); }),
            ' ', D.h('span', { class: 'mono muted small sq-at', text: ats.join(', ') }))));
    }));
  }
  /** 一條啟動鏈邊：徽章＋事實句＋可展開的路徑（第一次展開才建 DOM） */
  function edgeItem(e, side) {
    const n = blkText(e.p), who = e.sk === 't' ? '轉移' : '步驟';
    const gates = e.d ? [] : gateTypes(e.p);
    const sentence = side === 'out'
      ? D.frag('此', who, ' ', D.mono(e.s, 'b'), ' 的輸出經 ', n, e.d ? '到達 ' : '連到 ', seqLink(e.t, 'b'), ' 的 RUN')
      : D.frag(seqLink(e.f, 'b'), ' 的', who, ' ', D.mono(e.s, 'b'), ' 的輸出經 ', n, e.d ? '到達' : '連到', '本序列的 RUN');
    const note = e.d ? null : D.h('div', { class: 'muted small' }, '間接：路徑經過 ', gates.join('、') || '非傳遞方塊', '；可能是許可、重置或停止條件，不一定代表啟動。');
    const body = D.h('div', { class: 'sq-pbody' });
    const det = D.h('details', { class: 'sq-pdet' }, D.h('summary', { class: 'small', text: '路徑（' + nBlocks(e.p) + ' 個方塊' + (nIface(e.p) ? '、' + nIface(e.p) + ' 個介面' : '') + '、' + e.p.filter((h) => h.v).length + ' 個變數' + (e.p.some((h) => h.egd != null) ? '、經 EGD' : '') + '）' }), body);
    det.addEventListener('toggle', () => { if (det.open && !body.firstChild) D.set(body, pathList(e.p)); });
    return D.h('li', { class: 'sq-edge' + (e.d ? '' : ' ind') }, D.h('div', { class: 'sq-eb' }, edgeBadges(e)), D.h('div', { class: 'sq-es' }, sentence), note, det);
  }

  /* ------------------------------------------------------------------ 圖（dagre → SVG） */
  let mctx = null, uid = 0;
  function tw(s, px, bold) {
    if (!mctx) mctx = document.createElement('canvas').getContext('2d');
    mctx.font = (bold ? '700 ' : '') + px + 'px Consolas, "Cascadia Mono", ui-monospace, monospace';
    return mctx.measureText(String(s == null ? '' : s)).width;
  }
  const r1 = (v) => Math.round(v * 10) / 10;
  const clip = (s, n) => (s.length > n ? s.slice(0, n - 1) + '…' : s);
  /** nodes [{id, label, sub, href, cls, title, onClick}]、edges [{from, to, label, cls}] → {svg, W, H}；同一對節點的邊併成一條（標籤合併） */
  function layoutSvg(nodes, edges, opt) {
    const g = new dagre.graphlib.Graph({ multigraph: true });
    g.setGraph({ rankdir: opt.rankdir || 'LR', nodesep: opt.nodesep || 18, ranksep: opt.ranksep || 60, edgesep: 12, marginx: 14, marginy: 14, acyclicer: 'greedy', ranker: 'network-simplex' });
    g.setDefaultEdgeLabel(() => ({}));
    const byId = new Map();
    for (const n of nodes) {
      n.w = Math.ceil(Math.max(tw(n.label, 12, true), n.sub ? tw(n.sub, 10.5) : 0) + 22);
      n.h = n.sub ? 38 : 26;
      g.setNode(n.id, { width: n.w, height: n.h });
      byId.set(n.id, n);
    }
    const pairs = new Map();
    for (const e of edges) {
      if (e.from === e.to || !byId.has(e.from) || !byId.has(e.to)) continue;
      const k = e.from + '\n' + e.to;
      const p = pairs.get(k) || { from: e.from, to: e.to, labels: [], refs: [], cls: e.cls, onClick: e.onClick };
      if (e.label && p.labels.indexOf(e.label) < 0) p.labels.push(e.label);
      if (e.ref != null && p.refs.indexOf(e.ref) < 0) p.refs.push(e.ref);
      if (e.cls !== p.cls) p.cls = p.cls || e.cls;
      pairs.set(k, p);
    }
    let k = 0;
    for (const p of pairs.values()) {
      const full = p.labels.join(' / ');
      p.text = p.labels.length > 3 ? clip(p.labels.slice(0, 2).join(' / '), 40) + ' …（共 ' + p.labels.length + '）' : clip(full, 48);
      p.title = full;
      p.id = 'e' + (k++);
      const lw = p.text ? Math.ceil(tw(p.text, 10.5) + 8) : 0;
      g.setEdge(p.from, p.to, { label: p.text, width: lw, height: p.text ? 15 : 0, labelpos: 'c', minlen: 1, weight: p.labels.length || 1 }, p.id);
    }
    let W, H, ok = true;
    try { dagre.layout(g); W = g.graph().width; H = g.graph().height; }
    catch (err) {
      console.warn('dagre', err); ok = false;
      let y = 14; W = 0;
      for (const n of nodes) { n.x = 14 + n.w / 2; n.y = y + n.h / 2; y += n.h + 18; W = Math.max(W, n.w + 28); }
      H = y;
    }
    if (ok) for (const n of nodes) { const gn = g.node(n.id); n.x = gn.x; n.y = gn.y; }
    W = Math.max(Math.ceil(W || 0), 40); H = Math.max(Math.ceil(H || 0), 40);
    const mk = 'sq-arr-' + (++uid);
    const svg = D.svg('svg', { class: 'sq-svg', xmlns: 'http://www.w3.org/2000/svg', viewBox: '0 0 ' + W + ' ' + H, width: String(W), height: String(H), role: 'img', 'aria-label': opt.title || '圖' },
      D.svg('defs', null, D.svg('marker', { id: mk, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' }, D.svg('path', { d: 'M0 0 L10 5 L0 10 z', class: 'sq-mk' }))));
    const gE = D.svg('g', { class: 'sq-edges' }), gL = D.svg('g', { class: 'sq-labels' }), gN = D.svg('g', { class: 'sq-nodes' });
    for (const p of pairs.values()) {
      const a = byId.get(p.from), b = byId.get(p.to);
      let pts = ok ? (g.edge(p.from, p.to, p.id) || {}).points : null;
      if (!pts || !pts.length) pts = [{ x: a.x, y: a.y + a.h / 2 }, { x: b.x, y: b.y - b.h / 2 }];
      const d = 'M' + pts.map((q) => r1(q.x) + ' ' + r1(q.y)).join(' L');
      gE.appendChild(D.svg('path', { class: 'sq-e' + (p.cls ? ' ' + p.cls : ''), d, 'marker-end': 'url(#' + mk + ')' }, D.svg('title', { text: seqTitle(a) + ' → ' + seqTitle(b) + (p.title ? '\n' + p.title : '') })));
      if (p.text) {
        const le = ok ? g.edge(p.from, p.to, p.id) : null;
        const mid = pts[Math.floor(pts.length / 2)];
        const x = le && Number.isFinite(le.x) ? le.x : mid.x, y = le && Number.isFinite(le.y) ? le.y : mid.y;
        const w = tw(p.text, 10.5) + 8;
        const lab = D.svg('g', { class: 'sq-el' + (p.onClick ? ' click' : ''), transform: 'translate(' + r1(x) + ' ' + r1(y) + ')', tabindex: p.onClick ? '0' : null, role: p.onClick ? 'button' : null },
          D.svg('title', { text: p.title }),
          D.svg('rect', { x: r1(-w / 2), y: '-8', width: r1(w), height: '15', rx: '3' }),
          D.svg('text', { x: '0', y: '3.5', 'text-anchor': 'middle', text: p.text }));
        if (p.onClick) bindClick(lab, () => p.onClick(p.refs, p.labels));
        gL.appendChild(lab);
      }
    }
    for (const n of nodes) {
      const kids = [D.svg('title', { text: n.title || n.label }),
        D.svg('rect', { class: 'sq-box', x: r1(-n.w / 2), y: r1(-n.h / 2), width: n.w, height: n.h, rx: '5' }),
        D.svg('text', { class: 'sq-nl', x: '0', y: n.sub ? '-2' : '4', 'text-anchor': 'middle', text: n.label }),
        n.sub ? D.svg('text', { class: 'sq-ns', x: '0', y: '12', 'text-anchor': 'middle', text: n.sub }) : null];
      const tr = 'translate(' + r1(n.x) + ' ' + r1(n.y) + ')';
      let el;
      if (n.href) el = D.svg('a', { class: 'sq-node' + (n.cls ? ' ' + n.cls : ''), href: n.href, transform: tr }, kids);
      else {
        el = D.svg('g', { class: 'sq-node' + (n.cls ? ' ' + n.cls : '') + (n.onClick ? ' click' : ''), transform: tr, tabindex: n.onClick ? '0' : null, role: n.onClick ? 'button' : null }, kids);
        if (n.onClick) bindClick(el, n.onClick);
      }
      gN.appendChild(el);
    }
    svg.append(gE, gN, gL);
    return { svg, W, H, ok };
  }
  function seqTitle(n) { return n.title || n.label; }
  function bindClick(el, fn) {
    el.addEventListener('click', (e) => { if (el.closest('.sq-canvas.dragged')) return; e.preventDefault(); fn(); });
    el.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } });
  }
  /** 可捲動的畫布＋縮放列（−／＋／符合寬度／全圖／100%；Ctrl＋滾輪縮放；滑鼠拖曳平移）。插入 DOM 後呼叫 .fit() */
  function zoomBox(lay, label) {
    const { svg, W, H } = lay;
    const box = D.h('div', { class: 'sq-canvas', tabindex: '0', 'aria-label': label + '（可捲動；Ctrl＋滾輪縮放）' }, svg);
    let z = 1;
    const pct = D.h('span', { class: 'muted small sq-zpct' });
    const apply = () => { svg.setAttribute('width', String(Math.round(W * z))); svg.setAttribute('height', String(Math.round(H * z))); pct.textContent = Math.round(z * 100) + '%'; };
    const ZMIN = 0.05;
    const clamp = (v) => Math.min(3, Math.max(ZMIN, v));
    const fitW = (minZ) => { const cw = Math.max(box.clientWidth - 4, 100); z = Math.min(1, Math.max(minZ == null ? 0.15 : minZ, cw / W)); apply(); };
    /** 全圖：寬高都放進畫布（畫布高度上限取 CSS max-height；很長的序列字會很小，用來看全貌，再放大） */
    const fitAll = () => {
      const cw = Math.max(box.clientWidth - 4, 100);
      const mh = parseFloat(getComputedStyle(box).maxHeight);
      const ch = Math.max((Number.isFinite(mh) ? mh : box.clientHeight) - 4, 100);
      z = Math.min(1, Math.max(ZMIN, Math.min(cw / W, ch / H))); apply(); box.scrollLeft = 0; box.scrollTop = 0;
    };
    const btn = (text, title, fn) => D.h('button', { type: 'button', class: 'btn sm', text, title, onclick: fn });
    const bar = D.h('div', { class: 'sq-zbar' }, btn('−', '縮小', () => { z = clamp(z / 1.25); apply(); }), btn('＋', '放大', () => { z = clamp(z * 1.25); apply(); }),
      btn('符合寬度', '縮放到容器寬度', () => fitW()), btn('全圖', '整張圖放進畫布（寬與高）', fitAll), btn('100%', '原尺寸', () => { z = 1; apply(); }), pct);
    box.addEventListener('wheel', (e) => { if (!e.ctrlKey && !e.metaKey) return; e.preventDefault(); z = clamp(z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)); apply(); }, { passive: false });
    let drag = null;
    const onMove = (e) => {
      if (!drag || !box.isConnected) { onUp(); return; }
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.moved = Math.max(drag.moved, Math.abs(dx), Math.abs(dy));
      if (drag.moved >= 4) { box.classList.add('dragging', 'dragged'); box.scrollLeft = drag.sl - dx; box.scrollTop = drag.st - dy; }
    };
    function onUp() {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (drag) { drag = null; box.classList.remove('dragging'); setTimeout(() => box.classList.remove('dragged'), 0); }
    }
    box.addEventListener('pointerdown', (e) => { // 視窗層的 move／up 只在拖曳期間掛上
      if (e.pointerType !== 'mouse' || e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, sl: box.scrollLeft, st: box.scrollTop, moved: 0 };
      box.classList.remove('dragged');
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
    box.addEventListener('click', (e) => { if (box.classList.contains('dragged')) { e.preventDefault(); e.stopPropagation(); } }, true);
    apply();
    return { el: D.h('div', { class: 'sq-graph' }, bar, box), fit: fitW };
  }
  const dagreP = () => D.loadScript('dagre.min.js');

  /* ------------------------------------------------------------------ 頁面 */
  D.page('q', async (ctx) => {
    const { route, view } = ctx;
    D.set(view, D.loading('載入序列資料…'));
    if (!D.sfcAvail()) { D.setTitle('序列'); D.set(view, header([D.link('#/', '搜尋'), ' › 序列'], '序列（SFC）', null), D.errorBox('此匯出沒有序列資料', '這份資料是舊版匯出，沒有 sfc.json；重新匯出後才有序列頁。')); return; }
    const data = await D.sfcData();
    if (!ctx.alive()) return;
    if (!data) { D.setTitle('序列'); D.set(view, header([D.link('#/', '搜尋'), ' › 序列'], '序列（SFC）', null), D.errorBox('此匯出沒有序列資料', 'sfc.json 不存在或內容不完整。')); return; }
    if (route.segs.length >= 3) return seqPage(ctx, data);
    return overview(ctx, data);
  });

  function header(kicker, title, sub, actions) {
    return D.h('div', { class: 'ph' }, D.h('div', { class: 'ph-kicker' }, kicker),
      D.h('h1', { class: 'ph-title' }, title), sub ? D.h('p', { class: 'ph-sub' }, sub) : null, actions ? D.h('div', { class: 'ph-actions' }, actions) : null);
  }
  function cutBox(list) {
    if (!list.length) return null;
    return D.h('div', { class: 'sq-cut' }, D.h('p', { class: 'warn-text b', text: '走訪超過上限，鏈路可能不完整（' + list.length + ' 個來源）' }),
      D.h('ul', { class: 'plain' }, list.map(([k, s]) => D.h('li', null, seqLink(k), ' ', D.mono(s, 'b')))));
  }

  /* ---- #/q 總覽 */
  async function overview({ route, view, alive }, data) {
    D.setTitle('序列');
    const keys = Object.keys(data.sfc).sort();
    const ctrls = Array.from(data.byCtrl.keys()).sort();
    let ctrl = route.query.c && data.byCtrl.has(route.query.c) ? route.query.c : '';
    const ind0 = route.query.ind === '1';
    const filter = D.h('input', { type: 'search', class: 'filter', placeholder: '篩選序列名／程式…', 'aria-label': '篩選序列' });
    const chips = D.h('div', { class: 'chips sq-chips', role: 'group', 'aria-label': '控制器' });
    const listEl = D.h('div', { class: 'secs' });
    const dagEl = D.h('div');
    const indEl = D.h('div');
    const nD = data.edges.filter((e) => e.d).length;
    D.set(view,
      header([D.link('#/', '搜尋'), ' › 序列'], '序列（SFC）',
        D.frag(D.int(keys.length), ' 個序列（', ctrls.length, ' 個控制器）· ', D.int(data.edges.length), ' 條啟動鏈關聯（直接 ', D.int(nD), '、間接 ', D.int(data.edges.length - nD), '）'),
        [filter]),
      D.h('p', { class: 'muted small' }, '啟動鏈 = 一個序列的步驟或轉移發出的變數，往下游走到另一個序列 RUN 介面變數的路徑（索引看得到的部分）。「直接」只經 MOVE／AND／OR／PULSE、介面腳、加密巨集與 EGD；「間接」經過 RUNG、LATCH、計時器等，可能是許可、重置或停止條件。'),
      D.h('p', { class: 'muted small' }, '搜尋深度：走到某個變數時若已經過 10 個方塊跳（進入 task／巨集介面也算一跳）就不再往下找；之後以 L: 直接接線的方塊仍會走完，所以列出的路徑可能超過 10 個方塊。更遠的關聯不會列出，也不算在「走訪超過上限」裡。'),
      cutBox(data.cut), chips, D.h('div', { class: 'secs' }, dagEl, indEl, listEl));
    const inScope = (k) => !ctrl || splitKey(k).ctrl === ctrl;
    const eScope = (e) => !ctrl || splitKey(e.f).ctrl === ctrl || splitKey(e.t).ctrl === ctrl;
    /** 篩選框對序列清單比對序列鍵，對啟動鏈圖與間接表比對來源序列、目標序列與步驟／轉移名 */
    const eMatch = (e, q) => !q || (e.f + ' ' + e.t + ' ' + e.s).toLowerCase().includes(q);
    function renderChips() {
      const chip = (c, text) => D.h('button', { type: 'button', class: 'chip-btn' + (ctrl === c ? ' on' : ''), 'aria-pressed': ctrl === c ? 'true' : 'false', text, onclick: () => { ctrl = c; sync(); renderChips(); drawAll(); } });
      D.set(chips, chip('', '全部 ' + keys.length), ctrls.map((c) => chip(c, c + ' ' + data.byCtrl.get(c))));
    }
    function sync() { const h = hrefOverview(ctrl, indDet && indDet.open); if (location.hash !== h) history.replaceState(null, '', h); }
    function drawList() {
      const q = filter.value.trim().toLowerCase();
      const ks = keys.filter((k) => inScope(k) && (!q || k.toLowerCase().includes(q)));
      const groups = new Map();
      for (const k of ks) { const c = splitKey(k).ctrl; if (!groups.has(c)) groups.set(c, []); groups.get(c).push(k); }
      const cnt = (arr, d) => (arr || []).filter((e) => !!e.d === d).length;
      D.set(listEl, groups.size ? Array.from(groups, ([c, arr]) => D.section(c, D.table(['序列', '程式', '步驟', '轉移', '直接 出／入', '間接 出／入', '出處'], arr.map((k) => {
        const s = data.sfc[k], sk = splitKey(k), o = data.out.get(k), i = data.inc.get(k);
        return [D.h('a', { href: D.hrefSeq(k), class: 'lk mono b', text: sk.task }), D.mono(sk.prog, 'small'), String((s.steps || []).length), String((s.trans || []).length),
          D.mono(cnt(o, true) + ' ／ ' + cnt(i, true)), D.mono(cnt(o, false) + ' ／ ' + cnt(i, false), 'muted'), cite(s.file, s.line)];
      }), 'sq-list'), { count: arr.length })) : D.empty(q ? '沒有符合「' + q + '」的序列' : '沒有序列'));
    }
    let indDet = null;
    function drawInd() {
      const q = filter.value.trim().toLowerCase();
      const es = data.edges.filter((e) => !e.d && eScope(e) && eMatch(e, q));
      const wasOpen = indDet ? indDet.open : ind0;
      const body = es.length ? D.table(['來源序列', '步驟／轉移', '目標序列（RUN）', '方塊數', '標記'], es.map((e) => [seqLink(e.f), D.frag(B.sk(e.sk), ' ', D.mono(e.s, 'b')), seqLink(e.t),
        String(nBlocks(e.p)), D.frag(e.inf ? B.inf() : null, e.op ? [' ', B.op()] : null, e.sv != null ? [' ', B.sv(e.sv)] : null, ' ', D.h('span', { class: 'muted small', text: gateTypes(e.p).join('、') }))]), 'sq-ind')
        : D.empty(q ? '沒有符合「' + q + '」的間接關聯' : '此範圍沒有間接關聯');
      indDet = D.section('間接關聯（不一定代表啟動）', D.frag(D.h('p', { class: 'muted small', text: '路徑經過 RUNG、LATCH、計時器、比較器等方塊才到另一個序列的 RUN：可能是許可、重置或停止條件，本頁不判定作用。標記欄列出路徑上這些方塊的型別；逐跳路徑在各序列頁。方塊數不含 task／巨集介面。' }), body),
        { count: es.length, open: wasOpen });
      indDet.addEventListener('toggle', sync);
      D.set(indEl, indDet);
    }
    let dagSeq = 0;
    async function drawDag() {
      const seq = ++dagSeq;
      const q = filter.value.trim().toLowerCase();
      const es = data.edges.filter((e) => e.d && eScope(e) && eMatch(e, q));
      const sec = (body, note) => D.set(dagEl, D.section('啟動鏈（直接）', body, { count: es.length, note }));
      if (!es.length) { sec(D.empty(q ? '沒有符合「' + q + '」的直接啟動鏈' : ctrl ? ctrl + ' 沒有直接啟動鏈（間接關聯見下方）' : '沒有直接啟動鏈')); return; }
      sec(D.loading('載入排版引擎…'));
      await dagreP();
      if (seq !== dagSeq || !alive()) return;
      const ids = new Set();
      for (const e of es) { ids.add(e.f); ids.add(e.t); }
      const nodes = Array.from(ids).sort().map((k) => { const s = splitKey(k); return { id: k, label: s.task, sub: s.ctrl + ' / ' + s.prog, href: D.hrefSeq(k), title: k.replace('|', '/') + '\n開啟序列頁', cls: ctrl && s.ctrl !== ctrl ? 'other' : '' }; });
      const lay = layoutSvg(nodes, es.map((e) => ({ from: e.f, to: e.t, label: e.s })), { rankdir: 'LR', ranksep: 90, nodesep: 14, title: '直接啟動鏈' });
      const zb = zoomBox(lay, '直接啟動鏈');
      const nOut = q ? 0 : keys.filter(inScope).filter((k) => !ids.has(k)).length;
      sec(D.frag(D.h('p', { class: 'muted small' }, '節點 = 序列（點擊開啟），箭頭 = 步驟或轉移的輸出經直接路徑到達目標序列的 RUN，標籤 = 來源步驟／轉移名；同一對序列的多條邊併成一條。', q ? ' 只畫符合篩選「' + q + '」的邊（比對來源序列、目標序列與步驟／轉移名）。' : '', nOut ? ' 此範圍另有 ' + nOut + ' 個序列不在直接啟動鏈中。' : ''),
        lay.ok ? null : D.h('p', { class: 'warn-text small', text: '排版失敗，改用簡單排列。' }), zb.el), ids.size + ' 個序列');
      requestAnimationFrame(() => zb.fit(0.5));
    }
    function drawAll() { drawList(); drawInd(); drawDag().catch((e) => { console.warn(e); D.set(dagEl, D.errorBox('無法繪製啟動鏈', (e && e.message) || String(e))); }); }
    filter.addEventListener('input', D.debounce(drawAll, 200));
    renderChips();
    drawAll();
  }

  /* ---- #/q/<CTRL>/<Program>/<Task> 單一序列 */
  async function seqPage({ route, view, alive }, data) {
    const [ctrl, prog, task] = route.segs;
    const key = ctrl + '|' + prog + '/' + route.segs.slice(2).join('/');
    const s = data.sfc[key];
    D.setTitle('序列 ' + task + ' (' + ctrl + ')');
    const kick = [D.link('#/', '搜尋'), ' › ', D.link(hrefOverview(ctrl), '序列'), ' › ', D.mono(ctrl), ' › ', D.link(D.hrefP(ctrl, prog), prog, 'lk mono')];
    if (!s) {
      D.set(view, header(kick, D.mono(task || '?', 'b'), null), D.errorBox('找不到此序列', key.replace('|', '/') + '（sfc.json 沒有這個 task；它可能不是 SFC task）'),
        D.h('p', null, D.link(hrefOverview(ctrl), '所有序列', 'btn'), ' ', task ? D.link(D.hrefD(ctrl, prog, task), 'Task 邏輯圖', 'btn') : null));
      return;
    }
    const steps = (s.steps || []).slice().sort((a, b) => (a.i == null ? 1e9 : a.i) - (b.i == null ? 1e9 : b.i));
    const trans = (s.trans || []).slice().sort((a, b) => (a.i == null ? 1e9 : a.i) - (b.i == null ? 1e9 : b.i));
    const stepBy = new Map(), stepsAt = new Map();      // i → 第一個步驟、i → 同索引的全部步驟
    for (const x of steps) if (x.i != null) { if (!stepBy.has(x.i)) stepBy.set(x.i, x); if (!stepsAt.has(x.i)) stepsAt.set(x.i, []); stepsAt.get(x.i).push(x); }
    const noIdx = steps.filter((x) => x.i == null), dupIdx = Array.from(stepsAt.values()).filter((a) => a.length > 1);
    const stepName = (i) => (stepBy.has(i) ? stepBy.get(i).n : 'Steps[' + i + ']');
    const out = (data.out.get(key) || []).slice().sort((a, b) => b.d - a.d || (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
    const inc = (data.inc.get(key) || []).slice().sort((a, b) => b.d - a.d || (a.f < b.f ? -1 : a.f > b.f ? 1 : 0));
    const cuts = (data.cutBy.get(key) || []).map((n) => [key, n]);

    // 介面變數
    const iface = s.iface || {};
    const ifRows = IFACE.filter((p) => iface[p]).map((p) => [D.mono(p, 'b'), varLink(iface[p])]);
    for (const p of Object.keys(iface)) if (IFACE.indexOf(p) < 0) ifRows.push([D.mono(p, 'b'), varLink(iface[p])]);

    // 步驟／轉移圖
    const graphEl = D.h('div');
    // 表格列以物件對應（不用 id：索引缺漏或重複時 id 會撞）；圖上點擊閃爍所有對應列，捲到第一列
    const rowOf = new Map();
    const flash = (objs) => {
      const els = objs.map((o) => rowOf.get(o)).filter(Boolean);
      if (!els.length) return;
      const det = els[0].closest('details'); if (det) det.open = true;
      els[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
      for (const el of els) { el.classList.remove('sq-flash'); void el.offsetWidth; el.classList.add('sq-flash'); }
    };
    const bad = trans.filter((t) => t.bad);
    const half = trans.filter((t) => !t.bad && (!(t.f || []).length || !(t.t || []).length));

    // 轉移表
    const tRows = trans.map((t) => {
      const ft = t.bad ? D.h('span', { class: 'warn-text', text: 'Configuration 解不開' })
        : D.frag(stepsCell(t.f, stepName), D.h('span', { class: 'muted', text: ' → ' }), stepsCell(t.t, stepName));
      const reads = (t.r || []).map(([v, src]) => D.h('span', { class: 'sq-rv' }, varLink(v, true), ' ', D.srcBadge(src)));
      const tr = D.h('tr', null,
        D.h('td', { class: 'nowrap mono' }, t.i == null ? '?' : String(t.i)),
        D.h('td', null, D.mono(t.n, 'b'), t.flag ? D.h('div', { class: 'small' }, varLink(t.flag, true)) : null),
        D.h('td', null, ft),
        D.h('td', null, t.c ? D.h('a', { href: hrefUb(key, 'Evaluate_Transitions/' + t.n + '/' + t.c), class: 'lk nowrap', text: '條件邏輯', title: t.c + '（在 Task 邏輯圖中開啟條件內部）' }) : D.h('span', { class: 'muted', text: '—' })),
        D.h('td', null, reads.length ? D.h('div', { class: 'sq-rvs' }, reads) : D.h('span', { class: 'muted', text: '—' })),
        D.h('td', { class: 'nowrap' }, cite(s.file, t.line)));
      rowOf.set(t, tr);
      return tr;
    });
    // 步驟表
    const sRows = steps.map((st) => {
      const acts = (st.a || []).map((a) => {
        const w = a.w || [];
        const q = a.q ? String(a.q).replace(/^E:/, '') : null;
        const ws = w.map((v) => varLink(v, true));
        return D.h('div', { class: 'sq-act' }, D.mono(a.n, 'b'), q ? [' ', D.h('span', { class: 'tag sq-q', text: q, title: 'Action_Qualifier = ' + a.q }), ' '] : ' ',
          w.length > 6 ? D.h('details', { class: 'sq-wdet' }, D.h('summary', { class: 'small', text: '寫入 ' + w.length + ' 個變數' }), D.h('div', { class: 'sq-rvs' }, ws))
            : w.length ? D.h('span', { class: 'sq-rvs inl' }, D.h('span', { class: 'muted small', text: '寫入 ' }), ws) : D.h('span', { class: 'muted small', text: '（沒有寫入變數的輸出腳）' }));
      });
      const tr = D.h('tr', { class: st.i === 0 ? 'sq-init' : null },
        D.h('td', { class: 'nowrap mono' }, st.i == null ? '?' : String(st.i), st.i === 0 ? D.frag(' ', D.tag('初始')) : null),
        D.h('td', null, D.h('a', { href: hrefUb(key, 'Perform_Step_Actions/' + st.n), class: 'lk mono b', text: st.n, title: '在 Task 邏輯圖中開啟此步驟' })),
        D.h('td', null, st.flag ? varLink(st.flag, true) : D.h('span', { class: 'muted', text: '—' })),
        D.h('td', null, acts.length ? acts : D.h('span', { class: 'muted', text: '—' })),
        D.h('td', { class: 'nowrap' }, cite(s.file, st.line)));
      rowOf.set(st, tr);
      return tr;
    });

    /** 邊清單：依另一端序列分組（同一目標／來源的多條邊放在同一個標題下） */
    const edgeUl = (list, side) => {
      const by = new Map();
      for (const e of list) { const k = side === 'out' ? e.t : e.f; if (!by.has(k)) by.set(k, []); by.get(k).push(e); }
      if (by.size === list.length) return D.h('ul', { class: 'plain sq-edges-l' }, list.map((e) => edgeItem(e, side)));
      return D.h('div', { class: 'sq-egrps' }, Array.from(by, ([k, es]) => D.h('div', { class: 'sq-egrp' },
        D.h('div', { class: 'sq-egh' }, D.h('span', { class: 'muted', text: side === 'out' ? '到 ' : '來自 ' }), seqLink(k, 'b'), ' ', D.h('span', { class: 'sec-n', text: String(es.length) })),
        D.h('ul', { class: 'plain sq-edges-l' }, es.map((e) => edgeItem(e, side))))));
    };
    const edgeList = (arr, side) => {
      const dir = arr.filter((e) => e.d), ind = arr.filter((e) => !e.d);
      if (!arr.length) return D.empty(side === 'out' ? '索引中沒有從本序列的步驟／轉移到其他序列 RUN 的路徑' : '索引中沒有其他序列的步驟／轉移到本序列 RUN 的路徑');
      return D.frag(
        dir.length ? edgeUl(dir, side) : D.h('p', { class: 'muted small', text: '沒有直接路徑。' }),
        ind.length ? D.h('details', { class: 'grp sq-indg', open: !dir.length }, D.h('summary', null, '間接 ', D.h('span', { class: 'sec-n', text: String(ind.length) }), D.h('span', { class: 'muted small', text: '（經 RUNG／LATCH／計時器等：可能是許可、重置或停止條件，不一定代表啟動）' })),
          edgeUl(ind, side)) : null);
    };
    // 任何一個走訪超過上限的來源都可能連到本序列的 RUN：有 cut 時上游清單一律註明可能不完整
    const incCut = data.cut.length ? D.h('div', { class: 'sq-cut' }, D.h('p', { class: 'warn-text b', text: '有 ' + data.cut.length + ' 個來源走訪超過上限，上游清單可能不完整' }),
      D.h('p', { class: 'small' }, '這些來源（任何序列的步驟或轉移）的路徑沒有走完，可能也連到本序列的 RUN；清單見 ', D.link(hrefOverview(''), '序列總覽'), '。')) : null;

    const nDirOut = out.filter((e) => e.d).length, nDirIn = inc.filter((e) => e.d).length;
    D.set(view,
      header(kick.concat([' › ', D.mono(task)]), D.frag('序列 ', D.mono(task, 'b')),
        D.frag(D.mono(ctrl + '/' + prog + '/' + task), ' · ', String(steps.length), ' 個步驟 · ', String(trans.length), ' 個轉移 · ', cite(s.file, s.line)),
        [D.link(D.hrefD(ctrl, prog, task), 'Task 邏輯圖', 'btn sm'), D.link(D.hrefB(ctrl, prog + '/' + task), '方塊頁', 'btn sm'), D.link(hrefOverview(ctrl), ctrl + ' 的所有序列', 'btn sm'),
          D.h('span', { class: 'muted small' }, '下游序列：直接 ' + nDirOut + '、間接 ' + (out.length - nDirOut) + ' · 上游序列：直接 ' + nDirIn + '、間接 ' + (inc.length - nDirIn))]),
      cutBox(cuts),
      D.h('div', { class: 'secs' },
        D.section('介面變數', ifRows.length ? D.table(['介面腳', '變數'], ifRows, 'sq-if') : D.empty('索引中沒有介面變數'), { count: ifRows.length }),
        D.section('步驟／轉移圖', graphEl, { note: '點步驟或轉移名跳到下方表格' }),
        D.section('上游／下游序列', D.frag(
          D.h('h4', { text: '下游：本序列的步驟／轉移 → 其他序列的 RUN' }), edgeList(out, 'out'),
          D.h('h4', { text: '上游：其他序列的步驟／轉移 → 本序列的 RUN' }), incCut, edgeList(inc, 'in'),
          D.h('p', { class: 'muted small', text: '路徑是索引中先找到的一條（廣度優先；同一來源同一目標一條）；不經過名為 RESET／PAUSE 的介面腳。方向字母：U/T/C 明文或查表，L/H/R/M 推斷（虛框），G 圖面。' })),
        { count: out.length + inc.length }),
        D.section('轉移', D.table(['#', '轉移（Active_Flag）', '來源步 → 目的步', '條件', '條件讀取的變數（方向來源）', '出處'], tRows, 'sq-trans'), { count: trans.length }),
        D.section('步驟', D.table(['#', '步驟', 'Active_Flag', '動作（限定詞、寫入的變數）', '出處'], sRows, 'sq-steps'), { count: steps.length })));

    // 圖（延遲：排版引擎）
    D.set(graphEl, D.loading('載入排版引擎…'));
    try { await dagreP(); } catch (e) { if (alive()) D.set(graphEl, D.errorBox('無法載入排版引擎', (e && e.message) || String(e))); return; }
    if (!alive()) return;
    const nodes = Array.from(stepsAt, ([i, at]) => ({ id: 's' + i, label: at[0].n, sub: 'Steps[' + i + ']' + (i === 0 ? ' · 初始' : '') + (at.length > 1 ? ' · 同索引 ' + at.length + ' 個' : ''), cls: i === 0 ? 'init' : '',
      title: at.map((x) => x.n).join('、') + '（Steps[' + i + ']）\n點擊跳到步驟表', onClick: () => flash(at) }));
    const seen = new Set(nodes.map((n) => n.id));
    const edges = [];
    for (const t of trans) {
      if (t.bad) continue;
      for (const f of t.f || []) for (const to of t.t || []) {
        for (const i of [f, to]) if (!seen.has('s' + i)) { seen.add('s' + i); nodes.push({ id: 's' + i, label: 'Steps[' + i + ']', sub: '步驟表中沒有', cls: 'unk' }); }
        edges.push({ from: 's' + f, to: 's' + to, label: t.n, ref: t, onClick: (refs) => flash(refs) });
      }
    }
    const selfT = trans.filter((t) => !t.bad && (t.f || []).some((f) => (t.t || []).indexOf(f) >= 0));
    if (!nodes.length) { D.set(graphEl, D.empty(noIdx.length ? '沒有可畫的步驟（' + noIdx.length + ' 個步驟的 Enable 沒有接 Steps[i]，見下方步驟表）' : '沒有步驟')); return; }
    const lay = layoutSvg(nodes, edges, { rankdir: 'TB', ranksep: 34, nodesep: 22, title: '步驟／轉移圖 ' + task });
    const zb = zoomBox(lay, '步驟／轉移圖');
    const tnames = (arr) => arr.map((t, n) => D.frag(n ? '、' : null, D.mono(t.n, 'b')));
    D.set(graphEl,
      D.h('p', { class: 'muted small' }, '方框 = 步驟（Steps[i]，粗框 = 初始步 i = 0），箭頭 = 轉移（Configuration 解碼的來源步 → 目的步），標籤 = 轉移名；同一對步驟的多個轉移併成一條。'),
      lay.ok ? null : D.h('p', { class: 'warn-text small', text: '排版失敗，改用簡單排列。' }),
      zb.el,
      bad.length ? D.h('p', { class: 'warn-text small' }, 'Configuration 解不開（圖上不畫）：', tnames(bad)) : null,
      half.length ? D.h('p', { class: 'muted small' }, 'Configuration 只有來源步或只有目的步（圖上不畫）：', tnames(half)) : null,
      selfT.length ? D.h('p', { class: 'muted small' }, '來源步與目的步相同（圖上不畫）：', tnames(selfT)) : null,
      noIdx.length ? D.h('p', { class: 'warn-text small' }, '步驟的 Enable 沒有接 Steps[i]（索引不明，圖上不畫）：', tnames(noIdx)) : null,
      dupIdx.length ? D.h('p', { class: 'warn-text small' }, '多個步驟接同一個 Steps[i]（圖上併成一個方框）：', dupIdx.map((a, n) => D.frag(n ? '；' : null, 'Steps[' + a[0].i + '] = ', tnames(a)))) : null);
    requestAnimationFrame(() => zb.fit(0.6));
  }
  function stepsCell(arr, stepName) {
    if (!arr || !arr.length) return D.h('span', { class: 'muted', text: '—' });
    return D.frag(arr.map((i, n) => D.frag(n ? ', ' : null, D.h('span', { class: 'mono', text: stepName(i), title: 'Steps[' + i + ']' }))));
  }
})();
