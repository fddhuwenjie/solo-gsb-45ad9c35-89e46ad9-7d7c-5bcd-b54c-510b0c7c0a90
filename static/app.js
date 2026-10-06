/* 针织行式编译台 —— 前端逻辑（原生 JS + SVG） */
'use strict';

const srcEl = document.getElementById('src');
const defsEl = document.getElementById('defs');
const castEl = document.getElementById('castOn');
const gutterEl = document.getElementById('gutter');
const defsGutterEl = document.getElementById('defsGutter');
const defsInfoEl = document.getElementById('defsInfo');
const rowsEl = document.getElementById('rows');
const statusEl = document.getElementById('status');
const chartEl = document.getElementById('chart');
const traceModeBtn = document.getElementById('traceModeBtn');
const traceBodyEl = document.getElementById('traceBody');

const SVGNS = 'http://www.w3.org/2000/svg';
const LINE_H = 20;          // 与 CSS 中 textarea 行高一致
const MAX_CHIPS = 400;      // 展开结果中最多渲染的针法标记数
const MAX_CELLS = 1000;     // 图解中每行最多渲染的格数（超出画省略号）
const TRACE_SEGMENT_NODES = 6000; // 文字树每段最多加入的节点数；可继续展开，不截断来源

const SYM_NAME = { k: 'k 下针', p: 'p 上针', yo: 'yo 挂线', k2tog: 'k2tog 二并一' };

// 追溯来源分支配色（按叶子序号循环取用）
const TRACE_COLORS = [
  '#d84343', '#1565c0', '#2e7d32', '#b3541e', '#6a1b9a', '#00838f',
  '#ad1457', '#558b2f', '#4e342e', '#5c6bc0', '#c2185b', '#ef6c00',
];

// 示例：片段定义（含相互引用）+ 行源码引用
const DEFAULT_DEFS = [
  '罗纹 = k k, p p',
  '双罗纹 = @罗纹 @罗纹',
  '镂空 = yo, k2tog',
].join('\n');

const DEFAULT_SOURCE = [
  '(@罗纹)x6',
  'k k k k, *@镂空*',
  '*@双罗纹*',
  '(k, @镂空)x8',
  '(@罗纹, (p, yo)x2, k2tog)x3',
  '*k k p*',
].join('\n');

let lineStarts = [0];
let defLineStarts = [0];
let lastData = null;
let reqSeq = 0;

/* 逐针追溯状态：
   trace = {row, pos, root, nodeCount, complete, castLeaves, yoLeaves}
   树按段惰性展开：node.children 只包含已展开段中的针位；遇到 stitch 且
   expanded=false 时，它的文字行是可继续展开的边界，不能被当作来源已完整。
   每个节点都带实际 st / kind / inChart，不生成无针法数据的占位节点。 */
let trace = null;
let traceMode = false;   // 图解「追溯选择模式」：开启后点格元选择追溯目标
let chartGeom = null;    // 最近一次图解几何 {cell, left, top, n, yOf}

/* ---------- 工具 ---------- */

function computeLineStarts(text) {
  const arr = [0];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === '\n') arr.push(i + 1);
  }
  return arr;
}

function lineRange(row) { // row 为 1 基（行源码）
  const start = lineStarts[row - 1] ?? 0;
  const next = lineStarts[row] ?? (srcEl.value.length + 1);
  return { start, len: Math.max(0, next - 1 - start) };
}

function defLineRange(line) { // line 为 1 基（定义区）
  const start = defLineStarts[line - 1] ?? 0;
  const next = defLineStarts[line] ?? (defsEl.value.length + 1);
  return { start, len: Math.max(0, next - 1 - start) };
}

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('data-')) node.setAttribute(k, v);
    else node[k] = v;
  }
  for (const c of children) node.append(c);
  return node;
}

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  return node;
}

/* ---------- 编译 ---------- */

let timer = null;
function scheduleCompile() {
  clearTimeout(timer);
  timer = setTimeout(doCompile, 200);
}

async function doCompile() {
  // 任何重新编译都意味着源码/定义/起针可能已变：立即清除旧追溯，
  // 旧针位编号不得沿用到新结果（修正后可重新选择）。
  clearTrace({ silent: true });

  const text = srcEl.value;
  const defsText = defsEl.value;
  lineStarts = computeLineStarts(text);
  defLineStarts = computeLineStarts(defsText);
  const rows = text.split('\n');
  // 末尾单个换行不产生幻影空行（中间的空白行仍视为有效行）
  if (rows.length > 1 && rows[rows.length - 1] === '') rows.pop();
  const castOn = parseInt(castEl.value, 10);

  const my = ++reqSeq;
  let data;
  try {
    const res = await fetch('/api/compile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ castOn, defs: defsText, rows }),
    });
    data = await res.json();
    if (!res.ok) data = { requestError: data.error || ('HTTP ' + res.status) };
  } catch (err) {
    data = { requestError: '无法连接服务：' + err.message };
  }
  if (my !== reqSeq) return; // 已有更新的请求发出，丢弃过期响应

  lastData = data;
  renderGutter(rows.length, data);
  renderDefs(defsText, data);
  renderRows(data);
  renderChart(data);
  renderStatus(data);
  // 追溯已在编译前清除；若当前恰好有合法选择（正常不会发生），重绘覆盖层
  renderTrace();
}

/* ---------- 状态栏 ---------- */

