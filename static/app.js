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

// ── 日盤 / 日+夜 切換 ─────────────────────────────────
let showDayOnly = false;

const sessionToggleBtn = document.getElementById('session-toggle');
sessionToggleBtn.addEventListener('click', () => {
  showDayOnly = !showDayOnly;
  sessionToggleBtn.textContent = showDayOnly ? '日盤' : '日+夜';
  sessionToggleBtn.classList.toggle('active', !showDayOnly);
  // 用最近一次的 rows 重繪
  if (window._lastRows) updateTable(window._lastRows);
});

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

    const callPct = (Math.abs(nc) / maxAbsNet * 100).toFixed(1);
    const putPct  = (Math.abs(np) / maxAbsNet * 100).toFixed(1);

    const key = r.strike + sfx;
    const prev = prevValues[key] || {};
    const ch = (v, k) => prev[k] !== v;
    prevValues[key] = { nc, vc, rc_, avg_c: r.avg_price_call, ac, bc,
                        np, vp, rp_, avg_p: r.avg_price_put,  ap, bp };

    const row = document.createElement('div');
    row.className = 'row' + (r.highlight ? ' highlight' : '');

    row.appendChild(_barCell('col-call-bar', 'bar-call ' + (nc >= 0 ? 'positive' : 'negative'), callPct));
    row.appendChild(_cell('col-call-val',   nc !== 0 ? nc.toFixed(0) : '',                ch(nc,  'nc')   ? ' flash' : ''));
    row.appendChild(_cell('col-call-avg',   r.avg_price_call > 0 ? r.avg_price_call.toFixed(1) : '', ch(r.avg_price_call, 'avg_c') ? ' flash' : ''));
    row.appendChild(_cell('col-call-buy',   ac > 0 ? String(ac) : '',                     ch(ac,  'ac')   ? ' flash' : ''));
    row.appendChild(_cell('col-call-sell',  bc > 0 ? String(bc) : '',                     ch(bc,  'bc')   ? ' flash' : ''));
    row.appendChild(_cell('col-call-vol',   vc > 0 ? String(vc) : '',                     ch(vc,  'vc')   ? ' flash' : ''));
    row.appendChild(_cell('col-call-ratio', vc > 0 ? rc_.toFixed(1) : '',                 ch(rc_, 'rc_')  ? ' flash' : ''));
    row.appendChild(_cell('col-strike',     String(r.strike),                              ''));
    row.appendChild(_cell('col-put-ratio',  vp > 0 ? rp_.toFixed(1) : '',                 ch(rp_, 'rp_')  ? ' flash' : ''));
    row.appendChild(_cell('col-put-vol',    vp > 0 ? String(vp) : '',                     ch(vp,  'vp')   ? ' flash' : ''));
    row.appendChild(_cell('col-put-sell',   bp > 0 ? String(bp) : '',                     ch(bp,  'bp')   ? ' flash' : ''));
    row.appendChild(_cell('col-put-buy',    ap > 0 ? String(ap) : '',                     ch(ap,  'ap')   ? ' flash' : ''));
    row.appendChild(_cell('col-put-avg',    r.avg_price_put > 0 ? r.avg_price_put.toFixed(1) : '',  ch(r.avg_price_put, 'avg_p') ? ' flash' : ''));
    row.appendChild(_cell('col-put-val',    np !== 0 ? np.toFixed(0) : '',                ch(np,  'np')   ? ' flash' : ''));
    row.appendChild(_barCell('col-put-bar', 'bar-put '  + (np >= 0 ? 'positive' : 'negative'), putPct));

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
