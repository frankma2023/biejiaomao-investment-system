var API = 'http://' + window.location.hostname + ':8788/api/canslim-score';
var API_CFG = 'http://' + window.location.hostname + ':8788/api/config?signal_type=canslim_scorecard';
var chart = null;

document.addEventListener('DOMContentLoaded', function() {
  // URL 参数预填（最优先）
  var p = new URLSearchParams(window.location.search);
  var c = p.get('code');
  var inp = document.getElementById('code');
  if (c) inp.value = c; else if (!inp.value) inp.value = '600519';

  document.getElementById('date').value = new Date().toISOString().slice(0, 10);
  chart = echarts.init(document.getElementById('radar-chart'));
  document.querySelector('.theme-toggle').addEventListener('click', function() {
    var html = document.documentElement;
    html.dataset.theme = html.dataset.theme === 'light' ? 'dark' : 'light';
    this.textContent = html.dataset.theme === 'light' ? '🌙' : '☀️';
  });
  loadConfig();
  fetchName();
  doScore();
});

function fetchName() {
  var code = document.getElementById('code').value.trim();
  fetch('http://' + window.location.hostname + ':8788/api/stock-name?code=' + code + '&mode=stock')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      document.getElementById('stock-name').textContent = d.name || '';
    })
    .catch(function() { document.getElementById('stock-name').textContent = ''; });
}

function doScore() {
  var code = document.getElementById('code').value.trim();
  var date = document.getElementById('date').value;
  var btn = document.querySelector('.btn-run');
  btn.disabled = true; btn.textContent = '⏳';

  fetch(API + '?code=' + code + '&date=' + date)
    .then(function(r) { return r.json(); })
    .then(function(d) { renderResults(d); })
    .catch(function(e) { alert('Error: ' + e.message); })
    .finally(function() { btn.disabled = false; btn.textContent = '🎯 评分'; });
}