function renderStatus(data) {
  statusEl.className = 'status';
  if (data.requestError) {
    statusEl.textContent = '⚠ ' + data.requestError;
    statusEl.classList.add('err');
    return;
  }
  const errRow = data.rows.find(r => r.status === 'error');
  if (errRow) {
    const e = errRow.error;
    statusEl.textContent =
      `第 ${e.row} 行第 ${e.col} 列出错：${e.message}` +
      (e.def_line != null ? `（定义第 ${e.def_line} 行）` : '') +
      (e.remaining != null ? `（余 ${e.remaining} 针）` : '');
    statusEl.classList.add('err');
    return;
  }
  const okRows = data.rows.filter(r => r.status === 'ok');
  const last = okRows[okRows.length - 1];
  let text = last
    ? `✓ 共 ${data.rows.length} 行全部通过 · 末行针数 ${last.out}`
    : `✓ 起针 ${data.castOn} 针`;
  const defErrs = data.defs
    ? data.defs.lines.filter(d => d.status === 'error').length : 0;
  if (defErrs) text += ` ｜ ⚠ 定义区 ${defErrs} 行有问题`;
  statusEl.textContent = text;
  statusEl.classList.add('ok');
}

/* ---------- 行号栏（行源码 + 定义区） ---------- */

function renderGutter(rowCount, data) {
  const statusByRow = {};
  if (!data.requestError) {
    for (const r of data.rows) statusByRow[r.row] = r.status;
  }
  gutterEl.textContent = '';
  for (let i = 1; i <= rowCount; i++) {
    const st = statusByRow[i];
    const cls = st === 'error' ? 'g-err' : st === 'blocked' ? 'g-blk' : '';
    gutterEl.append(el('div', { class: cls }, String(i)));
  }
}

function renderDefs(defsText, data) {
  const lineCount = defsText.split('\n').length;
  const byLine = {};
  if (!data.requestError && data.defs) {
    for (const d of data.defs.lines) byLine[d.line] = d;
  }
  defsGutterEl.textContent = '';
  let total = 0, used = 0, errs = 0;
  for (let i = 1; i <= lineCount; i++) {
    const d = byLine[i];
    let cls = '';
    if (d) {
      if (d.status === 'error') { cls = 'g-err'; errs++; }
      else if (d.status === 'unused') cls = 'g-unused';
      if (d.name) { total++; if (d.used) used++; }
    }
    defsGutterEl.append(el('div', { class: cls }, String(i)));
  }
  if (data.requestError) {
    defsInfoEl.textContent = '';
    return;
  }
  defsInfoEl.textContent = total
    ? `共 ${total} 个片段 · ${used} 个被引用` +
      (errs ? ` · ${errs} 行有问题（未引用的片段不影响编译）` : '')
    : '可在此定义片段：名称 = 针法序列，行内用 @名称 引用';
}

/* ---------- 展开结果 ---------- */

function renderRows(data) {
  rowsEl.textContent = '';
  if (data.requestError) {
    rowsEl.append(el('div', { class: 'errbox' }, '⚠ ' + data.requestError));
    return;
  }
  for (const row of data.rows) {
    rowsEl.append(renderRowCard(row));
  }
}

function chipTitle(st) {
  const base = SYM_NAME[st.sym];
  if (!st.def) return `${base} · 源第 ${st.s + 1}–${st.e} 列`;
  const via = (st.chain && st.chain.length > 1)
    ? `（经 ${st.chain.slice(0, -1).join(' → ')}）` : '';
  return `${base} · 行内引用第 ${st.s + 1}–${st.e} 列 · ` +
    `定义「${st.def.name}」第 ${st.def.line} 行第 ${st.def.s + 1}–${st.def.e} 列${via}`;
}

function renderRowCard(row) {
  const card = el('div', { class: 'rowcard', 'data-row': row.row });

  const head = el('div', { class: 'rowhead' },
    el('span', { class: 'no' }, `第 ${row.row} 行`));

  if (row.status === 'ok') {
    head.append(
      el('span', { class: 'badge ok' }, '通过'),
      el('span', { class: 'io' }, `入 ${row.in} → 出 ${row.out}`));
    const chips = el('div', { class: 'chips' });
    const list = row.stitches.slice(0, MAX_CHIPS);
    list.forEach((st, j) => {
      const chip = el('span', {
        class: 'chip ' + st.sym + (st.def ? ' fromdef' : ''),
        title: chipTitle(st),
        'data-row': row.row, 'data-pos': j + 1,
        'data-s': st.s, 'data-e': st.e,
      }, st.sym);
      chip.append(el('button', {
        class: 'tracejump', type: 'button',
        title: '逐针追溯：本针沿各行实际消耗来自哪些针位',
        'data-trace-row': row.row, 'data-trace-pos': j + 1,
      }, '溯'));
      if (st.def) {
        chip.append(el('button', {
          class: 'defjump', type: 'button',
          title: `定位定义「${st.def.name}」第 ${st.def.line} 行`,
          'data-def-line': st.def.line,
          'data-def-s': st.def.s, 'data-def-e': st.def.e,
        }, '↗'));
      }
      chips.append(chip);
    });
    if (row.stitches.length > MAX_CHIPS) {
      chips.append(el('span', { class: 'chip more' },
        `… 共 ${row.stitches.length} 针`));
    }
    card.append(head, chips);
  } else if (row.status === 'error') {
    const e = row.error;
    head.append(
      el('span', { class: 'badge error' }, '错误'),
      el('span', { class: 'io' }, `入 ${row.in}`));
    const box = el('div', { class: 'errbox' },
      `第 ${e.row} 行 · 第 ${e.col} 列 · ${e.message}` +
      (e.remaining != null ? ` ｜ 余针 ${e.remaining}` : ''),
      el('button', {
        class: 'locate', type: 'button',
        'data-row': e.row, 'data-s': e.col - 1, 'data-e': e.col,
      }, '定位'));
    if (e.def_line != null) {
      box.append(el('button', {
        class: 'locate defjump', type: 'button',
        'data-def-line': e.def_line,
        'data-def-s': e.def_col - 1, 'data-def-e': e.def_col,
      }, `定位定义${e.def_name ? '「' + e.def_name + '」' : ''}`));
    }
    card.append(head, box);
  } else { // blocked
    head.append(el('span', { class: 'badge blocked' }, '未计算'));
    card.append(head,
      el('div', { class: 'blocked-note' },
        '因前面行存在错误，本行针数无法推导（修正后自动重算）'));
  }
  return card;
}

