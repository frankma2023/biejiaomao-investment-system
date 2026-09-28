/* ====== 欧奈尔投资系统 - 主JavaScript文件 ======
 * 功能：
 * 1. 主题切换（深色/浅色）
 * 2. ECharts图表初始化
 * 3. 数据加载和渲染
 * 4. 交互功能
 */

// ====== 全局变量 ======
let currentTheme = 'dark';
let echartsInstance = null;

// ====== DOM 加载完成 ======
document.addEventListener('DOMContentLoaded', function() {
  // 逐个初始化并互相隔离：任一模块失败不能让后面的模块都不执行。
  // 原先 initCharts() 在 echarts 未加载时抛错，initTooltips / initDateRangePicker 从不运行。
  [['theme', initTheme], ['themeToggle', initThemeToggle], ['charts', initCharts],
   ['tooltips', initTooltips], ['dateRangePicker', initDateRangePicker]].forEach(function(pair){
    try { pair[1](); } catch (e) { console.error('[init] ' + pair[0] + ' 失败', e); }
  });
});

// ====== 主题功能 ======
function initTheme() {
  // 检查本地存储的主题偏好
  const savedTheme = localStorage.getItem('oinell-theme');
  if (savedTheme) {
    currentTheme = savedTheme;
  } else {
    // 检查系统偏好
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    currentTheme = prefersDark ? 'dark' : 'light';
  }
  
  // 应用主题
  applyTheme(currentTheme);
  updateThemeToggleIcon();
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  currentTheme = theme;
  localStorage.setItem('oinell-theme', theme);
  
  // 更新ECharts主题
  if (echartsInstance) {
    echartsInstance.dispose();
    initCharts();
  }
}

function initThemeToggle() {
  const toggleBtn = document.getElementById('themeToggle');
  if (!toggleBtn) return;
  
  toggleBtn.addEventListener('click', function() {
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    applyTheme(newTheme);
    updateThemeToggleIcon();
    
    // 添加动画效果
    toggleBtn.style.transform = 'rotate(180deg)';
    setTimeout(() => {
      toggleBtn.style.transform = 'rotate(0deg)';
    }, 300);
  });
}

function updateThemeToggleIcon() {
  const icon = document.querySelector('.theme-icon');
  if (!icon) return;
  
  // 更新SVG图标（这里用CSS类控制，实际项目中可以使用SVG）
  icon.textContent = currentTheme === 'dark' ? '☀️' : '🌙';
}

// ====== ECharts图表初始化 ======
function initCharts() {
  // 四张图互相隔离：任一张失败不能让其余三张都不画
  [['KLineChart', initKLineChart], ['IndustryHeatmap', initIndustryHeatmap],
   ['RSChart', initRSChart], ['VolumeChart', initVolumeChart]].forEach(function(pair){
    try { pair[1](); } catch (e) { console.error('[init] ' + pair[0] + ' 失败', e); }
  });
}

