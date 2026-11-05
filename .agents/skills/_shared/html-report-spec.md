# 报告 HTML 格式规范（公用组件）

**本文件是投研类 HTML 报告的唯一格式基准。** 所有生成报告的技能都引用本文件，
不各自复写样式。改动只改这里。

适用技能：`index-research`、`stock-health-report`、`market-logic`、`hanako-report`（呈现层）。

---

## 1. 交付形态

| 要求 | 规定 |
|---|---|
| 形态 | **单文件 HTML**，离线双击可开 |
| 外链 CSS | **禁止**。令牌与组件样式全部内联进 `<style>` |
| 外链 JS | **禁止**。交互脚本内联进 `<script>` |
| 字体 | **禁止 Google Fonts**。走系统栈（见 §4） |
| 图表库 | **禁止**。用内联 SVG 或原生 JS 绘制，不引 ECharts/Chart.js |
| 体积 | 单文件建议 ≤ 300 KB。折线点超过 1,100 个必须降采样 |
| 移动端 | 必须 `viewport` meta；表格容器 `overflow-x:auto`，页面不得横向溢出 |

> 例外：`stock-health-report` 的「个股体检」允许外链 `web/shared/css/hanako-glass.css`
> 并使用深色玻璃拟态皮肤；但**其报告正文的排版规则（§3、§4）不变**。

---

## 2. 主题

**默认浅色，写死在 `<html>` 上。**

```html
<html lang="zh-CN" data-theme="light">
```

- 深色作为 `html[data-theme="dark"]` 变体保留
- 右上角提供 `◐` 切换，用 `localStorage` 记忆（键名各报告自定）
- 投研报告在浅色下可读性更好；hanako-glass 的深色是它的默认，不是本规范的默认

```css
:root{--bg:#f7f7f9;--card:#fff;--line:#e4e4ea;--tx:#1c1c22;--mut:#6b6b76;
--rise:#10b981;--fall:#ef4444;--accent:#b45309;--blue:#2563eb;
--warnbg:#fff8eb;--warnbd:#f0c36d}
html[data-theme="dark"]{--bg:#0e0e12;--card:#16161d;--line:#2a2a33;--tx:#e9e9ec;
--mut:#8f8f97;--accent:#f59e0b;--blue:#539BF5;--warnbg:#241d10;--warnbd:#6b5320}
body{margin:0;background:var(--bg);color:var(--tx);font-size:15px;line-height:1.72;
font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
```

---

## 3. 标题与排版

```css
h1,h2,h3,h4{font-family:'Noto Serif SC','Source Han Serif SC','Songti SC',
Georgia,serif;font-weight:900;letter-spacing:.005em}
h1{font-size:27px;line-height:1.3;margin:6px 0 8px}
h2{font-size:19px;margin:32px 0 10px;padding-left:11px;border-left:4px solid var(--accent)}
```

- **必须衬线 + 900 字重**
- **交付前截图确认标题确实呈现为衬线**：`font-weight` 生效 ≠ 字体正确
- 正文行高 1.72；段落 `margin:9px 0`
- 页宽 1000~1040px，居中，底部留白 ≥ 60px

---

## 4. 表格三件套（硬性）

```css
th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
th{background:rgba(0,0,0,.03);font-size:12.5px;color:var(--mut);font-weight:600}
html[data-theme="dark"] th{background:rgba(255,255,255,.04)}
td:first-child,th:first-child{text-align:left;padding-left:14px}      /* ① 首列左对齐 */
td:last-child,th:last-child{padding-right:14px;padding-left:26px;
text-align:center}                                                     /* ② 末列居中 */
td.num:last-child,th.num:last-child{text-align:right}
table.wide td:last-child,table.wide th:last-child{
text-align:left!important;padding-left:10px}                           /* ③ 两列表加 .wide */
.num{font-family:ui-monospace,Menlo,monospace;text-align:right;
font-variant-numeric:tabular-nums}
.tw{overflow-x:auto;margin:10px 0}
```

**用法**：
- 两列的「键值表」或「说明表」→ `<table class="wide">`，否则末列会被居中
- 数值列 → `<td class="num">`，右对齐 + 等宽数字
- 表格必须包在 `<div class="tw">` 里，防横向溢出撑破页面

---

## 5. 涨跌语义类（红涨绿跌）

```css
.rise{color:#10b981}   /* 涨 / 正向 */
.fall{color:#ef4444}   /* 跌 / 风险 */
```

**只用于「价格变动」与「买卖票标」。** 评分、比率、估值差、分位一律用中性色——
这些没有方向，着色会被误读。

---

## 6. 组件约定

| 组件 | 类名 | 用途 |
|---|---|---|
| 结论块 | `.lead` / `.ess` | 一句话结论、核心判断，左边框 4px |
| 数据卡 | `.kpi > div`（内含 `.k` `.v`） | 关键指标，等宽数字 |
| 提示框 | `.warn` | 口径限制、需注意的前提 |
| 警示框 | `.danger` | 证伪、反例、重大风险 |
| 状态徽标 | `.done` + `.ok/.lag/.no` | 成立 / 滞后 / 不成立 |
| 顶栏 | `.top`（sticky） | 标题 + 数据截止日 + 主题切换 + 返回链接 |
| 页脚 | `.ft` | 生成器、数据源、免责声明 |

---

## 7. 内容纪律

1. **数字必须由脚本现算**，不得手工誊写；同一份 HTML 内数字口径一致。
2. **图表必须标口径**：图下注明「归一化起点」「样本区间」「是否含分红」。
3. **缺失项必须显式声明**，用 `.warn` 或写入缺口表；禁止留空表格不解释。
4. **合规**：末尾必备免责声明；不写买卖建议、目标价、仓位建议。
5. **只改生成器，不直接编辑 HTML**。HTML 是产物。

---

## 8. 交付前自检（六项）

- [ ] `data-theme="light"` 写死在 `<html>`
- [ ] 标题截图确认是衬线字体、900 字重
- [ ] 表格首列左对齐、末列居中、两列表已加 `.wide`
- [ ] 单文件、无外链 CSS/JS、无 Google Fonts、无图表库
- [ ] 页面无横向溢出（`scrollWidth <= innerWidth`）
- [ ] 控制台 0 错误、0 个 4xx 请求

---

## 9. 已落地的实现（可直接抄）

| 报告 | 生成器 | 备注 |
|---|---|---|
| 国证自由现金流研究报告 | `docs/report/make_fcf100_report.py` | 内联 SVG 折线/柱状，含降采样 |
| 市场总结（每日） | `scripts/gen_market_logic_report.py` | 轻量版，7 章 |
| 个股体检报告 | `web/stock-health/reports/` | 例外：外链 hanako-glass + 深色皮肤 |