/* ---------- 源码 / 定义区定位 ---------- */

function revealSource(row, s, e) {
  const { start, len } = lineRange(row);
  const a = start + Math.min(Math.max(0, s), len);
  const b = start + Math.min(Math.max(0, e), len);
  srcEl.focus();
  srcEl.setSelectionRange(a, b);
  srcEl.scrollTop = Math.max(0, (row - 2) * LINE_H - LINE_H);
  gutterEl.scrollTop = srcEl.scrollTop;

  const card = rowsEl.querySelector(`.rowcard[data-row="${row}"]`);
  if (card) {
    card.scrollIntoView({ block: 'nearest' });
    card.classList.add('flash');
    setTimeout(() => card.classList.remove('flash'), 900);
  }
  markActiveRow(row);
}

function revealDef(line, s, e) {
  const { start, len } = defLineRange(line);
  const a = start + Math.min(Math.max(0, s), len);
  const b = start + Math.min(Math.max(0, e), len);
  defsEl.focus();
  defsEl.setSelectionRange(a, b);
  defsEl.scrollTop = Math.max(0, (line - 2) * LINE_H - LINE_H);
  defsGutterEl.scrollTop = defsEl.scrollTop;
}

/* 光标所在行 ↔ 行卡片/图解行 联动高亮 */
function markActiveRow(row) {
  rowsEl.querySelectorAll('.rowcard.active')
    .forEach(c => c.classList.toggle('active', +c.dataset.row === row));
  chartEl.querySelectorAll('text.rowlabel')
    .forEach(t => t.classList.toggle('active', +t.dataset.row === row));
}

function cursorRow() {
  const pos = srcEl.selectionStart;
  const text = srcEl.value;
  let row = 1;
  for (let i = 0; i < pos; i++) if (text[i] === '\n') row++;
  return row;
}

/* ---------- SVG 图解 ---------- */

function renderChart(data) {
  chartEl.textContent = '';
  chartGeom = null;
  if (data.requestError) return;

  const rows = data.rows;
  const n = rows.length;
  const maxOut = Math.max(data.castOn, 1,
    ...rows.map(r => (r.status === 'ok' ? r.stitches.length : 0)));
  const shownMax = Math.min(maxOut, MAX_CELLS);
  const cell = maxOut > 300 ? 10 : maxOut > 120 ? 14 : 22;
  const LEFT = 46;
  const RIGHT = maxOut > MAX_CELLS ? 130 : 70;
  const TOP = 8, BOTTOM = 30;
  const width = LEFT + shownMax * cell + RIGHT;
  const height = TOP + n * cell + BOTTOM;

  chartEl.setAttribute('width', width);
  chartEl.setAttribute('height', height);
  chartEl.style.minWidth = width + 'px';

  const yOf = i => TOP + (n - 1 - i) * cell; // 第 1 行在底部
  chartGeom = {
    cell, left: LEFT, top: TOP, n, yOf,
    origHeight: height, capY: TOP + n * cell + BOTTOM / 2,
    contentEnd: TOP + n * cell,
  };

  rows.forEach((row, i) => {
    const y = yOf(i);

    // 左侧行号（点击选中整行源码）
    const lab = svgEl('text', {
      x: LEFT - 8, y: y + cell / 2, 'text-anchor': 'end',
      'dominant-baseline': 'central', 'font-size': 11,
      fill: row.status === 'error' ? '#c62828' : '#8a8378',
      class: 'rowlabel', 'data-row': row.row, 'data-s': 0,
      'data-e': lineRange(row.row).len, cursor: 'pointer',
    });
    lab.textContent = `行${row.row}`;
    chartEl.append(lab);

    if (row.status === 'ok') {
      const shown = Math.min(row.stitches.length, MAX_CELLS);
      for (let j = 0; j < shown; j++) {
        chartEl.append(makeCell(row.row, j, row.stitches[j],
          LEFT + j * cell, y, cell));
      }
      if (row.stitches.length > MAX_CELLS) {
        const t = svgEl('text', {
          x: LEFT + shown * cell + 6, y: y + cell / 2,
          'dominant-baseline': 'central', 'font-size': 11, fill: '#8a8378',
        });
        t.textContent = `… 共 ${row.stitches.length} 针`;
        chartEl.append(t);
      }
      // 右侧产出针数
      const outLab = svgEl('text', {
        x: LEFT + shown * cell +
          (row.stitches.length > MAX_CELLS ? 68 : 8),
        y: y + cell / 2, 'dominant-baseline': 'central',
        'font-size': 11, fill: '#8a8378',
      });
      outLab.textContent = `→${row.out}`;
      chartEl.append(outLab);
    } else if (row.status === 'error') {
      const band = svgEl('rect', {
        x: LEFT, y: y + 1, width: shownMax * cell, height: cell - 2,
        fill: '#fbe4e4', stroke: '#e0a3a0', rx: 3,
      });
      const t = svgEl('text', {
        x: LEFT + 6, y: y + cell / 2, 'dominant-baseline': 'central',
        'font-size': 11, fill: '#c62828',
      });
      t.textContent = `⚠ 第 ${row.error.col} 列：${row.error.message}`;
      chartEl.append(band, t);
    } else {
      const band = svgEl('rect', {
        x: LEFT, y: y + 1, width: shownMax * cell, height: cell - 2,
        fill: '#f2f2f2', stroke: '#d5d5d5', 'stroke-dasharray': '4 3', rx: 3,
      });
      const t = svgEl('text', {
        x: LEFT + 6, y: y + cell / 2, 'dominant-baseline': 'central',
        'font-size': 11, fill: '#9e9e9e',
      });
      t.textContent = '未计算（上行出错）';
      chartEl.append(band, t);
    }
  });

  // 底部起针标注（追溯展开时由 drawTraceOverlay 下移，避开起针标记）
  const co = svgEl('text', {
    id: 'castCaption',
    x: LEFT, y: TOP + n * cell + BOTTOM / 2,
    'dominant-baseline': 'central', 'font-size': 11, fill: '#8a8378',
  });
  co.textContent = `起针 ${data.castOn} 针（第 1 行消耗这些针）`;
  chartEl.append(co);
}

