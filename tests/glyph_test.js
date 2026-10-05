/* 方塊圖示（docs/assets/glyph.js）的純函式測試：node tests/glyph_test.js [< rungs.json]
 *
 * 不帶輸入：只跑內建案例（列印圖上看得到的排法、語法拒收、各型別的圖示規格）。
 * 帶輸入（golden_test.py 由索引產生，不進 repo）：JSON 陣列 [{eqn, pins:[腳名…]}]，每個 RUNG 方程式都要
 *   1) 解析成功，且用到的字母（大小寫不分）正好等於方塊的輸入腳；
 *   2) 畫出來的梯形圖當成電路（接點閉合才導通、導線與垂直線永遠導通）從左端到線圈是否導通，
 *      和「方程式字串直接求值」（另一套求值，不經本檔的解析器）在所有輸入組合下都一樣——
 *      字母 ≤ 14 個全部窮舉，超過則固定種子抽 4096 組（含全真、全假）。
 * 全部通過印 ALL OK、exit 0；否則列出失敗並 exit 1。 */
'use strict';
const path = require('path');
const G = require(path.join(__dirname, '..', 'docs', 'assets', 'glyph.js'));

let fails = 0;
const check = (label, cond, detail) => { if (!cond) { fails++; console.log('FAIL ' + label + (detail ? ' :: ' + detail : '')); } };
const key = (a) => a.map((x) => x.join(',')).sort().join(' ');
const cellsOf = (L) => L.cells.map((x) => x.r + ',' + x.c + ',' + x.v + (x.nc ? '/' : '')).sort().join(' ');

/** 梯形圖當電路：欄界 b（0..W）× 列 r 的節點，導線／閉合接點連左右、垂直線連上下；左端 (0,0) 到線圈 (W,0) 是否導通 */
function conducts(L, val) {
  const W = L.w, par = new Map();
  const id = (b, r) => b + ':' + r;
  const find = (a) => { while (par.has(a) && par.get(a) !== a) a = par.get(a); return a; };
  const uni = (a, b) => { a = find(a); b = find(b); if (a !== b) par.set(a, b); };
  for (const [r, c] of L.wires) uni(id(c, r), id(c + 1, r));
  for (const [b, r] of L.vbars) uni(id(b, r), id(b, r + 1));
  for (const x of L.cells) if (!!val[x.v] !== x.nc) uni(id(x.c, x.r), id(x.c + 1, x.r));
  return find(id(0, 0)) === find(id(W, 0));
}
/** 方程式字串直接求值（獨立於解析器）：字母 → 值、* → &&、+ → ||、~ → ! */
function direct(eqn) {
  const s = String(eqn).replace(/^N:/, '');
  const js = s.replace(/[A-Za-z]/g, (ch) => 'v.' + ch.toUpperCase()).replace(/\*/g, '&&').replace(/\+/g, '||').replace(/~/g, '!');
  return new Function('v', 'return !!(' + js + ');');
}
function equivalent(eqn) {
  const tree = G.parse(eqn);
  if (!tree) return 'parse failed';
  const L = G.ladder(tree), f = direct(eqn);
  const vars = [...G.letters(tree)].sort();
  const n = vars.length;
  let seed = 12345;
  const rnd = () => { seed = (seed * 1103515245 + 12345) >>> 0; return seed; };
  const total = n <= 14 ? 1 << n : 4096;
  for (let i = 0; i < total; i++) {
    const bits = n <= 14 ? i : i === 0 ? 0 : i === 1 ? 0xffffffff : rnd();
    const val = {};
    vars.forEach((v, j) => { val[v] = !!((bits >>> j) & 1); });
    const want = f(val);
    if (G.evalTree(tree, val) !== want) return 'tree differs at ' + JSON.stringify(val);
    if (conducts(L, val) !== want) return 'ladder differs at ' + JSON.stringify(val);
  }
  return null;
}

