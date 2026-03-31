/**
 * app.js
 * WebSocket 接收 + 左側表格 + 右側 ECharts 損益曲線
 */

// ── ECharts 初始化 ─────────────────────────────────────
const chartDom = document.getElementById('pnl-chart');
const chart    = echarts.init(chartDom, 'dark');

// 全域：供插值 + hover 使用
let _chartStrikes = [];
let _chartPnl     = [];   // 單位：萬元
let _atmStrike    = null; // 當前 ATM 履約價
let _tableScrolled = false; // 是否已完成首次自動捲動

function _interpolate(x) {
  if (_chartStrikes.length === 0) return null;
  if (x <= _chartStrikes[0])                        return _chartPnl[0];
  if (x >= _chartStrikes[_chartStrikes.length - 1]) return _chartPnl[_chartPnl.length - 1];
  for (let i = 0; i < _chartStrikes.length - 1; i++) {
    if (x >= _chartStrikes[i] && x <= _chartStrikes[i + 1]) {
      const t = (x - _chartStrikes[i]) / (_chartStrikes[i + 1] - _chartStrikes[i]);
      return _chartPnl[i] + t * (_chartPnl[i + 1] - _chartPnl[i]);
    }
  }
  return null;
}

const _GRID = { top: 40, right: 20, bottom: 50, left: 75 };

const chartOption = {
  backgroundColor: 'transparent',
  tooltip: { show: false },
  grid: _GRID,
  xAxis: {
    type: 'value',
    name: '履約價', nameLocation: 'center', nameGap: 35,
    axisLabel: { color: '#8b949e', fontSize: 11, formatter: v => Math.round(v) },
    axisLine:  { lineStyle: { color: '#30363d' } },
    splitLine: { show: false },
    minInterval: 50,
  },
  yAxis: {
    type: 'value',
    name: '損益（萬元）', nameLocation: 'middle', nameGap: 60,
    axisLabel:  { color: '#8b949e', fontSize: 11, formatter: v => v.toFixed(0) },
    axisLine:   { lineStyle: { color: '#30363d' } },
    splitLine:  { lineStyle: { color: '#21262d' } },
  },
  series: [
    {   // X軸以上：紅色面積
      name: '_pos', type: 'line', data: [], smooth: true,
      symbol: 'none',
      lineStyle: { width: 0, opacity: 0 },
      areaStyle: { color: 'rgba(248,81,73,0.22)', origin: 0 },
      silent: true, animation: false, z: 1,
    },
    {   // X軸以下：綠色面積
      name: '_neg', type: 'line', data: [], smooth: true,
      symbol: 'none',
      lineStyle: { width: 0, opacity: 0 },
      areaStyle: { color: 'rgba(63,185,80,0.22)', origin: 0 },
      silent: true, animation: false, z: 1,
    },
    {   // 主折線 + 藍色空心圓點
      name: '合併損益', type: 'line', data: [], smooth: true,
      symbol: 'circle', symbolSize: 6, showSymbol: true,
      lineStyle: { color: '#388bfd', width: 2 },
      itemStyle: { color: 'transparent', borderColor: '#388bfd', borderWidth: 1.5 },
      animation: false, z: 3,
    },
  ],
  graphic: [],
};
chart.setOption(chartOption);
window.addEventListener('resize', () => chart.resize());

// ── Hover：插值 + 十字準星 + 實心圓點 + Tooltip ────────
let _hoverRaf = false;
let _mouseStrike = null;  // 目前滑鼠在圖表上對應的履約價（data-space X）

function _clearHover() {
  chart.setOption({ graphic: [] }, { replaceMerge: ['graphic'] });
}

chart.getZr().on('mousemove', function(e) {
  if (_hoverRaf) return;
  _hoverRaf = true;
  requestAnimationFrame(() => { _hoverRaf = false; });

  const px = [e.offsetX, e.offsetY];
  if (!chart.containPixel('grid', px) || _chartStrikes.length === 0) {
    _mouseStrike = null;
    _clearHover(); return;
  }

  const [hx] = chart.convertFromPixel({ seriesIndex: 2 }, px);
  _mouseStrike = hx;
  const hy   = _interpolate(hx);
  if (hy === null) { _clearHover(); return; }

  const dot   = chart.convertToPixel({ seriesIndex: 2 }, [hx, hy]);
  const gBot  = chartDom.offsetHeight - _GRID.bottom;
  const gLeft = _GRID.left;

  const isPos  = hy >= 0;
  const color  = isPos ? '#f85149' : '#3fb950';
  const sign   = isPos ? '+' : '';
  const label1 = `結算 ${Math.round(hx)}`;
  const label2 = `${sign}${hy.toFixed(1)}萬`;

  const ttW = 115, ttH = 44;
  let ttX = dot[0] + 12;
  let ttY = dot[1] - ttH - 6;
  if (ttX + ttW > chartDom.offsetWidth - _GRID.right) ttX = dot[0] - ttW - 12;
  if (ttY < _GRID.top) ttY = dot[1] + 8;

  chart.setOption({ graphic: [
    { id: '_vl',   type: 'line',   z: 5, silent: true,
      shape: { x1: dot[0], y1: dot[1], x2: dot[0], y2: gBot },
      style: { stroke: '#58a6ff', lineWidth: 1, lineDash: [4, 4] } },
    { id: '_hl',   type: 'line',   z: 5, silent: true,
      shape: { x1: gLeft, y1: dot[1], x2: dot[0], y2: dot[1] },
      style: { stroke: '#58a6ff', lineWidth: 1, lineDash: [4, 4] } },
    { id: '_dot',  type: 'circle', z: 10, silent: true,
      shape: { cx: dot[0], cy: dot[1], r: 5 },
      style: { fill: '#388bfd' } },
    { id: '_ttbg', type: 'rect',   z: 8, silent: true,
      shape: { x: ttX, y: ttY, width: ttW, height: ttH, r: 4 },
      style: { fill: '#1c2128ee', stroke: '#30363d', lineWidth: 1 } },
    { id: '_tt1',  type: 'text',   z: 9, silent: true,
      x: ttX + 8, y: ttY + 7,
      style: { text: label1, fill: '#c9d1d9', fontSize: 11 } },
    { id: '_tt2',  type: 'text',   z: 9, silent: true,
      x: ttX + 8, y: ttY + 24,
      style: { text: label2, fill: color, fontSize: 12, fontWeight: 'bold' } },
  ]}, { replaceMerge: ['graphic'] });
});