function makeCell(rowNo, idx, st, x, y, cell) {
  const attrs = {
    class: 'cell', 'data-row': rowNo, 'data-pos': idx + 1,
    'data-s': st.s, 'data-e': st.e,
  };
  if (st.def) { // 片段针法：双击可定位到定义
    attrs['data-def-line'] = st.def.line;
    attrs['data-def-s'] = st.def.s;
    attrs['data-def-e'] = st.def.e;
  }
  const g = svgEl('g', attrs);
  const fills = { k: '#eef6ee', p: '#e8f1fb', yo: '#fdf1e3', k2tog: '#f3e9f7' };
  const inks = { k: '#2e7d32', p: '#1565c0', yo: '#b3541e', k2tog: '#6a1b9a' };

  g.append(svgEl('rect', {
    class: 'bg', x: x + .5, y: y + .5, width: cell - 1, height: cell - 1,
    fill: fills[st.sym], stroke: '#cfc8bb', 'stroke-width': 1,
  }));

  const ink = inks[st.sym];
  const pad = Math.max(3, cell * 0.22);
  if (st.sym === 'p') {
    g.append(svgEl('line', {
      x1: x + pad, y1: y + cell / 2, x2: x + cell - pad, y2: y + cell / 2,
      stroke: ink, 'stroke-width': 1.6,
    }));
  } else if (st.sym === 'yo') {
    g.append(svgEl('circle', {
      cx: x + cell / 2, cy: y + cell / 2, r: (cell - 2 * pad) / 2,
      fill: 'none', stroke: ink, 'stroke-width': 1.6,
    }));
  } else if (st.sym === 'k2tog') {
    g.append(svgEl('line', {
      x1: x + pad, y1: y + cell - pad, x2: x + cell - pad, y2: y + pad,
      stroke: ink, 'stroke-width': 1.6,
    }));
  } else { // k：竖线
    g.append(svgEl('line', {
      x1: x + cell / 2, y1: y + pad, x2: x + cell / 2, y2: y + cell - pad,
      stroke: ink, 'stroke-width': 1.4,
    }));
  }

  const title = svgEl('title');
  title.textContent =
    `第 ${rowNo} 行 · 第 ${idx + 1} 针 · ${SYM_NAME[st.sym]} · ` +
    (st.def
      ? `引用第 ${st.s + 1}–${st.e} 列 · 定义「${st.def.name}」第 ` +
        `${st.def.line} 行（双击定位定义）`
      : `源第 ${st.s + 1}–${st.e} 列`) +
    '（打开「追溯选择模式」后单击可逐针追溯来源）';
  g.append(title);
  return g;
}

/* ---------- 事件 ---------- */

// 编辑源码/定义/起针：立即清除旧追溯（无需等待防抖编译返回），
// 再调度重编译；编译返回前旧追溯不会残留在界面上。
function onEdit() {
  clearTrace({ silent: true });
  scheduleCompile();
}
srcEl.addEventListener('input', onEdit);
defsEl.addEventListener('input', onEdit);
castEl.addEventListener('input', onEdit);
srcEl.addEventListener('scroll', () => { gutterEl.scrollTop = srcEl.scrollTop; });
defsEl.addEventListener('scroll',
  () => { defsGutterEl.scrollTop = defsEl.scrollTop; });
srcEl.addEventListener('keyup', () => markActiveRow(cursorRow()));
srcEl.addEventListener('click', () => markActiveRow(cursorRow()));

