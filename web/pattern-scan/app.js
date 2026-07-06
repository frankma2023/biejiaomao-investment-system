/**
 * 形态识别看板 - 前端逻辑 v2
 * 单 ECharts 三格（K线+MA / 布林带 / 成交量）+ 分析建议 + 信号时间线
 */

const API = 'http://' + window.location.hostname + ':8788/api/pattern-scan';

// ── 调色板（按 category 动态分配）──
const PALETTE = [
  '#E53935', '#FF9800', '#4CAF50', '#2196F3',
  '#9C27B0', '#FF5722', '#009688', '#673AB7',
  '#F44336', '#FFEB3B', '#00BCD4', '#795548'
];
const SYMBOLS = [
  'triangle', 'diamond', 'pin', 'rect',
  'arrow', 'circle', 'roundRect', 'emptyCircle',
  'diamond', 'pin', 'triangle', 'rect'
];

// ── 全局状态 ──
let state = {
  data: null,
  colorMap: {},
  displayNameMap: {},
  chart: null,
  overlayMode: 'ma',   // 'ma' | 'bb'
  period: 'daily',      // 'daily' | 'weekly' | 'monthly'
  mode: 'stock'          // 'stock' | 'index'
};

// ── 初始化 ──
document.addEventListener('DOMContentLoaded', function () {
  // 默认日期范围：18个月
  var end = new Date();
  var start = new Date(end);
  start.setMonth(start.getMonth() - 18);
  document.getElementById('date-start').value = start.toISOString().slice(0, 10);
  document.getElementById('date-end').value = end.toISOString().slice(0, 10);

  // URL 参数预填股票代码 (如 ?code=600519)
  var urlParams = new URLSearchParams(window.location.search);
  var urlCode = urlParams.get('code');
  if (urlCode) {
    document.getElementById('code').value = urlCode;
  }

  state.chart = echarts.init(document.getElementById('chart'));

  // 主题切换
  var themeBtn = document.querySelector('.theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var html = document.documentElement;
      var next = html.dataset.theme === 'light' ? 'dark' : 'light';
      html.dataset.theme = next;
      themeBtn.textContent = next === 'light' ? '🌙' : '☀️';
      if (state.data) renderChart();
    });
  }

  // 主题切换时重绘
  new MutationObserver(function () {
    if (state.data && state.data.klines && state.data.klines.length) {
      state.chart.dispose();
      state.chart = echarts.init(document.getElementById('chart'));
      renderChart();
    }
  }).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme']
  });

  // 自动扫描
  scan();
});

