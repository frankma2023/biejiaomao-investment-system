/**
 * Nav.js — 欧奈尔投资系统全站导航栏 (Dark Glass Edition for web4)
 */

// 全站 favicon（所有页面统一）
(function(){var l=document.createElement('link');l.rel='icon';l.type='image/svg+xml';l.href=(function(){var s=document.querySelector('script[src$=\"nav.js\"]');return s?s.src.replace(/\/shared\/js\/nav\.js.*/,'')+'/images/favicon.svg':'../images/favicon.svg'})();document.head.appendChild(l)})();

// API Proxy: forward /api/ requests to Flask
(function(){var B='http://localhost:8788',_f=window.fetch;window.fetch=function(u,o){if(typeof u==='string'&&u.indexOf('/api/')===0)u=B+u;return _f.call(window,u,o)}})();

(function (global) {
  'use strict';
  var Nav = {}, config = {};

  var BACKTEST_ITEMS = [
    { href: '../distribution-day/',     label: '抛盘日',           page: 'distribution-day' },
    { href: '../follow-through-day/',   label: '追盘日',           page: 'follow-through-day' },
    { href: '../accumulation-day/',     label: '吸筹日',           page: 'accumulation-day' },
    { href: '../index-rs-backtest/',    label: '指数RS强度',       page: 'index-rs-backtest' },
    { href: '../index-crowdedness/',    label: '指数拥挤度',       page: 'index-crowdedness' },
    { href: '../stock-rs-backtest/',    label: '个股RS强度',       page: 'stock-rs-backtest' },
    { href: '../index-ad-backtest/',    label: '机构吸筹/出货',    page: 'index-ad-backtest' },
    { href: '../divergence-backtest/',  label: '指数背离',         page: 'divergence-backtest' },
    { href: '../strongest-index/',      label: '最强指数',         page: 'strongest-index' },
    { href: '../cup-handle-backtest/',  label: '杯柄形态',         page: 'cup-handle-backtest' },
    { href: '../double-bottom/',        label: '双重底',           page: 'double-bottom' },
    { href: '../flat-base/',            label: '扁平基部',         page: 'flat-base' },
    { href: '../saucer-base-backtest/', label: '碟形基部',         page: 'saucer-base-backtest' },
    { href: '../base-breakout/',        label: '基部突破',         page: 'base-breakout' },
    { href: '../pocket-pivot/',         label: '口袋支点',         page: 'pocket-pivot' },
    { href: '../railroad-tracks/',      label: '铁轨线',           page: 'railroad-tracks' },
    { href: '../climax-top/',           label: '高潮见顶',         page: 'climax-top' },
    { href: '../top-pattern/',          label: '头部形态',         page: 'top-pattern' },
    { href: '../volume-divergence/',    label: '量价背离',         page: 'volume-divergence' },
    { href: '../breakout-failure/',     label: '突破失败',         page: 'breakout-failure' },
    { href: '../discipline/screening-backtest.html', label: '股票精选回测', page: 'screening-backtest' },
    { href: '../discipline/screening-backtest-index.html', label: '指数精选回测', page: 'screening-backtest-index' },
    { href: '../chanlun-backtest-compare/', label: '缠论vs欧奈尔', page: 'chanlun-backtest-compare' },
    { href: '../backtest-lab/',          label: '🔬 回测实验室',    page: 'backtest-lab' },
    { href: '../mw-report/',             label: '📊 MW回测报告',    page: 'mw-report' },
      { href: '../progress.html',         label: '进展',             page: 'progress' },
];

  var PATTERN_ITEMS = [
    { href: '../pattern-structure/',    label: 'MW分析',           page: 'pattern-structure' },
    { href: '../pattern-scan/',         label: '形态识别',         page: 'pattern-scan' },
    { href: '../daily-pattern-scan/',   label: '形态扫描',         page: 'daily-pattern-scan' },
    { href: '../mw-signals/',           label: 'MW信号',           page: 'mw-signals' },
    { href: '../prompt-generator/',    label: 'Prompt 生成',     page: 'prompt-generator' },
  ];

  var DISCIPLINE_ITEMS = [
    { href: '../discipline/',            label: '知行首页',  page: 'discipline' },
    { href: '../discipline/screening.html', label: '每日精选',  page: 'screening' },
    { href: '../discipline/observation.html', label: '观察池', page: 'observation' },
    { href: '../discipline/watchlist.html', label: '自选池',  page: 'watchlist' },
    { href: '../discipline/trades.html', label: '交易记录',  page: 'trades' },
    { href: '../discipline/monitor.html', label: '持仓监控', page: 'monitor' },
  ];

  var CHANLUN_ITEMS = [
    { href: '../chanlun/',                   label: '缠论看板',         page: 'chanlun' },
    { href: '../chanlun-backtest/',          label: '缠论回测',         page: 'chanlun-backtest' },
    { href: '../chanlun-scan/',              label: '缠论扫描',         page: 'chanlun-scan' },
    { href: '../discipline/chanlun-daily.html', label: '缠论精选',      page: 'chanlun-daily' },
    { href: '../chanlun-backtest-compare/',  label: '缠论vs欧奈尔',     page: 'chanlun-backtest-compare' },
  ];

  var MAIN_ITEMS = [
    { href: '../index-scan/',           label: '指数扫描',         page: 'index-scan' },
    { href: '../index-valuation/',      label: '指数估值',         page: 'index-valuation' },
    { href: '../stock-valuation/',      label: '个股扫描',         page: 'stock-valuation' },
    { href: '../market-scan/',          label: '大盘扫描',         page: 'market-scan' },
    { href: '../market-scan/red-dividend/', label: '🧭 指数投资', page: 'red-dividend' },
    { href: '../canslim-scores/',       label: 'CAN SLIM',          page: 'canslim-scores' },
    { href: '../cockpit/',             label: '驾驶舱',           page: 'cockpit' },
  ];

  // 深目录页面前缀：web/market-scan/red-dividend/ 等深两层页面在 Nav.init 前设 window.NAV_PREFIX='../..'
  var P = window.NAV_PREFIX || '';
  function H(h) { return P + h.substring(2); }  // '../xxx' → P + '/xxx'

  function render() {
    var el = document.getElementById('top-nav');
    if (!el) return;
    el.className = 'top-nav';

    var cp = config.currentPage || '';
    var icon = config.brandIcon || '';

    var html = '<div class="nav-brand">' + (icon ? '<span>' + icon + '</span>' : '') + '<span>' + (config.brandText || '') + '</span></div><div class="nav-links">';

    // Home
    html += '<a href="' + H('../') + '" class="nav-item' + (cp === 'home' ? ' active' : '') + '">看板</a>';

    // 形态分析 dropdown
    var isPattern = PATTERN_ITEMS.some(function (p) { return p.page === cp; });
    html += '<div class="nav-dropdown"><a href="javascript:void(0)" class="nav-item' + (isPattern ? ' active' : '') + '">形态分析</a><div class="nav-dropdown-menu">';
    for (var k = 0; k < PATTERN_ITEMS.length; k++) {
      var pt = PATTERN_ITEMS[k];
      html += '<a href="' + H(pt.href) + '" class="' + (pt.page === cp ? 'active' : '') + '">' + pt.label + '</a>';
    }
    html += '</div></div>';

    // Backtest dropdown
    var isBacktest = BACKTEST_ITEMS.some(function (b) { return b.page === cp; });
    html += '<div class="nav-dropdown"><a href="javascript:void(0)" class="nav-item' + (isBacktest ? ' active' : '') + '">回测</a><div class="nav-dropdown-menu">';
    for (var i = 0; i < BACKTEST_ITEMS.length; i++) {
      var b = BACKTEST_ITEMS[i];
      html += '<a href="' + H(b.href) + '" class="' + (b.page === cp ? 'active' : '') + '">' + b.label + '</a>';
    }
    html += '</div></div>';

    // 知行 dropdown
    var isDiscipline = DISCIPLINE_ITEMS.some(function (d) { return d.page === cp; });
    html += '<div class="nav-dropdown"><a href="javascript:void(0)" class="nav-item' + (isDiscipline ? ' active' : '') + '">知行</a><div class="nav-dropdown-menu">';
    for (var d = 0; d < DISCIPLINE_ITEMS.length; d++) {
      var di = DISCIPLINE_ITEMS[d];
      html += '<a href="' + H(di.href) + '" class="' + (di.page === cp ? 'active' : '') + '">' + di.label + '</a>';
    }
    html += '</div></div>';

    // 缠论 dropdown
    var isChanlun = CHANLUN_ITEMS.some(function (c) { return c.page === cp; });
    html += '<div class="nav-dropdown"><a href="javascript:void(0)" class="nav-item' + (isChanlun ? ' active' : '') + '">缠论</a><div class="nav-dropdown-menu">';
    for (var c = 0; c < CHANLUN_ITEMS.length; c++) {
      var cl = CHANLUN_ITEMS[c];
      html += '<a href="' + H(cl.href) + '" class="' + (cl.page === cp ? 'active' : '') + '">' + cl.label + '</a>';
    }
    html += '</div></div>';

    // Main items
    for (var j = 0; j < MAIN_ITEMS.length; j++) {
      var m = MAIN_ITEMS[j];
      html += '<a href="' + H(m.href) + '" class="nav-item' + (m.page === cp ? ' active' : '') + '">' + m.label + '</a>';
    }

    // Theme toggle
    html += '<button class="theme-toggle" onclick="if(typeof toggleTheme==\'function\')toggleTheme()">◐</button>';

    html += '</div>';
    el.innerHTML = html;
  }

  Nav.init = function (cfg) {
    config = cfg || {};
    // 只要 nav 元素已在 DOM 中，立即渲染（避免大页面等 DOMContentLoaded 过久）
    if (document.getElementById('top-nav')) {
      render();
    } else {
      document.addEventListener('DOMContentLoaded', render);
    }
  };

  global.Nav = Nav;
})(typeof window !== 'undefined' ? window : this);