function initKLineChart() {
  const chartDom = document.getElementById('klineChart');
  if (!chartDom) return;
  
  // 销毁现有实例
  if (echartsInstance) {
    echartsInstance.dispose();
  }
  
  // 创建ECharts实例
  echartsInstance = echarts.init(chartDom, currentTheme === 'dark' ? 'dark' : 'light');
  
  // 模拟K线数据（实际项目中从API获取）
  const data = generateMockKLineData();
  
  // 计算技术指标
  const ma5 = calculateMA(data, 5);
  const ma10 = calculateMA(data, 10);
  const ma20 = calculateMA(data, 20);
  const ma50 = calculateMA(data, 50);
  const ma120 = calculateMA(data, 120);
  const ma250 = calculateMA(data, 250);
  
  // 配置项
  const option = {
    backgroundColor: 'transparent',
    animation: true,
    legend: {
      data: ['K线', 'MA5', 'MA10', 'MA20', 'MA50', 'MA120', 'MA250'],
      textStyle: {
        color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a'
      },
      top: 10
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        lineStyle: {
          color: currentTheme === 'dark' ? 'rgba(59, 130, 246, 0.5)' : 'rgba(59, 130, 246, 0.3)',
          width: 1
        },
        label: {
          backgroundColor: currentTheme === 'dark' ? '#1e293b' : '#f8fafc',
          color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a'
        }
      },
      backgroundColor: currentTheme === 'dark' ? 'rgba(30, 41, 59, 0.9)' : 'rgba(248, 250, 252, 0.9)',
      borderColor: currentTheme === 'dark' ? 'rgba(71, 85, 105, 0.5)' : 'rgba(203, 213, 225, 0.5)',
      textStyle: {
        color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a'
      },
      formatter: function(params) {
        const date = params[0].axisValue;
        const open = params[0].data[1];
        const close = params[0].data[2];
        const low = params[0].data[3];
        const high = params[0].data[4];
        const volume = params[1] ? params[1].data[1] : 0;
        
        const change = close - open;
        const changePercent = ((change / open) * 100).toFixed(2);
        
        return `
          <div style="font-weight: bold; margin-bottom: 8px;">${date}</div>
          <div>开盘: ${open.toFixed(2)}</div>
          <div>收盘: ${close.toFixed(2)} <span style="color: ${change >= 0 ? '#ef4444' : '#10b981'}">(${change >= 0 ? '+' : ''}${change.toFixed(2)}, ${changePercent}%)</span></div>
          <div>最高: ${high.toFixed(2)}</div>
          <div>最低: ${low.toFixed(2)}</div>
          <div>成交量: ${(volume / 10000).toFixed(2)}万手</div>
        `;
      }
    },
    axisPointer: {
      link: [{ xAxisIndex: 'all' }],
      label: {
        backgroundColor: '#777'
      }
    },
    grid: [
      {
        left: '10%',
        right: '8%',
        top: '15%',
        height: '60%'
      },
      {
        left: '10%',
        right: '8%',
        top: '80%',
        height: '15%'
      }
    ],
    xAxis: [
      {
        type: 'category',
        data: data.map(item => item.date),
        scale: true,
        boundaryGap: false,
        axisLine: {
          lineStyle: {
            color: currentTheme === 'dark' ? '#64748b' : '#94a3b8'
          }
        },
        axisLabel: {
          color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
        },
        splitLine: {
          show: true,
          lineStyle: {
            color: currentTheme === 'dark' ? 'rgba(100, 116, 139, 0.2)' : 'rgba(148, 163, 184, 0.2)'
          }
        },
        splitNumber: 20,
        min: 'dataMin',
        max: 'dataMax'
      },
      {
        type: 'category',
        gridIndex: 1,
        data: data.map(item => item.date),
        scale: true,
        boundaryGap: false,
        axisLine: {
          lineStyle: {
            color: currentTheme === 'dark' ? '#64748b' : '#94a3b8'
          }
        },
        axisLabel: {
          show: false
        },
        splitLine: { show: false },
        axisTick: { show: false },
        splitNumber: 20,
        min: 'dataMin',
        max: 'dataMax'
      }
    ],
    yAxis: [
      {
        scale: true,
        splitNumber: 5,
        axisLine: {
          lineStyle: {
            color: currentTheme === 'dark' ? '#64748b' : '#94a3b8'
          }
        },
        axisLabel: {
          color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
          formatter: '{value}'
        },
        splitLine: {
          show: true,
          lineStyle: {
            color: currentTheme === 'dark' ? 'rgba(100, 116, 139, 0.2)' : 'rgba(148, 163, 184, 0.2)'
          }
        },
        position: 'right'
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false }
      }
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: 70,
        end: 100,
        minValueSpan: 10
      },
      {
        show: true,
        xAxisIndex: [0, 1],
        type: 'slider',
        top: '97%',
        start: 70,
        end: 100,
        backgroundColor: currentTheme === 'dark' ? 'rgba(30, 41, 59, 0.8)' : 'rgba(248, 250, 252, 0.8)',
        borderColor: currentTheme === 'dark' ? 'rgba(71, 85, 105, 0.5)' : 'rgba(203, 213, 225, 0.5)',
        textStyle: {
          color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
        },
        handleStyle: {
          color: currentTheme === 'dark' ? 'rgba(59, 130, 246, 0.5)' : 'rgba(59, 130, 246, 0.3)'
        }
      }
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        data: data.map(item => [item.open, item.close, item.low, item.high]),
        itemStyle: {
          color: currentTheme === 'dark' ? '#ef4444' : '#dc2626', // 涨 - 红色
          color0: currentTheme === 'dark' ? '#10b981' : '#059669', // 跌 - 绿色
          borderColor: null,
          borderColor0: null
        },
        tooltip: {
          formatter: function(param) {
            return [
              '日期: ' + param.name,
              '开盘: ' + param.data[0],
              '收盘: ' + param.data[1],
              '最低: ' + param.data[2],
              '最高: ' + param.data[3]
            ].join('<br>');
          }
        }
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: data.map(item => ({
          value: item.volume,
          itemStyle: {
            color: item.close >= item.open ? 
              (currentTheme === 'dark' ? 'rgba(239, 68, 68, 0.7)' : 'rgba(220, 38, 38, 0.7)') : 
              (currentTheme === 'dark' ? 'rgba(16, 185, 129, 0.7)' : 'rgba(5, 150, 105, 0.7)')
          }
        })),
        barWidth: '60%'
      },
      {
        name: 'MA5',
        type: 'line',
        data: ma5,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#00d992' // 翠绿色
        },
        symbol: 'none'
      },
      {
        name: 'MA10',
        type: 'line',
        data: ma10,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#3b82f6' // 蓝色
        },
        symbol: 'none'
      },
      {
        name: 'MA20',
        type: 'line',
        data: ma20,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#8b5cf6' // 紫色
        },
        symbol: 'none'
      },
      {
        name: 'MA50',
        type: 'line',
        data: ma50,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#f59e0b' // 橙色
        },
        symbol: 'none'
      },
      {
        name: 'MA120',
        type: 'line',
        data: ma120,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#ec4899' // 粉色
        },
        symbol: 'none'
      },
      {
        name: 'MA250',
        type: 'line',
        data: ma250,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#6b7280' // 灰色
        },
        symbol: 'none'
      }
    ]
  };
  
  // 应用配置
  echartsInstance.setOption(option);
  
  // 窗口大小变化时重绘
  window.addEventListener('resize', function() {
    echartsInstance.resize();
  });
}