/* ------------------------------------------------------------------ 內建：列印圖上的排法 */
{
  let L = G.ladder(G.parse('N:A*~B'));
  check('series', L.w === 2 && L.h === 1 && cellsOf(L) === '0,0,A 0,1,B/' && L.vbars.length === 0, cellsOf(L));
  L = G.ladder(G.parse('N:A*~B+C*D'));
  check('sum of products: one row per product', L.w === 2 && L.h === 2 && cellsOf(L) === '0,0,A 0,1,B/ 1,0,C 1,1,D' && key(L.vbars) === '0,0 2,0', key(L.vbars));
  L = G.ladder(G.parse('N:(A+~B)*(C+D)'));
  check('adjacent groups share one bar', L.w === 2 && L.h === 2 && key(L.vbars) === '0,0 1,0 2,0', key(L.vbars));
  L = G.ladder(G.parse('N:A*(B+C)*D'));
  check('group in series + free-hanging left rail', L.w === 3 && L.h === 2 && cellsOf(L) === '0,0,A 0,1,B 0,2,D 1,1,C' && key(L.vbars) === '0,0 1,0 2,0' && L.wires.length === 0, key(L.vbars));
  L = G.ladder(G.parse('N:A+~A*(B+C)'));
  check('short branch padded on its top row only', key(L.wires) === '0,1' && key(L.vbars) === '0,0 0,1 1,1 2,0 2,1', key(L.wires) + ' | ' + key(L.vbars));
  L = G.ladder(G.parse('N:(A+B+C)+(D*~E)'));
  check('bracketed OR inside OR stays nested', L.w === 2 && L.h === 4 && key(L.vbars) === '0,0 0,1 0,2 1,0 1,1 2,0 2,1 2,2' && key(L.wires) === '0,1', key(L.vbars) + ' | ' + key(L.wires));
  L = G.ladder(G.parse('N:((A+(B*~C))*~D)'));
  check('redundant parentheses ignored', L.w === 3 && L.h === 2 && cellsOf(L) === '0,0,A 0,2,D/ 1,0,B 1,1,C/', cellsOf(L));
  check('lowercase letter = its uppercase pin', cellsOf(G.ladder(G.parse('N:a*~B'))) === '0,0,A 0,1,B/');
  check('spaces ignored', cellsOf(G.ladder(G.parse('N:A + (B*~C)'))) === cellsOf(G.ladder(G.parse('N:A+(B*~C)'))));
  for (const bad of ['N:~(A*B)', 'N:A**B', 'N:A+', 'N:', 'N:IN1*IN2', 'N:(A', 'N:A)', 'N:A*B)', null, 'N:A^B'])
    check('reject ' + bad, G.parse(bad) === null);
  for (const e of ['N:A', 'N:~A', 'N:A*B*C', 'N:(A*~C*~D)+(B*~C*~D)', 'N:((A*~C*~D)+(B*~C*~D))*~E', 'N:(A*~B+~A*B)*~C', 'N:A+(B+G+H+~L)*~C+(D+I+J)*C*~N+(M+I+J)*N*C+F*E+K',
    'N:(A*B*~C)*((~D*~E*~I*~L)+((F+G+~N)*((H*I)+J+K+L)))*~M', 'N:(A+B+C)+(D*~E)', 'N:(((A)*~G+B)+C*~G+D)*~E*F'])
    check('equivalent ' + e, equivalent(e) === null, equivalent(e));
}

