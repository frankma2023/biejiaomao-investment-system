var API = 'http://' + window.location.hostname + ':8788/api/canslim-scores';
var allData = [];
var filteredData = [];
var currentPage = 1;
var pageSize = 200;
var sortCol = 3;
var sortDir = 1;
var colNames = ['#','代码','名称','总评分','C 当期盈利','A 年度盈利','N 形态新高','S 供给需求','L 领军股','I 机构认同'];

document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('date').value = new Date().toISOString().slice(0, 10);
  var themeBtn = document.querySelector('.theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function() {
      var html = document.documentElement;
      html.dataset.theme = html.dataset.theme === 'light' ? 'dark' : 'light';
      this.textContent = html.dataset.theme === 'light' ? '🌙' : '☀️';
    });
  }
  loadData();
});

function loadData() {
  var date = document.getElementById('date').value;
  fetch(API + '?date=' + date)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      allData = d.scores || [];
      document.getElementById('date').value = d.date || date;
      if (allData.length === 0) {
        document.getElementById('tbody').innerHTML = '<tr><td colspan="10" style="padding:20px;color:var(--text-tertiary)">暂无数据，请先运行 batch_canslim_score.py</td></tr>';
        return;
      }
      sortBy(3);
      filteredData = [];  // reset filter to show all sorted data
      renderTable();
      renderPager();
    });
}

function fetchName() {
  var code = document.getElementById('code').value.trim();
  if (!code) { document.getElementById('stock-name').textContent = ''; return; }
  fetch('http://' + window.location.hostname + ':8788/api/stock-name?code=' + code + '&mode=stock')
    .then(function(r) { return r.json(); })
    .then(function(d) { document.getElementById('stock-name').textContent = d.name || ''; });
}

function filterTable() {
  var code = document.getElementById('code').value.trim();
  if (!code) { filteredData = allData; }
  else { filteredData = allData.filter(function(r) { return r.stock_code === code; }); }
  currentPage = 1;
  renderTable();
  renderPager();
  updateSortHeaders();
}

function sortBy(col) {
  if (sortCol === col) sortDir = -sortDir;
  else { sortCol = col; sortDir = -1; }
  var data = filteredData.length ? filteredData : allData;
  var isNum = col !== 1 && col !== 2;
  data.sort(function(a, b) {
    var keys = ['rank','stock_code','name','score','c','a','n','s','l','i'];
    var va = a[keys[col]], vb = b[keys[col]];
    if (isNum) { va = parseFloat(va)||0; vb = parseFloat(vb)||0; }
    if (va < vb) return -sortDir;
    if (va > vb) return sortDir;
    return 0;
  });
  // Update rank after sort
  data.forEach(function(r, i) { r.rank = i + 1; });
  if (!filteredData.length) allData = data; else filteredData = data;
  currentPage = 1;
  renderTable();
  renderPager();
  updateSortHeaders();
}

function renderTable() {
  var data = filteredData.length ? filteredData : allData;
  var start = (currentPage - 1) * pageSize;
  var end = Math.min(start + pageSize, data.length);
  var html = '';
  for (var i = start; i < end; i++) {
    var r = data[i];
    html += '<tr>';
    html += '<td>' + (i + 1) + '</td>';
    html += '<td><a class="code-link" href="../canslim-scorecard/?code=' + r.stock_code + '" target="_blank">' + r.stock_code + '</a></td>';
    html += '<td>' + (r.name || '') + '</td>';
    html += '<td class="num"><b class="grade-' + (r.grade||'') + '">' + (r.score||0) + '</b></td>';
    html += '<td class="num">' + fmt(r.c) + '</td>';
    html += '<td class="num">' + fmt(r.a) + '</td>';
    html += '<td class="num">' + fmt(r.n) + '</td>';
    html += '<td class="num">' + fmt(r.s) + '</td>';
    html += '<td class="num">' + fmt(r.l) + '</td>';
    html += '<td class="num">' + fmt(r.i) + '</td>';
    html += '</tr>';
  }
  document.getElementById('tbody').innerHTML = html;
}

function renderPager() {
  var data = filteredData.length ? filteredData : allData;
  var total = data.length;
  var totalPages = Math.ceil(total / pageSize) || 1;
  if (currentPage > totalPages) currentPage = totalPages;

  var pagerHtml = function(id) {
    return '<span class="info">共 ' + total + ' 条</span> ' +
      '<button onclick="goPage(1)"' + (currentPage<=1?' disabled':'') + '>««</button> ' +
      '<button onclick="goPage(' + (currentPage-1) + ')"' + (currentPage<=1?' disabled':'') + '>«</button> ' +
      '<span>第 ' + currentPage + '/' + totalPages + ' 页</span> ' +
      '<button onclick="goPage(' + (currentPage+1) + ')"' + (currentPage>=totalPages?' disabled':'') + '>»</button> ' +
      '<button onclick="goPage(' + totalPages + ')"' + (currentPage>=totalPages?' disabled':'') + '>»»</button> ' +
      '每页 <select onchange="setPageSize(+this.value)">' +
      '<option value="50"' + (pageSize==50?' selected':'') + '>50</option>' +
      '<option value="100"' + (pageSize==100?' selected':'') + '>100</option>' +
      '<option value="200"' + (pageSize==200?' selected':'') + '>200</option>' +
      '<option value="500"' + (pageSize==500?' selected':'') + '>500</option></select> 条';
  };
  document.getElementById('pager-top').innerHTML = pagerHtml('top');
  document.getElementById('pager-bottom').innerHTML = pagerHtml('bottom');
}

function goPage(n) {
  var data = filteredData.length ? filteredData : allData;
  var totalPages = Math.ceil(data.length / pageSize) || 1;
  if (n < 1) n = 1;
  if (n > totalPages) n = totalPages;
  currentPage = n;
  renderTable();
  renderPager();
}

function setPageSize(n) {
  pageSize = n;
  currentPage = 1;
  renderTable();
  renderPager();
  updateSortHeaders();
}

function updateSortHeaders() {
  var ths = document.querySelectorAll('#tbl thead th');
  for (var i = 0; i < ths.length; i++) {
    var arrow = '';
    if (i === sortCol) {
      arrow = sortDir === 1 ? ' ▴' : ' ▾';
    }
    ths[i].innerHTML = colNames[i] + arrow;
  }
}

function fmt(v) {
  if (v == null) return '—';
  return Math.round(v * 10) / 10;
}
