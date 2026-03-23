/**
 * app.js
 * WebSocket 接收 + 左側表格 + 右側 ECharts 損益曲線
 */

// ── ECharts 初始化 ─────────────────────────────────────
const chartDom = document.getElementById('pnl-chart');
const chart    = echarts.init(chartDom, 'dark');

const chartOption = {
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    formatter: (params) => {
      const p = params[0];
      return `履約價 ${p.axisValue}<br/>合併損益：${p.data.toFixed(4)} 億元`;
    }
  },
  grid: { top: 40, right: 20, bottom: 60, left: 70 },
  xAxis: {
    type: 'category',
    data: [],
    name: '履約價',
    nameLocation: 'center',
    nameGap: 40,
    axisLabel: { color: '#8b949e', fontSize: 11, rotate: 45 },
    axisLine: { lineStyle: { color: '#30363d' } },
  },
  yAxis: {
    type: 'value',
    name: '損益（億元）',
    nameLocation: 'middle',
    nameGap: 55,
    axisLabel: { color: '#8b949e', fontSize: 11,
                 formatter: v => v.toFixed(2) },
    axisLine: { lineStyle: { color: '#30363d' } },
    splitLine: { lineStyle: { color: '#21262d' } },
  },
  series: [
    {
      name: '合併損益',
      type: 'line',
      data: [],
      smooth: true,
      symbol: 'none',
      lineStyle: { color: '#388bfd', width: 2 },
      areaStyle: { color: 'rgba(248,81,73,0.15)' },
    }
  ],
  // 標注線（Max Pain / 目前指數）動態加入
  graphic: [],
};
chart.setOption(chartOption);
window.addEventListener('resize', () => chart.resize());

// ── 表格最大絕對值（用來計算 bar 寬度比例） ────────────
let maxAbsNet = 1;

// ── 前一次各欄數值（用於偵測變化 → 閃爍） ────────────
const prevValues = {};  // key: `${strike}_C` / `${strike}_P` / etc.

// ── 更新右側損益圖 ─────────────────────────────────────
function updateChart(pnl, currentIndex) {
  if (!pnl || !pnl.strikes || pnl.strikes.length === 0) return;

  const markLines = [];

  if (pnl.max_pain != null) {
    markLines.push({
      xAxis: String(pnl.max_pain),
      label: { formatter: `Max Pain\n${pnl.max_pain}`, color: '#f0883e' },
      lineStyle: { color: '#f0883e', type: 'dashed', width: 1.5 },
    });
  }
  if (currentIndex != null) {
    markLines.push({
      xAxis: String(currentIndex),
      label: { formatter: `現價\n${currentIndex}`, color: '#79c0ff' },
      lineStyle: { color: '#79c0ff', type: 'solid', width: 1.5 },
    });
  }

  chart.setOption({
    xAxis: { data: pnl.strikes.map(String) },
    series: [{
      data: pnl.pnl,
      markLine: markLines.length > 0
        ? { silent: true, symbol: 'none', data: markLines }
        : undefined,
    }],
  });
}

