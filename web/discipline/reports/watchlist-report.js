/* 自选池日报 · 共享渲染 JS（watchlist-report.html 与每日静态快照共用）
 * 数据来源：window.REPORT_DATA（静态快照内嵌）或 /api/watchlist-report/data（动态页）
 */
(function () {
  const LV = {
    buy_strong: { cn: '买入（强）', color: '#ef4444', bg: 'rgba(239,68,68,.10)', bd: 'rgba(239,68,68,.35)' },
    buy: { cn: '买入', color: '#ef4444', bg: 'rgba(239,68,68,.08)', bd: 'rgba(239,68,68,.28)' },
    hold: { cn: '持有', color: '#3b82f6', bg: 'rgba(59,130,246,.08)', bd: 'rgba(59,130,246,.28)' },
    wait: { cn: '等回调', color: '#f59e0b', bg: 'rgba(245,158,11,.08)', bd: 'rgba(245,158,11,.28)' },
    avoid: { cn: '回避', color: '#10b981', bg: 'rgba(16,185,129,.08)', bd: 'rgba(16,185,129,.28)' },
  };
  const LV_ORDER = { avoid: 0, wait: 1, buy_strong: 2, buy: 3, hold: 4 };

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function pct(v, plus = true) {
    if (v == null || isNaN(Number(v))) return '—';
    var n = Math.round(Number(v) * 100) / 100;  // 去浮点尾差(0.0199×100=1.9900000000000002)并保留2位
    if (n === 0) n = 0;
    return (plus && n > 0 ? '+' : '') + n + '%';
  }
  function fmtPrice(v) {
    if (v == null || isNaN(v)) return '—';
    return Number(v).toFixed(3).replace(/\.?0+$/, '');
  }

  function summaryBar(sum, total) {
    const items = [
      ['回避', sum.avoid || 0, '#10b981'], ['等回调', sum.wait || 0, '#f59e0b'],
      ['买入', (sum.buy || 0) + (sum.buy_strong || 0), '#ef4444'],
      ['持有', sum.hold || 0, '#3b82f6'], ['异常', sum.error || 0, '#8b8b90'],
    ];
    return '<div class="sum-bar">' + items.filter(i => i[1] > 0).map(i =>
      '<span class="sum-chip" style="color:' + i[2] + ';border-color:' + i[2] + '44">' + i[0] + ' <b>' + i[1] + '</b></span>'
    ).join('') + '<span class="sum-total">共 ' + total + ' 标的</span></div>';
  }

  function sigLine(card) {
    const sigs = card.signals || [];
    if (!sigs.length) return '';
    return '<div class="sig-line">' + sigs.map(s =>
      '<span class="sig-tag ' + (s.dir === 'short' ? 'sig-short' : 'sig-long') + '" title="' + esc(s.note) + '">' +
      esc(s.source) + '@' + esc(String(s.date).slice(5)) + ' (' + s.score + '分)</span>'
    ).join('') + '</div>';
  }

  function chanLine(card) {
    const c = card.chanlun;
    if (!c) return '';
    const col = c.side === 'sell' ? '#10b981' : '#ef4444';
    return '<div class="chan-line">🧬 <span style="color:' + col + '">' + esc(c.text) + '</span><span class="dim">(' + esc(c.date) + ')</span></div>';
  }

  function missBlock(card, lastView) {
    const m = card.missed || [];
    if (!m.length || !lastView) return '';
    const fresh = m.filter(x => x.missed);
    if (!fresh.length) return '<div class="miss-line dim">📌 距上次查看（' + lastView + '）无新错过信号</div>';
    return '<div class="miss-line">📌 距上次查看（' + lastView + '）新增信号：' +
      fresh.map(x => '<b style="color:#ef4444">' + esc(x.source) + '</b>@' + esc(x.date) +
        '（当时 ' + fmtPrice(x.close_at) + ' → 现 +' + x.gain_pct + '%）' +
        (x.chaseable ? '<span class="ok">仍可关注</span>' : '<span class="dim">已错过/勿追</span>')
      ).join(' · ') + '</div>';
  }

  function holdingBlock(card) {
    const h = card.holding;
    if (!h) return '';
    const col = h.pnl_pct >= 0 ? '#ef4444' : '#10b981';
    return '<div class="hold-line">💼 持仓 成本 ' + h.cost + ' · 浮盈 <b style="color:' + col + '">' + pct(h.pnl_pct) + '</b> · 止损位 ' + (h.stop_loss || '—') + '</div>';
  }

  function callbackBlock(card) {
    const cb = (card.eval || {}).callback;
    const fib = (card.ctx || {}).fib_levels || [];
    if (!cb && !fib.length) return '';
    const parts = [];
    if (fib.length) parts.push('斐波那契 ' + fib.map(f => fmtPrice(f)).join(' / '));
    if (cb) parts.push('<b style="color:#f59e0b">最近目标 ' + fmtPrice(cb.price) + '（距 ' + cb.pct + '%）</b>');
    return '<div class="cb-line">🎯 回调位：' + parts.join(' · ') + '</div>';
  }

  function cardHtml(card, lastView) {
    const ev = card.eval || {};
    const lv = LV[ev.level] || LV.hold;
    const ctx = card.ctx || {};
    const kindTag = card.kind === 'index' ? '<span class="kind-tag">指数</span>' : card.kind === 'etf' ? '<span class="kind-tag">ETF</span>' : '';
    const err = card.error ? '<div class="err-line">⚠️ ' + esc(card.error) + '</div>' : '';
    const chg = card.change_pct != null
      ? '<span style="color:' + (card.change_pct >= 0 ? '#ef4444' : '#10b981') + '">' + pct(card.change_pct * 100) + '</span>' : '';  // change_pct 库内为比率(0.0036=0.36%)，显示 ×100
    return '<div class="r-card" style="border-color:' + lv.bd + '">' +
      '<div class="r-head"><span class="r-name">' + esc(card.name) + kindTag + '</span>' +
      '<span class="r-code">' + esc(card.code) + '</span>' +
      '<span class="r-lv" style="background:' + lv.bg + ';color:' + lv.color + ';border-color:' + lv.bd + '">' + lv.cn + '</span>' +
      (card.close != null ? '<span class="r-price">' + fmtPrice(card.close) + ' ' + chg + '</span>' : '') +
      '</div>' +
      (ev.net != null ? '<div class="r-net">净分 <b>' + ev.net + '</b> <span class="dim">(买 ' + ev.buy_score + ' / 卖 ' + ev.sell_score + ')</span></div>' : '') +
      '<div class="r-ctx"><span class="dim">位置 ' + (ctx.pos_250 != null ? ctx.pos_250 + '%' : '—') + '</span>' +
      '<span class="dim">自低点 ' + (ctx.gain_from_low != null ? pct(ctx.gain_from_low) : '—') + '</span>' +
      (card.rps && card.rps.rps_20 != null ? '<span class="dim">RS20 ' + card.rps.rps_20 + '</span>' : '') +
      (card.rps && card.rps.rps_250 != null ? '<span class="dim">RS250 ' + card.rps.rps_250 + '</span>' : '') +
      (card.metrics && card.metrics.dd_250 != null ? '<span class="dim">回撤 ' + card.metrics.dd_250 + '%</span>' : '') +
      (card.metrics && card.metrics.pe_pct != null ? '<span class="dim">PE分位 ' + card.metrics.pe_pct + '%</span>' : '') +
      '</div>' +
      holdingBlock(card) + chanLine(card) + missBlock(card, lastView) +
      (ev.reasons && ev.reasons.length ? '<div class="r-reasons">' + ev.reasons.map(r => '<div>' + esc(r) + '</div>').join('') + '</div>' : '') +
      sigLine(card) + callbackBlock(card) +
      (ev.tips && ev.tips.length ? '<div class="r-tips">' + ev.tips.map(t => '<div>⚠️ ' + esc(t) + '</div>').join('') + '</div>' : '') +
      err + '</div>';
  }

  function render(data) {
    const root = document.getElementById('report-root');
    if (!root) return;
    if (!data || data.error) {
      root.innerHTML = '<div class="r-empty">暂无日报（' + esc(data && data.date || '') + '）——每日盘后由 daily_update 生成</div>';
      return;
    }
    const cards = (data.cards || []).slice().sort((a, b) => {
      const ka = LV_ORDER[(a.eval || {}).level] ?? 9, kb = LV_ORDER[(b.eval || {}).level] ?? 9;
      return ka - kb;
    });
    root.innerHTML =
      '<div class="r-top">' +
      '<div class="r-title">📋 自选池日报 <span class="dim">' + esc(data.date) + '</span>' +
      '<span class="dim gen">生成于 ' + esc(data.generated_at || '') + '</span></div>' +
      summaryBar(data.summary || {}, data.total) +
      '</div>' +
      cards.map(c => cardHtml(c, data.last_view)).join('');
    // 记录查看锚点（错过检测）
    if (window.API_BASE && data.date) {
      fetch(API_BASE + '/api/watchlist-report/view', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: data.date }),
      }).catch(() => {});
    }
    return root;
  }

  function loadCalendar(current) {
    const cal = document.getElementById('cal-select');
    if (!cal) return;
    const api = window.API_BASE || '';
    fetch(api + '/api/watchlist-report/index').then(r => r.json()).then(d => {
      cal.innerHTML = (d.dates || []).map(dt =>
        '<option value="' + dt + '"' + (dt === current ? ' selected' : '') + '>' + dt + '</option>').join('') ||
        '<option value="">暂无历史</option>';
    }).catch(() => {});
  }

  // 动态页入口：从 API 加载
  window.loadReport = function (date) {
    const api = window.API_BASE || '';
    fetch(api + '/api/watchlist-report/data' + (date ? '?date=' + date : '')).then(r => r.json()).then(d => {
      render(d);
      if (!date) date = d.date;
      if (date) {
        if (window.history && history.replaceState) history.replaceState(null, '', '?date=' + date);
        loadCalendar(date);
      }
    }).catch(e => {
      const root = document.getElementById('report-root');
      if (root) root.innerHTML = '<div class="r-empty">加载失败: ' + esc(e.message) + '</div>';
    });
  };
  // 静态快照入口：window.REPORT_DATA 已内嵌
  window.renderReport = function () {
    if (window.REPORT_DATA) {
      render(window.REPORT_DATA);
      loadCalendar(window.REPORT_DATA.date);
    }
  };
})();