// 图解「追溯选择模式」开关
traceModeBtn.addEventListener('click', () => setTraceMode(!traceMode));
traceBodyEl.addEventListener('click', ev => {
  if (ev.target.closest('.trace-clear')) clearTrace();
  if (ev.target.closest('.trace-continue')) continueTrace();
});

// 行卡片 / 图解中的点击：
//   「溯」按钮 → 开始逐针追溯（不做源码定位）；
//   追溯模式下点图解格元 → 选择追溯目标；
//   其余 → 回到源码（原单击定位行源码保持可用）。
// ↗ 按钮始终定位定义；双击格元定位定义保持不变。
document.addEventListener('click', ev => {
  const tj = ev.target.closest('.tracejump');
  if (tj) {
    startTrace(+tj.dataset.traceRow, +tj.dataset.tracePos);
    return;
  }
  const dj = ev.target.closest('.defjump');
  if (dj) {
    revealDef(+dj.dataset.defLine, +dj.dataset.defS, +dj.dataset.defE);
    return;
  }
  const cell = ev.target.closest('#chart .cell');
  if (cell && traceMode) {
    startTrace(+cell.dataset.row, +cell.dataset.pos);
    return;
  }
  const t = ev.target.closest('[data-row][data-s]');
  if (!t) return;
  revealSource(+t.dataset.row, +t.dataset.s, +t.dataset.e);
});

// 图解格元双击 → 定位到针法所在定义
chartEl.addEventListener('dblclick', ev => {
  const t = ev.target.closest('[data-def-line]');
  if (!t) return;
  revealDef(+t.dataset.defLine, +t.dataset.defS, +t.dataset.defE);
});

/* ---------- 逐针来源追溯 ---------- */

function setTraceMode(on) {
  traceMode = on;
  traceModeBtn.textContent = on ? '🧭 追溯选择模式：开（点格元选择）'
                                : '🧭 追溯选择模式：关';
  traceModeBtn.classList.toggle('on', on);
  traceModeBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  chartEl.classList.toggle('trace-mode', on);
}

function rowOk(rowNo) {
  const r = lastData && lastData.rows && lastData.rows[rowNo - 1];
  return r && r.status === 'ok' ? r : null;
}

/* 选择追溯目标：只有 status=ok 且针位合法的行可追溯；
   出错 / 未计算行在图解与展开结果中根本没有可选格元，这里再兜底校验。 */
function startTrace(rowNo, pos) {
  if (!Number.isInteger(rowNo) || !Number.isInteger(pos)) {
    flashTraceDenied('请填写有效的行号与针位（正整数）。');
    return;
  }
  const r = rowOk(rowNo);
  if (!r || !Array.isArray(r.stitches)) {
    flashTraceDenied('该行出错或未计算，不能选择追溯；修正后可依新结果重新选择。');
    return;
  }
  if (pos < 1 || pos > r.stitches.length) {
    flashTraceDenied(`第 ${rowNo} 行共 ${r.stitches.length} 个产出针，` +
      `针位需在 1–${r.stitches.length} 之间。`);
    return;
  }

  trace = {
    row: rowNo,
    pos,
    root: makeTraceNode(rowNo, pos),
    nodeCount: 1,
    complete: false,
    castLeaves: [],
    yoLeaves: [],
  };
  expandTraceSegment();
  renderTrace();
  const ri = document.getElementById('traceRowInp');
  const pi = document.getElementById('tracePosInp');
  if (ri) ri.value = rowNo;
  if (pi) pi.value = pos;
  // 不强制把页面滚到面板：从展开列表点「溯」时保持原有阅读位置；
  // 直接输入行号/针位时，面板就在输入框上方，仍然可见。
}

function makeTraceNode(rowNo, pos) {
  const node = {
    row: rowNo,
    pos,
    kind: 'stitch',
    st: null,
    children: [],
    expanded: false,
    color: null,
    inChart: false,
  };
  if (rowNo === 0) {
    node.kind = 'cast';
    node.expanded = true;
    node.inChart = pos <= MAX_CELLS;
    return node;
  }

  const row = rowOk(rowNo);
  const st = row && row.stitches[pos - 1];
  if (!st) return null; // 防御：针位在新结果中已不存在
  node.st = st;
  node.inChart = pos <= MAX_CELLS;
  if (st.sym === 'yo') {
    node.kind = 'yo';
    node.expanded = true; // yo 没有输入：新增针，回溯终止
  }
  return node;
}

function expandTraceNode(node) {
  if (node.expanded || node.kind !== 'stitch' || !node.st) return 0;
  const children = [];
  if (node.row === 1) {
    for (const p of (node.st.pre || [])) {
      children.push(makeTraceNode(0, p));
    }
  } else {
    for (const p of (node.st.pre || [])) {
      children.push(makeTraceNode(node.row - 1, p));
    }
  }
  node.children = children.filter(Boolean);
  node.expanded = true;
  return node.children.length;
}

function firstUnexpandedNode(node) {
  if (node.kind === 'stitch' && !node.expanded) return node;
  for (const ch of node.children) {
    const found = firstUnexpandedNode(ch);
    if (found) return found;
  }
  return null;
}