function renderResults(d) {
  // Grade badge
  var badge = document.getElementById('grade-badge');
  badge.textContent = d.grade + '级 ' + d.score + '/100';
  badge.className = 'grade-badge grade-' + d.grade;

  // M advice
  if (d.M && d.M.position && d.M.position !== 'N/A') {
    document.getElementById('m-advice').textContent = '大盘: ' + d.M.position;
  }

  // Render each dimension with sub-items
  var dimConfig = [
    {key: 'C', name: 'C 当期盈利', max: 22, color: '#E53935', cls: 'dim-c',
     subs: [
       {key: 'eps_yoy', label: 'EPS同比增速'},
       {key: 'eps_accel', label: 'EPS加速度'},
       {key: 'revenue_yoy', label: '营收同比增速'},
       {key: 'nonrecurring', label: '扣非占比'}
     ]},
    {key: 'A', name: 'A 年度盈利', max: 15, color: '#FF9800', cls: 'dim-a',
     subs: [
       {key: 'eps_cagr_3y', label: '3年EPS CAGR'},
       {key: 'pos_years', label: '正增长年数'},
       {key: 'stability', label: '盈利稳定性'}
     ]},
    {key: 'N', name: 'N 形态新高', max: 17, color: '#4CAF50', cls: 'dim-n',
     subs: [
       {key: 'high52', label: '距52周高点'},
       {key: 'form_breakout', label: '形态突破'}
     ]},
    {key: 'S', name: 'S 供给需求', max: 7, color: '#2196F3', cls: 'dim-s',
     subs: [
       {key: 'market_cap', label: '流通市值'},
       {key: 'vol_ratio', label: '成交量放大'},
       {key: 'buyback', label: '回购注销'}
     ]},
    {key: 'L', name: 'L 领军股', max: 22, color: '#9C27B0', cls: 'dim-l',
     subs: [
       {key: 'rs_250', label: 'RS评级(250日)'},
       {key: 'rs_momentum', label: 'RS动量'},
       {key: 'industry_rs', label: '行业RS评级'},
       {key: 'excess', label: '近期超额收益'}
     ]},
    {key: 'I', name: 'I 机构认同', max: 17, color: '#795548', cls: 'dim-i',
     subs: [
       {key: 'inst_holding', label: '机构持股比例'},
       {key: 'inst_change', label: '机构数量变化'},
       {key: 'analyst', label: '研报覆盖'},
       {key: 'first_cov', label: '首次覆盖'},
       {key: 'rating_up', label: '评级上调'},
       {key: 'debt', label: '负债率检查'}
     ]}
  ];

  var html = '';
  dimConfig.forEach(function(dim) {
    var s = d[dim.key];
    var score = s.score || 0;
    var cls = score > 0 ? 'score-pos' : (score < 0 ? 'score-neg' : 'score-zero');
    html += '<div class="dim-card ' + dim.cls + '">';
    html += '<div class="dim-header"><span style="color:' + dim.color + '">' + dim.name + '</span><span class="pts ' + cls + '">' + score + '/' + dim.max + '</span></div>';
    html += '<div class="dim-detail">' + (s.detail || '') + '</div>';

    // Sub-items
    dim.subs.forEach(function(sub) {
      var bd = s.breakdown || {};
      var subData = bd[sub.key];
      if (subData) {
        var val = subData.value != null ? subData.value : '-';
        var subScore = subData.score != null ? subData.score : 0;
        var scCls = subScore > 0 ? 'score-pos' : (subScore < 0 ? 'score-neg' : 'score-zero');
        var note = subData.note || '';
        html += '<div class="dim-sub">' + sub.label + ': <b>' + val + '</b>';
        if (subScore !== 0 || note) {
          html += ' <span class="' + scCls + '">(' + (subScore > 0 ? '+' : '') + subScore + ')</span>';
        }
        if (note) html += ' <span style="font-size:0.52rem;color:var(--text-tertiary)">' + note + '</span>';
        html += '</div>';
      }
    });
    html += '</div>';
  });

  document.getElementById('dim-results').innerHTML = html;

  // Radar chart
  renderRadar(d, dimConfig);
}

function renderRadar(d, dimConfig) {
  var values = dimConfig.map(function(dim) {
    var s = d[dim.key];
    return Math.max(0, Math.round((s.score || 0) / dim.max * 100));
  });
  var labels = dimConfig.map(function(dim) { return dim.name; });

  chart.setOption({
    radar: {
      indicator: labels.map(function(l) { return {name: l, max: 100}; }),
      shape: 'polygon', radius: '60%',
      axisName: { fontSize: 10, color: 'var(--text-tertiary)' },
      splitArea: { areaStyle: { color: ['rgba(254,44,85,0.02)', 'rgba(254,44,85,0.04)'] } }
    },
    series: [{
      type: 'radar', data: [{value: values, name: d.stock_code,
        areaStyle: {color: 'rgba(254,44,85,0.12)'},
        lineStyle: {color: '#FE2C55', width: 2},
        itemStyle: {color: '#FE2C55'}
      }],
      symbol: 'circle', symbolSize: 6
    }]
  }, true);
}

function saveResults() {
  var code = document.getElementById('code').value.trim();
  var date = document.getElementById('date').value;
  var btn = event.target;
  btn.disabled = true; btn.textContent = '...';

  fetch(API, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({stock_code: code, date: date})
  })
  .then(function(r) { return r.json(); })
  .then(function(d) {
    btn.textContent = '✅ 已保存';
    setTimeout(function() { btn.textContent = '💾 保存结果'; btn.disabled = false; }, 2000);
  });
}

