/* ─────────────────────────────────────────────────────────────
 * 持仓建议卡 · 共用渲染组件
 *
 * 视觉口径与 market-scan/dividend-advice-detail.html 一致
 * （该页仍用自己内联的样式，不迁移；本组件供之后新增的指数页面使用）
 *
 * 用法：
 *   <link rel="stylesheet" href="/shared/css/advice-card.css">
 *   <script src="/shared/js/advice-card.js"></script>
 *   <div id="advice"></div>
 *   <script>
 *     document.getElementById('advice').innerHTML = window.renderAdviceCard({
 *       level: 'neutral', label: '中性区',
 *       reasons: ['锚定回撤 17.3% 落在 15~20% 档'],
 *       buyScore: 30, riskScore: 0,
 *       scoreDetail: [{factor:'锚定回撤', desc:'17.3%', value:30, note:'...'}],
 *       noteBox: { title:'回撤参考点', rows:[['参考高点','9624.67（2026-03-12）'], ...] },
 *       footer: '该档中位年化 17.48% · 独立段 5'
 *     });
 *   </script>
 * ───────────────────────────────────────────────────────────── */
(function (global) {
  'use strict';

  var LEVEL_COLOR = {
    low: '#10b981',      // 偏低区
    neutral: '#539BF5',  // 中性区
    high: '#f59e0b',     // 偏高区
    reduce: '#ef4444'    // 减仓提示
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function barColor(val, min, mid, invert) {
    // invert=true 时高分是风险（红），否则高分是机会（绿）
    if (invert) {
      return val >= min ? '#ef4444' : val >= mid ? '#f59e0b' : '#a1a1aa';
    }
    return val >= min ? '#10b981' : val >= mid ? '#539BF5' : '#a1a1aa';
  }

  function scoreBar(label, val, color) {
    var w = Math.max(0, Math.min(val, 100));
    return '<div class="ac-bar-row">' +
      '<span class="ac-bar-label">' + esc(label) + '</span>' +
      '<span class="ac-bar-track"><span class="ac-bar-fill" ' +
      'style="width:' + w + '%;background:' + color + '"></span></span>' +
      '<span class="ac-bar-val" style="color:' + color + '">' + val + '</span>' +
      '</div>';
  }

  /**
   * 渲染持仓建议卡。
   * @param {Object} o
   * @param {string} o.level        等级码（low/neutral/high/reduce）
   * @param {string} o.label        等级中文
   * @param {string[]} [o.reasons]  等级旁的一句话说明（取第一条）
   * @param {number} o.buyScore     买入分 0-100
   * @param {number} o.riskScore    风险分 0-100
   * @param {Array}  [o.scoreDetail] 因子明细 [{factor,desc,value,note,risk}]
   * @param {Object} [o.noteBox]    {title, rows:[[label, value]]} 说明框
   * @param {Object} [o.breadth]    {hit, total, text} 风险广度
   * @param {string} [o.footer]     底部元信息（可含 HTML）
   * @returns {string} HTML 字符串
   */
  function renderAdviceCard(o) {
    o = o || {};
    var lc = LEVEL_COLOR[o.level] || '#a1a1aa';
    var buy = o.buyScore == null ? 0 : o.buyScore;
    var risk = o.riskScore == null ? 0 : o.riskScore;
    var reasons = o.reasons || [];
    var detail = o.scoreDetail || [];
    var h = '<div class="advice-card">';

    // 头部
    h += '<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">';
    h += '<span class="lv" style="color:' + lc + '">' + esc(o.label || '—') + '</span>';
    h += '<span class="tx">' + esc(reasons[0] || '') + '</span></div>';

    // 分数条
    h += '<div style="border-top:1px solid rgba(255,255,255,.06);padding-top:10px">';
    h += scoreBar('买入分', buy, barColor(buy, 60, 30, false));
    h += scoreBar('风险分', risk, barColor(risk, 70, 35, true));
    if (o.breadth) {
      h += '<div class="ac-breadth">风险因子 <b style="color:#f59e0b">' +
        o.breadth.hit + ' / ' + o.breadth.total + '</b> 达到 30 分' +
        (o.breadth.text ? '（' + esc(o.breadth.text) + '）' : '') + '</div>';
    }
    h += '</div>';

    // 说明框（回撤参考点等）
    if (o.noteBox && o.noteBox.rows && o.noteBox.rows.length) {
      h += '<div class="ac-note-box"><b>' + esc(o.noteBox.title || '') + '</b>';
      if (o.noteBox.subtitle) h += '（' + esc(o.noteBox.subtitle) + '）';
      h += '<br>';
      h += o.noteBox.rows.map(function (r) {
        return esc(r[0]) + '：<b>' + r[1] + '</b>';
      }).join('<br>');
      h += '</div>';
    }

    // 因子表
    if (detail.length) {
      h += '<table class="ac-table"><tbody>';
      h += '<tr><th>因子</th><th>说明</th><th class="r">当前值</th><th class="r">得分</th></tr>';
      h += detail.map(function (d) {
        var c = d.risk ? barColor(d.value, 70, 35, true)
                       : barColor(d.value, 30, 1, false);
        return '<tr><td>' + esc(d.factor) + '</td>' +
          '<td style="color:#a1a1aa">' + esc(d.note || '') + '</td>' +
          '<td class="r" style="color:#e4e4e7">' + esc(d.desc || '') + '</td>' +
          '<td class="r" style="color:' + c + '">' + (d.value == null ? '—' : d.value) +
          '</td></tr>';
      }).join('');
      h += '</tbody></table>';
    }

    // 底部
    if (o.footer) h += '<div class="ac-foot">' + o.footer + '</div>';

    h += '</div>';
    return h;
  }

  global.renderAdviceCard = renderAdviceCard;
  global.ADVICE_LEVEL_COLOR = LEVEL_COLOR;
})(window);