chart.getZr().on('mouseout', () => { _mouseStrike = null; _clearHover(); });

// ── 滑鼠滾輪縮放（調整 noUiSlider X 軸範圍） ──────────
chartDom.addEventListener('wheel', function(e) {
  e.preventDefault();
  if (!_nouiSlider || _chartStrikes.length === 0) return;
  const [curMin, curMax] = _nouiSlider.get().map(Number);
  const center   = (_mouseStrike !== null) ? _mouseStrike : (curMin + curMax) / 2;
  const factor   = e.deltaY > 0 ? 1.15 : 0.87;  // 向下放大、向上縮小
  const fullMin  = Math.min(..._chartStrikes);
  const fullMax  = Math.max(..._chartStrikes);
  const newRange = Math.max((curMax - curMin) * factor, 200);  // 最小 200 點
  if (newRange >= fullMax - fullMin) {
    _nouiSlider.set([fullMin, fullMax]);
    return;
  }
  // 若一側被邊界夾住，把多出的空間補到另一側，確保 zoom out 能持續擴張直到全範圍
  let newMin = center - newRange / 2;
  let newMax = center + newRange / 2;
  if (newMax > fullMax) { newMax = fullMax; newMin = fullMax - newRange; }
  if (newMin < fullMin) { newMin = fullMin; newMax = fullMin + newRange; }
  _nouiSlider.set([Math.max(fullMin, newMin), Math.min(fullMax, newMax)]);
}, { passive: false });

// ── 全日盤 / 日盤(一般) 切換 ──────────────────────────
const showDayOnly = false;  // 保留供 _day suffix 邏輯使用，固定 false

const btnFull = document.getElementById('btn-full-session');
const btnDay  = document.getElementById('btn-day-session');

function _setSessionMode(mode) {
  btnFull.classList.toggle('active', mode === 'full');
  btnDay.classList.toggle('active',  mode === 'day');
  _currentSessionMode = mode;
  _updateSeriesCode();
  fetch('/api/set-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  }).catch(() => {});
}

btnFull.addEventListener('click', () => { _setSessionMode('full'); });
btnDay.addEventListener('click',  () => { _setSessionMode('day');  });

// ── 表格最大絕對值（用來計算 bar 寬度比例） ────────────
let maxAbsNet = 1;

// ── 前一次各欄數值（用於偵測變化 → 閃爍） ────────────
const prevValues = {};  // key: `${strike}_C` / `${strike}_P` / etc.

// ── noUiSlider 初始化 / 更新 ──────────────────────────
let _nouiSlider  = null;
let _sliderMin   = 0, _sliderMax = 0;

function _recalcYAxis(xMin, xMax) {
  const vis = [];
  for (let i = 0; i < _chartStrikes.length; i++) {
    if (_chartStrikes[i] >= xMin && _chartStrikes[i] <= xMax)
      vis.push(_chartPnl[i]);
  }
  const yL = _interpolate(xMin), yR = _interpolate(xMax);
  if (yL !== null) vis.push(yL);
  if (yR !== null) vis.push(yR);
  if (vis.length === 0) return;
  let yMin = Math.min(...vis), yMax = Math.max(...vis);
  const pad = (yMax - yMin) * 0.12 || 10;
  chart.setOption({
    xAxis: { min: xMin, max: xMax },
    yAxis: { min: yMin - pad, max: yMax + pad },
  }, { notMerge: false, lazyUpdate: false });
}

function _initSlider(minS, maxS, forceReset = false) {
  const el = document.getElementById('range-slider');
  if (_nouiSlider) {
    if (!forceReset && minS === _sliderMin && maxS === _sliderMax) return;
    _nouiSlider.updateOptions({ range: { min: minS, max: maxS } }, true);
    _nouiSlider.set([minS, maxS]);
  } else {
    _nouiSlider = noUiSlider.create(el, {
      start:   [minS, maxS],
      connect: true,
      step:    50,
      range:   { min: minS, max: maxS },
      tooltips: [
        { to: v => String(Math.round(v)) },
        { to: v => String(Math.round(v)) },
      ],
      format: { to: v => Math.round(v), from: v => Number(v) },
    });
    let _sliderRaf = null;
    _nouiSlider.on('update', (values) => {
      if (_sliderRaf) cancelAnimationFrame(_sliderRaf);
      _sliderRaf = requestAnimationFrame(() => {
        _sliderRaf = null;
        _recalcYAxis(Number(values[0]), Number(values[1]));
      });
    });
  }
  _sliderMin = minS;
  _sliderMax = maxS;
}

// ── ATM 垂直虛線 ───────────────────────────────────────
function updateATMLine(atmStrike) {
  _atmStrike = atmStrike;
  if (!atmStrike) return;
  chart.setOption({
    series: [{
      name: '合併損益',
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#ffa657', type: 'dashed', width: 1.5 },
        label: {
          show: true,
          position: 'end',
          formatter: `價平\n${atmStrike}`,
          color: '#ffa657',
          fontSize: 11,
          fontWeight: 'bold',
        },
        data: [{ xAxis: atmStrike }],
      },
    }],
  }, false);
}

// ── 更新右側損益圖 ─────────────────────────────────────
function updateChart(pnl, forceReset = false) {
  if (!pnl || !pnl.strikes || pnl.strikes.length === 0) return;

  _chartStrikes = pnl.strikes;
  _chartPnl     = pnl.pnl.map(v => Math.round(v * 10000 * 10) / 10);  // 億→萬，1位小數
  const minS = Math.min(..._chartStrikes);
  const maxS = Math.max(..._chartStrikes);

  const posData  = _chartStrikes.map((s, i) => [s, Math.max(_chartPnl[i], 0)]);
  const negData  = _chartStrikes.map((s, i) => [s, Math.min(_chartPnl[i], 0)]);
  const mainData = _chartStrikes.map((s, i) => [s, _chartPnl[i]]);

  chart.setOption({
    series: [
      { name: '_pos',    data: posData  },
      { name: '_neg',    data: negData  },
      { name: '合併損益', data: mainData },
    ],
  }, false);

  _initSlider(minS, maxS, forceReset);
  // 每次資料更新都重算 Y 軸：合約切換、盤別切換、一般 tick 皆 fit
  const [curMin, curMax] = _nouiSlider ? _nouiSlider.get().map(Number) : [minS, maxS];
  _recalcYAxis(curMin, curMax);

  _updatePnlStats();
}