function initIndustryHeatmap() {
  const chartDom = document.getElementById('industryHeatmap');
  if (!chartDom) return;
  
  const chart = echarts.init(chartDom, currentTheme === 'dark' ? 'dark' : 'light');
  
  // 模拟行业数据
  const industries = [
    '电子', '食品饮料', '医药生物', '计算机', '电力设备', '机械设备',
    '国防军工', '化工', '汽车', '通信', '有色金属', '银行',
    '非银金融', '房地产', '建筑材料', '建筑装饰', '家用电器', '农林牧渔',
    '轻工制造', '商贸零售', '社会服务', '交通运输', '公用事业', '环保'
  ];
  
  const data = industries.map((industry, index) => {
    const rs = Math.random() * 100; // 相对强度
    const score = 50 + Math.random() * 50; // 综合得分
    const trend = Math.random() > 0.5 ? 'up' : 'down'; // 趋势
    
    return {
      name: industry,
      value: [index % 6, Math.floor(index / 6), rs, score, trend]
    };
  });
  
  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      formatter: function(params) {
        return `
          <strong>${params.data.name}</strong><br/>
          相对强度: ${params.data.value[2].toFixed(1)}<br/>
          综合得分: ${params.data.value[3].toFixed(1)}<br/>
          趋势: <span style="color: ${params.data.value[4] === 'up' ? '#ef4444' : '#10b981'}">${params.data.value[4] === 'up' ? '上涨' : '下跌'}</span>
        `;
      },
      backgroundColor: currentTheme === 'dark' ? 'rgba(30, 41, 59, 0.9)' : 'rgba(248, 250, 252, 0.9)',
      textStyle: {
        color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a'
      }
    },
    grid: {
      top: '10%',
      left: '10%',
      right: '10%',
      bottom: '15%'
    },
    xAxis: {
      type: 'category',
      data: ['第1列', '第2列', '第3列', '第4列', '第5列', '第6列'],
      splitArea: {
        show: true
      },
      axisLabel: {
        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
      }
    },
    yAxis: {
      type: 'category',
      data: ['第1行', '第2行', '第3行', '第4行'],
      splitArea: {
        show: true
      },
      axisLabel: {
        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
      }
    },
    visualMap: {
      min: 0,
      max: 100,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      textStyle: {
        color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a'
      },
      inRange: {
        color: currentTheme === 'dark' ? 
          ['#0f172a', '#1e293b', '#334155', '#475569', '#64748b', '#94a3b8', '#cbd5e1'] :
          ['#f8fafc', '#f1f5f9', '#e2e8f0', '#cbd5e1', '#94a3b8', '#64748b', '#475569']
      }
    },
    series: [{
      name: '行业强度',
      type: 'heatmap',
      data: data,
      label: {
        show: true,
        color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a',
        formatter: function(params) {
          return params.data.name;
        }
      },
      emphasis: {
        itemStyle: {
          shadowBlur: 10,
          shadowColor: 'rgba(0, 0, 0, 0.5)'
        }
      }
    }]
  };
  
  chart.setOption(option);
  
  window.addEventListener('resize', function() {
    chart.resize();
  });
}

