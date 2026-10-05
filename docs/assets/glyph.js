/* Signal Atlas — 方塊圖示 D.glyph：依方塊型別與腳位常數，在 Task 圖／訊號圖的方塊裡畫出列印邏輯圖同款的符號
 * （RUNG 梯形圖、比較符號、H/L 監視、閘、延時波形…），讓人不必讀腳位就知道方塊在做什麼。
 *
 *   D.glyph.of(type, pins) → spec | null   spec = { kind, w, h, eat:[腳名], title, … }
 *       pins = 原始 pin tuple（[name, dir, src, ck, conn, varFull, …]）；eat = 圖示已經畫出、不必再畫成腳位的常數腳（側欄腳位表照列）
 *   D.glyph.draw(spec, {x, y, ports}) → <g class="gl">（x/y = 圖示左上角；ports = Map(腳名 → port)，梯形圖接點據此帶 data-var／高亮）
 *   D.glyph.parse(eqn) → 樹 | null；D.glyph.ladder(樹) → {w, h, cells, wires, vbars}；D.glyph.evalTree(樹, 值)
 *       純函式、不碰 DOM：tests/glyph_test.js 以 node 直接 require 本檔。
 *
 * 規則：只畫從型別或「字面常數」就能確定的部分。常數腳接變數、或未設定而列印圖也沒印出預設值時，那一部分不畫（寧缺勿錯）。
 * 梯形圖排法與列印圖相同（以全部列印出來的 RUNG 逐格比對過）：`*` 串聯、`+` 並聯分支、`~` 常閉接點（斜線），
 *   優先序 ~ > * > +；加括號的 OR 若是另一個 OR 的分支則保持為巢狀群組；並聯群組左右垂直線畫到最後一個分支的頂列；
 *   較短的分支只在頂列補導線；多列時最左側一條垂直線貫穿全高；線圈在第 0 列最右；接點上只標腳位字母（大小寫不分）。 */
