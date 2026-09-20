/* Signal Atlas — 訊號圖 #/g/<CTRL.NAME>?up=N&down=N（plan §五）
 * 鏡射 trace.js 的 BFS（pinsOf / expand）成一張圖：變數 pill（v:<full>）、方塊（b:<key>，多次觸及＝同一節點、腳位取聯集）、
 * 葉節點（現場 I/O、EGD、加密、常數、無寫入者…文案同 trace.js）；邊以 from>to 去重；EGD 邊虛線藍；多寫入者變數入邊 .multi；
 * L: 鏈沿同 task 方塊延伸（≤12、不算跳數）；上限 200 節點（未展開者 .capped）、深度截止（.cut）。交給 D.dg（diagram.js）排版／渲染／互動。
 * 雙擊變數：從該變數再展一層（依其所在側；根節點雙向），整圖重排但保留 viewBox 與選取，新節點 .new。
 * 說明：變數節點 desc 取自訊號卡 d.desc（已展開者；截止／上限節點在 fillDesc 補抓自身卡片，EGD 副本亦用自身卡片）；
 * 方塊腳位 desc 取 tuple 第 11 欄；graph.varDesc 供側欄腳位表；?desc=off|brief|full（0|1|2）/ localStorage 由 D.dg.descPref 解析（三段密度）。 */
