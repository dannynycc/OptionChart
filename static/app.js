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

function _clearHover() {
  chart.setOption({ graphic: [] }, { replaceMerge: ['graphic'] });
}

chart.getZr().on('mousemove', function(e) {
  if (_hoverRaf) return;
  _hoverRaf = true;
  requestAnimationFrame(() => { _hoverRaf = false; });

  const px = [e.offsetX, e.offsetY];
  if (!chart.containPixel('grid', px) || _chartStrikes.length === 0) {
    _clearHover(); return;
  }

  const [hx] = chart.convertFromPixel({ seriesIndex: 2 }, px);
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

chart.getZr().on('mouseout', _clearHover);

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

btnFull.addEventListener('click', () => _setSessionMode('full'));
btnDay.addEventListener('click',  () => _setSessionMode('day'));

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
  // 模式切換時 slider 可能不觸發 update（範圍未變），直接強制重算 Y 軸
  if (forceReset) _recalcYAxis(minS, maxS);
}

// ── DOM helper ────────────────────────────────────────
function _cell(cls, text, flash) {
  const d = document.createElement('div');
  d.className = cls + (flash ? ' flash' : '');
  d.textContent = text;
  return d;
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
  body.textContent = '';   // 清空（比 innerHTML='' 更安全）

  for (const r of rows) {
    const nc  = gC(r, 'net_call'),   np  = gC(r, 'net_put');
    const vc  = gC(r, 'vol_call'),   vp  = gC(r, 'vol_put');
    const rc_ = gC(r, 'ratio_call'), rp_ = gC(r, 'ratio_put');
    const ac  = gC(r, 'ask_match_call'), bc = gC(r, 'bid_match_call');
    const ap  = gC(r, 'ask_match_put'),  bp = gC(r, 'bid_match_put');

    // 淨Put 顯示值 = 外盤(Buy Put) − 內盤(Sell Put) = bp − ap = np
    const displayedNp = np;

    // pct 最大 50%，配合 bar 從中心（right/left:50%）延伸
    const callPct = (Math.abs(nc)         / maxAbsNet * 50).toFixed(1);
    const putPct  = (Math.abs(displayedNp) / maxAbsNet * 50).toFixed(1);

    const key = r.strike + sfx;
    const prev = prevValues[key] || {};
    const ch = (v, k) => prev[k] !== v;
    prevValues[key] = { nc, vc, rc_, avg_c: r.avg_price_call, ac, bc,
                        np, vp, rp_, avg_p: r.avg_price_put,  ap, bp };

    // 淨Call 正→紅/負→綠；淨Put 正→綠/負→紅
    const ncCls = nc         > 0 ? ' val-pos' : nc         < 0 ? ' val-neg' : '';
    const npCls = displayedNp > 0 ? ' val-pos' : displayedNp < 0 ? ' val-neg' : '';

    const row = document.createElement('div');
    row.className = 'row' + (r.highlight ? ' highlight' : '');

    // CALL side（左→右）：外盤成交量(Buy Call) | 內盤成交量(Sell Call) | 總成交量 | 內外盤% | 成交均價 | 淨Call | bar
    // col-call-sell → 外盤(Buy Call) = bid_match(bc)；col-call-buy → 內盤(Sell Call) = ask_match(ac)
    row.appendChild(_cell('col-call-sell',  bc > 0 ? String(bc) : '',                     ch(bc,  'bc')   ? ' flash' : ''));
    row.appendChild(_cell('col-call-buy',   ac > 0 ? String(ac) : '',                     ch(ac,  'ac')   ? ' flash' : ''));
    row.appendChild(_cell('col-call-vol',   vc > 0 ? String(vc) : '',                     ch(vc,  'vc')   ? ' flash' : ''));
    row.appendChild(_cell('col-call-ratio', vc > 0 ? rc_.toFixed(2) : '',                 ch(rc_, 'rc_')  ? ' flash' : ''));
    row.appendChild(_cell('col-call-avg',   r.avg_price_call > 0 ? r.avg_price_call.toFixed(1) : '', ch(r.avg_price_call, 'avg_c') ? ' flash' : ''));
    row.appendChild(_cell('col-call-val' + ncCls, nc !== 0 ? nc.toFixed(0) : '',          ch(nc,  'nc')   ? ' flash' : ''));
    row.appendChild(_barCell('col-call-bar', 'bar-call ' + (nc >= 0 ? 'positive' : 'negative'), callPct));
    row.appendChild(_cell('col-strike',     String(r.strike),                              ''));
    // PUT side（左→右）：bar | 淨Put | 外盤成交量(Buy Put) | 內盤成交量(Sell Put) | 總成交量 | 內外盤% | 成交均價
    // col-put-sell → 外盤(Buy Put) = bid_match(bp)；col-put-buy → 內盤(Sell Put) = ask_match(ap)
    row.appendChild(_barCell('col-put-bar', 'bar-put ' + (displayedNp >= 0 ? 'positive' : 'negative'), putPct));
    row.appendChild(_cell('col-put-val' + npCls, displayedNp !== 0 ? displayedNp.toFixed(0) : '', ch(np, 'np') ? ' flash' : ''));
    row.appendChild(_cell('col-put-sell',   bp > 0 ? String(bp) : '',                     ch(bp,  'bp')   ? ' flash' : ''));
    row.appendChild(_cell('col-put-buy',    ap > 0 ? String(ap) : '',                     ch(ap,  'ap')   ? ' flash' : ''));
    row.appendChild(_cell('col-put-vol',    vp > 0 ? String(vp) : '',                     ch(vp,  'vp')   ? ' flash' : ''));
    row.appendChild(_cell('col-put-ratio',  vp > 0 ? rp_.toFixed(2) : '',                 ch(rp_, 'rp_')  ? ' flash' : ''));
    row.appendChild(_cell('col-put-avg',    r.avg_price_put > 0 ? r.avg_price_put.toFixed(1) : '',  ch(r.avg_price_put, 'avg_p') ? ' flash' : ''));
    row.appendChild(_cell('col-pnl-call',     r.pnl_call     != null ? r.pnl_call.toFixed(4)     : '', ''));
    row.appendChild(_cell('col-pnl-put',      r.pnl_put      != null ? r.pnl_put.toFixed(4)      : '', ''));
    row.appendChild(_cell('col-pnl-combined', r.pnl_combined != null ? r.pnl_combined.toFixed(4) : '', ''));

    body.appendChild(row);
  }

  // 自動捲動到高亮列
  const highlighted = body.querySelector('.highlight');
  if (highlighted) {
    highlighted.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}

// ── 合約下拉選單 ───────────────────────────────────────
let _contractsData = [];

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

    const today = new Date().toISOString().slice(0, 10); // "2026-03-25"
    let defaultIdx = list.findIndex(c => c.settlement_date >= today);
    if (defaultIdx < 0) defaultIdx = 0;

    list.forEach((c, i) => {
      const opt = document.createElement('option');
      opt.value = i;
      opt.textContent = c.label;
      sel.appendChild(opt);
    });

    sel.value = defaultIdx;
    document.getElementById('settlement-date').textContent =
      list[defaultIdx].settlement_display;
    _updateSeriesCode();

    sel.onchange = () => {
      const c = _contractsData[parseInt(sel.value)];
      if (c) document.getElementById('settlement-date').textContent = c.settlement_display;
      _updateSeriesCode();
    };
    return true;
  } catch(e) {
    console.warn('fetchContracts failed', e);
    return false;
  }
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
    if (status.connected) {
      dot.className   = 'dot dot-green';
      label.textContent = '已連線';
    } else {
      dot.className   = 'dot dot-red';
      label.textContent = '斷線中...';
    }
    if (status.subscribed_count) {
      document.getElementById('sub-count').textContent = status.subscribed_count;
    }
    if (status.last_updated) {
      const d = new Date(status.last_updated * 1000);
      document.getElementById('last-updated').textContent =
        d.toLocaleTimeString('zh-TW', { hour12: false });
    }
  }
}

// ── 上次收到資料的時間戳 ────────────────────────────
let lastDataTime = 0;
let dataSource = '--';
let _currentSessionMode = null;

// ── 通用資料處理（WS + polling 共用） ────────────────
function handleData(data, source) {
  lastDataTime = Date.now();
  dataSource = source || 'WS';
  const modeChanged = data.session_mode !== _currentSessionMode;
  _currentSessionMode = data.session_mode;
  if (modeChanged) _updateSeriesCode();
  updateTable(data.table);
  updateChart(data.pnl, modeChanged);
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