// ── API 调用 ──
async function scan() {
  var btn = document.querySelector('.btn-scan');
  btn.disabled = true;
  btn.textContent = '⏳ 扫描中...';

  var params = new URLSearchParams({
    code: document.getElementById('code').value.trim(),
    start: document.getElementById('date-start').value,
    end: document.getElementById('date-end').value,
    period: state.period,
    mode: state.mode
  });

  // 查询股票名称
  var code = document.getElementById('code').value.trim();
  try {
    var nr = await fetch('http://' + window.location.hostname + ':8788/api/stock-name?code=' + code + '&mode=' + state.mode);
    if (nr.ok) {
      var nd = await nr.json();
      document.getElementById('stock-name').textContent = nd.name || '';
    }
  } catch (e) {}

  try {
    var resp = await fetch(API + '?' + params);
    var data = await resp.json();
    if (data.error) {
      alert('扫描失败：' + data.error);
      btn.disabled = false;
      btn.textContent = '🔍 扫描';
      return;
    }
    state.data = data;
    
    buildMaps(data.engines);
    renderAll();
  } catch (e) {
    alert('无法连接 API：' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 扫描';
  }
}

// ── 动态颜色 & 名称映射 ──
function buildMaps(engines) {
  state.colorMap = {};
  state.displayNameMap = {};
  var pi = 0;
  engines.forEach(function (eng) {
    state.displayNameMap[eng.name] = eng.display_name || eng.name;
    if (eng.name === 'pocket_pivot') {
      state.colorMap[eng.name] = { color: '#FD03E7', symbol: 'pin', size: 15 };
    } else if (eng.name === 'pocket_pivot_v2') {
      state.colorMap[eng.name] = { color: '#FF9800', symbol: 'pin', size: 15 };
    } else if (eng.name === 'mw_signal') {
      state.colorMap[eng.name] = { color: '#a78bfa', symbol: 'diamond', size: 16 };
    } else if (eng.category === 'candlestick') {
      state.colorMap[eng.name + '_bullish'] = { color: '#9C27B0', symbol: 'emptyCircle', size: 10 };
      state.colorMap[eng.name + '_bearish'] = { color: '#333333', symbol: 'emptyCircle', size: 10 };
    } else if (eng.category === 'indicator') {
      state.colorMap[eng.name] = { color: '#8D6E63', symbol: 'roundRect', size: 10 };
    } else {
      state.colorMap[eng.name] = {
        color: PALETTE[pi % PALETTE.length],
        symbol: SYMBOLS[pi % SYMBOLS.length],
        size: 14
      };
      pi++;
    }
  });
}

function getSignalStyle(signal) {
  var src = signal.source;
  var type = signal.type || 'bullish';
  if (src === 'cdl') {
    var key = src + '_' + type;
    return state.colorMap[key] || { color: type === 'bullish' ? '#9C27B0' : '#333333', symbol: 'emptyCircle', size: 10 };
  }
  return state.colorMap[src] || { color: '#999', symbol: 'circle', size: 10 };
}

// ── 指标切换：均线 / 布林 ──
function toggleOverlay(mode) {
  if (!state.chart || !state.data) return;
  state.overlayMode = mode;

  // 按钮激活态
  document.getElementById('btn-ma').className = 'ctrl-btn' + (mode === 'ma' ? ' active' : '');
  document.getElementById('btn-bb').className = 'ctrl-btn' + (mode === 'bb' ? ' active' : '');

  // 通过 legend.selected 控制 series 显隐
  state.chart.setOption({
    legend: {
      selected: {
        'MA5': mode === 'ma',
        'MA10': mode === 'ma',
        'MA20': mode === 'ma',
        'MA30': mode === 'ma',
        'MA60': mode === 'ma',
        'MA120': mode === 'ma',
        'MA250': mode === 'ma',
        'BB上轨': mode === 'bb',
        'BB中轨': mode === 'bb',
        'BB下轨': mode === 'bb'
      }
    }
  });
}

// ── 类型切换：个股 / 指数 ──
function switchMode(mode) {
  if (state.mode === mode) return;
  state.mode = mode;
  document.getElementById('btn-stock').className = 'mode-btn' + (mode === 'stock' ? ' active' : '');
  document.getElementById('btn-index').className = 'mode-btn' + (mode === 'index' ? ' active' : '');
  scan();
}

// ── 周期切换：日K / 周K / 月K ──
function switchPeriod(period) {
  if (state.period === period) return;
  state.period = period;

  // 按钮激活态
  document.getElementById('btn-daily').className = 'ctrl-btn' + (period === 'daily' ? ' active' : '');
  document.getElementById('btn-weekly').className = 'ctrl-btn' + (period === 'weekly' ? ' active' : '');
  document.getElementById('btn-monthly').className = 'ctrl-btn' + (period === 'monthly' ? ' active' : '');

  scan();
}

// ── 总渲染 ──
function renderAll() {
  if (!state.data || !state.data.klines || state.data.klines.length === 0) return;
  renderChart();
  renderSignalLegend(state.data.engines);
  renderRecommendation(state.data.recommendation);
  renderTimeline(state.data.signals);
  renderLegend(state.data.engines);
}

// ── 主图表：单 ECharts 三格 ──
function renderChart() {
  if (!state.chart || !state.data || !state.data.klines.length) return;

  var d = state.data;
  var ck = d.klines;
  var dates = ck.map(function (k) { return k.date; });
  var ind = d.indicators || {};

  // ── 计算移动均线（前端自算，确保 MA10 和 MA120 都有）──
  function calcMA(p) {
    var r = [], sum = 0;
    for (var i = 0; i < ck.length; i++) {
      if (ck[i].close != null) {
        sum += ck[i].close;
        if (i >= p) { sum -= ck[i - p].close; r.push(sum / p); }
        else { r.push(null); }
      } else { r.push(null); }
    }
    return r;
  }
  var ma5 = calcMA(5);
  var ma10 = calcMA(10);
  var ma20 = calcMA(20);
  var ma30 = calcMA(30);
  var ma60 = calcMA(60);
  var ma120 = calcMA(120);
  var ma250 = calcMA(250);

  // MA 颜色
  var mc = ['#FF9800', '#2196F3', '#4CAF50', '#E91E63', '#9C27B0', '#00BCD4', '#795548'];

  // ── 信号标注 ──
  var dateIndex = {};
  dates.forEach(function (dt, i) { dateIndex[dt] = i; });

  // 按日期聚合信号（供 tooltip 查询）
  var signalsByDate = {};
  (d.signals || []).forEach(function (sig) {
    if (!signalsByDate[sig.date]) signalsByDate[sig.date] = [];
    signalsByDate[sig.date].push(sig);
  });

  // 按日期分组信号 → 堆叠在蜡烛下方
  var signalsByIndex = {};
  (d.signals || []).forEach(function (sig) {
    var idx = dateIndex[sig.date];
    if (idx === undefined) return;
    if (!signalsByIndex[idx]) signalsByIndex[idx] = [];
    signalsByIndex[idx].push(sig);
  });

  var signalPoints = [];
  Object.keys(signalsByIndex).forEach(function (idxStr) {
    var idx = parseInt(idxStr);
    var sigs = signalsByIndex[idx];
    var k = ck[idx];
    var range = k.high - k.low;
    var gap = Math.max(range * 0.50, 0.06);  // 拉开距离，避免遮盖 K 线

    sigs.forEach(function (sig, i) {
      var name = '';
      if (sig.details) {
        name = sig.details.cdl_name || sig.details.description || sig.details.signal_type || '';
      }
      if (!name) {
        name = state.displayNameMap[sig.source] || sig.source;
      }
      var style = getSignalStyle(sig);
      var y = k.low - gap * (i + 1);
      signalPoints.push({
        name: name,
        value: [idx, y],
        symbol: style.symbol,
        symbolSize: style.size,
        symbolRotate: style.symbol === 'triangle' ? 180 : 0,
        itemStyle: { color: style.color, borderColor: '#FFF', borderWidth: 0.8 }
      });
    });
  });

  // ── 布林带数据 ──
  var bbU = ind.bb_upper || [];
  var bbM = ind.bb_middle || [];
  var bbL = ind.bb_lower || [];
  var hasBB = bbM.some(function (v) { return v !== null; });

  // ── 成交量 ──
  var vols = ck.map(function (k) { return k.volume; });
  var volMA = ind.vol_ma50 || [];

  // ── 主题背景（与卡片底色一致）──
  var bg = document.documentElement.dataset.theme === 'dark' ? 'rgba(26,26,31,.6)' : 'rgba(255,255,255,.75)';
  var axisColor = document.documentElement.dataset.theme === 'dark' ? 'rgba(200,200,200,0.3)' : 'rgba(128,128,128,0.3)';
  var gridColor = document.documentElement.dataset.theme === 'dark' ? 'rgba(200,200,200,0.08)' : 'rgba(128,128,128,0.1)';

  // ── 成交量格式化 ──
  function fmtVol(v) {
    if (v == null) return '—';
    if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
    if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
  }
  function fmtAmount(v) {
    if (v == null) return '—';
    if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
    if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
    return v.toFixed(0);
  }

  // ── series 数组 ──
  var series = [];

  // Grid 0 — K线蜡烛
  series.push({
    name: 'K线',
    type: 'candlestick',
    xAxisIndex: 0,
    yAxisIndex: 0,
    data: ck.map(function (k, i) {
      // 判断真实涨跌（相对前日收盘），优先用数据库 change_pct
      var isUp;
      if (k.change_pct != null) {
        var raw = k.change_pct;
        isUp = (Math.abs(raw) > 1 ? raw : raw * 100) >= 0;
      } else if (i > 0 && ck[i-1].close != null) {
        isUp = k.close >= ck[i-1].close;
      } else {
        isUp = k.close >= k.open;
      }
      return {
        value: [k.open, k.close, k.low, k.high],
        itemStyle: {
          color: isUp ? '#E53935' : '#26C6DA',
          color0: isUp ? '#E53935' : '#26C6DA',
          borderColor: isUp ? '#E53935' : '#26C6DA',
          borderColor0: isUp ? '#E53935' : '#26C6DA'
        }
      };
    })
  });

  // Grid 0 — 均线
  var maConfig = [
    { data: ma5, name: 'MA5', color: mc[0] },
    { data: ma10, name: 'MA10', color: mc[1] },
    { data: ma20, name: 'MA20', color: mc[2] },
    { data: ma30, name: 'MA30', color: mc[3] },
    { data: ma60, name: 'MA60', color: mc[4] },
    { data: ma120, name: 'MA120', color: mc[5] },
    { data: ma250, name: 'MA250', color: mc[6] }
  ];
  maConfig.forEach(function (m) {
    series.push({
      name: m.name,
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: m.data,
      smooth: true,
      symbol: 'none',
      lineStyle: { width: 1, color: m.color }
    });
  });

  // Grid 0 — 信号标注
  if (signalPoints.length > 0) {
    series.push({
      name: '信号标注',
      type: 'scatter',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: signalPoints,
      z: 10,
      emphasis: {
        scale: 1.6,
        itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.3)' }
      }
    });
  }

  // Grid 0 — 布林带（与 K 线同格）
  if (hasBB) {
    series.push({
      name: 'BB上轨',
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: bbU,
      symbol: 'none',
      lineStyle: { color: '#FF9800', width: 1.2, type: 'dashed' }
    });
    series.push({
      name: 'BB中轨',
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: bbM,
      symbol: 'none',
      lineStyle: { color: '#4CAF50', width: 1.2 }
    });
    series.push({
      name: 'BB下轨',
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      data: bbL,
      symbol: 'none',
      lineStyle: { color: '#9C27B0', width: 1.2, type: 'dashed' }
    });
  }

  // Grid 1 — 成交量
  series.push({
    name: '成交量',
    type: 'bar',
    xAxisIndex: 1,
    yAxisIndex: 1,
    data: vols.map(function (v, i) {
      // 成交量柱颜色跟随真实涨跌
      var isUp;
      if (ck[i].change_pct != null) {
        var raw = ck[i].change_pct;
        isUp = (Math.abs(raw) > 1 ? raw : raw * 100) >= 0;
      } else if (i > 0 && ck[i-1].close != null) {
        isUp = ck[i].close >= ck[i-1].close;
      } else {
        isUp = ck[i].close >= ck[i].open;
      }
      return {
        value: v,
        itemStyle: { color: isUp ? '#E53935' : '#26C6DA' }
      };
    })
  });
  series.push({
    name: 'VOL均线',
    type: 'line',
    xAxisIndex: 1,
    yAxisIndex: 1,
    data: volMA,
    symbol: 'none',
    lineStyle: { color: '#607D8B', width: 1 }
  });

  // ── ECharts option ──
  var option = {
    backgroundColor: bg,
    legend: {
      show: false,
      selected: {
        'MA5': state.overlayMode === 'ma',
        'MA10': state.overlayMode === 'ma',
        'MA20': state.overlayMode === 'ma',
        'MA30': state.overlayMode === 'ma',
        'MA60': state.overlayMode === 'ma',
        'MA120': state.overlayMode === 'ma',
        'MA250': state.overlayMode === 'ma',
        'BB上轨': state.overlayMode === 'bb',
        'BB中轨': state.overlayMode === 'bb',
        'BB下轨': state.overlayMode === 'bb'
      }
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        crossStyle: { color: '#999' },
        link: [{ xAxisIndex: 'all' }]
      },
      backgroundColor: 'rgba(255,255,255,0.75)',
      borderColor: '#ddd',
      textStyle: { fontSize: 10, color: '#333' },
      formatter: function (params) {
        if (!params || !params.length) return '';
        var k = ck[params[0].dataIndex];
        if (!k) return '';
        var chg = k.close - k.open;
        // 优先用理杏仁涨跌幅(相对前日收盘)，兼容数据库小数和API百分比两种格式
        var chgPct;
        if (k.change_pct != null) {
          var raw = k.change_pct;
          chgPct = (Math.abs(raw) > 1 ? raw : raw * 100).toFixed(2);
        } else {
          chgPct = k.open > 0 ? (chg / k.open * 100).toFixed(2) : '0.00';
        }
        var col = parseFloat(chgPct) >= 0 ? '#E53935' : '#26C6DA';
        var html = '<span style="font-size:12px;font-weight:700">' + k.date + '</span><br/>';
        html += '开盘：<b>' + (k.open != null ? k.open.toFixed(2) : '—') + '</b><br/>';
        html += '最高：<b>' + (k.high != null ? k.high.toFixed(2) : '—') + '</b><br/>';
        html += '最低：<b>' + (k.low != null ? k.low.toFixed(2) : '—') + '</b><br/>';
        html += '收盘：<b style="color:' + col + '">' + (k.close != null ? k.close.toFixed(2) : '—') + '</b><br/>';
        var priceChg = k.change_pct != null ? (k.close * parseFloat(chgPct) / 100) : chg;
        html += '涨跌：<b style="color:' + col + '">' + (parseFloat(chgPct) >= 0 ? '+' : '') + priceChg.toFixed(2) + ' (' + (parseFloat(chgPct) >= 0 ? '+' : '') + chgPct + '%)</b><br/>';
        html += '成交量：<b>' + fmtVol(k.volume) + '</b>';
        if (k.amount != null || k.turnover != null) {
          html += '<br/>成交额：<b>' + fmtAmount(k.amount || k.turnover) + '</b>';
        }
        if (k.turnover_rate != null) {
          html += '<br/>换手率：<b>' + (k.turnover_rate * 100).toFixed(2) + '%</b>';
        }
        // 均线数值逐行显示
        var di = params[0].dataIndex;
        var maLines = [
          { v: ma5[di], name: 'MA5', color: mc[0] },
          { v: ma10[di], name: 'MA10', color: mc[1] },
          { v: ma20[di], name: 'MA20', color: mc[2] },
          { v: ma30[di], name: 'MA30', color: mc[3] },
          { v: ma60[di], name: 'MA60', color: mc[4] },
          { v: ma120[di], name: 'MA120', color: mc[5] },
          { v: ma250[di], name: 'MA250', color: mc[6] }
        ];
        html += '<br/>';
        maLines.forEach(function (m) {
          if (m.v != null) {
            html += '<span style="color:' + m.color + '">' + m.name + '：' + m.v.toFixed(2) + '</span><br/>';
          }
        });
        // 当日信号详情
        var daySignals = signalsByDate[k.date];
        if (daySignals && daySignals.length > 0) {
          html += '<div style="margin-top:4px;padding-top:4px;border-top:1px dashed #ddd;font-weight:700;font-size:10px;">📌 当日信号</div>';
          daySignals.forEach(function (sig) {
            var s = getSignalStyle(sig);
            var typeLabel = sig.type === 'bearish' ? '看跌' : '看涨';
            var displayName = state.displayNameMap[sig.source] || sig.source;
            var desc = '';
            if (sig.details) {
              desc = sig.details.cdl_name || sig.details.description || sig.details.signal_type || '';
            }
            html += '<span style="color:' + s.color + ';font-weight:700">●</span> ';
            html += '<b>' + escHtml(displayName) + '</b> · ' + typeLabel;
            if (desc) html += ' <span style="font-size:9px;color:#888">(' + escHtml(desc) + ')</span>';
            if (sig.details && sig.details.tech_score) {
              var ts = sig.details.tech_score;
              var tsTier = ts>=85?'极高':ts>=75?'很高':ts>=65?'高':ts>=50?'中':'低';
              html += ' <span style="font-size:9px;color:#f59e0b">[TS:' + ts + '/' + tsTier + ']</span>';
            }
            html += '<br/>';
          });
        }
        return html;
      }
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }]
    },
    grid: [
      { left: '8%', right: '1%', top: 15, height: '60%' },
      { left: '8%', right: '1%', top: '78%', height: '16%' }
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        gridIndex: 0,
        axisLabel: { fontSize: 9, color: axisColor },
        axisLine: { lineStyle: { color: axisColor } },
        axisTick: { show: false },
        splitLine: { show: false }
      },
      {
        type: 'category',
        data: dates,
        gridIndex: 1,
        axisLabel: { fontSize: 8, color: axisColor },
        axisLine: { lineStyle: { color: axisColor } },
        axisTick: { show: false },
        splitLine: { show: false }
      }
    ],
    yAxis: [
      {
        type: 'value',
        scale: true,
        gridIndex: 0,
        axisLabel: { fontSize: 9, color: axisColor },
        axisLine: { show: false },
        splitLine: { lineStyle: { color: gridColor } }
      },
      {
        type: 'value',
        scale: true,
        gridIndex: 1,
        axisLabel: {
          fontSize: 8,
          color: axisColor,
          formatter: function (v) {
            if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿';
            if (v >= 1e4) return (v / 1e4).toFixed(0) + '万';
            return v;
          }
        },
        axisLine: { show: false },
        splitLine: { show: false }
      }
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        start: 0,
        end: 100,
        height: 18,
        bottom: 4,
        borderColor: axisColor,
        backgroundColor: bg,
        fillerColor: 'rgba(254,44,85,0.1)',
        handleStyle: { color: '#FE2C55' }
      }
    ],
    series: series
  };

  state.chart.setOption(option, true);

  // ── 点击信号标注 → 高亮 ──
  state.chart.off('click');
  state.chart.on('click', function (params) {
    if (params.seriesName === '信号标注' && params.data && params.data.name) {
      // 已通过 showTip 自动处理
    }
  });
}