/* ------------------------------------------------------------------ 內建：各型別的圖示規格 */
{
  const P = (name, ck, conn, v) => [name, 'I', '-', ck, conn, v || null, null, null, null, null, null, null];
  const ops = { GE: '≥', GT: '>', LT: '<', EQ: '=', LE: '≤', NE: '≠' };
  for (const k in ops) {
    const s = G.of('COMPARE', [P('FUNC', 'E', 'E:' + k), P('IN1', 'V', 'X', 'C.X'), P('IN2', 'N', 'N:1')]);
    check('COMPARE ' + k, s && s.kind === 'op' && s.sym === ops[k] && s.eat.join() === 'FUNC');
  }
  check('COMPARE numeric 1 = >', (G.of('COMPARE', [P('FUNC', 'N', 'N:1')]) || {}).sym === '>');
  check('COMPARE other numeric: no glyph', G.of('COMPARE', [P('FUNC', 'N', 'N:2')]) === null);
  check('COMPARE FUNC on a variable: no glyph', G.of('COMPARE', [P('FUNC', 'V', 'F', 'C.F')]) === null);
  check('GT type', (G.of('GT', []) || {}).sym === '>');
  check('MIN_MAX Max', (G.of('MIN_MAX', [P('FUNC', 'E', 'E:Max')]) || {}).text === 'MAX');
  check('MIN_MAX unset = MIN (printed default)', (G.of('MIN_MAX', [P('FUNC', '-', null)]) || {}).text === 'MIN');
  check('MIN_MAX on a variable: no glyph', G.of('MIN_MAX', [P('FUNC', 'V', 'F', 'C.F')]) === null);
  check('LATCH RDOM True = R', (G.of('LATCH', [P('RDOM', 'N', 'N:True')]) || {}).dom === 'R');
  check('LATCH RDOM FALSE = S', (G.of('LATCH', [P('RDOM', 'N', 'N:FALSE')]) || {}).dom === 'S');
  check('LATCH RDOM unset = library default FALSE: S circled (as printed)', (G.of('LATCH', [P('RDOM', 'A', null)]) || {}).dom === 'S' && (G.of('LATCH', []) || {}).dom === 'S');
  check('LATCH RDOM on a variable: no circle', (G.of('LATCH', [P('RDOM', 'V', 'R', 'C.R')]) || {}).dom === null);
  const sel = (n, m) => G.of('SELECT', [P('CASC', 'A', null)].concat(Array.from({ length: n }, (_, i) => P('IN' + (i + 1), 'V', 'X', 'C.X')), Array.from({ length: m }, (_, i) => P('SEL' + (i + 1), 'V', 'S', 'C.S'))));
  check('SELECT draws one switch per IN/SEL pair', (sel(1, 1) || {}).n === 1 && (sel(4, 4) || {}).n === 4 && (sel(8, 8) || {}).n === 8);
  check('SELECT with unequal IN / SEL counts: no glyph', sel(2, 1) === null && sel(0, 0) === null);
  let d = G.of('ON_OFF_DELAY', [P('PU_DEL', 'N', 'N:5000'), P('DO_DEL', 'A', null)]);
  check('ON_OFF_DELAY on-delay const', d && d.on && !d.off && d.onV === '5000' && d.eat.join() === 'PU_DEL');
  d = G.of('ON_OFF_DELAY', [P('PU_DEL', 'V', 'T', 'C.T'), P('DO_DEL', 'N', 'N:0')]);
  check('ON_OFF_DELAY variable delay, zero off-delay', d && d.on && !d.off && d.onV === '' && d.eat.join() === 'DO_DEL');
  d = G.of('ON_OFF_DELAY', [P('PU_DEL', 'A', null, 'C.T')]);
  check('ON_OFF_DELAY declared-at-pin variable counts as set', d && d.on);
  check('ON_OFF_DELAY with a delay of an unknown connection kind: no glyph', G.of('ON_OFF_DELAY', [P('PU_DEL', 'D', 'X.Y')]) === null);
  const rung = (eqn, pins) => G.of('RUNG', [P('EQN', 'N', eqn)].concat(pins.map((n) => P(n, 'V', n, 'C.' + n))));
  check('RUNG ladder', (rung('N:A*~B', ['A', 'B']) || {}).kind === 'ladder');
  check('RUNG letters must equal the input pins', rung('N:A*~B', ['A', 'B', 'C']) === null && rung('N:A*~C', ['A', 'B']) === null);
  check('RUNG bad equation falls back', rung('N:A**B', ['A', 'B']) === null);
  check('unknown type: no glyph', G.of('SOMETHING', []) === null && G.of('', []) === null);
}

/* ------------------------------------------------------------------ 輸入：索引裡的每個 RUNG 方程式 */
let input = '';
try { if (!process.stdin.isTTY) input = require('fs').readFileSync(0, 'utf8'); } catch (e) { input = ''; }
let nIn = 0;
if (input.trim()) {
  const rows = JSON.parse(input);
  for (const r of rows) {
    nIn++;
    const tree = G.parse(r.eqn);
    if (!tree) { check('parse ' + r.eqn, false); continue; }
    const used = [...G.letters(tree)].sort().join('');
    const ins = r.pins.filter((p) => /^[A-P]$/.test(p)).sort().join('');
    check('letters = input pins ' + r.eqn, used === ins, used + ' vs ' + ins);
    const bad = equivalent(r.eqn);
    check('ladder = equation ' + r.eqn, bad === null, bad);
    const L = G.ladder(tree);
    check('size cap ' + r.eqn, L.w <= 16 && L.h <= 16, L.w + 'x' + L.h);
  }
}
console.log((fails ? fails + ' FAILED' : 'ALL OK') + ' (index equations checked: ' + nIn + ')');
process.exit(fails ? 1 : 0);
