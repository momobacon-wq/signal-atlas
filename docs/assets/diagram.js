/* Signal Atlas — 邏輯方塊圖引擎 D.dg（Task 圖 dtask.js、訊號圖 dsignal.js 共用；本檔不含任何資料規則）
 *
 * 圖模型（由呼叫端建好再交給引擎）：
 *   Graph { nodes: Map<id,Node>, edges: Edge[], tags: Tag[], meta:{title,warn:[]…} }
 *   Node  { id, kind:'block'|'ub'|'comment'|'var'|'leaf', key, name, type, sub, desc, attrs, pins(原始 pin tuple), ord,
 *           left:[Port], right:[Port], body:[lines], cls, varFull(var 節點), href(leaf 連結) }
 *           desc：方塊描述（型別行下方一行 .bdesc）／var 節點的變數描述（pill 第二行）
 *   Port  { id:'<node>#<pin>', pin, dir, src, ck, conn, varFull, inline(行內常數文字), desc(腳位自身描述), varDesc(所接變數描述) }   // side 由所在陣列決定
 *   Edge  { id, from:portId, to:portId, kind:'L'|'V'|'P'|'EGD'|'IO'|'sticky', varFull, label, cls, multi }
 *           kind:'sticky' 只影響分群／排版（註解黏到方塊），不畫線
 *   Tag   { port:portId, side:'L'|'R', text, varFull, desc(變數描述→第二行), cls:'in'|'out'|'iface'|'warn'|'ext-l'… }
 *   graph.varDesc { varFull: description }（選用；側欄腳位表「說明」欄的後備來源）
 *
 * 說明顯示（showDesc）：預設開；`?desc=0|1` 只影響本次（頁面以 D.dg.descPref(query) 解析）、工具列「說明」切換並記在
 *   localStorage `atlas.dg.desc`（'0'/'1'）。開啟時：腳位列「pin␣␣描述(28)」、xref 標籤第二行描述(36)、var pill 第二行(40)、
 *   方塊型別下一行方塊描述(36)；描述一律只取第一行；tooltip <title> 含全文。切換 → 重新 size→layout→route→render，保留 viewBox／選取／高亮。
 *   有描述標籤的腳位列高 16 → 28（否則兩行標籤會互相重疊）；其餘列維持 16。
 *
 * 管線（全部掛在 D.dg）：
 *   D.dg.prepare(graph)          正規化：graph.ports Map、port.node/side、varIndex
 *   D.dg.size(graph, {showDesc}) measureText 量測 → node.w/h/boxW/tagL/tagR/titleH/pinsH、port.y（相對 node 頂）；graph.showDesc
 *   D.dg.layout(graph, opts)     連通群組 → 各群組 dagre（只排節點）→ 依 ord 欄式打包 → node.x/y、graph.bounds；回傳 {ms, comps}
 *   D.dg.route(graph)            正交走線 → edge.path、edge.labelXY
 *   D.dg.render(graph, opts)     → <svg class="dg-svg">（g.wires / g.nodes / g.tags）
 *   D.dg.create(view, opts)      頁面骨架（工具列／svg／側欄／狀態列）＋ viewport ＋ 選取／高亮 ＋ 面板；回傳實例：
 *       inst.setGraph(graph, {keepViewport})  執行 prepare→size→layout→route→render 並掛上；回傳 timings
 *       inst.fit(pad) / zoom(f, cx, cy) / pan(dx, dy) / centerNode(id) / centerVar(varFull)
 *       inst.select(nodeId) / highlight(varFull) / clear() / showSide(el, title) / hideSide() / status(text)
 *       inst.addTool(el) 工具列加按鈕；inst.setLoading(msg)；inst.print()；inst.exportSvg(name)；inst.destroy()
 *       inst.showDesc（目前狀態）／inst.setDesc(on)（切換說明顯示；重排但保留 viewBox／選取）
 *       opts: { crumbs:[el], title:string, nodeActions(node, inst)→[el], varActions(varFull, inst)→[el],
 *               onSelect(node, inst), onVar(varFull, inst), onDblNode(node, inst), onDblVar(varFull, ctx, inst),
 *               varHint:string（變數面板提示，例「雙擊標籤展開到寫入者所在 task」）, showDesc:boolean（省略 → localStorage／預設開）}
 *   D.dg.blockPanel(node, inst) / D.dg.varPanel(varFull, inst)   預設側欄內容（可被 opts 取代）
 *   D.dg.wrapText(text, maxW, maxLines) / D.dg.measure(text) / D.dg.trunc(s, n) / D.dg.firstLine(s) / D.dg.descPref(query)
 * 需先 D.loadScript('dagre.min.js')（全域 dagre）。CSP：無 inline script；SVG 文字皆走 textContent。 */
