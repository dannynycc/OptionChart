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

    row.innerHTML = `
      <div class="col-call-bar bar-wrapper">
        <div class="bar-call ${callCls}" style="width:${callPct}%"></div>
      </div>
      <div class="col-call-val">${r.net_call !== 0 ? r.net_call.toFixed(0) : ''}</div>
      <div class="col-strike">${r.strike}</div>
      <div class="col-put-val">${r.net_put !== 0 ? r.net_put.toFixed(0) : ''}</div>
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

// ── WebSocket 連線 ─────────────────────────────────────
function connect() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    console.log('WebSocket 已連線');
  };

  ws.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); }
    catch { return; }

    updateTable(data.table);
    updateChart(data.pnl);
    updateStatus(data.status, data.settlement);
  };

  ws.onclose = () => {
    console.log('WebSocket 斷線，3 秒後重連...');
    document.getElementById('conn-dot').className   = 'dot dot-red';
    document.getElementById('conn-label').textContent = '斷線中...';
    setTimeout(connect, 3000);
  };

  ws.onerror = (e) => {
    console.error('WebSocket 錯誤', e);
  };
}

connect();