// ── 損益統計（兩平點 / 最大獲利 / 最大損失）────────────
function _statSpan(cls, text) {
  const s = document.createElement('span');
  s.className = cls;
  s.textContent = text;
  return s;
}

let _impliedForward = null;
function _updatePnlStats() {
  const el = document.getElementById('pnl-stats');
  if (!el) return;
  el.textContent = '';
  if (_chartPnl.length === 0) return;

  // 最大獲利 / 最大損失
  const maxPnl    = Math.max(..._chartPnl);
  const maxStrike = _chartStrikes[_chartPnl.indexOf(maxPnl)];
  const minPnl    = Math.min(..._chartPnl);
  const minStrike = _chartStrikes[_chartPnl.indexOf(minPnl)];

  // 損益兩平點（線性內插，取整數）
  const bePts = [];
  for (let i = 0; i < _chartPnl.length - 1; i++) {
    const y0 = _chartPnl[i], y1 = _chartPnl[i + 1];
    if (y0 === 0) {
      bePts.push(_chartStrikes[i]);
    } else if ((y0 < 0) !== (y1 < 0)) {
      const x = _chartStrikes[i] + (y0 / (y0 - y1)) * (_chartStrikes[i + 1] - _chartStrikes[i]);
      bePts.push(Math.round(x));
    }
  }
  if (_chartPnl.at(-1) === 0) bePts.push(_chartStrikes.at(-1));

  const fmt = v => (v >= 0 ? '+' : '-') + Math.abs(Math.round(v)).toLocaleString();

  // 第一行：損益兩平
  const row1 = document.createElement('div');
  row1.appendChild(_statSpan('stat-label', '損益兩平'));
  row1.appendChild(document.createTextNode('\u3000'));
  row1.appendChild(_statSpan('stat-be', bePts.length ? bePts.join(' / ') : '無'));
  el.appendChild(row1);

  // 第二行：最大獲利 / 最大損失
  const row2 = document.createElement('div');
  row2.appendChild(_statSpan('stat-label', '最大獲利'));
  row2.appendChild(document.createTextNode('\u3000'));
  row2.appendChild(_statSpan('stat-profit', fmt(maxPnl) + ' 萬 '));
  row2.appendChild(_statSpan('stat-strike', '@' + maxStrike));
  row2.appendChild(document.createTextNode('\u3000\u3000'));
  row2.appendChild(_statSpan('stat-label', '最大損失'));
  row2.appendChild(document.createTextNode('\u3000'));
  row2.appendChild(_statSpan('stat-loss', fmt(minPnl) + ' 萬 '));
  row2.appendChild(_statSpan('stat-strike', '@' + minStrike));
  el.appendChild(row2);

  // 第三行：預估結算價（15檔合成期貨平均）
  const row3 = document.createElement('div');
  row3.appendChild(_statSpan('stat-label', '預估結算價'));
  row3.appendChild(document.createTextNode('\u3000'));
  row3.appendChild(_statSpan('stat-atm', _impliedForward != null ? String(_impliedForward) : '--'));
  el.appendChild(row3);
}

// ── DOM helper ────────────────────────────────────────
function _cell(cls, text, flash) {
  const d = document.createElement('div');
  d._baseCls = cls;
  d.className = cls + (flash ? ' flash' : '');
  d.textContent = text;
  if (flash) {
    d._flashTimer = setTimeout(() => {
      d.classList.remove('flash');
      d._flashTimer = null;
    }, 2000);
  }
  return d;
}
function _fmtPrice(v) {
  return v >= 50 ? String(Math.round(v)) : v.toFixed(1);
}
function _updateCell(el, cls, text, flash) {
  if (el._baseCls !== cls) {
    el._baseCls = cls;
    el.className = cls + (el.classList.contains('flash') ? ' flash' : '');
  }
  if (el.textContent !== text) el.textContent = text;
  if (flash && !el._flashTimer) {
    el.classList.add('flash');
    el._flashTimer = setTimeout(() => {
      el.classList.remove('flash');
      el._flashTimer = null;
    }, 2000);
  }
}
function _barCell(wrapCls, barCls, pct) {
  const w = document.createElement('div');
  w.className = wrapCls + ' bar-wrapper';
  const b = document.createElement('div');
  b.className = barCls;
  b.style.width = pct + '%';
  w.appendChild(b);
  return w;
}