function initRSChart() {
  const chartDom = document.getElementById('rsChart');
  if (!chartDom) return;
  
  const chart = echarts.init(chartDom, currentTheme === 'dark' ? 'dark' : 'light');
  
  // 模拟相对强度数据
  const dates = [];
  for (let i = 30; i >= 0; i--) {
    const date = new Date();
    date.setDate(date.getDate() - i);
    dates.push(date.toISOString().split('T')[0]);
  }
  
  const rs20 = dates.map(() => 50 + Math.random() * 50 - 25);
  const rs120 = dates.map(() => 50 + Math.random() * 40 - 20);
  const rs250 = dates.map(() => 50 + Math.random() * 30 - 15);
  
  const option = {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: currentTheme === 'dark' ? 'rgba(30, 41, 59, 0.9)' : 'rgba(248, 250, 252, 0.9)',
      textStyle: {
        color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a'
      }
    },
    legend: {
      data: ['RS20', 'RS120', 'RS250'],
      textStyle: {
        color: currentTheme === 'dark' ? '#f1f5f9' : '#0f172a'
      },
      top: 10
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      top: '15%',
      containLabel: true
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates,
      axisLabel: {
        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b'
      },
      axisLine: {
        lineStyle: {
          color: currentTheme === 'dark' ? '#64748b' : '#94a3b8'
        }
      }
    },
    yAxis: {
      type: 'value',
      axisLabel: {
        color: currentTheme === 'dark' ? '#94a3b8' : '#64748b',
        formatter: '{value}'
      },
      axisLine: {
        lineStyle: {
          color: currentTheme === 'dark' ? '#64748b' : '#94a3b8'
        }
      },
      splitLine: {
        lineStyle: {
          color: currentTheme === 'dark' ? 'rgba(100, 116, 139, 0.2)' : 'rgba(148, 163, 184, 0.2)'
        }
      }
    },
    series: [
      {
        name: 'RS20',
        type: 'line',
        data: rs20,
        smooth: true,
        lineStyle: {
          width: 3,
          color: '#00d992'
        },
        symbol: 'none',
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(0, 217, 146, 0.3)' },
            { offset: 1, color: 'rgba(0, 217, 146, 0.05)' }
          ])
        }
      },
      {
        name: 'RS120',
        type: 'line',
        data: rs120,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#3b82f6'
        },
        symbol: 'none'
      },
      {
        name: 'RS250',
        type: 'line',
        data: rs250,
        smooth: true,
        lineStyle: {
          width: 2,
          color: '#8b5cf6'
        },
        symbol: 'none'
      }
    ]
  };
  
  chart.setOption(option);
  
  window.addEventListener('resize', function() {
    chart.resize();
  });
}

function initVolumeChart() {
  // 成交量图表已集成在K线图中
}