function expandTraceSegment() {
  let added = 0;
  let budget = TRACE_SEGMENT_NODES;
  while (budget > 0) {
    const node = firstUnexpandedNode(trace.root);
    if (!node) {
      trace.complete = true;
      break;
    }
    const childCount = expandTraceNode(node);
    trace.nodeCount += childCount;
    added += childCount;
    budget -= childCount;
  }
  if (!firstUnexpandedNode(trace.root)) trace.complete = true;
  assignTraceColors();
  return added;
}

function continueTrace() {
  if (!trace || trace.complete) return;
  expandTraceSegment();
  renderTrace();
}

function assignTraceColors() {
  trace.castLeaves = [];
  trace.yoLeaves = [];
  let leafSeq = 0;
  const collapsedColor = '#8a8378';

  const visit = node => {
    if (node.kind === 'cast' || node.kind === 'yo') {
      node.color = TRACE_COLORS[leafSeq % TRACE_COLORS.length];
      node.leafNo = leafSeq + 1;
      leafSeq += 1;
      (node.kind === 'cast' ? trace.castLeaves : trace.yoLeaves).push(node);
      return;
    }
    if (!node.expanded || !node.children.length) {
      node.color = collapsedColor;
      return;
    }
    node.children.forEach(visit);
    node.color = node.children[0].color;
  };
  visit(trace.root);
}

function clearTrace({ silent } = {}) {
  trace = null;
  document.querySelectorAll('.chip.trace-target, .chip.trace-ancestor')
    .forEach(c => c.classList.remove('trace-target', 'trace-ancestor'));
  const old = chartEl.querySelector('#traceOverlay');
  if (old) old.remove();
  // 还原追溯时为起针标记撑开的 SVG 高度与说明文字位置
  if (chartGeom) {
    chartEl.setAttribute('height', chartGeom.origHeight);
    const caption = chartEl.querySelector('#castCaption');
    if (caption) caption.setAttribute('y', chartGeom.capY);
  }
  if (!silent) renderTrace();
}

function flashTraceDenied(msg) {
  clearTrace({ silent: true }); // 先移除旧的高亮/连线，再显示拒绝原因
  traceBodyEl.textContent = '';
  traceBodyEl.append(el('p', { class: 'trace-denied' }, '⚠ ' + msg));
  traceBodyEl.append(traceSelector());
}

/* ---- 渲染：文字面板 + 展开列表高亮 + SVG 覆盖层 ---- */

function renderTrace() {
  traceBodyEl.textContent = '';
  if (!trace) {
    traceBodyEl.append(el('p', { class: 'trace-empty' },
      '尚未选择产出针。仅已计算通过的行可追溯；出错或未计算的行不能选择。'));
    traceBodyEl.append(traceSelector());
    return;
  }

  const targetSt = trace.root.st;
  const sourceText = trace.complete
    ? `${trace.castLeaves.length} 个起针来源 · ${trace.yoLeaves.length} 个 yo 新针来源`
    : `已展开 ${trace.castLeaves.length} 个起针来源、${trace.yoLeaves.length} 个 yo 新针来源（尚未完整）`;
  const head = el('div', { class: 'trace-head' },
    el('span', { class: 'trace-target-name' },
      `目标：第 ${trace.row} 行 · 第 ${trace.pos} 针 · ${SYM_NAME[targetSt.sym]}`),
    el('span', { class: 'trace-count' },
      `累计显示 ${trace.nodeCount} 个针位 · ${sourceText}`),
    el('button', { class: 'trace-clear', type: 'button' }, '清除追溯'));
  traceBodyEl.append(head);

  const branchLine = el('div', { class: 'trace-branches' },
    el('span', { class: 'tb-label' },
      trace.complete ? '来源分支：' : '已展开来源分支：'));
  trace.castLeaves.forEach(n => branchLine.append(leafTag(n, '起针 ' + n.pos)));
  trace.yoLeaves.forEach(n =>
    branchLine.append(leafTag(n, `yo：第 ${n.row} 行第 ${n.pos} 针`)));
  if (!trace.complete) {
    branchLine.append(el('span', { class: 'branch-more' },
      `其余来源在未展开边界之后，需继续追溯，当前并非完整来源。`));
  }
  traceBodyEl.append(branchLine);

  // 逐跳树形文字按 DFS 分段展开；每个已展开节点的实际 pre 都完整列出。
  const treeWrap = el('div', { class: 'trace-tree' });
  treeWrap.append(treeNodeLine(trace.root, true));
  const ul = el('ol', { class: 'tt-children' });
  trace.root.children.forEach(ch => ul.append(renderTreeNode(ch)));
  treeWrap.append(ul);
  traceBodyEl.append(treeWrap);

  const undrawn = countUndrawn(trace.root);
  if (undrawn) {
    traceBodyEl.append(el('p', { class: 'trace-note' },
      `注：其中 ${undrawn} 个针位超出图解绘制上限（每行 ${MAX_CELLS} 格），` +
      '未在图中连线，已在上方逐针文字列出；也可用下方行号、针位直接选择。'));
  }
  if (!trace.complete) {
    traceBodyEl.append(el('p', { class: 'trace-note warn' },
      `文字树已按 ${TRACE_SEGMENT_NODES} 个针位为一段展开；灰色边界仍带实际针法数据，` +
      '但其前驱尚未在本段展开。继续展开即可沿实际前驱追到全部起针或 yo 来源，' +
      '不应把当前已展开分支误认为来源已完整。'));
    traceBodyEl.append(el('button', {
      class: 'trace-continue', type: 'button',
    }, `继续展开下一段（再追 ${TRACE_SEGMENT_NODES} 个针位）`));
  } else {
    traceBodyEl.append(el('p', { class: 'trace-note complete' },
      '追溯已完整：全部实际前驱均已追到起针针位或 yo 新针来源。'));
  }

  traceBodyEl.append(traceSelector());

  markChips();
  drawTraceOverlay();
}