// ── 更新左側 T 字報價表 ────────────────────────────────
function updateTable(rows) {
  if (!rows || rows.length === 0) return;
  window._lastRows = rows;  // 供切換模式時重繪

  // 依目前模式選欄位（日盤 / 日+夜）
  const sfx = showDayOnly ? '_day' : '';
  const gC = (r, f) => r[f + sfx] ?? r[f];

  // 找最大絕對值，供 bar 比例計算
  let newMax = 1;
  for (const r of rows) {
    newMax = Math.max(newMax, Math.abs(gC(r, 'net_call')), Math.abs(gC(r, 'net_put')));
  }
  maxAbsNet = newMax;

  const body = document.getElementById('strike-table-body');
  if (!body._rowMap) body._rowMap = {};

  const seenKeys = new Set();

  for (const r of rows) {
    const nc  = gC(r, 'net_call'),   np  = gC(r, 'net_put');
    const vc  = gC(r, 'vol_call'),   vp  = gC(r, 'vol_put');
    const rc_ = gC(r, 'ratio_call'), rp_ = gC(r, 'ratio_put');
    const ac  = gC(r, 'ask_match_call'), bc = gC(r, 'bid_match_call');
    const ap  = gC(r, 'ask_match_put'),  bp = gC(r, 'bid_match_put');
    const cbid = gC(r, 'bid_price_call'),  cask = gC(r, 'ask_price_call'),  clast = gC(r, 'last_price_call');
    const pbid = gC(r, 'bid_price_put'),   pask = gC(r, 'ask_price_put'),   plast = gC(r, 'last_price_put');

    const displayedNp = np;
    const callPct = (Math.abs(nc)          / maxAbsNet * 50).toFixed(1);
    const putPct  = (Math.abs(displayedNp) / maxAbsNet * 50).toFixed(1);

    const key = r.strike + sfx;
    seenKeys.add(key);

    const prev = prevValues[key] || {};
    const ch = (v, k) => prev[k] !== v;
    const syn = r.synthetic_futures, pc = r.pnl_call, pp = r.pnl_put, pcomb = r.pnl_combined;
    prevValues[key] = { nc, vc, rc_, avg_c: r.avg_price_call, ac, bc, cbid, cask, clast,
                        np, vp, rp_, avg_p: r.avg_price_put,  ap, bp, pbid, pask, plast,
                        syn, pc, pp, pcomb };

    const ncCls = 'col-call-val' + (nc > 0 ? ' val-pos' : nc < 0 ? ' val-neg' : '');
    const npCls = 'col-put-val'  + (displayedNp > 0 ? ' val-pos' : displayedNp < 0 ? ' val-neg' : '');

    const existing = body._rowMap[key];
    if (existing) {
      // ── 原地更新：保留 row DOM 節點，hover 狀態不中斷 ──
      existing.className = 'row' + (r.highlight ? ' highlight' : '');
      const c = existing._cells;
      _updateCell(c.call_sell,  'col-call-sell',  bc > 0 ? String(bc) : '',                          ch(bc,  'bc'));
      _updateCell(c.call_buy,   'col-call-buy',   ac > 0 ? String(ac) : '',                          ch(ac,  'ac'));
      _updateCell(c.call_vol,   'col-call-vol',   vc > 0 ? String(vc) : '',                          ch(vc,  'vc'));
      _updateCell(c.call_ratio, 'col-call-ratio', vc > 0 ? rc_.toFixed(2) : '',                      ch(rc_, 'rc_'));
      _updateCell(c.call_avg,   'col-call-avg',   r.avg_price_call > 0 ? r.avg_price_call.toFixed(1) : '', ch(r.avg_price_call, 'avg_c'));
      _updateCell(c.call_bid,  'col-call-bid',   cbid  > 0 ? _fmtPrice(cbid)  : '',                 ch(cbid,  'cbid'));
      _updateCell(c.call_ask,  'col-call-ask',   cask  > 0 ? _fmtPrice(cask)  : '',                 ch(cask,  'cask'));
      _updateCell(c.call_last, 'col-call-last',  clast > 0 ? _fmtPrice(clast) : '',                 ch(clast, 'clast'));
      _updateCell(c.call_val,   ncCls,            nc !== 0 ? nc.toFixed(0) : '',                     ch(nc,  'nc'));
      c.call_bar.firstChild.className = 'bar-call ' + (nc >= 0 ? 'positive' : 'negative');
      c.call_bar.firstChild.style.width = callPct + '%';
      c.put_bar.firstChild.className  = 'bar-put '  + (displayedNp >= 0 ? 'positive' : 'negative');
      c.put_bar.firstChild.style.width = putPct + '%';
      _updateCell(c.put_val,    npCls,            displayedNp !== 0 ? displayedNp.toFixed(0) : '',   ch(np,  'np'));
      _updateCell(c.put_bid,   'col-put-bid',    pbid  > 0 ? _fmtPrice(pbid)  : '',                 ch(pbid,  'pbid'));
      _updateCell(c.put_ask,   'col-put-ask',    pask  > 0 ? _fmtPrice(pask)  : '',                 ch(pask,  'pask'));
      _updateCell(c.put_last,  'col-put-last',   plast > 0 ? _fmtPrice(plast) : '',                 ch(plast, 'plast'));
      _updateCell(c.put_sell,   'col-put-sell',   bp > 0 ? String(bp) : '',                          ch(bp,  'bp'));
      _updateCell(c.put_buy,    'col-put-buy',    ap > 0 ? String(ap) : '',                          ch(ap,  'ap'));
      _updateCell(c.put_vol,    'col-put-vol',    vp > 0 ? String(vp) : '',                          ch(vp,  'vp'));
      _updateCell(c.put_ratio,  'col-put-ratio',  vp > 0 ? rp_.toFixed(2) : '',                      ch(rp_, 'rp_'));
      _updateCell(c.put_avg,    'col-put-avg',    r.avg_price_put > 0 ? r.avg_price_put.toFixed(1) : '', ch(r.avg_price_put, 'avg_p'));
      _updateCell(c.synthetic,    'col-synthetic',    syn   != null ? syn.toFixed(1)   : '', ch(syn,   'syn'));
      _updateCell(c.pnl_call,    'col-pnl-call',     pc    != null ? pc.toFixed(4)    : '', ch(pc,    'pc'));
      _updateCell(c.pnl_put,     'col-pnl-put',      pp    != null ? pp.toFixed(4)    : '', ch(pp,    'pp'));
      _updateCell(c.pnl_combined,'col-pnl-combined', pcomb != null ? pcomb.toFixed(4) : '', ch(pcomb, 'pcomb'));
    } else {
      // ── 首次建立 row ──
      const row = document.createElement('div');
      row.className = 'row' + (r.highlight ? ' highlight' : '');
      row.dataset.strike = String(r.strike);
      const c = {};
      c.call_sell  = _cell('col-call-sell',  bc > 0 ? String(bc) : '',                          ch(bc,  'bc')  ? ' flash' : '');
      c.call_buy   = _cell('col-call-buy',   ac > 0 ? String(ac) : '',                          ch(ac,  'ac')  ? ' flash' : '');
      c.call_vol   = _cell('col-call-vol',   vc > 0 ? String(vc) : '',                          ch(vc,  'vc')  ? ' flash' : '');
      c.call_ratio = _cell('col-call-ratio', vc > 0 ? rc_.toFixed(2) : '',                      ch(rc_, 'rc_') ? ' flash' : '');
      c.call_avg   = _cell('col-call-avg',   r.avg_price_call > 0 ? r.avg_price_call.toFixed(1) : '', ch(r.avg_price_call, 'avg_c') ? ' flash' : '');
      c.call_bid   = _cell('col-call-bid',   cbid  > 0 ? _fmtPrice(cbid)  : '',                 ch(cbid,  'cbid')  ? ' flash' : '');
      c.call_ask   = _cell('col-call-ask',   cask  > 0 ? _fmtPrice(cask)  : '',                 ch(cask,  'cask')  ? ' flash' : '');
      c.call_last  = _cell('col-call-last',  clast > 0 ? _fmtPrice(clast) : '',                 ch(clast, 'clast') ? ' flash' : '');
      c.call_val   = _cell(ncCls,            nc !== 0 ? nc.toFixed(0) : '',                     ch(nc,  'nc')  ? ' flash' : '');
      c.call_bar   = _barCell('col-call-bar', 'bar-call ' + (nc >= 0 ? 'positive' : 'negative'), callPct);
      c.strike     = _cell('col-strike',     String(r.strike), '');
      c.put_bar    = _barCell('col-put-bar',  'bar-put '  + (displayedNp >= 0 ? 'positive' : 'negative'), putPct);
      c.put_val    = _cell(npCls,            displayedNp !== 0 ? displayedNp.toFixed(0) : '',   ch(np,  'np')  ? ' flash' : '');
      c.put_bid    = _cell('col-put-bid',    pbid  > 0 ? _fmtPrice(pbid)  : '',                 ch(pbid,  'pbid')  ? ' flash' : '');
      c.put_ask    = _cell('col-put-ask',    pask  > 0 ? _fmtPrice(pask)  : '',                 ch(pask,  'pask')  ? ' flash' : '');
      c.put_last   = _cell('col-put-last',   plast > 0 ? _fmtPrice(plast) : '',                 ch(plast, 'plast') ? ' flash' : '');
      c.put_buy    = _cell('col-put-buy',    ap > 0 ? String(ap) : '',                          ch(ap,  'ap')  ? ' flash' : '');
      c.put_sell   = _cell('col-put-sell',   bp > 0 ? String(bp) : '',                          ch(bp,  'bp')  ? ' flash' : '');
      c.put_vol    = _cell('col-put-vol',    vp > 0 ? String(vp) : '',                          ch(vp,  'vp')  ? ' flash' : '');
      c.put_ratio  = _cell('col-put-ratio',  vp > 0 ? rp_.toFixed(2) : '',                      ch(rp_, 'rp_') ? ' flash' : '');
      c.put_avg    = _cell('col-put-avg',    r.avg_price_put > 0 ? r.avg_price_put.toFixed(1) : '', ch(r.avg_price_put, 'avg_p') ? ' flash' : '');
      c.synthetic    = _cell('col-synthetic',    r.synthetic_futures != null ? r.synthetic_futures.toFixed(1) : '', '');
      c.pnl_call    = _cell('col-pnl-call',     r.pnl_call     != null ? r.pnl_call.toFixed(4)     : '', '');
      c.pnl_put     = _cell('col-pnl-put',      r.pnl_put      != null ? r.pnl_put.toFixed(4)      : '', '');
      c.pnl_combined = _cell('col-pnl-combined', r.pnl_combined != null ? r.pnl_combined.toFixed(4) : '', '');
      for (const cell of Object.values(c)) row.appendChild(cell);
      row._cells = c;
      body._rowMap[key] = row;
      body.appendChild(row);
    }
  }

  // 移除不再存在的履約價 row
  for (const key of Object.keys(body._rowMap)) {
    if (!seenKeys.has(key)) {
      body._rowMap[key].remove();
      delete body._rowMap[key];
    }
  }

  // ATM 行（highlight）改變時自動置中；live 更新期間 ATM 不變就不重複捲動
  const highlighted = body.querySelector('.highlight');
  if (highlighted) {
    const currentAtmStrike = highlighted.dataset.strike;
    if (!_tableScrolled || currentAtmStrike !== body._lastAtmStrike) {
      highlighted.scrollIntoView({ block: 'center', behavior: 'smooth' });
      _tableScrolled = true;
      body._lastAtmStrike = currentAtmStrike;
    }
  }
}