'use strict';
(function () {
  const D = window.DC;
  const dg = (D.dg = D.dg || {});

  /* ------------------------------------------------------------------ 常數 */
  const PIN_H = 16, TITLE_H = 26, BODY_LH = 14, BOX_MIN = 110, BOX_MAX = 260, PORT_R = 3;
  const TAG_GAP = 10, TAG_PAD = 4, SHEET_H = 1500, GAP_X = 60, GAP_Y = 40;
  // 說明顯示：方塊寬上限、有兩行標籤的腳位列高、兩行標籤高、pill 高、方塊描述行高、描述字（9.5px）相對量測字（11px）的寬度比
  const BOX_MAX_D = 340, PIN_H_D = 28, TAG_H = 16, TAG_H_D = 27, VAR_H = 24, VAR_H_D = 34, BDESC_H = 12, DESC_K = 0.87;
  const DESC_LS = 'atlas.dg.desc';
  const TR_PIN = 28, TR_TAG = 36, TR_PILL = 40, TR_BLK = 36; // 各處描述截斷字數
  dg.C = { PIN_H, TITLE_H, BODY_LH, BOX_MIN, BOX_MAX, BOX_MAX_D, SHEET_H, PIN_H_D, TAG_H_D, VAR_H_D };

  /* ------------------------------------------------------------------ 文字量測 */
  let ctx = null, fontStr = '';
  const wCache = new Map();
  function font() {
    if (fontStr) return fontStr;
    const mono = getComputedStyle(document.documentElement).getPropertyValue('--mono').trim() || 'monospace';
    fontStr = '11px ' + mono;
    return fontStr;
  }
  dg.measure = function (text) {
    text = String(text == null ? '' : text);
    if (!text) return 0;
    let w = wCache.get(text);
    if (w != null) return w;
    if (!ctx) { try { ctx = document.createElement('canvas').getContext('2d'); } catch (e) { ctx = null; } }
    if (ctx) { ctx.font = font(); w = ctx.measureText(text).width * 1.08; }
    else { w = 0; for (const ch of text) w += ch.charCodeAt(0) > 255 ? 11 : 6.7; }
    if (wCache.size > 20000) wCache.clear();
    wCache.set(text, w);
    return w;
  };
  dg.trunc = (s, n) => { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  /** 描述只取第一個非空行（警報描述可達數百字／多行） */
  dg.firstLine = (s) => { if (s == null) return ''; const m = String(s).split(/\r?\n/).find((x) => x.trim()); return m ? m.trim() : ''; };
  const dline = (s, n) => dg.trunc(dg.firstLine(s), n);
  const dmeasure = (s) => dg.measure(s) * DESC_K; // 描述字較小
  /** 解析說明顯示偏好：query.desc '0'/'1' 只影響本次；否則 localStorage atlas.dg.desc；預設開 */
  dg.descPref = function (query) {
    const q = query && query.desc;
    if (q === '0' || q === 0 || q === false) return false;
    if (q === '1' || q === 1 || q === true) return true;
    try { const v = localStorage.getItem(DESC_LS); if (v === '0') return false; if (v === '1') return true; } catch (e) { /* 私密模式 */ }
    return true;
  };
  dg.saveDescPref = function (on) { try { localStorage.setItem(DESC_LS, on ? '1' : '0'); } catch (e) { /* ignore */ } };
  /** 依像素寬換行（先照 \n 切，再貪婪切字；單字過長則逐字切）；最多 maxLines 行（最後一行加 …） */
  dg.wrapText = function (text, maxW, maxLines) {
    const out = [];
    for (const raw of String(text == null ? '' : text).split(/\r?\n/)) {
      const words = raw.split(/(\s+)/).filter((x) => x !== '');
      let line = '';
      const push = () => { out.push(line.replace(/\s+$/, '')); line = ''; };
      for (const w of words) {
        if (dg.measure(line + w) <= maxW) { line += w; continue; }
        if (line.trim()) push();
        if (/^\s+$/.test(w)) continue;
        if (dg.measure(w) <= maxW) { line = w; continue; }
        for (const ch of w) { if (dg.measure(line + ch) > maxW && line) push(); line += ch; }
      }
      if (line.trim() || !words.length) push();
      if (out.length > maxLines) break;
    }
    while (out.length && !out[out.length - 1]) out.pop();
    if (out.length > maxLines) { out.length = maxLines; out[maxLines - 1] = dg.trunc(out[maxLines - 1] + '…', 60); }
    return out;
  };

  /* ------------------------------------------------------------------ prepare / size */
  dg.prepare = function (graph) {
    graph.ports = new Map();
    graph.varIndex = new Map();
    graph.meta = graph.meta || {};
    graph.meta.warn = graph.meta.warn || [];
    const addVar = (v, ref) => { if (!v) return; if (!graph.varIndex.has(v)) graph.varIndex.set(v, []); graph.varIndex.get(v).push(ref); };
    for (const n of graph.nodes.values()) {
      n.left = n.left || []; n.right = n.right || []; n.body = n.body || [];
      for (const side of ['L', 'R']) for (const p of side === 'L' ? n.left : n.right) {
        p.node = n.id; p.side = side;
        graph.ports.set(p.id, p);
        addVar(p.varFull, { port: p.id });
      }
      if (n.kind === 'var' && n.varFull) addVar(n.varFull, { node: n.id });
    }
    graph.edges = (graph.edges || []).filter((e) => e.kind === 'sticky' || (graph.ports.has(e.from) && graph.ports.has(e.to)));
    for (const e of graph.edges) if (e.kind !== 'sticky') addVar(e.varFull, { edge: e.id });
    graph.tags = (graph.tags || []).filter((t) => graph.ports.has(t.port));
    for (const t of graph.tags) addVar(t.varFull, { tag: t });
    return graph;
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  /** 腳位列文字寬（含腳位描述；描述字較小） */
  const pinTextW = (p, sd) => dg.measure(dg.trunc(p.pin, 12)) + (sd && p.desc ? dmeasure('  ' + dline(p.desc, TR_PIN)) : 0);
  dg.size = function (graph, opts) {
    const sd = graph.showDesc = !!(opts && opts.showDesc);
    const tagW = new Map(); // portId -> 標籤寬（含間距）
    const tagD = new Set(); // 有描述（兩行）標籤的 portId
    for (const t of graph.tags) {
      let w = dg.measure(t.text);
      if (sd && t.desc) { w = Math.max(w, dmeasure(dline(t.desc, TR_TAG))); tagD.add(t.port); }
      tagW.set(t.port, Math.max(tagW.get(t.port) || 0, w + TAG_PAD * 2 + TAG_GAP + 6));
    }
    for (const n of graph.nodes.values()) {
      if (n.kind === 'comment') {
        n.tagL = n.tagR = 0;
        n.boxW = clamp(Math.max(...n.body.map(dg.measure), 40) + 16, 80, 330);
        n.h = Math.max(n.body.length, 1) * BODY_LH + 10;
        n.w = n.boxW;
        continue;
      }
      if (n.kind === 'var' || n.kind === 'leaf') {
        const label = n.label || n.name || n.varFull || '';
        const d = sd && n.kind === 'var' ? dline(n.desc, TR_PILL) : '';
        n.descLine = d;
        n.boxW = clamp(Math.max(dg.measure(label) + 22 + (n.flag ? dg.measure(n.flag) + 8 : 0), d ? dmeasure(d) + 22 : 0), 60, 360);
        n.h = d ? VAR_H_D : VAR_H;
        const py = n.h / 2;
        n.left.forEach((p) => { p.y = py; p.noLabel = true; });
        n.right.forEach((p) => { p.y = py; p.noLabel = true; });
        n.tagL = Math.max(0, ...n.left.map((p) => tagW.get(p.id) || 0));
        n.tagR = Math.max(0, ...n.right.map((p) => tagW.get(p.id) || 0));
        n.w = n.tagL + n.boxW + n.tagR;
        continue;
      }
      const rows = Math.max(n.left.length, n.right.length);
      const bd = sd ? dline(n.desc, TR_BLK) : '';
      n.descLine = bd;
      n.titleH = TITLE_H + (bd ? BDESC_H : 0);
      let need = Math.max(dg.measure(n.name) + 16, dg.measure(n.type || '') + 16, bd ? dmeasure(bd) + 16 : 0);
      let pinsH = 0;
      for (let i = 0; i < rows; i++) {
        const l = n.left[i], r = n.right[i];
        const lw = l ? pinTextW(l, sd) + 7 : 0, rw = r ? pinTextW(r, sd) + 7 : 0;
        need = Math.max(need, lw + rw + (l && r ? 14 : 8));
        const rh = sd && ((l && tagD.has(l.id)) || (r && tagD.has(r.id))) ? PIN_H_D : PIN_H;
        if (l) l.y = n.titleH + pinsH + 8;
        if (r) r.y = n.titleH + pinsH + 8;
        pinsH += rh;
      }
      n.pinsH = pinsH;
      for (const b of n.body) need = Math.max(need, dg.measure(b) + 16);
      n.boxW = clamp(need, BOX_MIN, sd ? BOX_MAX_D : BOX_MAX);
      n.h = n.titleH + pinsH + (n.body.length ? 6 + n.body.length * BODY_LH : 0) + 6;
      let tl = 0, tr = 0;
      n.left.forEach((p) => { tl = Math.max(tl, tagW.get(p.id) || 0, p.inline ? dg.measure(dg.trunc(p.inline, 18)) + TAG_GAP + 4 : 0); });
      n.right.forEach((p) => { tr = Math.max(tr, tagW.get(p.id) || 0, p.inline ? dg.measure(dg.trunc(p.inline, 18)) + TAG_GAP + 4 : 0); });
      n.tagL = tl; n.tagR = tr;
      n.w = tl + n.boxW + tr;
    }
    return graph;
  };
  /** 腳位絕對座標（排版後） */
  dg.portXY = function (graph, portId) {
    const p = graph.ports.get(portId);
    if (!p) return null;
    const n = graph.nodes.get(p.node);
    return { x: n.x + n.tagL + (p.side === 'R' ? n.boxW : 0), y: n.y + p.y, p, n };
  };

  /* ------------------------------------------------------------------ 連通群組（union-find；sticky 邊也算）→ [[nodeId…]…] 依 min(ord) 排序；需先 prepare() */
  dg.components = function (graph) {
    const ids = Array.from(graph.nodes.keys());
    const parent = new Map(ids.map((i) => [i, i]));
    const find = (a) => { while (parent.get(a) !== a) { parent.set(a, parent.get(parent.get(a))); a = parent.get(a); } return a; };
    const union = (a, b) => { a = find(a); b = find(b); if (a !== b) parent.set(a, b); };
    const nodeOf = (pid) => { const p = graph.ports.get(pid); return p ? p.node : pid; };
    for (const e of graph.edges) { const a = nodeOf(e.from), b = nodeOf(e.to); if (graph.nodes.has(a) && graph.nodes.has(b)) union(a, b); }
    const groups = new Map();
    for (const id of ids) { const r = find(id); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(id); }
    const ordOf = (id) => { const o = graph.nodes.get(id).ord; return o == null ? 1e9 : o; };
    const out = Array.from(groups.values());
    for (const g of out) g.sort((a, b) => ordOf(a) - ordOf(b));
    out.sort((a, b) => ordOf(a[0]) - ordOf(b[0]));
    return out;
  };

  /* ------------------------------------------------------------------ 排版：union-find → dagre → 打包 */
  dg.layout = function (graph, opts) {
    opts = opts || {};
    const t0 = performance.now();
    const nodeOf = (pid) => { const p = graph.ports.get(pid); return p ? p.node : pid; };
    const comps = [];
    for (const members of dg.components(graph)) {
      const ns = members.map((i) => graph.nodes.get(i));
      const minOrd = Math.min(...ns.map((n) => (n.ord == null ? 1e9 : n.ord)));
      let w, h;
      if (ns.length === 1) { ns[0].x = 0; ns[0].y = 0; w = ns[0].w; h = ns[0].h; }
      else {
        const g = new dagre.graphlib.Graph({ multigraph: true });
        g.setGraph({ rankdir: 'LR', nodesep: opts.nodesep || 18, ranksep: opts.ranksep || 72, edgesep: 10, marginx: 0, marginy: 0,
          acyclicer: 'greedy', ranker: ns.length > 150 ? 'tight-tree' : 'network-simplex' });
        g.setDefaultEdgeLabel(() => ({}));
        ns.sort((a, b) => (a.ord == null ? 1e9 : a.ord) - (b.ord == null ? 1e9 : b.ord));
        for (const n of ns) g.setNode(n.id, { width: n.w, height: n.h });
        const set = new Set(members);
        let k = 0;
        for (const e of graph.edges) {
          const a = nodeOf(e.from), b = nodeOf(e.to);
          if (!set.has(a) || !set.has(b) || a === b) continue;
          g.setEdge(a, b, { weight: e.kind === 'L' ? 2 : 1, minlen: 1 }, 'e' + (k++));
        }
        try { dagre.layout(g); } catch (err) { console.warn('dagre', err); ns.forEach((n, i) => { n.x = 0; n.y = i * 40; }); }
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const n of ns) {
          const gn = g.node(n.id);
          if (gn && Number.isFinite(gn.x)) { n.x = gn.x - n.w / 2; n.y = gn.y - n.h / 2; } else { n.x = n.x || 0; n.y = n.y || 0; }
          minX = Math.min(minX, n.x); minY = Math.min(minY, n.y); maxX = Math.max(maxX, n.x + n.w); maxY = Math.max(maxY, n.y + n.h);
        }
        for (const n of ns) { n.x -= minX; n.y -= minY; }
        w = maxX - minX; h = maxY - minY;
      }
      comps.push({ nodes: ns, w, h, minOrd });
    }
    comps.sort((a, b) => a.minOrd - b.minOrd);
    // 欄式打包：上→下，滿 SHEET_H 換欄
    const sheetH = opts.sheetH || SHEET_H;
    let colX = 0, colY = 0, colW = 0, totalW = 0, totalH = 0;
    comps.forEach((c, i) => {
      if (colY > 0 && colY + c.h > sheetH) { colX += colW + GAP_X; colY = 0; colW = 0; }
      for (const n of c.nodes) { n.x += colX; n.y += colY; n.comp = i; }
      c.x = colX; c.y = colY;
      colY += c.h + GAP_Y; colW = Math.max(colW, c.w);
      totalW = Math.max(totalW, colX + c.w); totalH = Math.max(totalH, colY - GAP_Y);
    });
    graph.bounds = { x: 0, y: 0, w: Math.max(totalW, 10), h: Math.max(totalH, 10) };
    graph.comps = comps;
    return { ms: performance.now() - t0, comps: comps.length };
  };

  /* ------------------------------------------------------------------ 走線（正交；回饋線繞方塊底部） */
  dg.route = function (graph) {
    const perSrc = new Map(); // 同源腳序號 → 錯開 xm
    const perPair = new Map();
    for (const e of graph.edges) {
      if (e.kind === 'sticky') continue;
      const a = dg.portXY(graph, e.from), b = dg.portXY(graph, e.to);
      if (!a || !b) continue;
      const k = perSrc.get(e.from) || 0; perSrc.set(e.from, k + 1);
      const dirA = a.p.side === 'R' ? 1 : -1, dirB = b.p.side === 'L' ? -1 : 1; // 出線方向、入線方向（相對腳位往外）
      const x1 = a.x, y1 = a.y, x2 = b.x, y2 = b.y;
      const stub = 12 + (k % 5) * 6;
      let d, lx, ly;
      if (dirA === 1 && dirB === -1 && x2 - x1 >= 24) {
        const xm = Math.min(x1 + stub, x2 - 8 - (k % 3) * 3);
        d = 'M' + r1(x1) + ' ' + r1(y1) + ' H' + r1(xm) + ' V' + r1(y2) + ' H' + r1(x2);
        lx = xm; ly = (y1 + y2) / 2;
      } else {
        // 回饋／同欄：從出腳往外，繞到兩方塊底部下方，再從入腳外側進入
        const pk = (perPair.get(a.n.id + '>' + b.n.id) || 0); perPair.set(a.n.id + '>' + b.n.id, pk + 1);
        const yb = Math.max(a.n.y + a.n.h, b.n.y + b.n.h) + 14 + pk * 6 + (k % 3) * 4;
        const xo = x1 + dirA * stub, xi = x2 + dirB * (12 + pk * 6);
        d = 'M' + r1(x1) + ' ' + r1(y1) + ' H' + r1(xo) + ' V' + r1(yb) + ' H' + r1(xi) + ' V' + r1(y2) + ' H' + r1(x2);
        lx = (xo + xi) / 2; ly = yb;
      }
      e.path = d; e.labelXY = { x: lx, y: ly };
      if (Math.abs(y1 - y2) < 20 && dirA === 1 && dirB === -1) e.labelXY = { x: (x1 + x2) / 2, y: y1 - 4, mid: true };
    }
    return graph;
  };
  const r1 = (v) => Math.round(v * 10) / 10;

  /* ------------------------------------------------------------------ 渲染 */
  function marker(id, cls) {
    return D.svg('marker', { id, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' },
      D.svg('path', { d: 'M0 0 L10 5 L0 10 z', class: 'mk ' + cls }));
  }
  function defs() {
    return D.svg('defs', null,
      marker('dg-arrow', 'mk-l'), marker('dg-arrow-v', 'mk-v'), marker('dg-arrow-multi', 'mk-multi'), marker('dg-arrow-egd', 'mk-egd'), marker('dg-arrow-hl', 'mk-hl'),
      D.svg('pattern', { id: 'dg-hatch', width: '7', height: '7', patternUnits: 'userSpaceOnUse' }, D.svg('path', { d: 'M0 7 L7 0', class: 'hatch' })));
  }
  function renderNode(graph, n) {
    const cls = ['node', n.kind === 'ub' ? 'blk ub' : n.kind === 'block' ? 'blk' : n.kind, n.cls || ''].join(' ').trim();
    const g = D.svg('g', { class: cls, 'data-id': n.id, 'data-key': n.key || null, 'data-var': n.kind === 'var' ? n.varFull : null, tabindex: '0', transform: 'translate(' + r1(n.x) + ' ' + r1(n.y) + ')' });
    const tt = [n.key || n.name || '', n.type ? '[' + n.type + ']' : '', n.sub || '', n.desc || ''].filter(Boolean).join('\n');
    g.appendChild(D.svg('title', { text: tt }));
    const bx = n.tagL;
    if (n.kind === 'comment') {
      g.appendChild(D.svg('rect', { class: 'box', x: 0, y: 0, width: r1(n.boxW), height: r1(n.h), rx: 3 }));
      n.body.forEach((line, i) => g.appendChild(D.svg('text', { class: 'body', x: 8, y: 12 + i * BODY_LH, text: line })));
      return g;
    }
    if (n.kind === 'var' || n.kind === 'leaf') {
      const two = !!n.descLine;
      g.appendChild(D.svg('rect', { class: 'box', x: r1(bx), y: 0, width: r1(n.boxW), height: r1(n.h), rx: 12 }));
      g.appendChild(D.svg('text', { class: 'name', x: r1(bx + 11), y: two ? 14 : 16, text: dg.trunc(n.label || n.name || n.varFull || '', 34) }));
      if (n.flag) g.appendChild(D.svg('text', { class: 'flag', x: r1(bx + n.boxW - 8), y: two ? 14 : 16, 'text-anchor': 'end', text: n.flag }));
      if (two) g.appendChild(D.svg('text', { class: 'desc', x: r1(bx + 11), y: 27, text: n.descLine }));
      for (const p of n.left) g.appendChild(D.svg('circle', { class: 'port dir-' + (p.dir || 'q'), cx: r1(bx), cy: p.y, r: PORT_R, 'data-port': p.id, 'data-var': p.varFull || null }));
      for (const p of n.right) g.appendChild(D.svg('circle', { class: 'port dir-' + (p.dir || 'q'), cx: r1(bx + n.boxW), cy: p.y, r: PORT_R, 'data-port': p.id, 'data-var': p.varFull || null }));
      return g;
    }
    const sd = !!graph.showDesc;
    g.appendChild(D.svg('rect', { class: 'box', x: r1(bx), y: 0, width: r1(n.boxW), height: r1(n.h), rx: 4 }));
    if (n.kind === 'ub') g.appendChild(D.svg('rect', { class: 'box2', x: r1(bx + 3), y: 3, width: r1(n.boxW - 6), height: r1(n.h - 6), rx: 3 }));
    g.appendChild(D.svg('text', { class: 'name', x: r1(bx + 7), y: 12, text: dg.trunc(n.name || '', 30) }));
    if (n.type || n.sub) g.appendChild(D.svg('text', { class: 'type', x: r1(bx + 7), y: 23, text: dg.trunc((n.type || '') + (n.sub ? '  ' + n.sub : ''), 40) }));
    if (n.descLine) g.appendChild(D.svg('text', { class: 'bdesc', x: r1(bx + 7), y: 35, text: n.descLine }));
    const dirCls = (p) => 'dir-' + (p.dir === '?' || !p.dir ? 'q' : p.dir);
    // 腳位列：左側「pin␣␣描述」、右側「描述␣␣pin」（腳位名貼近腳位）；描述用 .pdesc tspan（nbsp 不會被折疊）
    const pinText = (p, right) => {
      const name = dg.trunc(p.pin, 12);
      const d = sd && p.desc ? dline(p.desc, TR_PIN) : '';
      const attrs = right ? { class: 'pin', x: r1(bx + n.boxW - 7), y: p.y + 3.5, 'text-anchor': 'end' } : { class: 'pin', x: r1(bx + 7), y: p.y + 3.5 };
      if (!d) { attrs.text = name; return D.svg('text', attrs); }
      const t = D.svg('text', attrs, right ? [D.svg('tspan', { class: 'pdesc', text: d + '  ' }), D.svg('tspan', { text: name })] : [D.svg('tspan', { text: name }), D.svg('tspan', { class: 'pdesc', text: '  ' + d })]);
      t.appendChild(D.svg('title', { text: p.pin + '\n' + p.desc }));
      return t;
    };
    for (const p of n.left) {
      g.appendChild(D.svg('circle', { class: 'port ' + dirCls(p), cx: r1(bx), cy: p.y, r: PORT_R, 'data-port': p.id, 'data-var': p.varFull || null }));
      g.appendChild(pinText(p, false));
      if (p.inline) g.appendChild(D.svg('text', { class: 'inline', x: r1(bx - TAG_GAP), y: p.y + 3.5, 'text-anchor': 'end', text: dg.trunc(p.inline, 18) }, D.svg('title', { text: p.inline })));
    }
    for (const p of n.right) {
      g.appendChild(D.svg('circle', { class: 'port ' + dirCls(p), cx: r1(bx + n.boxW), cy: p.y, r: PORT_R, 'data-port': p.id, 'data-var': p.varFull || null }));
      g.appendChild(pinText(p, true));
      if (p.inline) g.appendChild(D.svg('text', { class: 'inline', x: r1(bx + n.boxW + TAG_GAP), y: p.y + 3.5, text: dg.trunc(p.inline, 18) }, D.svg('title', { text: p.inline })));
    }
    const rows = Math.max(n.left.length, n.right.length);
    const bodyY = (n.titleH == null ? TITLE_H : n.titleH) + (n.pinsH == null ? rows * PIN_H : n.pinsH);
    n.body.forEach((line, i) => g.appendChild(D.svg('text', { class: 'body', x: r1(bx + 7), y: bodyY + 12 + i * BODY_LH, text: line })));
    return g;
  }
  function renderTag(graph, t) {
    const a = dg.portXY(graph, t.port);
    if (!a) return null;
    const text = dg.trunc(t.text, 28);
    const d = graph.showDesc && t.desc ? dline(t.desc, TR_TAG) : '';
    const tw = Math.max(dg.measure(text), d ? dmeasure(d) : 0) + TAG_PAD * 2;
    const left = t.side === 'L';
    const x0 = left ? a.x - TAG_GAP - tw : a.x + TAG_GAP;
    const g = D.svg('g', { class: 'tag ' + (t.cls || ''), 'data-var': t.varFull || null, 'data-port': t.port, tabindex: t.varFull ? '0' : null });
    const tt = t.title || t.text;
    g.appendChild(D.svg('title', { text: tt + (t.varFull && t.varFull !== tt ? '\n' + t.varFull : '') + (t.desc ? '\n' + t.desc : '') }));
    g.appendChild(D.svg('line', { class: 'tag-ln', x1: r1(left ? a.x - TAG_GAP : a.x), y1: a.y, x2: r1(left ? a.x : a.x + TAG_GAP), y2: a.y }));
    g.appendChild(D.svg('rect', { x: r1(x0), y: a.y - 8, width: r1(tw), height: d ? TAG_H_D : TAG_H, rx: 3 }));
    g.appendChild(D.svg('text', { x: r1(x0 + TAG_PAD), y: a.y + 3.5, text }));
    if (d) g.appendChild(D.svg('text', { class: 'desc', x: r1(x0 + TAG_PAD), y: a.y + 15.5, text: d }));
    return g;
  }
  dg.render = function (graph) {
    const b = graph.bounds;
    const svg = D.svg('svg', { class: 'dg-svg', xmlns: 'http://www.w3.org/2000/svg', viewBox: [b.x, b.y, b.w, b.h].join(' '), preserveAspectRatio: 'xMidYMid meet', tabindex: '0', role: 'img', 'aria-label': (graph.meta && graph.meta.title) || '邏輯圖' });
    svg.appendChild(defs());
    const wires = D.svg('g', { class: 'wires' }), nodes = D.svg('g', { class: 'nodes' }), tags = D.svg('g', { class: 'tags' }), labels = D.svg('g', { class: 'wlabels' });
    for (const e of graph.edges) {
      if (e.kind === 'sticky' || !e.path) continue;
      const cls = ['wire', 'kind-' + e.kind, e.multi ? 'multi' : '', e.kind === 'EGD' ? 'egd' : '', e.cls || ''].join(' ').trim();
      const p = D.svg('path', { class: cls, d: e.path, 'data-var': e.varFull || null, 'data-edge': e.id });
      p.appendChild(D.svg('title', { text: (e.label || e.varFull || e.kind) + '\n' + e.from + ' → ' + e.to }));
      wires.appendChild(p);
      if (e.label && e.labelXY) {
        const vd = (e.varFull && graph.varDesc && graph.varDesc[e.varFull]) || '';
        labels.appendChild(D.svg('text', { class: 'wlabel', x: r1(e.labelXY.x), y: r1(e.labelXY.y + (e.labelXY.mid ? 0 : 3.5)), 'text-anchor': 'middle', 'data-var': e.varFull || null, tabindex: e.varFull ? '0' : null, text: dg.trunc(e.label, 24) }, D.svg('title', { text: (e.varFull || e.label) + (vd ? '\n' + vd : '') })));
      }
    }
    for (const n of graph.nodes.values()) nodes.appendChild(renderNode(graph, n));
    for (const t of graph.tags) { const g = renderTag(graph, t); if (g) tags.appendChild(g); }
    svg.append(wires, nodes, tags, labels);
    return svg;
  };

  /* ------------------------------------------------------------------ 圖示 */
  function ico(d) { return D.svg('svg', { viewBox: '0 0 24 24', class: 'tb-ico', 'aria-hidden': 'true' }, D.svg('path', { d })); }
  const ICONS = {
    fit: 'M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5', plus: 'M12 5v14M5 12h14', minus: 'M5 12h14',
    print: 'M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v7H6z',
    save: 'M12 3v12M7 10l5 5 5-5M4 21h16', close: 'M6 6l12 12M18 6L6 18', block: 'M4 5h16v14H4zM4 10h16', pins: 'M4 7h6M4 12h6M4 17h6M14 7h6M14 12h6M14 17h6', cm: 'M4 5h16v10H8l-4 4z',
    desc: 'M4 6h16M4 11h10M4 16h13M17 11h3',
  };
  dg.tool = function (name, text, onclick, opt) {
    opt = opt || {};
    const b = D.h(opt.href ? 'a' : 'button', { class: 'btn sm tb' + (opt.cls ? ' ' + opt.cls : ''), type: opt.href ? null : 'button', href: opt.href || null, title: opt.title || text, 'aria-label': text, 'aria-pressed': opt.pressed == null ? null : String(!!opt.pressed), onclick },
      ICONS[name] ? ico(ICONS[name]) : null, D.h('span', { class: 'tb-txt', text }));
    return b;
  };

  /* ------------------------------------------------------------------ 實例：骨架＋viewport＋互動 */
  dg.create = function (view, opts) {
    opts = opts || {};
    const inst = { opts, graph: null, svg: null, vb: null, sel: null, hl: null };
    const crumbs = D.h('div', { class: 'dg-crumbs ph-kicker' }, opts.crumbs || []);
    const title = D.h('div', { class: 'dg-title mono', text: opts.title || '' });
    const tools = D.h('div', { class: 'dg-tools' });
    const bar = D.h('div', { class: 'dg-bar' }, D.h('div', { class: 'dg-head' }, crumbs, title), tools);
    const canvas = D.h('div', { class: 'dg-canvas' }, D.loading('準備中…'));
    const sideTitle = D.h('div', { class: 'dg-side-title' });
    const sideBody = D.h('div', { class: 'dg-side-body' });
    const side = D.h('aside', { class: 'dg-side', 'aria-label': '詳細資料' },
      D.h('div', { class: 'dg-side-head' }, sideTitle, D.h('button', { type: 'button', class: 'icon-btn dg-side-close', 'aria-label': '關閉面板', title: '關閉', onclick: () => inst.clear() }, ico(ICONS.close))), sideBody);
    const wrap = D.h('div', { class: 'dg-wrap' }, canvas, side);
    const status = D.h('div', { class: 'dg-status muted small', role: 'status', 'aria-live': 'polite' });
    const root = D.h('div', { class: 'dg', tabindex: '-1' }, bar, wrap, status);
    inst.el = root; inst.side = side; inst.sideBody = sideBody; inst.canvas = canvas; inst.tools = tools; inst.statusEl = status; inst.titleEl = title;
    document.body.classList.add('wide');
    D.set(view, root);

    inst.showDesc = opts.showDesc == null ? dg.descPref() : !!opts.showDesc;
    const descBtn = dg.tool('desc', '說明', () => inst.setDesc(!inst.showDesc), { pressed: inst.showDesc, cls: inst.showDesc ? 'on' : '', title: '顯示／隱藏說明（變數、腳位、方塊描述；?desc=0 關）' });
    /** 切換說明顯示：記到 localStorage、更新按鈕，重新 size→layout→route→render（保留 viewBox／選取／高亮） */
    inst.setDesc = (on) => {
      on = !!on;
      inst.showDesc = on;
      dg.saveDescPref(on);
      descBtn.classList.toggle('on', on);
      descBtn.setAttribute('aria-pressed', String(on));
      if (inst.graph && inst.svg) inst.setGraph(inst.graph, Object.assign({}, inst.lastSetOpts || {}, { keepViewport: true }));
      return on;
    };
    tools.append(
      dg.tool('fit', '適應', () => inst.fit(), { title: '適應視窗（0 / F）' }),
      dg.tool('plus', '放大', () => inst.zoom(1.25), { title: '放大（+）' }),
      dg.tool('minus', '縮小', () => inst.zoom(0.8), { title: '縮小（−）' }),
      descBtn);
    const extra = D.h('span', { class: 'dg-tools-x' });
    tools.append(extra,
      dg.tool('print', '列印', () => inst.print(), { title: '列印（先適應視窗）' }),
      dg.tool('save', '匯出 SVG', () => inst.exportSvg(), { title: '下載 SVG 檔' }));
    inst.addTool = (el) => { extra.appendChild(el); return el; };
    inst.status = (text) => { status.textContent = text || ''; };
    inst.setTitle = (t) => { title.textContent = t || ''; };
    inst.setLoading = (msg) => { D.set(canvas, D.loading(msg || '載入中…')); };
    inst.setMessage = (...els) => { D.set(canvas, D.h('div', { class: 'dg-msg' }, els)); };

    /* ---- viewport */
    const rect = () => canvas.getBoundingClientRect();
    inst.applyVB = () => { if (inst.svg && inst.vb) inst.svg.setAttribute('viewBox', [r1(inst.vb.x), r1(inst.vb.y), r1(inst.vb.w), r1(inst.vb.h)].join(' ')); };
    inst.fit = (pad) => {
      if (!inst.graph || !inst.svg) return;
      pad = pad == null ? 24 : pad;
      const b = inst.graph.bounds, r = rect();
      const rw = Math.max(r.width, 50), rh = Math.max(r.height, 50);
      let w = b.w + pad * 2, h = b.h + pad * 2;
      const scale = Math.min(rw / w, rh / h, 2.5); // 不放大超過 2.5×
      w = rw / scale; h = rh / scale;
      inst.vb = { x: b.x + b.w / 2 - w / 2, y: b.y + b.h / 2 - h / 2, w, h };
      inst.applyVB();
    };
    inst.zoom = (f, cx, cy) => { // cx,cy = client 座標；省略 → 視窗中心
      if (!inst.vb) return;
      const r = rect();
      const px = cx == null ? 0.5 : (cx - r.left) / r.width, py = cy == null ? 0.5 : (cy - r.top) / r.height;
      const v = inst.vb;
      const nw = clamp(v.w / f, 40, 200000), nh = v.h * (nw / v.w);
      v.x += (v.w - nw) * px; v.y += (v.h - nh) * py; v.w = nw; v.h = nh;
      inst.applyVB();
    };
    inst.pan = (dx, dy) => { if (!inst.vb) return; inst.vb.x += dx; inst.vb.y += dy; inst.applyVB(); };
    inst.scale = () => (inst.vb ? rect().width / inst.vb.w : 1);
    inst.centerOn = (x, y, w, h) => {
      if (!inst.vb) return;
      const v = inst.vb;
      const r = rect();
      if (r.width > 0 && v.w > r.width) { v.h *= r.width / v.w; v.w = r.width; } // 至少 1:1，讓目標可讀
      if (w != null && (w + 48 > v.w || h + 48 > v.h)) { const f = Math.min(v.w / (w + 48), v.h / (h + 48)); v.h /= f; v.w /= f; }
      v.x = x - v.w / 2; v.y = y - v.h / 2;
      inst.applyVB();
    };
    inst.centerNode = (id) => {
      const n = inst.graph && inst.graph.nodes.get(id);
      if (!n) return;
      inst.centerOn(n.x + n.w / 2, n.y + n.h / 2, n.w, n.h);
      pulse(inst.svg.querySelector('.node[data-id="' + cssEsc(id) + '"]'));
    };
    inst.centerVar = (varFull) => {
      const refs = (inst.graph && inst.graph.varIndex.get(varFull)) || [];
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      const add = (x, y) => { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y); };
      for (const r of refs) {
        if (r.port) { const a = dg.portXY(inst.graph, r.port); if (a) add(a.x, a.y); }
        else if (r.node) { const n = inst.graph.nodes.get(r.node); if (n) { add(n.x, n.y); add(n.x + n.w, n.y + n.h); } }
      }
      if (minX === Infinity) return false;
      inst.centerOn((minX + maxX) / 2, (minY + maxY) / 2, maxX - minX, maxY - minY);
      return true;
    };
    const onResize = () => { if (!inst.vb) return; const r = rect(); if (r.width > 0) { inst.vb.h = inst.vb.w * r.height / r.width; inst.applyVB(); } };
    let ro = null;
    if (window.ResizeObserver) { ro = new ResizeObserver(onResize); ro.observe(canvas); } else window.addEventListener('resize', onResize);

    /* ---- 指標：一指平移、兩指縮放、<4px 視為點擊、300ms 雙擊 */
    const ptrs = new Map();
    let drag = null, pinch = null, lastTap = { t: 0, id: '' };
    const pt = (e) => ({ x: e.clientX, y: e.clientY });
    function onDown(e) {
      if (e.button != null && e.button !== 0) return;
      ptrs.set(e.pointerId, pt(e));
      if (ptrs.size === 1) drag = { x: e.clientX, y: e.clientY, moved: 0, target: e.target, vb: Object.assign({}, inst.vb) };
      else if (ptrs.size === 2) { const [a, b] = Array.from(ptrs.values()); pinch = { d: dist(a, b), mid: mid(a, b), vb: Object.assign({}, inst.vb), moved: true }; drag = null; }
      if (e.pointerType === 'mouse') { window.addEventListener('pointermove', onMove); window.addEventListener('pointerup', onUp, { once: true }); }
    }
    function onMove(e) {
      if (!ptrs.has(e.pointerId)) return;
      ptrs.set(e.pointerId, pt(e));
      if (pinch && ptrs.size >= 2) {
        const [a, b] = Array.from(ptrs.values());
        const d = dist(a, b), m = mid(a, b);
        const f = d / Math.max(pinch.d, 1);
        const r = rect(), v0 = pinch.vb;
        const nw = clamp(v0.w / f, 40, 200000), nh = v0.h * (nw / v0.w);
        const px = (pinch.mid.x - r.left) / r.width, py = (pinch.mid.y - r.top) / r.height;
        const s = r.width / nw; // 縮放後每像素對應的圖單位
        inst.vb = { x: v0.x + (v0.w - nw) * px - (m.x - pinch.mid.x) / s, y: v0.y + (v0.h - nh) * py - (m.y - pinch.mid.y) / s, w: nw, h: nh };
        inst.applyVB();
        e.preventDefault();
        return;
      }
      if (drag && inst.vb) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        drag.moved = Math.max(drag.moved, Math.abs(dx), Math.abs(dy));
        if (drag.moved >= 4) {
          const s = rect().width / drag.vb.w;
          inst.vb.x = drag.vb.x - dx / s; inst.vb.y = drag.vb.y - dy / s;
          inst.applyVB();
          root.classList.add('dragging');
        }
      }
    }
    function onUp(e) {
      ptrs.delete(e.pointerId);
      if (e.pointerType === 'mouse') window.removeEventListener('pointermove', onMove);
      root.classList.remove('dragging');
      if (pinch) { if (ptrs.size === 0) pinch = null; return; }
      if (drag && drag.moved < 4) tap(e, drag.target);
      drag = null;
    }
    function tap(e, target) {
      const hit = hitOf(target);
      const now = performance.now();
      const dbl = hit && lastTap.id === hit.id && now - lastTap.t < 300;
      lastTap = { t: dbl ? 0 : now, id: hit ? hit.id : '' };
      if (!hit) { if (!dbl) inst.clear(); return; }
      if (hit.kind === 'node') {
        const n = inst.graph.nodes.get(hit.nodeId);
        if (n.kind === 'var' && n.varFull) { if (dbl && opts.onDblVar) opts.onDblVar(n.varFull, { node: n }, inst); else inst.highlight(n.varFull, { node: n }); return; }
        if (dbl && opts.onDblNode) opts.onDblNode(n, inst); else inst.select(hit.nodeId);
        return;
      }
      if (hit.kind === 'var') {
        const ctx = { port: hit.port ? inst.graph.ports.get(hit.port) : null, el: hit.el };
        if (dbl && opts.onDblVar) opts.onDblVar(hit.varFull, ctx, inst); else inst.highlight(hit.varFull, ctx);
      }
    }
    function hitOf(target) {
      if (!target || !target.closest || !inst.svg || !inst.svg.contains(target)) return null;
      const v = target.closest('.tag[data-var], .wlabel[data-var], .wire[data-var], .port[data-var]');
      if (v) return { kind: 'var', id: 'v:' + v.getAttribute('data-var'), varFull: v.getAttribute('data-var'), port: v.getAttribute('data-port'), el: v };
      const n = target.closest('.node[data-id]');
      if (n) return { kind: 'node', id: 'n:' + n.getAttribute('data-id'), nodeId: n.getAttribute('data-id'), el: n };
      return null;
    }
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('pointercancel', onUp);
    canvas.addEventListener('dblclick', (e) => e.preventDefault());
    canvas.addEventListener('wheel', (e) => {
      if (!inst.vb) return;
      e.preventDefault();
      if (e.shiftKey && !e.ctrlKey) { inst.pan((e.deltaY || e.deltaX) / inst.scale(), 0); return; }
      const f = Math.pow(1.1, -(e.deltaMode === 1 ? e.deltaY * 20 : e.deltaY) / 100);
      inst.zoom(f, e.clientX, e.clientY);
    }, { passive: false });
    for (const ev of ['gesturestart', 'gesturechange', 'gestureend']) canvas.addEventListener(ev, (e) => e.preventDefault());
    root.addEventListener('keydown', (e) => {
      if (/INPUT|TEXTAREA|SELECT/.test((e.target && e.target.tagName) || '')) return;
      if (!inst.vb) return;
      const step = inst.vb.w * 0.08;
      const k = e.key;
      if (k === '+' || k === '=') inst.zoom(1.25);
      else if (k === '-' || k === '_') inst.zoom(0.8);
      else if (k === '0' || k === 'f' || k === 'F') inst.fit();
      else if (k === 'Escape') inst.clear();
      else if (k === 'ArrowLeft') inst.pan(-step, 0);
      else if (k === 'ArrowRight') inst.pan(step, 0);
      else if (k === 'ArrowUp') inst.pan(0, -step);
      else if (k === 'ArrowDown') inst.pan(0, step);
      else if ((k === 'Enter' || k === ' ') && e.target && e.target.closest) { const hit = hitOf(e.target); if (hit) { tap(e, e.target); } else return; }
      else return;
      e.preventDefault();
    });

    /* ---- 選取／高亮／側欄 */
    inst.showSide = (title, body) => { D.set(sideTitle, title); D.set(sideBody, body); side.classList.add('open'); root.classList.add('side-open'); };
    inst.hideSide = () => { side.classList.remove('open'); root.classList.remove('side-open'); };
    inst.unhighlight = () => { if (!inst.svg) return; inst.svg.classList.remove('has-hl'); for (const el of inst.svg.querySelectorAll('.hl')) el.classList.remove('hl'); inst.hl = null; };
    inst.unselect = () => { if (inst.svg) for (const el of inst.svg.querySelectorAll('.node.sel')) el.classList.remove('sel'); inst.sel = null; };
    inst.select = (nodeId, quiet) => {
      const n = inst.graph && inst.graph.nodes.get(nodeId);
      if (!n) return false;
      inst.unselect(); inst.unhighlight();
      inst.sel = nodeId;
      const el = inst.svg.querySelector('.node[data-id="' + cssEsc(nodeId) + '"]');
      if (el) el.classList.add('sel');
      if (opts.onSelect) opts.onSelect(n, inst); else inst.showSide(D.h('span', { class: 'mono b', text: n.name || n.id }), dg.blockPanel(n, inst));
      if (!quiet) return true;
      return true;
    };
    inst.highlight = (varFull, ctx) => {
      if (!inst.svg || !varFull) return false;
      inst.unselect(); inst.unhighlight();
      inst.hl = varFull;
      let n = 0;
      for (const el of inst.svg.querySelectorAll('[data-var]')) if (el.getAttribute('data-var') === varFull) { el.classList.add('hl'); n++; }
      inst.svg.classList.add('has-hl');
      if (opts.onVar) opts.onVar(varFull, inst, ctx); else inst.showSide(D.h('a', { href: D.hrefV(varFull), class: 'lk mono b', text: varFull }), dg.varPanel(varFull, inst, ctx));
      return n;
    };
    inst.clear = () => { inst.unselect(); inst.unhighlight(); inst.hideSide(); };

    /* ---- 圖載入 */
    inst.setGraph = (graph, o) => {
      o = o || {};
      inst.lastSetOpts = { layout: o.layout };
      const t = {};
      let t0 = performance.now();
      dg.prepare(graph); dg.size(graph, { showDesc: inst.showDesc }); t.size = performance.now() - t0;
      const lay = dg.layout(graph, o.layout); t.layout = lay.ms; t.comps = lay.comps;
      t0 = performance.now(); dg.route(graph); t.route = performance.now() - t0;
      t0 = performance.now();
      const svg = dg.render(graph);
      const keepVB = o.keepViewport && inst.vb;
      inst.graph = graph; inst.svg = svg;
      D.set(canvas, svg);
      t.render = performance.now() - t0;
      if (keepVB) inst.applyVB(); else inst.fit();
      if (inst.sel && graph.nodes.has(inst.sel)) { const el = svg.querySelector('.node[data-id="' + cssEsc(inst.sel) + '"]'); if (el) el.classList.add('sel'); }
      if (inst.hl) { const v = inst.hl; inst.hl = null; for (const el of svg.querySelectorAll('[data-var]')) if (el.getAttribute('data-var') === v) el.classList.add('hl'); svg.classList.add('has-hl'); inst.hl = v; }
      inst.timings = t;
      return t;
    };

    /* ---- 列印／匯出 */
    inst.print = () => { inst.fit(8); setTimeout(() => window.print(), 50); };
    inst.exportSvg = (name) => {
      if (!inst.svg || !inst.graph) return;
      const b = inst.graph.bounds, pad = 16;
      const c = inst.svg.cloneNode(true);
      c.removeAttribute('tabindex'); c.classList.remove('has-hl');
      for (const el of c.querySelectorAll('.hl, .sel')) { el.classList.remove('hl'); el.classList.remove('sel'); }
      for (const el of c.querySelectorAll('[tabindex]')) el.removeAttribute('tabindex');
      c.setAttribute('viewBox', [b.x - pad, b.y - pad, b.w + pad * 2, b.h + pad * 2].join(' '));
      c.setAttribute('width', String(Math.round(b.w + pad * 2))); c.setAttribute('height', String(Math.round(b.h + pad * 2)));
      c.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
      const st = D.svg('style', { text: exportCss() });
      c.insertBefore(st, c.firstChild);
      const xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(c);
      const blob = new Blob([xml], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = D.h('a', { href: url, download: (name || opts.fileName || (inst.graph.meta && inst.graph.meta.title) || 'diagram').replace(/[\\/:*?"<>|\s]+/g, '_') + '.svg' });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      D.toast('已匯出 SVG');
    };
    inst.destroy = () => { if (ro) ro.disconnect(); else window.removeEventListener('resize', onResize); window.removeEventListener('pointermove', onMove); };
    return inst;
  };
  const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
  const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  const cssEsc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'));
  function pulse(el) { if (!el) return; el.classList.remove('pulse'); el.classList.add('pulse'); setTimeout(() => el.classList.remove('pulse'), 1200); }

  /* ------------------------------------------------------------------ 匯出用 CSS（token 解析成實際色值） */
  const EXPORT_CSS = `
svg{background:var(--bg);font:11px var(--mono);color:var(--text)}
.node .box{fill:var(--surface);stroke:var(--border-strong);stroke-width:1}
.node.ub .box2{fill:none;stroke:var(--border-strong);stroke-width:1}
.node.opaque .box{fill:url(#dg-hatch);stroke-dasharray:4 3}
.hatch{stroke:var(--border);stroke-width:1}
.node.root .box{stroke:var(--accent-2);stroke-width:2}
.node.var .box{fill:var(--accent-soft);stroke:var(--accent-2)}
.node.leaf .box{fill:var(--surface-3);stroke:var(--border)}
.node.leaf.enc .box,.node.leaf.warn .box{fill:var(--warn-bg);stroke:var(--warn-bd)}
.node.leaf.const .box{fill:var(--const-bg);stroke:var(--const)}
.node.comment .box{fill:var(--const-bg);stroke:var(--const);stroke-opacity:.5}
.node .name{font-weight:700;fill:var(--text)}
.node .type,.node .flag{font-size:9.5px;fill:var(--muted)}
.node .pin{fill:var(--text);font-size:10px}
.node .body{fill:var(--muted);font-size:10px}
.node.comment .body{fill:var(--const)}
.node .inline{fill:var(--const);font-size:10px}
.port{fill:var(--surface);stroke:var(--muted);stroke-width:1.2}
.port.dir-I{stroke:#1e40af}.port.dir-O{stroke:#166534;fill:#dcfce7}.port.dir-S{stroke:var(--const)}
.wire{fill:none;stroke:var(--muted);stroke-width:1.4;marker-end:url(#dg-arrow)}
.wire.kind-V{stroke:var(--accent-2);marker-end:url(#dg-arrow-v)}
.wire.multi{stroke:var(--warn);stroke-dasharray:5 3;marker-end:url(#dg-arrow-multi)}
.wire.egd{stroke:#1e40af;stroke-dasharray:6 3;marker-end:url(#dg-arrow-egd)}
.mk{fill:var(--muted)}.mk-v{fill:var(--accent-2)}.mk-multi{fill:var(--warn)}.mk-egd{fill:#1e40af}.mk-hl{fill:var(--warn)}
.wlabel{font-size:10px;fill:var(--accent-text);paint-order:stroke;stroke:var(--bg);stroke-width:3px;stroke-linejoin:round}
.tag rect{fill:var(--surface-2);stroke:var(--border-strong)}
.tag text{fill:var(--text);font-size:10px}
.tag.in rect{fill:#dbeafe;stroke:#93c5fd}.tag.out rect{fill:#dcfce7;stroke:#86efac}
.tag.iface rect{fill:var(--accent-soft);stroke:var(--accent-2)}
.tag.warn rect,.tag.ext-l rect{fill:var(--warn-bg);stroke:var(--warn-bd)}.tag.warn text,.tag.ext-l text{fill:var(--warn)}
.tag .tag-ln{stroke:var(--muted);stroke-width:1}
.node .desc,.node .pdesc,.node .bdesc,.tag text.desc{font-size:9.5px;fill:var(--muted);font-weight:400}
`;
  function exportCss() {
    const cs = getComputedStyle(document.documentElement);
    return EXPORT_CSS.replace(/var\((--[a-z0-9-]+)\)/g, (m, v) => { const val = cs.getPropertyValue(v).trim(); return val || m; });
  }

  /* ------------------------------------------------------------------ 預設側欄：方塊 */
  dg.blockPanel = function (n, inst) {
    const opts = inst.opts || {};
    const out = [];
    if (n.kind === 'comment') {
      out.push(D.h('p', { class: 'muted small', text: '註解' }), D.lines(n.desc || n.body.join('\n')));
      return D.frag(out);
    }
    if (n.kind === 'var' || n.kind === 'leaf') {
      out.push(D.h('p', null, n.href ? D.h('a', { href: n.href, class: 'lk', text: n.label || n.name }) : D.h('span', { text: n.label || n.name })));
      return D.frag(out);
    }
    const heads = D.h('div', { class: 'dg-side-tags' }, n.type ? D.tag(n.type, 'lg') : null, ' ', D.tag(n.kind === 'ub' ? 'UserBlock' : (n.kindLabel || 'block')), n.opaque ? D.frag(' ', D.tag('不透明 opaque', 'warn')) : null, n.sub ? D.frag(' ', D.h('span', { class: 'muted small mono', text: n.sub })) : null);
    out.push(heads);
    if (n.desc) out.push(D.h('p', { class: 'dg-side-desc small' }, D.lines(dg.trunc(n.desc, 600))));
    const acts = D.h('div', { class: 'dg-side-actions' },
      n.key ? D.link(D.hrefBKey(n.key), '開方塊頁', 'btn sm') : null,
      D.h('button', { type: 'button', class: 'btn sm', text: '置中', onclick: () => inst.centerNode(n.id) }),
      opts.nodeActions ? opts.nodeActions(n, inst) : null);
    out.push(acts);
    const attrs = Object.entries(n.attrs || {});
    if (attrs.length) out.push(D.h('h4', { text: '屬性' }), D.h('table', { class: 'kv' }, D.h('tbody', null, attrs.map(([k, v]) => D.kv(k, D.mono(v == null ? '—' : String(v)))))));
    const pins = n.pins || [];
    if (pins.length) {
      // compact 4-column table for the narrow side panel (full 8-column table lives on the block page)
      // 說明 = 腳位自身描述（tuple 第 11 欄）|| 所接變數的描述（port.varDesc → graph.varDesc）；舊資料（10 欄、無 vd）→ 空
      const CK = { V: '變數', L: '同 task', P: '介面腳', D: 'device', N: '常數', E: '列舉', A: '位址', '-': '—' };
      const vdMap = (inst.graph && inst.graph.varDesc) || {};
      const portByPin = new Map(n.left.concat(n.right).map((p) => [p.pin, p]));
      const descCell = (name, varFull, pinDesc) => {
        const port = portByPin.get(name);
        const d = dg.firstLine(pinDesc || (port && (port.desc || port.varDesc)) || (varFull && vdMap[varFull]) || '');
        return d ? D.h('td', { class: 'pdesc-cell', title: d, text: dg.trunc(d, 60) }) : D.h('td', { class: 'muted', text: '—' });
      };
      const rows = pins.map((p) => {
        const [name, dir, src, ck, conn, varFull, tgtKey, tgtPin] = p;
        let to;
        if (ck === 'V' && varFull) to = D.h('a', { href: D.hrefV(varFull), class: 'lk mono', text: varFull, title: varFull });
        else if ((ck === 'L' || ck === 'P') && tgtKey) to = D.h('a', { href: D.hrefBKey(tgtKey), class: 'lk mono', text: tgtKey.slice(tgtKey.lastIndexOf('/') + 1) + '.' + (tgtPin || ''), title: conn || '' });
        else if (ck === 'N' || ck === 'E') to = D.mono(conn || '', 'const');
        else to = D.mono(conn || '—');
        return [D.frag(D.mono(name, 'b'), ' ', D.dirBadge(dir), D.srcBadge(src)), CK[ck] || ck || '—', to, descCell(name, varFull, p.length > 10 ? p[10] : null)];
      });
      out.push(D.h('h4', { text: '腳位（' + pins.length + '）' }), D.table(['腳位', '種類', '連到', '說明'], rows, 'pins compact'));
    } else if (n.left.length || n.right.length) {
      out.push(D.h('h4', { text: '腳位' }), D.table(['腳位', '方向', '連線', '說明'], n.left.concat(n.right).map((p) => {
        const d = dg.firstLine(p.desc || p.varDesc || '');
        return [D.mono(p.pin, 'b'), D.dirBadge(p.dir), p.varFull ? D.h('a', { href: D.hrefV(p.varFull), class: 'lk mono', text: p.varFull }) : D.mono(D.val(p.conn)), d ? D.h('td', { class: 'pdesc-cell', title: d, text: dg.trunc(d, 60) }) : D.h('td', { class: 'muted', text: '—' })];
      })));
    }
    return D.frag(out);
  };

  /* ------------------------------------------------------------------ 預設側欄：變數（lazy 訊號卡） */
  dg.varPanel = function (varFull, inst, ctx) {
    const opts = inst.opts || {};
    const [ctrl] = D.splitFull(varFull);
    const box = D.h('div');
    const info = D.h('div', { class: 'dg-side-desc small muted', text: '載入訊號卡…' });
    const links = D.h('div', { class: 'dg-side-actions' },
      D.link(D.hrefV(varFull), '訊號頁', 'btn sm'), D.link(D.hrefT(varFull, 'up', 3), '上游', 'btn sm'), D.link(D.hrefT(varFull, 'down', 3), '下游', 'btn sm'),
      D.link(D.hrefG(varFull, 2, 2), '訊號圖', 'btn sm'),
      opts.varActions ? opts.varActions(varFull, inst, ctx) : null);
    // 端點清單
    const refs = (inst.graph && inst.graph.varIndex.get(varFull)) || [];
    const seen = new Set();
    const rows = [];
    for (const r of refs) {
      if (r.port) {
        const p = inst.graph.ports.get(r.port); if (!p || seen.has(p.id)) continue; seen.add(p.id);
        const n = inst.graph.nodes.get(p.node);
        rows.push(D.h('li', null, D.h('button', { type: 'button', class: 'lk-btn mono', text: (n.name || n.id) + '.' + p.pin, onclick: () => { inst.centerNode(n.id); } }), ' ', D.dirBadge(p.dir)));
      } else if (r.node) {
        const n = inst.graph.nodes.get(r.node); if (!n || seen.has(n.id)) continue; seen.add(n.id);
        rows.push(D.h('li', null, D.h('button', { type: 'button', class: 'lk-btn mono', text: n.label || n.name || n.id, onclick: () => inst.centerNode(n.id) })));
      }
    }
    box.append(info, links,
      rows.length ? D.frag(D.h('h4', { text: '圖中端點（' + rows.length + '）' }), D.h('ul', { class: 'plain dg-endpoints' }, rows)) : null,
      opts.varHint ? D.h('p', { class: 'muted small dg-hint', text: opts.varHint }) : null);
    const ctl = D.abortCurrent ? D.abortCurrent.signal : undefined;
    D.varCard(varFull, ctl).then((rec) => {
      if (!rec) { info.textContent = '找不到訊號卡'; return; }
      const d = rec.d || {};
      const w = rec.w || [], rd = rec.r || [];
      D.set(info, D.h('div', null, d.desc ? D.h('div', { text: d.desc }) : null,
        D.h('div', { class: 'muted small' }, D.mono(D.val(d.dt)), ' · 寫入者 ', D.int(w.length), ' · 讀取者 ', D.int(rd.length), ' ', D.flagIcons(d.flags)),
        !w.length && rec.egd && rec.egd.src ? D.h('div', { class: 'small' }, 'EGD 來自 ', D.h('a', { href: D.hrefV(rec.egd.src.ctrl + '.' + rec.egd.src.var), class: 'lk mono', text: rec.egd.src.ctrl + '.' + rec.egd.src.var })) : null,
        !w.length && rec.enc && rec.enc.length ? D.h('div', { class: 'warn-text small', text: '加密 — 無法追蹤（' + rec.enc.join('、') + '）' }) : null));
      inst.lastCard = { varFull, rec };
      if (opts.onCard) opts.onCard(varFull, rec, box, inst, ctx);
    }).catch((e) => { if (!(e && e.name === 'AbortError')) info.textContent = '訊號卡載入失敗'; });
    return box;
  };
})();
