/* Signal Atlas — 追蹤頁 #/t/<CTRL.NAME>?dir=up|down&hops=N
 * 前端 BFS：訊號卡 w/r → block 分片 → 該 block 其他腳位的變數 → 再載入 var 分片。
 * 停止條件：N:/E: 常數、加密（enc 非空且無寫入者）、不透明 userblock、深度、200 節點、循環（↺）。 */
'use strict';
(function () {
  const D = window.DC;
  const MAX_NODES = 200;
  const MAX_CHAIN = 12; // 同 task 內 L:Block.Pin 連鎖上限（不算跳數）

  function leaf(label, cls, href) { return { kind: 'leaf', label, cls: cls || '', href: href || null, children: [] }; }

  /** 追蹤主程式；回傳 root 節點（樹在 children 內），onLevel(root) 每層呼叫一次 */
  async function trace(full, dir, hops, signal, onLevel) {
    const up = dir === 'up';
    const seen = new Map();
    const root = { kind: 'var', full, depth: 0, children: [], edge: null, root: true };
    seen.set(full, root);
    const st = { count: 1, capped: false };
    let level = [root];

    async function pinsOf(blk, chain, edgeBase, only) {
      // 回傳子節點陣列：up 取 I/S 腳；down 取 O 腳；only = 只走這一支腳（介面腳來源，不論方向）
      const out = [];
      for (const p of blk.pins || []) {
        const [name, pdir, , ck, conn, varFull, tgtKey, tgtPin] = p;
        const wanted = only ? name === only : (up ? (pdir === 'I' || pdir === 'S') : pdir === 'O');
        if (!wanted) continue;
        const edge = Object.assign({}, edgeBase, { pin: name, pdir, src: p[2] });
        if ((ck === 'V' || ck === 'A') && varFull) { // V，或宣告於腳位的 A（有 varFull）
          if (varFull === edgeBase.from) continue; // 本身
          out.push({ kind: 'var', full: varFull, edge, children: [] });
        } else if (ck === 'L' && tgtKey) {
          if (chain.has(tgtKey) || chain.size >= MAX_CHAIN) { out.push(Object.assign(leaf('↺ 同 task 方塊循環 ' + conn, 'cyc', D.hrefBKey(tgtKey)), { edge })); continue; }
          const tb = await D.block(tgtKey, signal);
          if (!tb) { out.push(Object.assign(leaf('方塊分片缺失 ' + conn, 'warn', D.hrefBKey(tgtKey)), { edge })); continue; }
          const c2 = new Set(chain); c2.add(tgtKey);
          const sub = await pinsOf(tb, c2, { blockKey: tgtKey, block: tb.name || tgtKey, btype: tb.type, from: edgeBase.from, via: (edgeBase.via ? edgeBase.via + ' › ' : '') + blk.name + '.' + name + ' → ' + conn });
          if (tb.opaque) out.push(Object.assign(leaf('不透明 userblock ' + (tb.name || '') + ' — 無法追蹤內部', 'enc', D.hrefBKey(tgtKey)), { edge }));
          if (!sub.length && !tb.opaque) out.push(Object.assign(leaf('經 ' + conn + '（' + (tb.type || '') + '）沒有' + (up ? '輸入' : '輸出') + '變數', 'muted', D.hrefBKey(tgtKey)), { edge }));
          out.push(...sub);
        } else if (ck === 'P') {
          out.push(Object.assign(leaf('介面腳 ' + conn + '（外層巨集介面，需看上層 userblock）', 'muted'), { edge }));
        } else if (ck === 'N' || ck === 'E') {
          out.push(Object.assign(leaf((ck === 'N' ? '常數 N: ' : '列舉 E: ') + conn, 'const'), { edge }));
        } else if (ck === 'A' || ck === 'D') {
          out.addr = (out.addr || 0) + 1; // 只有位址／device pin：沒有訊號名可追，不列（避免一個 NOT 方塊冒出 8 列）
        }
      }
      return out;
    }

    /** 腳位值鏡像（d.m，kind I）的上游：由鏡像腳位的接線來源繼續——V 變數節點、L/P 目標方塊（走其輸入腳／該介面腳）、N/E 常數葉 */
    async function mirrorUp(node, m, kids) {
      const [mc, mprog, mpath, mtype, mpin] = m.pin;
      const mkey = mc + '|' + mpath;
      const bname = String(mpath).split('/').pop();
      const base = { blockKey: mkey, block: bname, btype: mtype, from: node.full, refPin: mpin, program: mprog, path: mpath, pin: mpin, pdir: 'I', src: m.pin[5], mirror: 'I' };
      const s = m.src;
      if (!s) { kids.push(Object.assign(leaf('腳位值 ' + bname + '.' + mpin + ' — 無接線資訊', 'muted', D.hrefB(mc, mpath)), { edge: base })); return; }
      if (s.k === 'V' && s.var) { kids.push({ kind: 'var', full: s.var, edge: base, children: [] }); return; }
      if ((s.k === 'L' || s.k === 'P') && s.block) {
        const tb = await D.block(s.block, signal);
        const tname = (tb && tb.name) || s.block.slice(s.block.lastIndexOf('/') + 1);
        const via = '腳位值 ' + bname + '.' + mpin + ' ← ' + (s.k === 'P' ? '介面腳 ' : '') + tname + '.' + (s.pin || '?');
        if (!tb) { kids.push(Object.assign(leaf('方塊分片缺失 ' + tname, 'warn', D.hrefBKey(s.block)), { edge: base })); return; }
        if (tb.opaque) { kids.push(Object.assign(leaf('不透明 userblock ' + tname + ' — 無法追蹤內部', 'enc', D.hrefBKey(s.block)), { edge: base })); return; }
        const sub = await pinsOf(tb, new Set([mkey, s.block]), { blockKey: s.block, block: tname, btype: tb.type, from: node.full, via, mirror: 'I' }, s.k === 'P' ? s.pin : null);
        if (!sub.length) kids.push(Object.assign(leaf('經 ' + tname + '.' + (s.pin || '?') + '（' + (tb.type || tb.kind || '') + '）沒有輸入變數', 'muted', D.hrefBKey(s.block)), { edge: base }));
        kids.push(...sub);
        return;
      }
      if (s.k === 'N' || s.k === 'E') { kids.push(Object.assign(leaf((s.k === 'N' ? '常數 N: ' : '列舉 E: ') + String(s.text == null ? '' : s.text).replace(/^[NEL]:/, ''), 'const'), { edge: base })); return; }
      kids.push(Object.assign(leaf('腳位值 ' + bname + '.' + mpin + ' — 接線來源種類 ' + (s.k || '?') + ' 無法追蹤', 'muted', D.hrefB(mc, mpath)), { edge: base }));
    }

    async function expand(node) {
      const rec = await D.varCard(node.full, signal);
      if (!rec) { node.missing = true; node.children.push(leaf('找不到訊號卡', 'warn')); return; }
      node.desc = (rec.d && rec.d.desc) || '';
      node.isConst = !!(rec.d && rec.d.const);
      let refs = (up ? rec.w : rec.r) || [];
      const kids = [];
      // 腳位值鏡像（舊卡片無 d.m → 略）：上游無寫入者時，kind O → 該方塊腳視同寫入者；kind I → 由腳位接線來源繼續
      const m = up && !refs.length && rec.d && rec.d.m && Array.isArray(rec.d.m.pin) && rec.d.m.pin.length >= 5 ? rec.d.m : null;
      let mirrorOut = false;
      if (m && m.kind === 'O') { refs = [m.pin]; mirrorOut = true; }
      const mirrorIn = m && m.kind === 'I' ? m : null;
      if (mirrorIn) await mirrorUp(node, mirrorIn, kids);
      if (!up) { // EGD 消費者一律算下游（即使站內也有讀取者）
        for (const c of (rec.egd && rec.egd.c) || []) kids.push({ kind: 'var', full: c.ctrl + '.' + c.local, edge: { egd: 'EGD → ' + c.ctrl }, children: [] });
      }
      if (!refs.length && !mirrorIn) {
        if (up) {
          const ioIn = (rec.io || []).filter((x) => x.dir === 'I');
          if (ioIn.length) kids.push(...ioIn.map((x) => leaf('現場 I/O 輸入：' + (x.module || '') + ' ' + (x.board || '') + ' ' + (x.point || '') + (x.tag ? ' ' + x.tag : ''), 'io', D.hrefIO(x.ctrl, x.module))));
          else if (rec.egd && rec.egd.src) kids.push({ kind: 'var', full: rec.egd.src.ctrl + '.' + rec.egd.src.var, edge: { egd: 'EGD 來自 ' + rec.egd.src.ctrl }, children: [] });
          else if (rec.enc && rec.enc.length) kids.push(leaf('加密 — 無法追蹤（' + rec.enc.join('、') + '）', 'enc'));
          else if (node.isConst) kids.push(leaf('常數（無寫入者）', 'const'));
          else kids.push(leaf((rec.u && rec.u.length) ? '無寫入者；有 ' + rec.u.length + ' 個方向未知的腳' : '無寫入者', 'muted', D.hrefV(node.full)));
        } else {
          const cons = (rec.egd && rec.egd.c) || [];
          const ioOut = (rec.io || []).filter((x) => x.dir === 'O');
          if (ioOut.length) kids.push(...ioOut.map((x) => leaf('現場 I/O 輸出：' + (x.module || '') + ' ' + (x.point || '') + (x.tag ? ' ' + x.tag : ''), 'io', D.hrefIO(x.ctrl, x.module))));
          if (rec.egd && rec.egd.p && rec.egd.p.length && !cons.length) kids.push(leaf('EGD 送出但 consumer outside checkout', 'warn'));
          if (!kids.length) kids.push(leaf(rec.enc && rec.enc.length ? '無讀取者；出現在加密程式（' + rec.enc.join('、') + '）— 無法追蹤' : '無讀取者', rec.enc && rec.enc.length ? 'enc' : 'muted', D.hrefV(node.full)));
        }
      }
      for (const ref of refs) {
        if (signal.aborted) throw new DOMException('aborted', 'AbortError');
        const [ctrl, program, path, btype, pin] = ref;
        const key = ctrl + '|' + path;
        const edgeBase = { blockKey: key, block: path.split('/').pop(), btype, from: node.full, refPin: pin, program, path, mirror: mirrorOut ? 'O' : null };
        const blk = await D.block(key, signal);
        if (!blk) { kids.push(Object.assign(leaf('方塊分片缺失 ' + path + '.' + pin, 'warn', D.hrefB(ctrl, path)), { edge: edgeBase })); continue; }
        if (blk.opaque) { kids.push(Object.assign(leaf('不透明 userblock ' + blk.name + ' — 無法追蹤內部', 'enc', D.hrefB(ctrl, path)), { edge: edgeBase })); continue; }
        const sub = await pinsOf(blk, new Set([key]), edgeBase);
        if (!sub.length) kids.push(Object.assign(leaf(blk.name + ' 沒有連到變數的' + (up ? '輸入' : '輸出') + '腳' + (sub.addr ? '（' + sub.addr + ' 腳只有位址）' : '（方向可能未推斷）'), 'muted', D.hrefB(ctrl, path)), { edge: edgeBase }));
        kids.push(...sub);
      }
      // 同一變數在多個腳位出現：合併為一個子節點；循環標 ↺
      const next = [];
      for (const k of kids) {
        if (k.kind !== 'var') { node.children.push(k); continue; }
        if (seen.has(k.full)) { k.cyc = true; k.depth = seen.get(k.full).depth; node.children.push(k); continue; }
        if (st.count >= MAX_NODES) { st.capped = true; k.capped = true; node.children.push(k); continue; }
        k.depth = node.depth + 1; st.count++; seen.set(k.full, k); node.children.push(k); next.push(k);
      }
      return next;
    }

    for (let d = 0; d < hops && level.length; d++) {
      const next = [];
      for (const node of level) {
        if (signal.aborted) throw new DOMException('aborted', 'AbortError');
        const n = await expand(node);
        if (n) next.push(...n);
        if (onLevel) onLevel(root, st);
      }
      level = next;
      if (onLevel) onLevel(root, st);
    }
    for (const n of level) n.cut = true; // 深度上限：尚未展開
    return { root, st };
  }

  /* ------------------------------------------------------------------ 渲染 */
  function edgeLabel(e, up) {
    if (!e) return null;
    if (e.egd) return D.h('span', { class: 'edge egd', text: e.egd });
    const parts = [];
    if (e.mirror && !e.via) parts.push(D.h('span', { class: 'bd src src-T', text: e.mirror === 'O' ? '輸出腳位值' : '腳位值', title: e.mirror === 'O' ? '此變數是該方塊輸出腳的發佈值（腳位值鏡像）' : '此變數是該腳位的發佈值（腳位值鏡像）；上游 = 腳位的接線來源' }), ' ');
    if (e.via) parts.push(D.h('span', { class: 'muted small', text: '經 ' + e.via + ' ' }));
    parts.push(D.h('a', { href: D.hrefBKey(e.blockKey), class: 'lk mono', text: e.block + (e.pin ? '.' + e.pin : ''), title: (e.path || e.blockKey) + (e.refPin ? '（' + (e.mirror ? '腳位值' : up ? '輸出' : '輸入') + '腳 ' + e.refPin + '）' : '') }));
    if (e.btype) parts.push(D.h('span', { class: 'ref-type', text: '[' + e.btype + ']' }));
    if (e.pdir) parts.push(D.dirBadge(e.pdir), D.srcBadge(e.src));
    return D.h('span', { class: 'edge' }, up ? '← ' : '→ ', parts);
  }
  function renderNode(node, up, dir, hops) {
    const line = D.h('span', { class: 'tn-line' });
    if (node.edge) line.append(edgeLabel(node.edge, up), ' ');
    if (node.kind === 'leaf') {
      line.append(node.href ? D.h('a', { href: node.href, class: 'lk tn-leaf ' + node.cls, text: node.label }) : D.h('span', { class: 'tn-leaf ' + node.cls, text: node.label }));
      return D.h('div', { class: 'tn leaf' }, line);
    }
    line.append(D.h('a', { href: D.hrefV(node.full), class: 'lk mono b tn-var', text: node.full }));
    if (node.desc) line.append(' ', D.h('span', { class: 'muted tn-desc', text: node.desc }));
    if (node.cyc) line.append(' ', D.h('span', { class: 'bd cyc', text: '↺ 循環', title: '已在樹中出現（深度 ' + node.depth + '）' }));
    if (node.capped) line.append(' ', D.h('span', { class: 'bd warn', text: '節點上限', title: '超過 ' + MAX_NODES + ' 個節點，未展開' }));
    if (node.cut) line.append(' ', D.h('a', { href: D.hrefT(node.full, dir, hops), class: 'bd cut', text: '深度上限 › 由此續追', title: '從這個訊號再追 ' + hops + ' 跳' }));
    if (node.missing) line.append(' ', D.h('span', { class: 'bd warn', text: '無訊號卡' }));
    if (!node.children.length) return D.h('div', { class: 'tn' + (node.root ? ' root' : '') }, line);
    return D.h('details', { class: 'tn' + (node.root ? ' root' : ''), open: true },
      D.h('summary', null, line, D.h('span', { class: 'sec-n', text: String(node.children.length) })),
      D.h('div', { class: 'kids' }, node.children.map((c) => renderNode(c, up, dir, hops))));
  }

  D.page('t', async ({ route, view, signal }) => {
    const full = route.segs.join('/');
    const dir = route.query.dir === 'down' ? 'down' : 'up';
    const hops = Math.min(6, Math.max(1, parseInt(route.query.hops, 10) || 3));
    const up = dir === 'up';
    D.setTitle((up ? '上游 ' : '下游 ') + full);
    const ctl = new AbortController();
    const onAbort = () => ctl.abort();
    signal.addEventListener('abort', onAbort);

    const status = D.h('span', { class: 'muted small', text: '準備中…' });
    const btnStop = D.h('button', { type: 'button', class: 'btn sm', text: '中止', onclick: () => { ctl.abort(); btnStop.disabled = true; } });
    const range = D.h('input', { type: 'range', min: '1', max: '6', step: '1', value: String(hops), 'aria-label': '跳數' });
    const rangeV = D.h('span', { class: 'mono', text: String(hops) });
    range.addEventListener('input', () => { rangeV.textContent = range.value; });
    range.addEventListener('change', () => D.go(D.hrefT(full, dir, range.value)));
    const tree = D.h('div', { class: 'tree' }, D.loading('追蹤中…'));
    const legend = D.h('p', { class: 'muted small' }, '每個節點可點到訊號頁或方塊頁；', D.h('span', { class: 'bd cyc', text: '↺' }), ' 循環、',
      D.h('span', { class: 'tn-leaf enc', text: '加密 / 不透明' }), ' 無法追蹤、', D.h('span', { class: 'tn-leaf const', text: '常數' }), ' 為葉節點；上限 ' + MAX_NODES + ' 個節點。方向 ? 的腳不追。');
    D.set(view, 
      D.h('div', { class: 'ph' },
        D.h('div', { class: 'ph-kicker' }, D.link('#/', '搜尋'), ' › ', D.link(D.hrefV(full), full, 'lk mono'), ' › 追蹤'),
        D.h('h1', { class: 'ph-title' }, (up ? '上游（誰決定它）' : '下游（它影響誰）') + ' ', D.mono(full, 'b')),
        D.h('div', { class: 'ph-actions trace-ctl' },
          D.h('span', { class: 'seg' },
            D.link(D.hrefT(full, 'up', hops), '上游', 'btn sm' + (up ? ' on' : '')),
            D.link(D.hrefT(full, 'down', hops), '下游', 'btn sm' + (!up ? ' on' : ''))),
          D.h('label', { class: 'hops' }, '跳數 ', range, ' ', rangeV),
          D.link(D.hrefG(full, up ? hops : 1, up ? 1 : hops), '圖形檢視', 'btn sm'),
          btnStop, status)),
      legend, tree);

    const f0 = D.fetchCount;
    let lastDraw = 0;
    let last = null;
    const draw = (root, st, final) => {
      last = { root, st };
      const now = performance.now();
      if (!final && now - lastDraw < 120) return;
      lastDraw = now;
      status.textContent = '本次新載入 ' + (D.fetchCount - f0) + ' 個分片 · ' + st.count + ' 個節點' + (st.capped ? '（已達上限）' : '') + (final ? ' · 完成' : '…');
      D.set(tree, renderNode(root, up, dir, hops));
    };
    try {
      const { root, st } = await trace(full, dir, hops, ctl.signal, draw);
      draw(root, st, true);
      btnStop.disabled = true;
    } catch (e) {
      if (e && e.name === 'AbortError') {
        if (!signal.aborted) {
          if (last) draw(last.root, last.st, true);
          status.textContent = status.textContent.replace(' · 完成', '') + ' · 已中止';
          tree.append(D.h('p', { class: 'warn-text', text: '已中止；上方為中止前的部分結果。' }));
        }
        return;
      }
      throw e;
    } finally { signal.removeEventListener('abort', onAbort); }
  });
})();