// ── 合約下拉選單 ───────────────────────────────────────
let _contractsData        = [];
let _viewingNonLive       = false;  // 用戶正在查看尚未 ready 的系列
let _viewingNonLiveSeries = null;   // 目標 series 名稱（e.g. "TXON04"）
let _ready = false;  // fetchContracts 完成前封鎖 handleData，防止 WS 在初始化期間渲染舊資料

// ── 損益視圖模式 ────────────────────────────────────────
let _viewMode            = 'live';   // 'live' | 'weekly' | 'snapshot'
let _lastLivePnl         = null;     // 最新 WS 推送的 live pnl（weekly 模式即時疊加用）
let _weeklyPnlBaseline   = null;     // /api/weekly-pnl 結果（排除今天的快照加總）
let _weeklyPnlCacheTime  = 0;        // baseline cache 時間戳（60s 內不重新 fetch）

function _mergeWithLive(livePnl) {
  if (!_weeklyPnlBaseline || !livePnl || !livePnl.strikes) return livePnl;
  const allStrikes = [...new Set([..._weeklyPnlBaseline.strikes, ...livePnl.strikes])].sort((a, b) => a - b);
  const baseMap = Object.fromEntries(_weeklyPnlBaseline.strikes.map((s, i) => [s, _weeklyPnlBaseline.pnl[i]]));
  const liveMap = Object.fromEntries(livePnl.strikes.map((s, i) => [s, livePnl.pnl[i]]));
  return { strikes: allStrikes, pnl: allStrikes.map(s => (baseMap[s] || 0) + (liveMap[s] || 0)) };
}

async function _loadWeeklyBaseline(series, settlementDate) {
  const now = Date.now() / 1000;
  const h = new Date().getHours(), m = new Date().getMinutes();
  const sessionReset = h > 14 || (h === 14 && m >= 35);
  // session_reset 狀態改變時強制重新 fetch（14:35 前後 cache 失效）
  if (_weeklyPnlBaseline && now - _weeklyPnlCacheTime < 60
      && _weeklyPnlBaseline._sessionReset === sessionReset) return;
  try {
    const params = new URLSearchParams({ series });
    if (settlementDate) params.set('settlement_date', settlementDate);
    const resp = await fetch(`/api/weekly-pnl?${params}`);
    if (!resp.ok) return;
    _weeklyPnlBaseline             = await resp.json();
    _weeklyPnlBaseline._sessionReset = sessionReset;
    _weeklyPnlCacheTime            = now;
  } catch (e) {
    console.warn('_loadWeeklyBaseline failed', e);
  }
}