'use strict';
(function () {
  const D = window.DC;
  const MAX_NODES = 200;   // 變數＋方塊節點上限（葉節點不計）
  const MAX_CHAIN = 12;    // 同 task 內 L:Block.Pin 連鎖上限（不算跳數）
  const MAX_HOPS = 4;
  const LAYOUT = { ranksep: 60, nodesep: 14 };

  const stripK = (s) => String(s == null ? '' : s).replace(/^[NEL]:/, '');
  const clampHops = (v, d) => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(MAX_HOPS, Math.max(0, n)) : d; };
  const abortErr = () => new DOMException('aborted', 'AbortError');
  const FLAG_TXT = [[2, 'I/O'], [4, 'EGD'], [16, '警報'], [32, '常數'], [64, 'EGD副本'], [128, '加密']];
  const flagText = (flags) => FLAG_TXT.filter(([bit]) => (Number(flags) || 0) & bit).map(([, t]) => t).join('·');
  const cssEsc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, '\\$&'));

  /* ------------------------------------------------------------------ 圖狀態 */
  function newState(full, signal) {
    const S = { full, signal, nodes: new Map(), edges: [], edgeIds: new Set(), tags: [], ord: 0, st: { count: 0, capped: false, leaves: 0, vars: 0, blocks: 0 }, blockDone: new Set() };
    S.root = varNode(S, full, null);
    S.root.root = true;
    S.root.fresh = false;
    S.root.depth = { up: 0, down: 0 };
    return S;
  }
  const isFull = (S) => S.st.count >= MAX_NODES;
  /** 變數節點（去重）；dir = 觸及方向；新節點 node.fresh = true（呼叫端負責排入 frontier）；達上限時回傳 null 並標 st.capped */
  function varNode(S, full, dir) {
    const id = 'v:' + full;
    let n = S.nodes.get(id);
    if (!n) {
      if (isFull(S)) { S.st.capped = true; return null; }
      n = { id, kind: 'var', name: full, label: full, varFull: full, key: null, ord: S.ord++, dirs: new Set(), expanded: new Set(), depth: { up: Infinity, down: Infinity },
        left: [{ id: id + '#in', pin: 'in', dir: 'I', varFull: full }], right: [{ id: id + '#out', pin: 'out', dir: 'O', varFull: full }], body: [], cls: '', fresh: true };
      S.nodes.set(id, n);
      S.st.count++; S.st.vars++;
    }
    if (dir) n.dirs.add(dir);
    return n;
  }
  /** 方塊節點（去重；腳位聯集）：key = 'CTRL|Program/Task/…/Block'；達上限時回傳 null */
  function blockNode(S, key, blk) {
    const id = 'b:' + key;
    let n = S.nodes.get(id);
    if (n) return n;
    if (isFull(S)) { S.st.capped = true; return null; }
    const i = key.indexOf('|');
    const ctrl = key.slice(0, i), path = key.slice(i + 1), segs = path.split('/');
    const rec = blk || {};
    n = { id, kind: rec.kind === 'userblock' ? 'ub' : 'block', key, ctrl, program: segs[0] || '', task: segs[1] || '', tkey: D.taskKeyOf(key),
      name: rec.name || segs[segs.length - 1], type: rec.type || (rec.kind === 'userblock' ? 'UserBlock' : ''), sub: (segs[0] || '') + '/' + (segs[1] || ''),
      desc: rec.desc || '', attrs: rec.attrs, pins: rec.pins || [], kindLabel: rec.kind, opaque: !!rec.opaque, ord: S.ord++, left: [], right: [], body: [], cls: '', fresh: true };
    S.nodes.set(id, n);
    S.st.count++; S.st.blocks++;
    return n;
  }
  /** 方塊腳位（去重）：side 由角色決定（'L' 輸入側／'R' 輸出側） */
  function blockPort(S, n, pin, side, tuple) {
    const id = n.id + '#' + pin;
    let p = n.left.find((x) => x.id === id) || n.right.find((x) => x.id === id);
    if (p) return p;
    const pins = n.pins || [];
    const t = tuple || pins.find((x) => x[0] === pin) || null;
    const idx = t ? pins.indexOf(t) : -1;
    p = { id, pin, dir: (t && t[1]) || (side === 'R' ? 'O' : 'I'), src: t ? t[2] : '-', ck: t ? t[3] : '-', conn: t ? t[4] : null, varFull: (t && t[5]) || null, inline: null, pinIdx: idx < 0 ? 1e6 : idx,
      desc: (t && t.length > 10 && t[10]) || null, varDesc: null };
    (side === 'R' ? n.right : n.left).push(p);
    return p;
  }
  function addEdge(S, from, to, kind, extra) {
    const id = from + '>' + to;
    if (S.edgeIds.has(id)) { if (extra && extra.multi) { const e = S.edges.find((x) => x.id === id); if (e) e.multi = true; } return; }
    S.edgeIds.add(id);
    S.edges.push(Object.assign({ id, from, to, kind }, extra || {}));
  }
  /** 葉節點（依錨點＋文字去重；不計入上限）：dir 'up' → 葉在左（其右腳連到 anchorPort）；'down' → 葉在右 */
  function leaf(S, anchorPort, dir, label, cls, href) {
    const id = 'l:' + anchorPort + '|' + label;
    if (S.nodes.has(id)) return S.nodes.get(id);
    const n = { id, kind: 'leaf', name: label, label, cls: cls || '', leafCls: cls || '', href: href || null, ord: S.ord++, left: [], right: [], body: [], fresh: true };
    const port = { id: id + '#p', pin: 'p', dir: dir === 'up' ? 'O' : 'I' };
    (dir === 'up' ? n.right : n.left).push(port);
    S.nodes.set(id, n);
    S.st.leaves++;
    if (dir === 'up') addEdge(S, port.id, anchorPort, 'IO'); else addEdge(S, anchorPort, port.id, 'IO');
    return n;
  }

  /* ------------------------------------------------------------------ BFS（鏡射 trace.js） */
  /** 方塊的另一側腳位：up 取 I/S 腳（左側）、down 取 O 腳（右側）；V（含宣告於腳位的 A+varFull）→ 變數節點＋邊；L → 同 task 鏈；P/N/E 行內；無變數的 A／D 忽略。新觸及的變數節點推入 out */
  async function pinsOf(S, bn, blk, dir, chain, out, only) {
    const up = dir === 'up';
    const done = bn.id + '|' + dir + (only ? '|' + only : '');
    if (S.blockDone.has(done)) return out;
    S.blockDone.add(done);
    for (const t of blk.pins || []) {
      if (S.signal.aborted) throw abortErr();
      const [name, pdir, , ck, conn, varFull, tgtKey, tgtPin] = t;
      const wanted = only ? name === only : (up ? (pdir === 'I' || pdir === 'S') : pdir === 'O'); // only = 只走這一支腳（介面腳來源，不論方向）
      if (!wanted) continue;
      if ((ck === 'V' || ck === 'A') && varFull) {
        const vn = varNode(S, varFull, dir);
        if (!vn) continue; // 上限
        const port = blockPort(S, bn, name, up ? 'L' : 'R', t);
        if (up) addEdge(S, vn.right[0].id, port.id, 'V', { varFull }); else addEdge(S, port.id, vn.left[0].id, 'V', { varFull });
        if (vn.fresh) { vn.fresh = false; out.push(vn); }
      } else if (ck === 'L' && tgtKey) {
        const port = blockPort(S, bn, name, up ? 'L' : 'R', t);
        if (chain.has(tgtKey) || chain.size >= MAX_CHAIN) { leaf(S, port.id, dir, '↺ 同 task 方塊循環 ' + stripK(conn), 'cyc', D.hrefBKey(tgtKey)); continue; }
        const tb = await D.block(tgtKey, S.signal);
        if (!tb) { leaf(S, port.id, dir, '方塊分片缺失 ' + stripK(conn), 'warn', D.hrefBKey(tgtKey)); continue; }
        const tn = blockNode(S, tgtKey, tb);
        if (!tn) continue; // 上限
        const tp = blockPort(S, tn, tgtPin || '?', up ? 'R' : 'L');
        if (up) addEdge(S, tp.id, port.id, 'L'); else addEdge(S, port.id, tp.id, 'L');
        if (tb.opaque) { tn.opaque = true; leaf(S, blockPort(S, tn, '內部', up ? 'L' : 'R').id, dir, '不透明 userblock ' + (tb.name || '') + ' — 無法追蹤內部', 'enc', D.hrefBKey(tgtKey)); continue; }
        const c2 = new Set(chain); c2.add(tgtKey);
        await pinsOf(S, tn, tb, dir, c2, out);
      } else if (ck === 'P') {
        blockPort(S, bn, name, up ? 'L' : 'R', t).inline = '⟨' + (tgtPin || stripK(conn) || '介面腳') + '⟩';
      } else if (ck === 'N' || ck === 'E') {
        blockPort(S, bn, name, up ? 'L' : 'R', t).inline = stripK(conn);
      }
      // 無變數的 A／D：只有位址／device 的腳不畫（同 trace.js）
    }
    return out;
  }

  /** 腳位值鏡像（d.m，kind I）的上游：由鏡像腳位的接線來源接到本變數的入腳，邊標「腳位值 <pin>」——
   *  V → 變數節點；L/P → 目標方塊節點（經其腳位，再走其輸入腳／該介面腳）；N/E → 常數葉；null → 無接線資訊葉 */
  async function mirrorUp(S, node, m, dir, out, seen) {
    const [mc, , mpath, , mpin] = m.pin;
    const mkey = mc + '|' + mpath;
    const bname = String(mpath).split('/').pop();
    const inP = node.left[0].id;
    const lbl = '腳位值 ' + mpin;
    const s = m.src;
    if (!s) { leaf(S, inP, dir, '腳位值 ' + bname + '.' + mpin + ' — 無接線資訊', 'muted', D.hrefB(mc, mpath)); return; }
    if (s.k === 'V' && s.var) {
      const vn = varNode(S, s.var, dir);
      if (!vn) return; // 上限
      addEdge(S, vn.right[0].id, inP, 'V', { varFull: s.var, label: lbl });
      seen(vn);
      return;
    }
    if ((s.k === 'L' || s.k === 'P') && s.block) {
      const tb = await D.block(s.block, S.signal);
      const tname = (tb && tb.name) || s.block.slice(s.block.lastIndexOf('/') + 1);
      if (!tb) { leaf(S, inP, dir, '方塊分片缺失 ' + tname, 'warn', D.hrefBKey(s.block)); return; }
      const tn = blockNode(S, s.block, tb);
      if (!tn) return; // 上限
      const tp = blockPort(S, tn, s.pin || '?', 'R');
      addEdge(S, tp.id, inP, 'L', { label: lbl + (s.k === 'P' ? '（介面腳）' : '') });
      if (tb.opaque) { tn.opaque = true; return; }
      await pinsOf(S, tn, tb, dir, new Set([mkey, s.block]), out, s.k === 'P' ? s.pin : null);
      return;
    }
    if (s.k === 'N' || s.k === 'E') { leaf(S, inP, dir, lbl + ' = ' + (s.k === 'N' ? '常數 ' : '列舉 ') + stripK(s.text), 'const'); return; }
    leaf(S, inP, dir, lbl + ' — 接線來源種類 ' + (s.k || '?') + ' 無法追蹤', 'muted', D.hrefB(mc, mpath));
  }

  /** 展開一個變數節點的一側（訊號卡 w/r → 方塊 → 另一側腳位）；回傳新觸及的變數節點（depth 已設） */
  async function expandVar(S, node, dir) {
    const up = dir === 'up';
    const out = [];
    if (node.expanded.has(dir)) return out;
    node.expanded.add(dir);
    node.capped = false;
    const rec = await D.varCard(node.varFull, S.signal);
    const inP = node.left[0].id, outP = node.right[0].id;
    if (!rec) { node.missing = true; leaf(S, up ? inP : outP, dir, '找不到訊號卡', 'warn'); return out; }
    node.desc = (rec.d && rec.d.desc) || '';
    node.isConst = !!(rec.d && rec.d.const);
    node.flag = flagText(rec.d && rec.d.flags) || node.flag;
    let refs = (up ? rec.w : rec.r) || [];
    const multi = up && refs.length > 1;
    const seen = (vn) => { if (vn && vn.fresh) { vn.fresh = false; out.push(vn); } };
    // 腳位值鏡像（d.m；舊卡片無 → 略）：上游無寫入者時，kind O → 該方塊腳視同寫入者；kind I → 由腳位接線來源接一條「腳位值 <pin>」邊
    const m = up && !refs.length && rec.d && rec.d.m && Array.isArray(rec.d.m.pin) && rec.d.m.pin.length >= 5 ? rec.d.m : null;
    let mirrorOut = false;
    if (m && m.kind === 'O') { refs = [m.pin]; mirrorOut = true; }
    const mirrorIn = m && m.kind === 'I' ? m : null;
    if (mirrorIn) await mirrorUp(S, node, mirrorIn, dir, out, seen);
    if (!up) { // EGD 消費者一律算下游（即使站內也有讀取者）
      for (const c of (rec.egd && rec.egd.c) || []) {
        const vn = varNode(S, c.ctrl + '.' + c.local, dir);
        if (!vn) continue;
        addEdge(S, outP, vn.left[0].id, 'EGD', { varFull: node.varFull, label: 'EGD → ' + c.ctrl });
        seen(vn);
      }
    }
    if (!refs.length && !mirrorIn) {
      if (up) {
        const ioIn = (rec.io || []).filter((x) => x.dir === 'I');
        if (ioIn.length) for (const x of ioIn) leaf(S, inP, dir, '現場 I/O 輸入：' + (x.module || '') + ' ' + (x.board || '') + ' ' + (x.point || '') + (x.tag ? ' ' + x.tag : ''), 'io', D.hrefIO(x.ctrl, x.module));
        else if (rec.egd && rec.egd.src) {
          const src = rec.egd.src.ctrl + '.' + rec.egd.src.var;
          const vn = varNode(S, src, dir);
          if (vn) { addEdge(S, vn.right[0].id, inP, 'EGD', { varFull: src, label: 'EGD ← ' + rec.egd.src.ctrl }); seen(vn); }
        }
        else if (rec.enc && rec.enc.length) leaf(S, inP, dir, '加密 — 無法追蹤（' + rec.enc.join('、') + '）', 'enc');
        else if (node.isConst) leaf(S, inP, dir, '常數（無寫入者）', 'const');
        else leaf(S, inP, dir, (rec.u && rec.u.length) ? '無寫入者；有 ' + rec.u.length + ' 個方向未知的腳' : '無寫入者', 'muted', D.hrefV(node.varFull));
      } else {
        const cons = (rec.egd && rec.egd.c) || [];
        const ioOut = (rec.io || []).filter((x) => x.dir === 'O');
        let any = cons.length > 0;
        if (ioOut.length) { any = true; for (const x of ioOut) leaf(S, outP, dir, '現場 I/O 輸出：' + (x.module || '') + ' ' + (x.point || '') + (x.tag ? ' ' + x.tag : ''), 'io', D.hrefIO(x.ctrl, x.module)); }
        if (rec.egd && rec.egd.p && rec.egd.p.length && !cons.length) { any = true; leaf(S, outP, dir, 'EGD 送出但 consumer outside checkout', 'warn'); }
        if (!any) leaf(S, outP, dir, rec.enc && rec.enc.length ? '無讀取者；出現在加密程式（' + rec.enc.join('、') + '）— 無法追蹤' : '無讀取者', rec.enc && rec.enc.length ? 'enc' : 'muted', D.hrefV(node.varFull));
      }
    }
    for (const ref of refs) {
      if (S.signal.aborted) throw abortErr();
      const [ctrl, , path, , pin] = ref;
      const key = ctrl + '|' + path;
      const blk = await D.block(key, S.signal);
      if (!blk) { leaf(S, up ? inP : outP, dir, '方塊分片缺失 ' + path + '.' + pin, 'warn', D.hrefB(ctrl, path)); continue; }
      const bn = blockNode(S, key, blk);
      if (!bn) continue; // 上限
      const port = blockPort(S, bn, pin, up ? 'R' : 'L');
      if (up) addEdge(S, port.id, inP, 'V', { varFull: node.varFull, multi, label: mirrorOut ? '輸出腳位值 ' + pin : undefined }); else addEdge(S, outP, port.id, 'V', { varFull: node.varFull });
      if (blk.opaque) { bn.opaque = true; continue; } // 不透明 userblock：斜線方塊，不追內部
      await pinsOf(S, bn, blk, dir, new Set([key]), out);
    }
    const d = node.depth[dir] + 1;
    for (const vn of out) if (vn.depth[dir] > d) vn.depth[dir] = d;
    return out;
  }

  /** BFS：frontier = [[node, dir]…]；lim(dir) 為該側跳數；onLevel(level, st) 每展開一個節點呼叫；達上限後未展開的 frontier 標 capped */
  async function bfs(S, frontier, lim, onLevel) {
    let level = 0;
    while (frontier.length) {
      level++;
      const next = [];
      for (const [n, dir] of frontier) {
        if (S.signal.aborted) throw abortErr();
        if (isFull(S)) { S.st.capped = true; if (!n.expanded.has(dir)) n.capped = true; continue; }
        const kids = await expandVar(S, n, dir);
        for (const k of kids) if (k.depth[dir] < lim(dir)) next.push([k, dir]);
        if (onLevel) onLevel(level, S.st);
      }
      frontier = next;
    }
  }

  /** 建圖：up/down 跳數；onProgress(level, st)；回傳 S（S.rootMissing 表示訊號不存在；S.bfsMs） */
  async function buildSignalGraph(full, up, down, signal, onProgress) {
    const t0 = performance.now();
    const S = newState(full, signal);
    const rec = await D.varCard(full, signal);
    if (!rec) { S.rootMissing = true; return S; }
    S.root.desc = (rec.d && rec.d.desc) || '';
    S.root.flag = flagText(rec.d && rec.d.flags) || '';
    const frontier = [];
    if (up > 0) frontier.push([S.root, 'up']);
    if (down > 0) frontier.push([S.root, 'down']);
    await bfs(S, frontier, (dir) => (dir === 'up' ? up : down), onProgress);
    await fillDesc(S);
    S.bfsMs = performance.now() - t0;
    return S;
  }
  /** 未展開（截止／上限／EGD 副本）的變數節點沒載過卡片 → 平行補抓自身卡片只為描述與旗標（分片有快取；失敗即略過） */
  async function fillDesc(S) {
    const todo = Array.from(S.nodes.values()).filter((n) => n.kind === 'var' && n.desc == null && !n.missing);
    if (!todo.length) return;
    await Promise.all(todo.map((n) => D.varCard(n.varFull, S.signal).then((rec) => {
      n.desc = (rec && rec.d && rec.d.desc) || '';
      if (rec && rec.d && !n.flag) n.flag = flagText(rec.d.flags) || '';
    }).catch((e) => { if (e && e.name === 'AbortError') throw e; n.desc = ''; })));
  }

  /** 整理成引擎可吃的 Graph：腳位依原 pin 順序、cls 依狀態（root / capped / cut / opaque / new）；graph.varDesc / port.varDesc 供側欄 */
  function finalize(S, newIds) {
    const varDesc = {};
    for (const n of S.nodes.values()) if (n.kind === 'var' && n.desc) varDesc[n.varFull] = n.desc;
    for (const n of S.nodes.values()) {
      const cls = [];
      if (n.kind === 'var') {
        if (n.root) cls.push('root');
        const want = n.root ? ['up', 'down'] : Array.from(n.dirs);
        if (n.capped) cls.push('capped');
        else if (want.some((d) => !n.expanded.has(d))) cls.push('cut');
        if (n.missing) cls.push('warn');
      } else if (n.kind === 'leaf') cls.push(n.leafCls);
      else {
        if (n.opaque) cls.push('opaque');
        n.left.sort((a, b) => a.pinIdx - b.pinIdx); n.right.sort((a, b) => a.pinIdx - b.pinIdx);
        for (const p of n.left.concat(n.right)) if (p.varFull && varDesc[p.varFull]) p.varDesc = varDesc[p.varFull];
      }
      if (newIds && newIds.has(n.id)) cls.push('new');
      n.cls = cls.join(' ');
    }
    return { nodes: S.nodes, edges: S.edges, tags: S.tags, meta: { title: '訊號圖 ' + S.full, warn: [] }, varDesc };
  }

  /* ------------------------------------------------------------------ 頁面 */
  D.page('g', async ({ route, view, signal, alive }) => {
    const full = route.segs.join('/');
    const q = route.query;
    const up = clampHops(q.up, 2), down = clampHops(q.down, 2);
    if (!full) { D.set(view, D.errorBox('路徑不完整', '需要 #/g/<CTRL.NAME>?up=N&down=N')); return; }
    D.setTitle('訊號圖 ' + full);
    const crumbs = [D.link('#/', '搜尋'), ' › ', D.link(D.hrefV(full), full, 'lk mono'), ' › 訊號圖'];
    let S = null, inst = null;

    /** 雙擊變數：再展一層（依其所在側；根節點雙向）；受 200 上限；保留 viewBox 與選取；新節點 .new */
    async function expandNode(varId) {
      if (!S || !inst || !inst.graph) return;
      const n = S.nodes.get(varId);
      if (!n || n.kind !== 'var') { D.toast('圖中沒有此變數節點'); return; }
      if (isFull(S)) { D.toast('已達 ' + MAX_NODES + ' 節點上限'); return; }
      const dirs = n.root ? ['up', 'down'] : Array.from(n.dirs);
      const todo = dirs.filter((d) => !n.expanded.has(d));
      if (!todo.length) { D.toast('此節點已展開'); return; }
      const before = new Set(S.nodes.keys());
      inst.status('展開 ' + n.varFull + '…');
      const f0 = D.fetchCount, t0 = performance.now();
      try {
        for (const d of todo) { if (signal.aborted) return; await expandVar(S, n, d); }
        await fillDesc(S);
      } catch (e) { if (e && e.name === 'AbortError') return; throw e; }
      if (!alive()) return;
      const newIds = new Set(Array.from(S.nodes.keys()).filter((id) => !before.has(id)));
      const g = finalize(S, newIds);
      const bfsMs = performance.now() - t0;
      await D.yieldMain();
      if (!alive()) return;
      const tm = inst.setGraph(g, { keepViewport: true, layout: LAYOUT });
      statusLine(D.fetchCount - f0, bfsMs, tm);
      if (S.st.capped) D.toast('已達 ' + MAX_NODES + ' 節點上限');
      else D.toast(newIds.size ? '新增 ' + newIds.size + ' 個節點' : '沒有新的節點');
    }
    function statusLine(fetched, bfsMs, tm) {
      const st = S.st;
      const parts = ['抓取 ' + fetched + ' 個分片', '節點 ' + D.int(st.count) + '（變數 ' + st.vars + ' · 方塊 ' + st.blocks + ' · 葉 ' + st.leaves + '）', '連線 ' + S.edges.length, 'BFS ' + Math.round(bfsMs) + ' ms'];
      if (tm) parts.push('排版 ' + Math.round(tm.layout) + ' ms');
      if (st.capped) parts.push('已達 ' + MAX_NODES + ' 節點上限');
      inst.status(parts.join(' · '));
    }

    inst = D.dg.create(view, {
      crumbs, title: full, fileName: 'signal_' + full,
      descMode: D.dg.descPref(q),
      varHint: '雙擊變數 pill（或按「展開」）：從該變數再展開一層——上游側展寫入者、下游側展讀取者，根節點雙向。',
      onDblVar: (v) => { expandNode('v:' + v).catch((e) => console.warn(e)); },
      varActions: (v) => (S && S.nodes.has('v:' + v) ? D.h('button', { type: 'button', class: 'btn sm', text: '展開', title: '從此變數再展開一層', onclick: () => expandNode('v:' + v).catch((e) => console.warn(e)) }) : null),
      nodeActions: (n) => ((n.kind === 'block' || n.kind === 'ub') && n.program && n.task ? D.link(D.hrefD(n.ctrl, n.program, n.task, { b: n.key }), '開啟 Task 邏輯圖', 'btn sm') : null),
    });
    // 工具列：上游／下游跳數步進（改寫 hash，可分享）
    const stepper = (label, key) => {
      const val = key === 'up' ? up : down;
      const go = (v) => D.go(key === 'up' ? D.hrefG(full, v, down) : D.hrefG(full, up, v));
      return D.h('span', { class: 'dg-step', title: label + '跳數 0–' + MAX_HOPS },
        D.h('span', { class: 'dg-step-lb', text: label }),
        D.h('button', { type: 'button', class: 'btn sm', 'aria-label': label + '減一', text: '−', disabled: val <= 0, onclick: () => go(val - 1) }),
        D.h('span', { class: 'mono b dg-step-v', text: String(val) }),
        D.h('button', { type: 'button', class: 'btn sm', 'aria-label': label + '加一', text: '＋', disabled: val >= MAX_HOPS, onclick: () => go(val + 1) }));
    };
    inst.addTool(stepper('上游', 'up'));
    inst.addTool(stepper('下游', 'down'));
    inst.addTool(D.dg.tool('pins', '追蹤', null, { href: D.hrefT(full, up >= down ? 'up' : 'down', Math.max(1, up, down)), title: '文字樹狀追蹤' }));
    inst.setLoading('載入排版引擎與訊號卡…');

    const f0 = D.fetchCount;
    let lastDraw = 0;
    const onProgress = (level, st) => {
      const now = performance.now();
      if (now - lastDraw < 100) return;
      lastDraw = now;
      inst.setLoading('追蹤中… 第 ' + level + ' 層 · ' + st.count + ' 個節點 · 抓取 ' + (D.fetchCount - f0) + ' 個分片');
      D.progress(Math.min(0.95, level / (Math.max(up, down) + 1)));
    };
    try {
      const [s] = await Promise.all([buildSignalGraph(full, up, down, signal, onProgress), D.loadScript('dagre.min.js')]);
      S = s;
    } catch (e) {
      D.progress(null);
      if (e && e.name === 'AbortError') return;
      throw e;
    }
    D.progress(null);
    if (!alive()) return;
    if (S.rootMissing) {
      const [ctrl, name] = D.splitFull(full);
      inst.setMessage(D.errorBox('找不到此訊號', full + '（沒有訊號卡；請確認控制器前綴與名稱）'),
        D.h('p', null, D.link(D.hrefQ(name || full, ctrl), '搜尋「' + (name || full) + '」', 'btn'), ' ', D.link('#/', '回到搜尋', 'btn')));
      inst.status('無此訊號');
      return;
    }
    const g = finalize(S, null);
    inst.setLoading('排版 ' + S.st.count + ' 個節點…');
    await D.yieldMain();
    if (!alive()) return;
    const tm = inst.setGraph(g, { layout: LAYOUT });
    statusLine(D.fetchCount - f0, S.bfsMs, tm);
    if (S.root.desc) inst.setTitle(full + ' — ' + D.dg.trunc(S.root.desc.split('\n')[0], 60));
    if (S.st.capped) D.toast('已達 ' + MAX_NODES + ' 節點上限，部分分支未展開');
    // 同 task 方塊 hover 同色高亮（.tk-hl）
    let hovTk = null;
    const setTk = (tk) => {
      if (tk === hovTk || !inst.svg) return;
      for (const el of inst.svg.querySelectorAll('.node.tk-hl')) el.classList.remove('tk-hl');
      hovTk = tk;
      if (!tk) return;
      for (const n of S.nodes.values()) if (n.tkey === tk) { const el = inst.svg.querySelector('.node[data-id="' + cssEsc(n.id) + '"]'); if (el) el.classList.add('tk-hl'); }
    };
    inst.canvas.addEventListener('pointerover', (e) => {
      const el = e.target && e.target.closest ? e.target.closest('.node.blk') : null;
      const n = el && S.nodes.get(el.getAttribute('data-id'));
      setTk(n ? n.tkey : null);
    });
    inst.canvas.addEventListener('pointerleave', () => setTk(null));
    inst.expandNode = expandNode;
    inst.signalState = S;
  });

  D.dsignal = { MAX_NODES, MAX_CHAIN, buildSignalGraph };
})();