// ── 信号标记图例（K线图下方，含符号形状）──
function renderSignalLegend(engines) {
  var container = document.getElementById('signal-legend');
  if (!container || !engines || engines.length === 0) {
    if (container) container.innerHTML = '';
    return;
  }

  // Unicode 符号映射（近似 ECharts symbol）
  var symChars = {
    'triangle': '▲',
    'diamond': '◆',
    'pin': '▼',
    'rect': '■',
    'arrow': '▶',
    'circle': '●',
    'roundRect': '▬',
    'emptyCircle': '○'
  };

  var html = '<span style="font-weight:700;margin-right:4px;">📌 标记图例</span>';
  engines.forEach(function (eng) {
    var cm = state.colorMap[eng.name];
    if (!cm) {
      // cdl 特殊处理
      if (eng.name === 'cdl') {
        var bStyle = state.colorMap['cdl_bullish'] || { color: '#9C27B0', symbol: 'emptyCircle' };
        var sStyle = state.colorMap['cdl_bearish'] || { color: '#333333', symbol: 'emptyCircle' };
        var symB = symChars[bStyle.symbol] || '●';
        var symS = symChars[sStyle.symbol] || '●';
        html += '<span class="sl-item"><span class="sl-sym" style="color:' + bStyle.color + '">' + symB + '</span>' + eng.display_name + '看涨</span>';
        html += '<span class="sl-item"><span class="sl-sym" style="color:' + sStyle.color + '">' + symS + '</span>看跌</span>';
      }
      return;
    }
    var sym = symChars[cm.symbol] || '●';
    html += '<span class="sl-item"><span class="sl-sym" style="color:' + cm.color + '">' + sym + '</span>' + (eng.display_name || eng.name) + '</span>';
  });

  // 附加：BB 和 VOL 均线说明
  html += '<span class="sl-item" style="margin-left:8px;"><span style="color:#4CAF50;font-weight:700">—</span> BB中轨</span>';
  html += '<span class="sl-item"><span style="color:#FF9800;font-weight:700">---</span> BB上轨</span>';
  html += '<span class="sl-item"><span style="color:#9C27B0;font-weight:700">---</span> BB下轨</span>';
  html += '<span class="sl-item"><span style="color:#607D8B;font-weight:700">—</span> VOL均线</span>';

  container.innerHTML = html;
}