async function updateViewModeDropdown(series) {
  const sel = document.getElementById('view-mode-select');
  if (!sel) return;
  // 清空舊快照選項（保留 live / weekly）
  while (sel.options.length > 2) sel.remove(2);
  try {
    const daySeries = series.replace('N', '');
    // 全日盤 + 日盤 各自 fetch，合併後按日期排序
    const [r1, r2] = await Promise.all([
      fetch(`/api/snapshots?series=${encodeURIComponent(series)}`),
      fetch(`/api/snapshots?series=${encodeURIComponent(daySeries)}`),
    ]);
    const snaps1 = r1.ok ? (await r1.json()).snapshots || [] : [];
    const snaps2 = r2.ok ? (await r2.json()).snapshots || [] : [];
    const all = [...snaps1, ...snaps2].sort((a, b) => a.date.localeCompare(b.date) || a.series.localeCompare(b.series));
    for (const snap of all) {
      sel.appendChild(new Option(snap.label, `snapshot:${snap.filename}`));
    }
  } catch (e) {
    console.warn('updateViewModeDropdown failed', e);
  }
}

function _setViewingNonLive(series, contractData) {
  _viewingNonLive       = true;
  _viewingNonLiveSeries = series;
  _clearDisplay();
  // 立刻更新 UI，不等下一次 poll
  const pct = (contractData && contractData.total_count > 0)
    ? Math.round(contractData.loaded_count / contractData.total_count * 100)
    : 0;
  document.getElementById('conn-dot').className    = 'dot dot-yellow';
  document.getElementById('conn-label').textContent = `連線中(${pct}%)`;
  document.getElementById('sub-count').textContent  = contractData ? contractData.loaded_count : 0;
}

function _clearDisplay() {
  const tb = document.getElementById('strike-table-body');
  tb.textContent = '';
  tb._rowMap = {};
  for (const k of Object.keys(prevValues)) delete prevValues[k];
  _tableScrolled = false;
  _chartStrikes = [];
  _chartPnl     = [];
  _atmStrike    = null;
  const statsEl = document.getElementById('pnl-stats');
  if (statsEl) statsEl.textContent = '';
  // 清空 chart：含 markLine（ATM 價平虛線）、x/y 軸範圍
  chart.setOption({ series: [
    { name: '_pos',    data: [] },
    { name: '_neg',    data: [] },
    { name: '合併損益', data: [], markLine: { data: [] } },
  ], xAxis: { min: 'dataMin', max: 'dataMax' },
     yAxis: { min: 'dataMin', max: 'dataMax' },
  }, false);
  // 重置 slider
  if (_nouiSlider) {
    _nouiSlider.updateOptions({ range: { min: 0, max: 1 } }, true);
    _nouiSlider.set([0, 1]);
    _sliderMin = 0;
    _sliderMax = 0;
  }
  // 清 sub-count
  document.getElementById('sub-count').textContent = '0';
}

async function fetchContracts() {
  try {
    const resp = await fetch('/api/contracts');
    if (!resp.ok) return false;
    const data = await resp.json();
    const list = data.contracts || [];
    if (list.length === 0) return false;

    _contractsData = list;
    const sel = document.getElementById('contract-select');
    while (sel.options.length) sel.remove(0);

    const today = new Date().toISOString().slice(0, 10);
    // 預設選 active_full 對應的那個；若沒有則選最近未到期
    let defaultIdx = list.findIndex(c => c.series === data.active_full);
    if (defaultIdx < 0) defaultIdx = list.findIndex(c => c.settlement_date >= today);
    if (defaultIdx < 0) defaultIdx = 0;

    list.forEach((c, i) => {
      const opt = document.createElement('option');
      opt.value = i;
      opt.textContent = c.live ? c.label : `${c.label} ·`;  // live 合約不加標記，非 live 加點
      sel.appendChild(opt);
    });

    sel.value = defaultIdx;
    document.getElementById('settlement-date').textContent =
      list[defaultIdx].settlement_display;
    _updateSeriesCode();
    const defaultContract = list[defaultIdx];
    _ready = true;  // 初始系列確定，開放 handleData 渲染
    if (!defaultContract.live) {
      // 預設合約尚未 ready → 立刻顯示連線中進度
      _setViewingNonLive(defaultContract.series, defaultContract);
    } else {
      _switchSeries(defaultContract);
    }
    // 填充損益視圖快照選單
    updateViewModeDropdown(defaultContract.series);

    sel.onchange = () => {
      _resetViewMode();
      const idx = parseInt(sel.value);
      const c   = _contractsData[idx];
      if (!c) return;
      document.getElementById('settlement-date').textContent = c.settlement_display;
      _updateSeriesCode();
      // 重置損益視圖並更新快照列表
      updateViewModeDropdown(c.series);
      if (!c.live) {
        // 系列尚未 ready：立刻顯示連線中進度，等待背景載入完成後自動切換
        _setViewingNonLive(c.series, c);
      } else {
        // 系列已 ready：不先清空，等 handleData 收到新 series 資料後原子置換
        _viewingNonLive       = false;
        _viewingNonLiveSeries = null;
        _switchSeries(c);
      }
    };
    return true;
  } catch(e) {
    console.warn('fetchContracts failed', e);
    return false;
  }
}

async function _switchSeries(c) {
  if (!c || !c.live) return;
  const seriesFull = c.series;
  const seriesDay  = c.series.replace('N', '');
  _targetSeries = seriesFull;  // 告知 handleData 只接受這個 series 的資料
  try {
    const resp = await fetch('/api/set-series', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ series_full: seriesFull, series_day: seriesDay }),
    });
    if (!resp.ok) return;
    const result = await resp.json();
    if (result.payload) {
      handleData(result.payload, 'HTTP');
    }
  } catch(e) {}
}

function _updateSeriesCode() {
  const sel = document.getElementById('contract-select');
  if (!sel || !_contractsData.length) return;
  const c = _contractsData[parseInt(sel.value)];
  if (!c) return;
  // 全日盤：TX4N03；日盤：去掉 N → TX403
  const code = _currentSessionMode === 'day'
    ? c.series.replace('N', '')
    : c.series;
  document.getElementById('series-code').textContent = code;
}

async function _initContracts() {
  const ok = await fetchContracts();
  if (!ok) setTimeout(_initContracts, 2000);  // xqfap 尚未推送，2 秒後重試
}
_initContracts();