/* 行/针快捷选择：展开列表超过 400 针、图解超过 1000 格未绘出时，
   仍可由此直接选择任意已计算行的产出针进行追溯。 */
function traceSelector() {
  const rowInp = el('input', {
    id: 'traceRowInp', class: 'trace-inp', type: 'number',
    min: '1', placeholder: '行',
  });
  const posInp = el('input', {
    id: 'tracePosInp', class: 'trace-inp', type: 'number',
    min: '1', placeholder: '针位',
  });
  const go = el('button', { class: 'trace-go', type: 'button' }, '开始追溯');
  go.addEventListener('click', () => {
    startTrace(parseInt(rowInp.value, 10), parseInt(posInp.value, 10));
  });
  [rowInp, posInp].forEach(i => i.addEventListener('keydown', e => {
    if (e.key === 'Enter') go.click();
  }));
  return el('div', { class: 'trace-selector' },
    el('span', { class: 'tb-label' }, '直接选择：第'),
    rowInp, el('span', null, '行 · 第'), posInp, el('span', null, '针'),
    go);
}

function leafTag(node, text) {
  return el('span', { class: 'leaf-tag' },
    el('i', { class: 'leaf-dot', style: `background:${node.color}` }), text);
}

function nodeLoc(st) {
  if (!st) return '';
  if (st.def) {
    const via = (st.chain && st.chain.length > 1)
      ? `（经 ${st.chain.slice(0, -1).join(' → ')}）` : '';
    return `行内引用第 ${st.s + 1}–${st.e} 列 · 定义「${st.def.name}」` +
      `第 ${st.def.line} 行第 ${st.def.s + 1}–${st.def.e} 列${via}`;
  }
  return `源第 ${st.s + 1}–${st.e} 列`;
}

function treeNodeLine(node, isRoot) {
  const swatch = el('i', { class: 'leaf-dot', style: `background:${node.color}` });
  if (node.kind === 'cast') {
    return el('div', { class: 'tt-node terminal' }, swatch,
      el('b', null, `起针第 ${node.pos} 针`),
      el('span', { class: 'tt-note' }, '（第 1 行消耗的起针针位）'));
  }
  if (node.kind === 'yo') {
    const line = el('div', {
      class: 'tt-node yo-term' + (isRoot ? ' root' : ''),
      title: nodeLoc(node.st),
    }, swatch,
      el('b', null, `第 ${node.row} 行第 ${node.pos} 针 · yo 挂线`),
      el('span', { class: 'tt-note' }, '（无输入针：此处产生的新针，回溯终止）'));
    if (!node.inChart) line.append(el('span', { class: 'tt-undrawn' }, '图中未绘出'));
    appendLocJump(line, node, '定位');
    return line;
  }
  if (!node.expanded) {
    const line = el('div', {
      class: 'tt-node collapsed',
      title: nodeLoc(node.st) + '；本段尚未展开其前驱',
    }, swatch,
      el('b', null, `第 ${node.row} 行第 ${node.pos} 针 · ${SYM_NAME[node.st.sym]}`),
      el('span', { class: 'tt-rel' },
        node.st.pre.length === 2
          ? `实际并入上一行第 ${node.st.pre[0]}、${node.st.pre[1]} 针`
          : `实际承接上一行第 ${node.st.pre[0]} 针`),
      el('span', { class: 'tt-collapsed' }, '前驱未展开 · 继续追溯'));
    if (!node.inChart) line.append(el('span', { class: 'tt-undrawn' }, '图中未绘出'));
    appendLocJump(line, node, '定位');
    if (node.st.def) appendDefJump(line, node);
    return line;
  }
  const cls = 'tt-node' + (isRoot ? ' root' : '');
  const line = el('div', { class: cls, title: nodeLoc(node.st) }, swatch,
    el('b', null, `第 ${node.row} 行第 ${node.pos} 针 · ${SYM_NAME[node.st.sym]}`),
    el('span', { class: 'tt-rel' },
      node.st.pre.length === 2
        ? `并入上一行第 ${node.st.pre[0]}、${node.st.pre[1]} 针`
        : `承接上一行第 ${node.st.pre[0]} 针`));
  if (!node.inChart) line.append(el('span', { class: 'tt-undrawn' }, '图中未绘出'));
  appendLocJump(line, node, '定位');
  if (node.st.def) appendDefJump(line, node);
  return line;
}

function appendDefJump(line, node) {
  line.append(el('button', {
    class: 'defjump linkish', type: 'button',
    'data-def-line': node.st.def.line,
    'data-def-s': node.st.def.s, 'data-def-e': node.st.def.e,
  }, '↗ 定义'));
}

function appendLocJump(line, node, label) {
  if (!node.st) return;
  line.append(el('button', {
    class: 'locate linkish', type: 'button',
    'data-row': node.row, 'data-s': node.st.s, 'data-e': node.st.e,
  }, label));
}