function saveConfig() {
  var config = {};
  document.querySelectorAll('.param-card-bd input').forEach(function(inp) {
    var path = inp.dataset.path;
    var val = inp.value;
    if (val.indexOf(',') >= 0) {
      val = val.split(',').map(function(s) { var n = parseFloat(s.trim()); return isNaN(n) ? s.trim() : n; });
    } else {
      var n = parseFloat(val);
      if (!isNaN(n)) val = n;
    }
    var keys = path.split('.');
    var obj = config;
    for (var i = 0; i < keys.length - 1; i++) {
      if (!obj[keys[i]]) obj[keys[i]] = {};
      obj = obj[keys[i]];
    }
    obj[keys[keys.length - 1]] = val;
  });

  // 直接发送 config dict，不用 wrapper
  fetch(API_CFG, {
    method: 'POST',
    headers: {'Content-Type': 'text/plain'},
    body: JSON.stringify(config)
  })
  .then(function(r) { return r.json(); })
  .then(function() {
    var btn = event.target;
    btn.textContent = '✅ 已保存';
    setTimeout(function() { btn.textContent = '⚙️ 保存配置'; }, 2000);
  });
}

function loadConfig() {
  fetch(API_CFG)
    .then(function(r) { return r.json(); })
    .then(function(cfg) {
      // API返回的就是配置dict，直接用
      if (!cfg || Object.keys(cfg).length === 0) return;
      var sections = {
        'c': [['c_current_earnings.eps_yoy_tiers', 'EPS增速阈值', '25,18,10'],
              ['c_current_earnings.eps_yoy_scores', '对应得分', '12,8,5'],
              ['c_current_earnings.eps_accel_threshold', '加速度阈值(%)', '10'],
              ['c_current_earnings.revenue_yoy_tiers', '营收增速阈值', '25,15'],
              ['c_current_earnings.revenue_yoy_scores', '对应得分', '4,2'],
              ['c_current_earnings.nonrecurring_ratio', '扣非占比阈值(%)', '90']],
        'a': [['a_annual_earnings.eps_cagr_3y_tiers', '3年CAGR阈值', '25,15,5'],
              ['a_annual_earnings.eps_cagr_scores', '对应得分', '9,6,3'],
              ['a_annual_earnings.stability_cv_threshold', '稳定性CV阈值(%)', '30']],
        'n': [['n_new.high52_tiers', '52周高点阈值', '-5,-15'],
              ['n_new.high52_scores', '对应得分', '7,5'],
              ['n_new.high_lookback_days', '回溯天数', '5']],
        's': [['s_supply_demand.market_cap_tiers', '市值分档(亿)', '50,200,500'],
              ['s_supply_demand.market_cap_scores', '对应得分', '4,2,1'],
              ['s_supply_demand.vol_ratio_tiers', '量比阈值', '1.5,1.2'],
              ['s_supply_demand.vol_ratio_scores', '对应得分', '3,2']],
        'l': [['l_leader.rs250_tiers', 'RS250阈值', '95,90,80,70'],
              ['l_leader.rs250_scores', '对应得分', '11,9,6,3'],
              ['l_leader.excess_return_threshold', '超额收益阈值(%)', '5']],
        'i': [['i_institutional.inst_holding_tiers', '机构持股阈值(%)', '15,5,1'],
              ['i_institutional.inst_holding_scores', '对应得分', '5,3,1'],
              ['i_institutional.analyst_coverage_tiers', '研报覆盖阈值', '3,1'],
              ['i_institutional.debt_ratio_warning', '负债率警告(%)', '60']]
      };

      Object.keys(sections).forEach(function(key) {
        var div = document.getElementById('body-' + key);
        var html = '';
        sections[key].forEach(function(p) {
          var val = getNested(cfg, p[0]);
          if (val == null) val = p[2];
          if (Array.isArray(val)) val = val.join(',');
          html += '<div class="param-row"><label>' + p[1] + '</label>';
          html += '<input data-path="' + p[0] + '" value="' + val + '"></div>';
        });
        div.innerHTML = html;
      });
    });
}

function getNested(obj, path) {
  return path.split('.').reduce(function(o, k) { return o ? o[k] : null; }, obj);
}