// ── 定期刷新合約 live 狀態（背景載入完成時移除 · 標記）──
setInterval(async () => {
  if (!_contractsData.length) return;
  try {
    const resp = await fetch('/api/contracts');
    if (!resp.ok) return;
    const data = await resp.json();
    const list = data.contracts || [];
    if (list.length !== _contractsData.length) return;  // 清單結構不同，跳過

    const sel = document.getElementById('contract-select');
    let anyNewLive = false;
    list.forEach((c, i) => {
      if (!_contractsData[i].live && c.live) {
        _contractsData[i].live = true;
        anyNewLive = true;
        if (sel.options[i]) sel.options[i].textContent = c.label;  // 移除 ·
        // 用戶正在等待這個系列 ready → 自動切換並恢復顯示（用 series 名稱比對，不用 index）
        if (_viewingNonLive && _viewingNonLiveSeries === c.series) {
          _viewingNonLive       = false;
          _viewingNonLiveSeries = null;
          _switchSeries(c);
        }
      }
    });
    if (anyNewLive) {
      console.log('背景系列載入完成，下拉選單已更新');
    }
    // 更新非 live 系列的載入進度顯示
    if (_viewingNonLive && _viewingNonLiveSeries) {
      const nc = list.find(c => c.series === _viewingNonLiveSeries);
      if (nc && nc.total_count > 0) {
        const pct = Math.round(nc.loaded_count / nc.total_count * 100);
        document.getElementById('conn-dot').className   = 'dot dot-yellow';
        document.getElementById('conn-label').textContent = `連線中(${pct}%)`;
        document.getElementById('sub-count').textContent  = nc.loaded_count;
      }
    }
  } catch(e) {}
}, 5000);