// ── 更新左側 T 字報價表 ────────────────────────────────
function updateTable(rows) {
  if (!rows || rows.length === 0) return;

  // 找最大絕對值，供 bar 比例計算
  let newMax = 1;
  for (const r of rows) {
    newMax = Math.max(newMax, Math.abs(r.net_call), Math.abs(r.net_put));
  }
  maxAbsNet = newMax;

  const body = document.getElementById('strike-table-body');
  body.innerHTML = '';   // 清空後重建（簡單可靠）

  for (const r of rows) {
    const row = document.createElement('div');
    row.className = 'row' + (r.highlight ? ' highlight' : '');

    const callPct = (Math.abs(r.net_call) / maxAbsNet * 100).toFixed(1);
    const putPct  = (Math.abs(r.net_put)  / maxAbsNet * 100).toFixed(1);
    const callCls = r.net_call >= 0 ? 'positive' : 'negative';
    const putCls  = r.net_put  >= 0 ? 'positive' : 'negative';

    const callVolStr   = r.vol_call > 0 ? r.vol_call              : '';
    const putVolStr    = r.vol_put  > 0 ? r.vol_put               : '';
    const callRatioStr = r.vol_call > 0 ? r.ratio_call.toFixed(1) : '';
    const putRatioStr  = r.vol_put  > 0 ? r.ratio_put.toFixed(1)  : '';
    const callNetStr   = r.net_call !== 0 ? r.net_call.toFixed(0) : '';
    const putNetStr    = r.net_put  !== 0 ? r.net_put.toFixed(0)  : '';

    // 偵測各欄是否有變化
    const key = r.strike;
    const prev = prevValues[key] || {};
    const changed = (f) => prev[f] !== r[f];
    prevValues[key] = { net_call: r.net_call, vol_call: r.vol_call, ratio_call: r.ratio_call, avg_price_call: r.avg_price_call, ask_match_call: r.ask_match_call, bid_match_call: r.bid_match_call,
                        net_put:  r.net_put,  vol_put:  r.vol_put,  ratio_put:  r.ratio_put,  avg_price_put:  r.avg_price_put,  ask_match_put:  r.ask_match_put,  bid_match_put:  r.bid_match_put };

    row.innerHTML = `
      <div class="col-call-bar bar-wrapper">
        <div class="bar-call ${callCls}" style="width:${callPct}%"></div>
      </div>
      <div class="col-call-val${changed('net_call')   ? ' flash' : ''}">${callNetStr}</div>
      <div class="col-call-avg${changed('avg_price_call') ? ' flash' : ''}">${r.avg_price_call > 0 ? r.avg_price_call.toFixed(1) : ''}</div>
      <div class="col-call-buy${changed('ask_match_call') ? ' flash' : ''}">${r.ask_match_call > 0 ? r.ask_match_call : ''}</div>
      <div class="col-call-sell${changed('bid_match_call') ? ' flash' : ''}">${r.bid_match_call > 0 ? r.bid_match_call : ''}</div>
      <div class="col-call-vol${changed('vol_call')   ? ' flash' : ''}">${callVolStr}</div>
      <div class="col-call-ratio${changed('ratio_call') ? ' flash' : ''}">${callRatioStr}</div>
      <div class="col-strike">${r.strike}</div>
      <div class="col-put-ratio${changed('ratio_put') ? ' flash' : ''}">${putRatioStr}</div>
      <div class="col-put-vol${changed('vol_put')     ? ' flash' : ''}">${putVolStr}</div>
      <div class="col-put-sell${changed('bid_match_put') ? ' flash' : ''}">${r.bid_match_put > 0 ? r.bid_match_put : ''}</div>
      <div class="col-put-buy${changed('ask_match_put') ? ' flash' : ''}">${r.ask_match_put > 0 ? r.ask_match_put : ''}</div>
      <div class="col-put-avg${changed('avg_price_put') ? ' flash' : ''}">${r.avg_price_put > 0 ? r.avg_price_put.toFixed(1) : ''}</div>
      <div class="col-put-val${changed('net_put')     ? ' flash' : ''}">${putNetStr}</div>
      <div class="col-put-bar bar-wrapper">
        <div class="bar-put ${putCls}" style="width:${putPct}%"></div>
      </div>
    `;
    body.appendChild(row);
  }

  // 自動捲動到高亮列
  const highlighted = body.querySelector('.highlight');
  if (highlighted) {
    highlighted.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}

// ── 更新頂部工具列與狀態角落 ──────────────────────────
function updateStatus(status, settlement) {
  if (settlement) {
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

// ── 通用資料處理（WS + polling 共用） ────────────────
function handleData(data, source) {
  lastDataTime = Date.now();
  dataSource = source || 'WS';
  updateTable(data.table);
  updateChart(data.pnl);
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