function renderTreeNode(node) {
  const li = el('li', { class: 'tt-li' });
  li.append(treeNodeLine(node, false));
  if (node.children.length) {
    const ul = el('ol', { class: 'tt-children' });
    node.children.forEach(ch => ul.append(renderTreeNode(ch)));
    li.append(ul);
  }
  return li;
}

function countUndrawn(node) {
  if (node.kind === 'cast') return node.inChart ? 0 : 1;
  let n = (node.kind === 'stitch' && !node.inChart) ? 1 : 0;
  node.children.forEach(ch => { n += countUndrawn(ch); });
  return n;
}

/* ---- 展开结果中的目标/前驱高亮 ---- */

function markChips() {
  document.querySelectorAll('.chip.trace-target, .chip.trace-ancestor')
    .forEach(c => c.classList.remove('trace-target', 'trace-ancestor'));
  const visit = (node, role) => {
    if (node.kind === 'stitch' || node.kind === 'yo') {
      const chip = rowsEl.querySelector(
        `.rowcard[data-row="${node.row}"] .chip[data-pos="${node.pos}"]`);
      if (chip) chip.classList.add(role);
      node.children.forEach(ch => visit(ch, 'trace-ancestor'));
    }
  };
  visit(trace.root, 'trace-target');
}

/* ---- SVG 覆盖层：来源分支连线 + 起针/yo 标记 ---- */

function drawTraceOverlay() {
  const old = chartEl.querySelector('#traceOverlay');
  if (old) old.remove();
  if (!chartGeom) return;
  const { cell, left, top, n } = chartGeom;

  const overlay = svgEl('g', { id: 'traceOverlay' });
  const edgeLayer = svgEl('g', { class: 'tr-edges' });
  const markLayer = svgEl('g', { class: 'tr-marks' });
  overlay.append(edgeLayer, markLayer);

  const drawn = new Set();
  const edgeDrawn = new Set();

  const cx = pos => left + (pos - 1) * cell + cell / 2;
  // 展开 SVG 底部空间放起针标记，并把「起针」说明文字下移避让
  const castY = chartGeom.contentEnd + cell + 6;
  const newHeight = castY + 24;
  chartEl.setAttribute('height', newHeight);
  const caption = chartEl.querySelector('#castCaption');
  if (caption) caption.setAttribute('y', castY + 17);

  function addEdges(node) {
    // 未展开边界与其所有更远前驱都尚未在文字树中展开，整支暂不连线；
    // 边界针位本身仍描灰框，继续展开后才会画出后续实际来源。
    if (node.kind === 'stitch' && !node.expanded) return;
    node.children.forEach(ch => {
      if (ch.inChart) {
        const key = [node.row, node.pos, ch.row, ch.pos].join(',');
        if (!edgeDrawn.has(key)) {
          edgeDrawn.add(key);
          const x2 = cx(node.pos);
          const y2 = chartGeom.yOf(node.row - 1) + 2;
          let x1, y1;
          if (ch.row === 0) {
            x1 = cx(ch.pos);
            y1 = castY - 5;           // 起针三角顶点
          } else {
            x1 = cx(ch.pos);
            y1 = chartGeom.yOf(ch.row - 1) + cell - 2;
          }
          edgeLayer.append(svgEl('line', {
            x1, y1, x2, y2, stroke: ch.color,
            'stroke-width': ch.kind === 'cast' ? 1.6 : 2,
            'stroke-opacity': .8,
            'stroke-dasharray': ch.kind === 'cast' ? '4 3' : null,
          }));
        }
      }
      addEdges(ch);
    });
  }

  function mark(node) {
    const key = node.row + ':' + node.pos + ':' + node.kind;
    if (drawn.has(key)) return;
    drawn.add(key);

    if (node.kind === 'cast') {
      if (!node.inChart) return;
      const x = cx(node.pos);
      markLayer.append(svgEl('polygon', {
        points: `${x - 4},${castY + 4} ${x + 4},${castY + 4} ${x},${castY - 4}`,
        fill: node.color, stroke: '#fff', 'stroke-width': 1,
      }));
      const t = svgEl('text', {
        x, y: castY + 15, 'text-anchor': 'middle',
        'font-size': 9, fill: node.color, class: 'tr-cast-label',
      });
      t.textContent = node.pos;
      markLayer.append(t);
      return;
    }
    if (node.inChart) {
      const x = cx(node.pos);
      const y = chartGeom.yOf(node.row - 1);
      const isRoot = node === trace.root;
      markLayer.append(svgEl('rect', {
        x: x - cell / 2 + 1.5, y: y + 1.5,
        width: cell - 3, height: cell - 3, rx: 3,
        fill: 'none', stroke: node.color,
        'stroke-width': isRoot ? 3 : 2,
        'stroke-dasharray': node.kind === 'yo' ? '3 2' : null,
      }));
      if (node.kind === 'yo') {
        // yo 来源端：填色圆点，标出「新针由此产生」
        markLayer.append(svgEl('circle', {
          cx: x, cy: y + cell / 2, r: Math.max(2.5, cell * .12),
          fill: node.color, stroke: '#fff', 'stroke-width': 1,
        }));
      }
    }
    node.children.forEach(mark);
  }

  addEdges(trace.root);
  mark(trace.root);
  chartEl.append(overlay);
}

/* ---------- 启动 ---------- */

defsEl.value = DEFAULT_DEFS;
srcEl.value = DEFAULT_SOURCE;
doCompile();