'use strict';
(function (root) {
  const G = {};

  /* ------------------------------------------------------------------ RUNG 方程式 → 樹 */
  /** 'N:A*~B+(C+D)*E' → {op:'OR'|'AND'|'GRP'|'VAR', kids|e|v,nc}；語法外的任何東西 → null */
  G.parse = function (eqn) {
    let s = String(eqn == null ? '' : eqn);
    if (s.startsWith('N:')) s = s.slice(2);
    s = s.replace(/\s+/g, '');
    if (!s || !/^[A-Za-z()*+~]+$/.test(s)) return null;
    let i = 0;
    const fail = () => { throw new Error('syntax'); };
    function expr() { const kids = [term()]; while (s[i] === '+') { i++; kids.push(term()); } return kids.length === 1 ? kids[0] : { op: 'OR', kids }; }
    function term() { const kids = [factor()]; while (s[i] === '*') { i++; kids.push(factor()); } return kids.length === 1 ? kids[0] : { op: 'AND', kids }; }
    function factor() {
      const ch = s[i++];
      if (ch === '~') { const f = factor(); if (f.op !== 'VAR') fail(); return { op: 'VAR', v: f.v, nc: !f.nc }; } // ~ 只作用在單一字母
      if (ch === '(') { const e = expr(); if (s[i++] !== ')') fail(); return e.op === 'OR' ? { op: 'GRP', e } : e; }
      if (ch && /[A-Za-z]/.test(ch)) return { op: 'VAR', v: ch.toUpperCase(), nc: false };
      return fail();
    }
    try { const e = expr(); return i === s.length ? flatten(e) : null; } catch (e) { return null; }
  };
  /** 同運算子攤平；加括號的 OR 只有在 OR 底下才保留成巢狀群組（列印圖如此畫），其餘括號不影響圖形 */
  function flatten(e) {
    if (e.op === 'VAR') return e;
    if (e.op === 'GRP') return { op: 'GRP', e: flatten(e.e) };
    const out = [];
    for (const k0 of e.kids) {
      const k = flatten(k0);
      if (k.op === e.op) out.push(...k.kids);
      else if (k.op === 'GRP' && e.op === 'OR') out.push(k);
      else if (k.op === 'GRP') out.push(k.e);
      else out.push(k);
    }
    return { op: e.op, kids: out };
  }
  /** 樹的布林值（val = {字母: bool}） */
  G.evalTree = function evalTree(e, val) {
    if (e.op === 'VAR') return !!val[e.v] !== e.nc;
    if (e.op === 'GRP') return evalTree(e.e, val);
    return e.op === 'AND' ? e.kids.every((k) => evalTree(k, val)) : e.kids.some((k) => evalTree(k, val));
  };
  G.letters = function (e, out) {
    out = out || new Set();
    if (e.op === 'VAR') out.add(e.v);
    else if (e.op === 'GRP') G.letters(e.e, out);
    else e.kids.forEach((k) => G.letters(k, out));
    return out;
  };

  /* ------------------------------------------------------------------ 樹 → 梯形圖格位 */
  /* 格位：cells [{r, c, v, nc}] 接點；wires [[r, c]] 整格導線；vbars [[b, r]] 欄界 b 上、第 r 列到 r+1 列的垂直線 */
  function lay(e) {
    if (e.op === 'VAR') return { w: 1, h: 1, cells: [{ r: 0, c: 0, v: e.v, nc: e.nc }], wires: [], vbars: [] };
    if (e.op === 'GRP') return lay(e.e);
    const parts = e.kids.map(lay);
    const cells = [], wires = [], vbars = [];
    if (e.op === 'AND') { // 串聯：左到右，全部從同一頂列開始；相鄰群組共用垂直線
      let W = 0, H = 0;
      for (const p of parts) {
        p.cells.forEach((x) => cells.push({ r: x.r, c: x.c + W, v: x.v, nc: x.nc }));
        p.wires.forEach(([r, c]) => wires.push([r, c + W]));
        p.vbars.forEach(([b, r]) => vbars.push([b + W, r]));
        W += p.w; H = Math.max(H, p.h);
      }
      return { w: W, h: H, cells, wires, vbars };
    }
    // 並聯：分支往下疊；短分支只在自己的頂列補導線到群組寬；左右垂直線從第 0 列畫到最後一個分支的頂列
    const W = Math.max(...parts.map((p) => p.w));
    let R = 0, last = 0;
    for (const p of parts) {
      last = R;
      p.cells.forEach((x) => cells.push({ r: x.r + R, c: x.c, v: x.v, nc: x.nc }));
      p.wires.forEach(([r, c]) => wires.push([r + R, c]));
      p.vbars.forEach(([b, r]) => vbars.push([b, r + R]));
      for (let c = p.w; c < W; c++) wires.push([R, c]);
      R += p.h;
    }
    for (let r = 0; r < last; r++) { vbars.push([0, r]); vbars.push([W, r]); }
    return { w: W, h: R, cells, wires, vbars };
  }
  G.ladder = function (tree) {
    const L = lay(tree);
    for (let r = 0; r < L.h - 1; r++) L.vbars.push([0, r]); // 多列：最左一條垂直線貫穿全高
    const seen = new Set();
    L.vbars = L.vbars.filter(([b, r]) => { const k = b + ',' + r; if (seen.has(k)) return false; seen.add(k); return true; });
    return L;
  };

  /* ------------------------------------------------------------------ 型別 → 圖示規格 */
  const OPS = { GE: '≥', GT: '>', LT: '<', EQ: '=', LE: '≤', NE: '≠' };
  const LADDER_MAX = 16; // 欄、列上限（全部 RUNG 實測最多 16）
  const pinOf = (pins, name) => pins.find((p) => p[0] === name) || null;
  const ckOf = (t) => (t ? t[3] : null);
  const constOf = (t) => (t && t[3] === 'N' ? String(t[4] == null ? '' : t[4]).replace(/^N:/, '') : null);
  /** 時間／寬度類參數：字面常數 → {set: 數值≠0, val}；接變數／連線／介面腳 → {set:true, val:''}；
   *  未設定（只有位址）→ 預設 0（列印圖印 0）；其他連線種類 → {unknown:true}（不猜） */
  function timeParam(t) {
    if (!t) return { set: false, val: '' };
    const ck = t[3];
    if (ck === 'N') { const v = constOf(t), x = Number(v); return { set: !(v !== '' && isFinite(x) && x === 0), val: v, lit: true }; }
    if (ck === 'V' || ck === 'L' || ck === 'P' || (ck === 'A' && t[5])) return { set: true, val: '' };
    if (ck === 'A') return { set: false, val: '' };
    return { set: false, val: '', unknown: true };
  }
  function ladderSpec(pins) {
    const eq = pinOf(pins, 'EQN');
    if (!eq || eq[3] !== 'N') return null;
    const tree = G.parse(eq[4]);
    if (!tree) return null;
    const used = G.letters(tree);
    const inputs = new Set(pins.filter((p) => /^[A-P]$/.test(p[0])).map((p) => p[0]));
    if (used.size !== inputs.size || [...used].some((v) => !inputs.has(v))) return null; // 字母必須正好是方塊的輸入腳
    const L = G.ladder(tree);
    if (L.w > LADDER_MAX || L.h > LADDER_MAX) return null;
    const P = L.w > 10 ? 15 : 18; // 格距（寬梯形圖縮一點）
    const map = {};
    for (const v of used) { const t = pinOf(pins, v); map[v] = { varFull: (t && t[5]) || null, conn: t ? t[4] : null, desc: (t && t.length > 10 && t[10]) || null }; }
    return { kind: 'ladder', L, P, x0: 3, y0: 11, w: 3 + L.w * P + P * 0.75 + 6, h: 11 + (L.h - 1) * P + 9, eat: ['EQN'], map,
      title: 'EQN = ' + String(eq[4]).replace(/^N:/, '') };
  }
  /** type + pins → spec（無對應 → null） */
  G.of = function (type, pins) {
    pins = pins || [];
    const t = String(type || '');
    switch (t) {
      case 'RUNG': return ladderSpec(pins);
      case 'COMPARE': {
        const f = pinOf(pins, 'FUNC');
        let k = null;
        if (f && f[3] === 'E') k = String(f[4] || '').replace(/^E:/, '').toUpperCase();
        else if (f && f[3] === 'N' && constOf(f) === '1') k = 'GT'; // 數值 1：列印圖畫 >（僅此一值有圖面佐證）
        return k && OPS[k] ? { kind: 'op', sym: OPS[k], w: 24, h: 20, eat: ['FUNC'], title: 'FUNC = ' + k + '（IN1 ' + OPS[k] + ' IN2）' } : null;
      }
      case 'GT': case 'LT': case 'EQ': case 'GE': case 'LE': case 'NE':
        return { kind: 'op', sym: OPS[t], w: 24, h: 20, eat: [], title: 'IN1 ' + OPS[t] + ' IN2' };
      case 'HI_LO_MON': return { kind: 'hilo', w: 30, h: 22, eat: [], title: 'H/L 監視（HS 高限 → LH、LS 低限 → LL、HYST 遲滯）' };
      case 'NOT': return { kind: 'not', w: 22, h: 16, eat: [], title: '反相' };
      case 'AND': return { kind: 'and', w: 22, h: 18, eat: [], title: 'AND' };
      case 'OR': return { kind: 'or', w: 24, h: 18, eat: [], title: 'OR' };
      case 'LATCH': {
        // RDOM 字面常數 True → RESET 優先（圈 R）、False → SET 優先（圈 S）；未設定 = 程式庫預設 FALSE（列印圖印 FALSE 並圈 S）；接變數 → 不圈
        const t = pinOf(pins, 'RDOM'), d = constOf(t);
        const unset = !t || t[3] === '-' || (t[3] === 'A' && !t[5]);
        const dom = unset ? 'S' : d == null ? null : /^true$/i.test(d) ? 'R' : /^false$/i.test(d) ? 'S' : null;
        const why = unset ? 'RDOM 未設定，預設 FALSE' : d != null ? 'RDOM = ' + d : 'RDOM 接變數';
        return { kind: 'latch', dom, w: 16, h: 30, eat: [], title: 'SET／RESET 閂鎖：' + (dom ? (dom === 'R' ? 'RESET' : 'SET') + ' 優先' : '未標優先') + '（' + why + '）' };
      }
      case 'MIN_MAX': {
        const f = pinOf(pins, 'FUNC');
        let m = null;
        if (f && f[3] === 'E') m = /^E:Max$/i.test(f[4] || '') ? 'MAX' : /^E:Min$/i.test(f[4] || '') ? 'MIN' : null;
        else if (!f || f[3] === '-') m = 'MIN'; // 未設定：程式庫預設，列印圖印 MIN
        return m ? { kind: 'text', text: m, w: 32, h: 16, eat: ['FUNC'], title: m === 'MAX' ? '取最大' : '取最小' } : null;
      }
      case 'MOVE': case 'MOVE_STATUS': return { kind: 'move', w: 28, h: 10, eat: [], title: 'SRC → DEST' };
      case 'ON_OFF_DELAY': {
        const on = timeParam(pinOf(pins, 'PU_DEL')), off = timeParam(pinOf(pins, 'DO_DEL'));
        if (on.unknown || off.unknown) return null;
        const eat = []; if (on.lit) eat.push('PU_DEL'); if (off.lit) eat.push('DO_DEL');
        return { kind: 'delay', on: on.set, off: off.set, onV: on.lit && on.set ? on.val : '', offV: off.lit && off.set ? off.val : '', w: 76, h: 40, eat,
          title: [on.set ? '延時接通 PU_DEL' + (on.lit ? ' = ' + on.val : '（接變數）') : 'PU_DEL = 0', off.set ? '延時斷開 DO_DEL' + (off.lit ? ' = ' + off.val : '（接變數）') : 'DO_DEL = 0'].join('；') };
      }
      case 'PULSE': {
        const wd = timeParam(pinOf(pins, 'WIDTH'));
        return { kind: 'pulse', val: wd.lit ? wd.val : '', w: 46, h: 30, eat: wd.lit ? ['WIDTH'] : [], title: '脈波' + (wd.lit ? ' WIDTH = ' + wd.val + ' ms' : '') };
      }
      case 'P_B': return { kind: 'pb', w: 24, h: 14, eat: [], title: '脈波' };
      case 'SWITCH': return { kind: 'swt', w: 22, h: 20, eat: [], title: '依 SEL 切換 T／F' };
      case 'ADD': return { kind: 'sum', sym: 'Σ', w: 22, h: 20, eat: [], title: '相加' };
      case 'SUB': return { kind: 'sum', sym: 'Δ', pm: true, w: 30, h: 20, eat: [], title: '相減（IN1 − IN2）' };
      case 'MULT': return { kind: 'sum', sym: 'X', w: 22, h: 20, eat: [], title: '相乘' };
      case 'LAG': return { kind: 'lag', w: 38, h: 24, eat: [], title: '一階延遲 1/(1+sTC)' };
      case 'RATELIM': return { kind: 'rate', w: 28, h: 18, eat: [], title: '變化率限制' };
      case 'CLAMP': return { kind: 'clamp', w: 36, h: 22, eat: [], title: '上下限箝制' };
      case 'ANALOG_ALARM': return { kind: 'alarm', w: 22, h: 22, eat: [], title: '類比警報' };
      case 'EXPAND': return { kind: 'expand', w: 22, h: 22, eat: [], title: '拆成 OUT0…OUTF' };
      case 'STATUS_SPLIT': return { kind: 'split', w: 30, h: 20, eat: [], title: '值與狀態拆開' };
      case 'STATUS_COMBINE': return { kind: 'split', join: true, w: 30, h: 20, eat: [], title: '值與狀態合併' };
      case 'SELECT': {
        // 列印圖：CASC 一個閉合接點，INn／SELn 每一對一個開路接點；兩者個數不一致就不畫
        const nIn = pins.filter((p) => /^IN\d+$/.test(p[0])).length, nSel = pins.filter((p) => /^SEL\d+$/.test(p[0])).length;
        if (!nIn || nIn !== nSel || nIn > 24) return null;
        return { kind: 'select', n: nIn, w: 30, h: 8 + 9 * nIn + 4, eat: [], title: '依 SELn 選擇輸入（' + nIn + ' 路）' };
      }
      default: return null;
    }
  };

  /* ------------------------------------------------------------------ 繪製（瀏覽器；D.svg） */
  const r1 = (v) => Math.round(v * 10) / 10;
  function el(tag, attrs, ...kids) { return root.DC.svg(tag, attrs, ...kids); }
  const path = (d, cls) => el('path', { class: cls || 'gl-s', d });
  const txt = (x, y, s, cls, anchor) => el('text', { class: cls || 'gl-t', x: r1(x), y: r1(y), 'text-anchor': anchor || 'middle', text: s });
  function arrowH(x1, x2, y) { // 雙箭頭（填色小三角）
    return [path('M' + r1(x1) + ' ' + y + 'H' + r1(x2)), path('M' + r1(x1) + ' ' + y + 'l3 -2v4z' + 'M' + r1(x2) + ' ' + y + 'l-3 -2v4z', 'gl-f')];
  }
  function drawLadder(s, opts) {
    const { L, P, x0, y0 } = s;
    const X = (b) => x0 + b * P, Y = (r) => y0 + r * P;
    const s1 = P * 0.3, cw = P * 0.4, ch = Math.min(P * 0.42, 8); // 接點前導線、接點寬、接點高
    const out = [];
    let d = '';
    for (const [r, c] of L.wires) d += 'M' + r1(X(c)) + ' ' + r1(Y(r)) + 'H' + r1(X(c + 1));
    for (const [b, r] of L.vbars) d += 'M' + r1(X(b)) + ' ' + r1(Y(r)) + 'V' + r1(Y(r + 1));
    for (const x of L.cells) {
      const xl = X(x.c) + s1, xr = xl + cw, y = Y(x.r);
      d += 'M' + r1(X(x.c)) + ' ' + r1(y) + 'H' + r1(xl) + 'M' + r1(xr) + ' ' + r1(y) + 'H' + r1(X(x.c + 1));
    }
    // 線圈：第 0 列最右（前導線 + 圓）
    const cx = X(L.w) + P * 0.3 + P * 0.2;
    d += 'M' + r1(X(L.w)) + ' ' + r1(Y(0)) + 'H' + r1(cx - P * 0.2);
    out.push(path(d, 'gl-s gl-wire'));
    out.push(el('circle', { class: 'gl-coil', cx: r1(cx), cy: r1(Y(0)), r: r1(P * 0.2) }, el('title', { text: '線圈 → OUT' })));
    const ports = opts.ports || null;
    for (const x of L.cells) {
      const xl = X(x.c) + s1, xr = xl + cw, y = Y(x.r);
      const m = s.map[x.v] || {};
      const port = ports ? ports.get(x.v) : null;
      const lit = !!(opts.lit && port);
      const g = el('g', { class: 'gl-ct' + (x.nc ? ' nc' : '') + (lit ? ' lit' : '') });
      g.appendChild(el('title', { text: x.v + (x.nc ? '（常閉：為假時導通）' : '（常開：為真時導通）') + (m.varFull ? '\n' + m.varFull : m.conn ? '\n' + m.conn : '') + (m.desc ? '\n' + m.desc : '') }));
      g.appendChild(el('rect', { class: 'gl-cbody', x: r1(xl), y: r1(y - ch / 2), width: r1(cw), height: r1(ch) }));
      let cd = 'M' + r1(xl) + ' ' + r1(y - ch / 2) + 'V' + r1(y + ch / 2) + 'M' + r1(xr) + ' ' + r1(y - ch / 2) + 'V' + r1(y + ch / 2);
      if (x.nc) cd += 'M' + r1(xr + 1.5) + ' ' + r1(y - ch / 2 - 1.5) + 'L' + r1(xl - 1.5) + ' ' + r1(y + ch / 2 + 1.5);
      g.appendChild(path(cd, 'gl-s gl-cedge'));
      g.appendChild(txt((xl + xr) / 2, y - ch / 2 - 2, x.v, 'gl-t gl-lab'));
      // 點接點 = 點它所接的變數（diagram.js hitOf 的 .gl-hit[data-var]）
      g.appendChild(el('rect', { class: 'gl-hit', x: r1(X(x.c)), y: r1(y - P / 2), width: r1(P), height: r1(P), 'data-var': m.varFull || null, 'data-port': port ? port.id : null }));
      out.push(g);
    }
    return out;
  }
  function draw(s, opts) {
    const w = s.w, h = s.h;
    switch (s.kind) {
      case 'ladder': return drawLadder(s, opts);
      case 'op': return [el('rect', { class: 'gl-s', x: 0.5, y: 0.5, width: w - 1, height: h - 1, rx: 2 }), txt(w / 2, h / 2 + 5.5, s.sym, 'gl-t gl-op')];
      case 'hilo': return [el('rect', { class: 'gl-s', x: 0.5, y: 0.5, width: w - 1, height: h - 1 }), path('M0.5 ' + (h - 0.5) + 'L' + (w - 0.5) + ' 0.5'),
        txt(8, 10, 'H', 'gl-t gl-hl'), txt(w - 8, h - 3, 'L', 'gl-t gl-hl')];
      case 'not': return [path('M0.5 0.5L16.5 8L0.5 15.5Z'), el('circle', { class: 'gl-s', cx: 19, cy: 8, r: 2.4 })];
      case 'and': return [path('M0.5 0.5H11A8.5 8.5 0 0 1 11 17.5H0.5Z')];
      case 'or': return [path('M0.5 0.5Q5.5 9 0.5 17.5Q15 17.5 23.5 9Q15 0.5 0.5 0.5Z')];
      case 'latch': {
        const o = [el('rect', { class: 'gl-s', x: 0.5, y: 0.5, width: w - 1, height: h - 1 }), path('M0.5 ' + h / 2 + 'H' + (w - 0.5)),
          txt(w / 2, h / 4 + 4, 'S', 'gl-t gl-hl'), txt(w / 2, (3 * h) / 4 + 4, 'R', 'gl-t gl-hl')];
        if (s.dom) o.push(el('circle', { class: 'gl-s', cx: w / 2, cy: s.dom === 'S' ? h / 4 : (3 * h) / 4, r: 5.6 }));
        return o;
      }
      case 'text': return [el('rect', { class: 'gl-s', x: 0.5, y: 0.5, width: w - 1, height: h - 1, rx: 2 }), txt(w / 2, h / 2 + 3.5, s.text, 'gl-t gl-word')];
      case 'move': return [path('M0 4H18V0.5L27.5 5L18 9.5V6H0Z', 'gl-f')];
      case 'delay': {
        // IN（上）與 OUT（下）的方波；PU_DEL 讓 OUT 晚上升、DO_DEL 讓 OUT 晚下降；延時值（常數）標在箭頭上方。IN／OUT 字在波形左側
        const x0 = 17, r = 28, f = 52, dl = 12, ro = s.on ? r + dl : r, fo = s.off ? f + dl : f;
        const o = [path('M' + x0 + ' 13H' + r + 'V3H' + f + 'V13H' + (w - 2)), path('M' + x0 + ' 37H' + ro + 'V27H' + fo + 'V37H' + (w - 2)),
          txt(1, 11, 'IN', 'gl-t gl-tiny', 'start'), txt(1, 35, 'OUT', 'gl-t gl-tiny', 'start')];
        if (s.on) { o.push(...arrowH(r, ro, 23)); if (s.onV) o.push(txt((r + ro) / 2, 20, s.onV, 'gl-t gl-val')); }
        if (s.off) { o.push(...arrowH(f, fo, 23)); if (s.offV) o.push(txt((f + fo) / 2, 20, s.offV, 'gl-t gl-val')); }
        return o;
      }
      case 'pulse': {
        const o = [path('M2 14H12V3H34V14H44')];
        o.push(...arrowH(12, 34, 19));
        if (s.val) o.push(txt(23, 28, s.val + ' ms', 'gl-t gl-val'));
        return o;
      }
      case 'pb': return [el('rect', { class: 'gl-s', x: 0.5, y: 0.5, width: w - 1, height: h - 1 }), path('M2 11H7V3H17V11H22')];
      case 'swt': return [el('rect', { class: 'gl-s', x: 3.5, y: 0.5, width: 14, height: 12 }), txt(10.5, 10, 'T', 'gl-t gl-hl'), path('M10.5 12.5V19H0.5')];
      case 'sum': {
        const cx = s.pm ? 19 : 11;
        const o = [el('circle', { class: 'gl-s', cx, cy: 10, r: 9 }), txt(cx, 14, s.sym, 'gl-t gl-sym')];
        if (s.pm) o.push(txt(4, 7, '+', 'gl-t gl-tiny'), txt(4, 18, '−', 'gl-t gl-tiny'));
        return o;
      }
      case 'lag': return [txt(w / 2, 8, '1', 'gl-t gl-tiny'), path('M2 11H' + (w - 2)), txt(w / 2, 21, '1+sTC', 'gl-t gl-tiny')];
      case 'rate': return [txt(5, 14, 'V', 'gl-t gl-hl'), path('M13 2L26 9L13 16'), path('M18 1V17')];
      case 'clamp': return [path('M0 11H' + w, 'gl-s gl-axis'), path('M2 19H11L25 3H34')];
      case 'alarm': return [path('M11 0.8L21.2 8.2L17.3 20.5H4.7L0.8 8.2Z'), txt(11, 16, 'A', 'gl-t gl-hl')];
      case 'expand': return [path('M0 11H7', 'gl-s'), path('M7 11l-3 -2v4z', 'gl-f'), path('M19 1H13V21H19')];
      case 'split': {
        const L1 = s.join ? [path('M20 8.5H29.5V12.5H20Z'), path('M0.5 2.5H9.5V5.5H0.5ZM0.5 15H9.5V18H0.5Z'), path('M9.5 4H14V16.5H9.5M14 10.5H20')]
          : [path('M0.5 8.5H9.5V12.5H0.5Z'), path('M20 2.5H29.5V5.5H20ZM20 15H29.5V18H20Z'), path('M9.5 10.5H15M15 4V16.5M15 4H20M15 16.5H20')];
        return L1;
      }
      case 'select': {
        // CASC 閉合接點在最上、每對 INn／SELn 一個開路接點，全部接到右側匯流線，OUT 由右上出
        const yl = 4 + 9 * s.n;
        let d = 'M2 4H8M16 4H24M24 4V' + yl + 'M24 4H30M11 4H13';
        const o = [el('circle', { class: 'gl-s', cx: 9.5, cy: 4, r: 1.5 }), el('circle', { class: 'gl-s', cx: 14.5, cy: 4, r: 1.5 })];
        for (let k = 1; k <= s.n; k++) {
          const y = 4 + 9 * k;
          d += 'M2 ' + y + 'H8M10.5 ' + (y - 1) + 'L17 ' + (y - 4.5) + 'M17 ' + y + 'H24';
          o.push(el('circle', { class: 'gl-s', cx: 9.5, cy: y, r: 1.5 }));
        }
        return [path(d)].concat(o);
      }
      default: return [];
    }
  }
  G.draw = function (s, opts) {
    opts = opts || {};
    const g = el('g', { class: 'gl gl-' + s.kind, transform: 'translate(' + r1(opts.x || 0) + ' ' + r1(opts.y || 0) + ')' });
    if (s.kind !== 'ladder') g.appendChild(el('title', { text: s.title || '' }));
    for (const c of draw(s, opts)) g.appendChild(c);
    return g;
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = G;
  if (root && root.DC) root.DC.glyph = G;
})(typeof window !== 'undefined' ? window : null);