// ── 分析建议面板 ──
function renderRecommendation(rec) {
  var panel = document.getElementById('rec-content');
  if (!rec || (!rec.trend && !rec.signals_summary && !rec.assessment && !rec.advice)) {
    panel.innerHTML = '<span style="color:var(--text-tertiary)">暂无分析建议</span>';
    return;
  }

  // 前端兜底翻译：API 返回英文时替换为中文（详见 recommend.py 后端应输出中文）
  rec = translateRec(rec);

  var html = '';
  if (rec.trend) {
    html += '<div class="rec-section"><div class="rec-label">📈 走势判断</div><div class="rec-text">' + escHtml(rec.trend) + '</div></div>';
  }
  if (rec.signals_summary) {
    html += '<div class="rec-section"><div class="rec-label">📋 信号汇总</div><div class="rec-text">' + escHtml(rec.signals_summary).replace(/\n  \* /g, '<br/>  • ') + '</div></div>';
  }
  if (rec.assessment) {
    html += '<div class="rec-section"><div class="rec-label">🔬 综合评估</div><div class="rec-text">' + escHtml(rec.assessment).replace(/\n  \* /g, '<br/>  • ') + '</div></div>';
  }
  if (rec.advice) {
    html += '<div class="rec-section"><div class="rec-label">💡 建议</div><div class="rec-text">' + escHtml(rec.advice).replace(/\n  \* /g, '<br/>  • ') + '</div></div>';
  }
  panel.innerHTML = html || '<span style="color:var(--text-tertiary)">暂无分析建议</span>';
}