// ── 更新頂部工具列與狀態角落 ──────────────────────────
function updateStatus(status, settlement) {
  // 下拉選單已有資料時，結算日由選單驅動，不被 WS 覆寫
  const sel = document.getElementById('contract-select');
  if ((!sel || sel.options.length === 0) && settlement) {
    document.getElementById('settlement-date').textContent = settlement;
  }
  if (status) {
    const dot   = document.getElementById('conn-dot');
    const label = document.getElementById('conn-label');
    if (_viewingNonLive) {
      // 非 live 系列：不覆寫「連線中」狀態，由 poll 負責更新
    } else if (status.connected) {
      dot.className     = 'dot dot-green';
      label.textContent = '已連線';
    } else {
      dot.className     = 'dot dot-red';
      label.textContent = '斷線中...';
    }
    if (!_viewingNonLive && status.subscribed_count) {
      document.getElementById('sub-count').textContent = status.subscribed_count;
    }
    if (status.last_updated) {
      _serverLastUpdated = status.last_updated;
      const d = new Date(status.last_updated * 1000);
      const pad = n => String(n).padStart(2, '0');
      const ts = `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
      document.getElementById('last-updated').textContent = ts;
    }
  }
}

// ── 上次收到資料的時間戳 ────────────────────────────
let lastDataTime = 0;
let dataSource = '--';
let _currentSessionMode = null;
let _currentActiveSeries = null;  // 目前畫面顯示的 series（偵測切換用）
let _targetSeries        = null;  // 用戶點選後預期要顯示的 series（過渡期間過濾非目標資料）

// ── 饋送中斷 Toast ────────────────────────────────
let _serverLastUpdated = 0;  // server 端 last_updated（Unix 秒）
const _FEED_DEAD_THRESHOLD = 90;   // 超過 90s 無更新視為中斷
const _RESTART_COOLDOWN    = 180;  // 重啟後至少等 180s 才能再重啟

const _feedToast   = document.getElementById('feed-dead-toast');
const _feedDeadMsg = document.getElementById('feed-dead-msg');
let _lastRestartAt = 0;  // Unix 秒，上次觸發重啟的時間

setInterval(() => {
  if (_serverLastUpdated === 0) return;  // 尚未收到第一筆
  const now = Date.now() / 1000;
  const ago = Math.round(now - _serverLastUpdated);

  if (ago >= _FEED_DEAD_THRESHOLD) {
    if (!_feedToast._manualDismiss) {
      const m = Math.floor(ago / 60), s = ago % 60;
      const agoStr = `${m > 0 ? m + 'm ' : ''}${s}s`;

      // 自動重啟（cooldown 內不重複觸發）
      if (now - _lastRestartAt > _RESTART_COOLDOWN) {
        _lastRestartAt = now;
        _feedDeadMsg.textContent = `xqfap 停止 ${agoStr}　重啟中...`;
        fetch('/api/restart-feed', { method: 'POST' }).catch(() => {});
      } else {
        const wait = Math.round(_RESTART_COOLDOWN - (now - _lastRestartAt));
        _feedDeadMsg.textContent = `xqfap 停止 ${agoStr}　重啟後等待中 (${wait}s)`;
      }
      _feedToast.classList.add('visible');
    }
  } else {
    _feedToast._manualDismiss = false;  // 資料恢復 → 解除手動關閉鎖
    _feedToast.classList.remove('visible');
  }
}, 5000);

// ── 通用資料處理（WS + polling 共用） ────────────────
function handleData(data, source) {
  lastDataTime = Date.now();
  dataSource = source || 'WS';
  if (_viewMode === 'snapshot') return;  // 快照模式：忽略 live 資料
  if (!_ready) return;       // fetchContracts 尚未完成：不渲染任何資料
  const modeChanged = data.session_mode !== _currentSessionMode;
  _currentSessionMode = data.session_mode;
  if (modeChanged) {
    btnFull.classList.toggle('active', data.session_mode === 'full');
    btnDay.classList.toggle('active',  data.session_mode === 'day');
    _updateSeriesCode();
  }
  // 正在查看未 ready 的系列
  if (_viewingNonLive) {
    // 必須同時滿足：series 名稱吻合 AND _contractsData 已標記為 live
    // （防止 bulk_req 完成前 WS 廣播同名 series 造成兩段式顯示）
    const matchAndLive = data.series && data.series === _viewingNonLiveSeries
      && _contractsData.some(c => c.series === data.series && c.live);
    if (matchAndLive) {
      _viewingNonLive       = false;
      _viewingNonLiveSeries = null;
    } else {
      updateStatus(data.status, null);
      return;
    }
  }
  // 切換過渡期間：忽略非目標 series 的舊資料，防止 _periodic_broadcast 送來的舊 series 覆蓋
  if (_targetSeries && data.series && data.series !== _targetSeries) {
    return;
  }
  // series 切換：清空舊資料再 render，確保原子置換（不顯示舊 series 殘留）
  if (data.series && data.series !== _currentActiveSeries) {
    _clearDisplay();
    _resetViewMode();   // 合約切換時重置損益視圖，防止舊合約 baseline 混入新合約 live
    _currentActiveSeries = data.series;
    _targetSeries = null;  // 目標已到達，清除過渡旗標
  }
  // 永遠記錄最新 live pnl（weekly 模式即時疊加用）
  _lastLivePnl = data.pnl;

  updateTable(data.table);
  if (_viewMode === 'weekly') {
    updateChart(_mergeWithLive(data.pnl), modeChanged);
  } else {
    updateChart(data.pnl, modeChanged);
  }
  if (data.atm_strike) updateATMLine(data.atm_strike);
  if (data.implied_forward != null) { _impliedForward = data.implied_forward; _updatePnlStats(); }
  updateStatus(data.status, data.settlement);
}

// ── 每秒更新 browser-side "上次收到" 顯示 ────────────
setInterval(() => {
  const age = lastDataTime > 0 ? Math.round((Date.now() - lastDataTime) / 1000) : null;
  const el = document.getElementById('rx-age');
  if (el) {
    if (age === null) {
      el.textContent = '等待中...';
      el.style.color = '#8b949e';
    } else if (age < 5) {
      el.textContent = `${dataSource} ${age}s前`;
      el.style.color = '#3fb950';
    } else {
      el.textContent = `${dataSource} ${age}s前`;
      el.style.color = '#f85149';
    }
  }
}, 1000);

// ── WebSocket 連線 ─────────────────────────────────────
let _ws = null;

function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  _ws = ws;

  ws.onopen = () => {
    console.log('WebSocket 已連線');
  };

  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); }
    catch { return; }
    handleData(data, 'WS');
  };

  ws.onclose = () => {
    console.log('WebSocket 斷線，1 秒後重連...');
    document.getElementById('conn-dot').className   = 'dot dot-red';
    document.getElementById('conn-label').textContent = '斷線中...';
    _ws = null;
    setTimeout(connect, 1000);
  };

  ws.onerror = (e) => {
    console.error('WebSocket 錯誤', e);
  };
}

// ── HTTP polling fallback（WS 超過 2 秒沒資料就主動拉） ──
async function pollFallback() {
  const now = Date.now();
  const wsAlive = _ws && _ws.readyState === WebSocket.OPEN;
  if (!wsAlive || (now - lastDataTime) > 2000) {
    try {
      const resp = await fetch('/api/data');
      if (resp.ok) {
        const data = await resp.json();
        handleData(data, 'HTTP');
      }
    } catch(e) {
      console.warn('polling fallback failed', e);
    }
  }
}

setInterval(pollFallback, 2000);

connect();

// ── 損益視圖模式切換 ──────────────────────────────────

function _resetViewMode() {
  _viewMode           = 'live';
  _weeklyPnlBaseline  = null;
  _weeklyPnlCacheTime = 0;
  const sel = document.getElementById('view-mode-select');
  if (sel) sel.value = 'live';
  btnFull.classList.toggle('active', _currentSessionMode === 'full');
  btnDay.classList.toggle('active',  _currentSessionMode === 'day');
}

function _exitSnapshot() {
  // 相容舊呼叫點（合約切換 onchange）
  _resetViewMode();
}

document.getElementById('view-mode-select').addEventListener('change', async function () {
  const val = this.value;

  if (val === 'live') {
    const wasSnapshot = (_viewMode === 'snapshot');
    _viewMode = 'live';
    _weeklyPnlBaseline = null;
    // 從快照切回即時：永遠預設全日盤；否則維持目前 session
    if (wasSnapshot) _setSessionMode('full');
    else {
      btnFull.classList.toggle('active', _currentSessionMode === 'full');
      btnDay.classList.toggle('active',  _currentSessionMode === 'day');
    }
    // live 模式：下次 handleData 自然更新圖表

  } else if (val === 'weekly') {
    _viewMode = 'weekly';
    btnFull.classList.remove('active');
    btnDay.classList.remove('active');
    // 取得當前 active series
    const sel = document.getElementById('contract-select');
    const c   = _contractsData[parseInt(sel.value)];
    if (!c) return;
    await _loadWeeklyBaseline(c.series, c.settlement_date);
    // 立刻用最新 live pnl 重繪
    if (_lastLivePnl) updateChart(_mergeWithLive(_lastLivePnl), true);

  } else if (val.startsWith('snapshot:')) {
    const filename = val.slice('snapshot:'.length);
    _viewMode = 'snapshot';
    btnFull.classList.remove('active');
    btnDay.classList.remove('active');
    try {
      const resp = await fetch(`/api/snapshots/${encodeURIComponent(filename)}`);
      if (!resp.ok) return;
      const snap = await resp.json();
      document.getElementById('last-updated').textContent = `${snap.date} ${snap.time.slice(0,2)}:${snap.time.slice(2)}:00`;
      if (snap.table) { _tableScrolled = false; updateTable(snap.table); }
      if (snap.atm_strike) updateATMLine(snap.atm_strike);
      if (snap.implied_forward != null) { _impliedForward = snap.implied_forward; _updatePnlStats(); }
      updateChart({ strikes: snap.strikes, pnl: snap.pnl }, true);
    } catch (e) {
      console.warn('snapshot fetch failed', e);
    }
  }
});

// ── 左側面板拖拉調整寬度 ─────────────────────────────
const _leftPanel    = document.getElementById('left-panel');
const _resizeHandle = document.getElementById('resize-handle');
let _isResizing = false, _resizeStartX = 0, _resizeStartW = 0;

_resizeHandle.addEventListener('mousedown', e => {
  _isResizing   = true;
  _resizeStartX = e.clientX;
  _resizeStartW = _leftPanel.offsetWidth;
  _resizeHandle.classList.add('dragging');
  document.body.style.cursor     = 'col-resize';
  document.body.style.userSelect = 'none';
  e.preventDefault();
});

document.addEventListener('mousemove', e => {
  if (!_isResizing) return;
  const newW = Math.max(200, _resizeStartW + e.clientX - _resizeStartX);
  _leftPanel.style.width = newW + 'px';
});

document.addEventListener('mouseup', () => {
  if (!_isResizing) return;
  _isResizing = false;
  _resizeHandle.classList.remove('dragging');
  document.body.style.cursor     = '';
  document.body.style.userSelect = '';
  chart.resize();
});