// ====== 工具函数 ======
function generateMockKLineData(count = 100) {
  const data = [];
  let basePrice = 100;
  const baseDate = new Date('2024-01-01');
  
  for (let i = 0; i < count; i++) {
    const date = new Date(baseDate);
    date.setDate(baseDate.getDate() + i);
    
    const open = basePrice;
    const change = (Math.random() - 0.5) * 10;
    const close = open + change;
    const high = Math.max(open, close) + Math.random() * 5;
    const low = Math.min(open, close) - Math.random() * 5;
    const volume = Math.random() * 1000000 + 500000;
    
    data.push({
      date: date.toISOString().split('T')[0],
      open: parseFloat(open.toFixed(2)),
      close: parseFloat(close.toFixed(2)),
      high: parseFloat(high.toFixed(2)),
      low: parseFloat(low.toFixed(2)),
      volume: parseInt(volume)
    });
    
    basePrice = close;
  }
  
  return data;
}

function calculateMA(data, dayCount) {
  const result = [];
  for (let i = 0; i < data.length; i++) {
    if (i < dayCount - 1) {
      result.push('-');
      continue;
    }
    
    let sum = 0;
    for (let j = 0; j < dayCount; j++) {
      sum += data[i - j].close;
    }
    result.push(parseFloat((sum / dayCount).toFixed(2)));
  }
  return result;
}

function initTooltips() {
  const tooltipTriggers = document.querySelectorAll('[data-tooltip]');
  
  tooltipTriggers.forEach(trigger => {
    const tooltipText = trigger.getAttribute('data-tooltip');
    const tooltip = document.createElement('div');
    tooltip.className = 'tooltip';
    tooltip.textContent = tooltipText;
    document.body.appendChild(tooltip);
    
    trigger.addEventListener('mouseenter', function(e) {
      const rect = trigger.getBoundingClientRect();
      tooltip.style.left = rect.left + rect.width / 2 + 'px';
      tooltip.style.top = rect.top - 40 + 'px';
      tooltip.classList.add('show');
    });
    
    trigger.addEventListener('mouseleave', function() {
      tooltip.classList.remove('show');
    });
  });
}

function initDateRangePicker() {
  const picker = document.getElementById('dateRangePicker');
  if (!picker) return;
  
  // 设置默认日期范围（最近30天）
  const endDate = new Date();
  const startDate = new Date();
  startDate.setDate(startDate.getDate() - 30);
  
  picker.value = `${startDate.toISOString().split('T')[0]} 至 ${endDate.toISOString().split('T')[0]}`;
  
  picker.addEventListener('click', function() {
    // 实际项目中这里会打开一个日期选择器
    alert('日期范围选择器（实际项目中会集成第三方库）');
  });
}

// ====== 数据加载函数 ======
async function loadMarketData() {
  try {
    // 显示加载状态
    showLoading();
    
    // 实际项目中这里会调用API
    // const response = await fetch('/api/market/scan');
    // const data = await response.json();
    
    // 模拟API延迟
    await new Promise(resolve => setTimeout(resolve, 1000));
    
    // 更新图表
    if (echartsInstance) {
      echartsInstance.dispose();
      initCharts();
    }
    
    // 隐藏加载状态
    hideLoading();
    
    // 显示成功消息
    showToast('市场数据加载成功', 'success');
  } catch (error) {
    console.error('加载市场数据失败:', error);
    showToast('加载失败，请重试', 'error');
    hideLoading();
  }
}

function showLoading() {
  const loadingEl = document.getElementById('loadingOverlay');
  if (loadingEl) {
    loadingEl.style.display = 'flex';
  }
}

function hideLoading() {
  const loadingEl = document.getElementById('loadingOverlay');
  if (loadingEl) {
    loadingEl.style.display = 'none';
  }
}

function showToast(message, type = 'info') {
  // 创建Toast元素
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  
  // 添加到页面
  document.body.appendChild(toast);
  
  // 显示动画
  setTimeout(() => {
    toast.classList.add('show');
  }, 10);
  
  // 3秒后移除
  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => {
      document.body.removeChild(toast);
    }, 300);
  }, 3000);
}

// ====== 导出函数供HTML调用 ======
window.OinellApp = {
  loadMarketData,
  switchTheme: function() {
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    applyTheme(newTheme);
    updateThemeToggleIcon();
  },
  refreshCharts: function() {
    if (echartsInstance) {
      echartsInstance.dispose();
    }
    initCharts();
    showToast('图表已刷新', 'success');
  }
};