// ── 信号时间线 ──
function renderTimeline(signals) {
  var container = document.getElementById('tl-list');
  var empty = document.getElementById('tl-empty');

  if (!signals || signals.length === 0) {
    container.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  // 按日期降序
  var sorted = signals.slice().sort(function (a, b) { return b.date.localeCompare(a.date); });

  var html = '';
  sorted.forEach(function (sig) {
    var style = getSignalStyle(sig);
    var typeLabel = sig.type === 'bearish' ? '看跌' : '看涨';
    var displayName = state.displayNameMap[sig.source] || sig.source;
    var desc = '';
    if (sig.details) {
      desc = sig.details.cdl_name || sig.details.description || sig.details.signal_type || '';
    }
    html +=
      '<div class="tl-item" data-date="' + sig.date + '" onclick="locateSignal(\'' + sig.date + '\')">' +
        '<span class="tl-dot" style="background:' + style.color + '"></span>' +
        '<div class="tl-info">' +
          '<div class="tl-date">' + sig.date + '</div>' +
          '<div class="tl-name">' + escHtml(displayName) + ' · ' + typeLabel +
            (sig.details && sig.details.tech_score ? ' <span style="font-size:9px;color:#f59e0b">TS:' + sig.details.tech_score + '</span>' : '') +
          '</div>' +
          (desc ? '<div class="tl-desc">' + escHtml(desc) + '</div>' : '') +
        '</div>' +
      '</div>';
  });

  container.innerHTML = html;
}

// ── 图例行 ──
function renderLegend(engines) {
  var container = document.getElementById('legend-row');
  var html = '<strong style="font-size:0.65rem;">图例：</strong>';

  var groups = { pattern: [], indicator: [], candlestick: [] };
  engines.forEach(function (e) {
    if (groups[e.category]) groups[e.category].push(e);
    else {
      if (!groups.other) groups.other = [];
      groups.other.push(e);
    }
  });

  var catNames = { pattern: '自研形态', indicator: '指标信号', candlestick: 'K线形态' };
  Object.keys(groups).forEach(function (cat) {
    var engs = groups[cat];
    if (engs.length === 0) return;
    html += '<span style="color:var(--text-tertiary);font-size:0.6rem;margin-right:4px;">' + (catNames[cat] || cat) + '</span>';
    engs.forEach(function (e) {
      if (e.name === 'cdl') {
        html += '<span class="lg-item"><span class="lg-dot lg-circle" style="background:#9C27B0"></span>' + e.display_name + '看涨</span>';
        html += '<span class="lg-item"><span class="lg-dot lg-circle" style="background:#333333"></span>看跌</span>';
      } else {
        var cm = state.colorMap[e.name] || { color: '#999' };
        html += '<span class="lg-item"><span class="lg-dot" style="background:' + cm.color + '"></span>' + e.display_name + '</span>';
      }
    });
  });

  container.innerHTML = html;
}

// ── 信号定位（时间线点击 → K线图滚动）──
function locateSignal(date) {
  if (!state.data || !state.data.klines) return;
  var idx = -1;
  for (var i = 0; i < state.data.klines.length; i++) {
    if (state.data.klines[i].date === date) { idx = i; break; }
  }
  if (idx < 0) return;

  // 显示 tooltip
  state.chart.dispatchAction({
    type: 'showTip',
    seriesIndex: 0,
    dataIndex: idx
  });

  // 滚动 dataZoom 到目标附近
  var total = state.data.klines.length;
  var pct = (idx / total) * 100;
  var w = 15; // 15% 窗口
  var s = Math.max(0, pct - w / 2);
  var e = Math.min(100, pct + w / 2);
  state.chart.dispatchAction({
    type: 'dataZoom',
    start: s,
    end: e
  });
}

// ── 推荐文本英→中兜底翻译（后端 recommend.py 输出中文后可移除此函数）──
function translateRec(rec) {
  if (!rec) return rec;
  var map = {
    'Price above SMA50': '价格位于SMA50上方',
    'Price below SMA50': '价格位于SMA50下方',
    'Price between SMA50 and SMA200': '价格在SMA50与SMA200之间',
    'Price above both SMA50 and SMA200': '价格位于SMA50和SMA200上方',
    'Price below both SMA50 and SMA200': '价格位于SMA50和SMA200下方',
    'medium-term uptrend': '中期上升趋势',
    'medium-term downtrend': '中期下降趋势',
    'range-bound consolidation': '区间整理',
    'Golden cross signal': '金叉信号',
    'trend may be strengthening': '趋势可能转强',
    'pullback confirmed near SMA50': '回调在SMA50附近确认',
    'rebounding from SMA50 support': '从SMA50支撑位反弹',
    'SMA50 crossed above SMA200 recently': 'SMA50近期上穿SMA200',
    'near SMA50 level': '位于SMA50附近',
    'consolidating': '震荡整理',
    'recovery phase': '恢复阶段',
    'Total signals in scan range': '扫描范围内共检测到',
    'Recent 3 months': '近3个月',
    'signals': '个信号',
    'Bullish': '看涨',
    'Bearish': '看跌',
    'Candlestick Patterns': 'K线形态',
    'Pocket Pivot': '口袋支点',
    'TA-Lib Indicators': 'TA-Lib指标',
    'Double Bottom': '双重底',
    'Breakout': '标准突破',
    'Flat Base': '扁平基部',
    'Indicators': '指标信号',
    'overbought, caution': '超买，注意风险',
    'oversold, reversal opportunity possible': '超卖，可能存在反转机会',
    'weak, momentum lacking': '偏弱，动量不足',
    'neutral, within normal range': '中性，处于正常区间',
    'strong, momentum healthy': '偏强，动量健康',
    'Contradictory signals present': '存在矛盾信号',
    'await clearer confirmation': '等待更明确确认',
    'Strong multi-engine resonance': '多引擎信号强共振',
    'pattern + indicator layers confirm each other': '形态层与指标层互相确认',
    'Multiple buy signals resonating': '多个买入信号共振',
    'Multiple bearish signals': '多个看跌信号',
    'BB bandwidth extremely narrow': '布林带极度收窄',
    'potential large move ahead': '可能酝酿大幅波动',
    'Consider adding to watchlist': '可纳入观察名单',
    'actively monitor': '积极关注',
    'Consider small position at current level': '可考虑当前价位小仓位介入',
    'stop loss below SMA50': '止损设在SMA50下方',
    'await pullback to SMA50 before entry': '等待回踩SMA50确认后介入',
    'await for clearer signal': '建议等待更明确信号',
    'caution advised, manage position': '建议谨慎，控制仓位'
  };
  var result = {};
  for (var key in rec) {
    var val = rec[key];
    if (typeof val === 'string') {
      for (var en in map) {
        val = val.split(en).join(map[en]);
      }
    }
    result[key] = val;
  }
  return result;
}

// ── HTML 转义 ──
function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
