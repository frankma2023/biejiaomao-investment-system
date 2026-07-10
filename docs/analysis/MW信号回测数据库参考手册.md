# MW 信号回测 · 数据库参考手册

> 面向：不熟悉本项目的回测分析者 / AI 模型  
> 目的：理解 MW B1 信号的定义、数据存储、以及如何进行回测分析  
> 数据库：SQLite，`data/lixinger.db`，WAL 模式  
> 数据范围：2016-01-01 ~ 2026-07-07，全部 A 股（含已退市）  
> 价格基准：前复权

---

## 一、MW 信号是什么

MW 信号识别的是"A 股强势股在牛市中回调后再次启动"的形态。结构如下：

```
前高(H) ──→ 回调到最低点(L) ──→ 横盘整理(C) ──→ 首次突破(B1) ──→ 二次确认(B2)
```

- **H（前高）**：缠论笔顶，之前有 ≥20% 的涨幅
- **L（最低点）**：H 之后缠论笔底，距 H 至少 10% 的回调
- **C（横盘区）**：L 之后振幅 <10% 的窄幅整理期
- **B1（突破日 1）**：放量突破 C 区间高点，涨幅 ≥2%，站上 MA5/MA10 且 MA5>MA10
- **B2（突破日 2）**：B1 之后 30 天内出现的二次确认，涨幅 ≥3%，站上 ≥4 条均线，含 MA60

**核心认知**：B1 是"突破尝试"，不是"买入信号"。B2 是"突破确认"，B2 出现后再买胜率 60%+。

---

## 二、核心数据表

### 2.1 mw_signal_daily — MW 信号主表

**每行 = 一个 B1 信号。B2 信息作为同一行的附加字段**。

| 字段 | 类型 | 含义 | 示例 |
|------|------|------|------|
| `stock_code` | TEXT | 股票代码（PK） | `300750` |
| `b1_date` | TEXT | B1 突破日（PK） | `2026-07-06` |
| `stock_name` | TEXT | 股票名称 | `宁德时代` |
| `b2_date` | TEXT | B2 确认日（可为 NULL） | `2026-07-10` |
| `h_date` | TEXT | H 前高日期 | `2026-04-17` |
| `h_price` | REAL | H 前高价格 | `40.28` |
| `l_date` | TEXT | L 最低点日期 | `2026-05-20` |
| `l_price` | REAL | L 最低点价格 | `30.15` |
| `c_start` | TEXT | C 横盘开始日 | `2026-05-20` |
| `c_end` | TEXT | C 横盘结束日 | `2026-06-15` |
| `b1_return_pct` | REAL | B1 日涨幅（%） | `5.8` |
| `b1_vol_ratio` | REAL | B1 日量比（vs 20日均量） | `1.78` |
| `decline_pct` | REAL | H→L 回调深度（%） | `25.1` |
| `c_amplitude_pct` | REAL | C 横盘振幅（%） | `7.3` |
| `c_amount_avg` | REAL | C 横盘日均成交额（元） | `150000000` |
| `h_rs250` | INTEGER | H 点的个股 RS250（0-99） | `85` |
| `h_rs20` | INTEGER | H 点的个股 RS20（0-99） | `72` |
| `ind_rs250` | INTEGER | 行业的 RS250（可为 NULL） | `92` |
| `ind_code` | TEXT | 行业指数代码（L2 优先） | `H30184` |
| `ind_name` | TEXT | 行业名称 | `半导体` |
| `score` | INTEGER | HDC v4.0 总分（0-100） | `70` |
| `confidence` | TEXT | 置信度：`高`/`中`/`低` | `高` |
| `score_h` | INTEGER | H 子项：前高趋势（0/15） | `15` |
| `score_d` | INTEGER | D 子项：调整深度（0/5/15/25） | `25` |
| `score_c` | INTEGER | C 子项：横盘质量（v4.0 已删除，始终为 0） | `0` |
| `score_p` | INTEGER | P 子项：整理回撤（0/15，仅 B2 时计算） | `15` |
| `score_i1` | INTEGER | I1 子项：行业 RS（0/10/20） | `20` |
| `score_i2` | INTEGER | I2 子项：个股 RS（0/10/20/30） | `30` |
| `score_sig` | INTEGER | Sig 子项：信号共振（0-10） | `5` |
| `score_gap` | INTEGER | Gap 子项：跳空（0/10，仅 B2 时） | `0` |
| `tech_score` | INTEGER | 技术置信度（9 因子，0-100，独立体系） | `77` |
| `tech_score_detail` | TEXT | 技术置信度 9 因子明细（JSON） | `{"ma20":12,...}` |
| `is_plus` | INTEGER | PLUS 标志：总分≥80 且 D=25 且 I1=20 | `1` |
| `scan_date` | TEXT | 最后扫描日期 | `2026-07-06` |

**索引**：`(b1_date)`, `(stock_code)`, `(b2_date)`  
**数据量**：~103,000 行  
**唯一键**：`(stock_code, b1_date)`  
**B2 确认判断**：`b2_date IS NOT NULL AND b2_date > b1_date`

---

### 2.2 backtest_results — 回测结果表

**每行 = 一笔回测交易**。每个 B1 信号按 3 种入场方式 × 4 个持有期展开为最多 12 行。

| 字段 | 类型 | 含义 |
|------|------|------|
| `stock_code` | TEXT | 股票代码 |
| `signal_date` | TEXT | 信号日期（B1 日） |
| `signal_mask` | INTEGER | 信号位掩码：bit0=MW_B1, bit1=MW_B2, bit2=PLUS, bit3=PP_V1, bit4=PP_V2, bit5=BO_V2 |
| `combo_label` | TEXT | 信号组合标签，如 `MW_B1` |
| `entry_method` | TEXT | 入场方式：`T+0_C`(当日收盘) / `T+1_O`(次日开盘) / `T+2_O`(第3日开盘) |
| `hold_days` | INTEGER | 持有天数：5 / 10 / 20 / 60 |
| `market_regime` | TEXT | 市场环境：`bull` / `bear` / `ranging` |
| `entry_price` | REAL | 入场价格 |
| `exit_price` | REAL | 出场价格 |
| `net_ret_pct` | REAL | 净收益（%，已扣除 0.3% 交易成本） |
| `ret_pct` | REAL | 毛收益（%） |
| `is_win` | INTEGER | 是否盈利：1/0 |
| `peak_ret_pct` | REAL | 持有期内最大浮盈 |
| `trough_ret_pct` | REAL | 持有期内最大浮亏 |
| `index_ret_pct` | REAL | 同期中证全指（000985）收益 |
| `excess_ret_pct` | REAL | 超额收益 = net_ret - index_ret |
| `pool_mode` | TEXT | 股票池模式：`full` 或 `filtered` |

**索引**：`(signal_mask, entry_method, hold_days, signal_date)`  
**数据量**：~9,500,000 行  
**唯一键**：`(stock_code, signal_date, entry_method, hold_days, pool_mode)`

**常用查询**：MW B1 的 H20/T+1_O 交易：
```sql
SELECT * FROM backtest_results 
WHERE signal_mask & 1 = 1           -- MW_B1 bit
  AND entry_method = 'T+1_O' 
  AND hold_days = 20
```

---

### 2.3 signal_events — 信号事件汇总表

**每行 = 一个交易日的所有信号聚合**。用于回测引擎快速遍历。

| 字段 | 类型 | 含义 |
|------|------|------|
| `stock_code` | TEXT | 股票代码（PK） |
| `date` | TEXT | 信号日期（PK） |
| `signal_mask` | INTEGER | 位掩码（同 backtest_results） |
| `combo_label` | TEXT | 如 `MW_B1+PP_V1` |
| `signal_count` | INTEGER | 当天该股票触发的信号数 |
| `mw_b1_decline_pct` | REAL | MW B1 的调整深度 |
| `mw_b1_h_rs250` | INTEGER | MW B1 的 H 点 RS250 |
| `pp_v1_vol_ratio` | REAL | PP_V1 的量比 |
| … | | 其他信号的因子值 |

**数据量**：~841,000 行  
**按天筛选 MW B1**：`WHERE signal_mask & 1 = 1`

---

### 2.4 daily_kline — 个股日 K 线

| 字段 | 类型 | 含义 |
|------|------|------|
| `stock_code` | TEXT | 股票代码（PK） |
| `date` | TEXT | 交易日期（PK） |
| `open` / `close` / `high` / `low` | REAL | 开/收/高/低价（前复权） |
| `volume` | INTEGER | 成交量（股） |
| `amount` | REAL | 成交额（元） |

**数据量**：~1,800 万行  
**索引**：`(date)`, `(stock_code)`

**按 B1 日查收盘价**：
```sql
SELECT close FROM daily_kline 
WHERE stock_code = '300750' AND date = '2026-07-06'
```

---

### 2.5 stock_rs_daily — 个股 RS 强度

| 字段 | 类型 | 含义 |
|------|------|------|
| `stock_code` | TEXT | 股票代码（PK） |
| `date` | TEXT | 日期（PK） |
| `rps_20` | INTEGER | RPS20（0-99） |
| `rps_250` | INTEGER | RPS250（0-99） |

**数据量**：~1,200 万行  
**按 H 日期查 RS**：
```sql
SELECT rps_250 FROM stock_rs_daily 
WHERE stock_code = '300750' AND date <= '2026-04-17' 
ORDER BY date DESC LIMIT 1
```

---

### 2.6 stock_equity_change — 股本变动历史

| 字段 | 类型 | 含义 |
|------|------|------|
| `stock_code` | TEXT | 股票代码 |
| `change_date` | TEXT | 变动日期 |
| `outstanding_shares_a` | REAL | 流通A股（股） |
| `capitalization` | REAL | 总股本（股） |
| `change_reason` | TEXT | 变动原因 |

**数据量**：~156,000 行（5,388 只股票）  
**按日期查流通股本**：
```sql
SELECT outstanding_shares_a FROM stock_equity_change 
WHERE stock_code = '300750' AND change_date <= '2026-07-06' 
ORDER BY change_date DESC LIMIT 1
```

---

### 2.7 index_daily_kline — 指数日 K 线

| 字段 | 类型 | 含义 |
|------|------|------|
| `stock_code` | TEXT | 指数代码（如 `000985`=中证全指） |
| `date` | TEXT | 日期 |
| `close` | REAL | 收盘价 |

**市场环境分类**（用于 `market_regime` 字段）：
- **bull（牛市）**：MA50 斜率 > +0.5%（20日）且价格在 MA200 上方
- **bear（熊市）**：MA50 斜率 < -0.5%（20日）且价格在 MA200 下方
- **ranging（震荡市）**：其余情况

---

### 2.8 其他相关表

| 表名 | 用途 |
|------|------|
| `stock_basic` | 股票名称、上市状态（排除 ST） |
| `pattern_scan_signals` | B1/B2 日的共振信号详情（PP_V1/BO_V2/缠论背驰等） |
| `pocket_pivot_daily` | PP_V1/V2 信号日期（用于共现分析） |
| `market_breakout_v2_daily` | BO_V2 信号日期 |
| `index_rs_daily` | 行业指数的 RS 强度 |
| `index_constituents` | 指数成分股关系 |

---

## 三、评分体系（HDC v4.0）

MW B1 信号的形态质量由 5 个维度评分，满分 100。**B1-only 和 B1+B2 使用统一满分**。

### 硬门禁

**前高 RS250 ≥ 60**。不满足则不出 B1 信号。`h_rs250` 字段直接取自 `stock_rs_daily`。

### 评分项

| 子项 | 满分 | 评分规则 |
|------|:---:|------|
| **I2 个股RS** | 30 | `h_rs250 ≥ 90`(30) / `≥ 85`(20) / `≥ 75`(10) / `< 75`(0) |
| **D 调整深度** | 25 | 跌幅 `25%~40%`(25) / `20%~25%`(15) / `15%~20%`(5) |
| **I1 行业RS** | 20 | `ind_rs250 ≥ 85`(20) / `≥ 80`(10) |
| **H 前高趋势** | 15 | H 点 SMA50 斜率 > 0 且价格在 MA200 上方 |
| **Sig 信号共振** | 10 | PP_V1(+5) + BO_V2(+3) + 缠论背驰(+2) + 蜡烛形态(+1)，上限 10 |

### 置信度

| 标签 | 分数 | 含义 |
|:---:|:---:|------|
| 高 | ≥ 70 | 形态优秀 |
| 中 | 50 ~ 69 | 形态一般 |
| 低 | < 50 | 形态较差 |

### 已删除

- **C（横盘质量）**：v4.0 删除。全周期与收益负相关（r=-0.036），C=5 分的信号反而比 C=0 的更差。

### PLUS 标志

`is_plus = 1`：总分 ≥ 80 且 D=25（满分）且 I1=20（满分）。仅 B1+B2 完整信号中才可能出现。

---

## 四、关键数据关系

### 计算换手率

```
换手率(%) = mw_signal_daily.c_amount_avg 
          / (stock_equity_change.outstanding_shares_a × daily_kline.close) 
          × 100
```

其中：
- `c_amount_avg`：横盘期日均成交额（元）
- `outstanding_shares_a`：B1 日之前最近一次股本变动中的流通A股
- `close`：B1 日的收盘价

### B2 确认判断

```sql
b2_date IS NOT NULL AND b2_date > b1_date
```

### MW B1 + 回测结果 JOIN

```sql
SELECT m.stock_code, m.stock_name, m.b1_date,
       m.h_rs250, m.decline_pct, m.c_amount_avg,
       b.net_ret_pct, b.is_win, b.market_regime
FROM mw_signal_daily m
JOIN backtest_results b 
  ON b.stock_code = m.stock_code 
 AND b.signal_date = m.b1_date
WHERE b.entry_method = 'T+1_O' 
  AND b.hold_days = 20 
  AND b.signal_mask & 1 = 1
  AND m.b1_date >= '2016-01-01'
  AND m.stock_code != '_sentinel_'
```

---

## 五、常用 SQL 模板

### 1. 查某日所有 B1 信号及收益

```sql
SELECT m.stock_code, m.stock_name, m.b1_date, m.confidence, m.score,
       b.net_ret_pct, b.is_win
FROM mw_signal_daily m
JOIN backtest_results b ON b.stock_code=m.stock_code AND b.signal_date=m.b1_date
WHERE m.b1_date = '2026-07-06'
  AND b.entry_method='T+1_O' AND b.hold_days=20 AND b.signal_mask & 1 = 1
```

### 2. 按因子分层统计胜率

```sql
SELECT 
  CASE WHEN h_rs250 >= 80 THEN 'RS≥80'
       WHEN h_rs250 >= 60 THEN 'RS60-79'
       ELSE 'RS<60' END AS rs_group,
  COUNT(*) n,
  AVG(b.is_win) * 100 AS win_rate,
  AVG(b.net_ret_pct) AS avg_return
FROM mw_signal_daily m
JOIN backtest_results b ON b.stock_code=m.stock_code AND b.signal_date=m.b1_date
WHERE b.entry_method='T+1_O' AND b.hold_days=20 AND b.signal_mask & 1 = 1
GROUP BY rs_group
ORDER BY win_rate DESC
```

### 3. 按年统计胜率

```sql
SELECT SUBSTR(m.b1_date, 1, 4) AS year,
       COUNT(*) n, AVG(b.is_win)*100 AS win_rate, AVG(b.net_ret_pct) AS avg_return
FROM mw_signal_daily m
JOIN backtest_results b ON b.stock_code=m.stock_code AND b.signal_date=m.b1_date
WHERE b.entry_method='T+1_O' AND b.hold_days=20 AND b.signal_mask & 1 = 1
GROUP BY year ORDER BY year
```

### 4. 检查 B2 确认的收益影响

```sql
SELECT 
  CASE WHEN m.b2_date IS NOT NULL AND m.b2_date > m.b1_date THEN 'B2确认' ELSE 'B1-only' END AS b2_status,
  COUNT(*) n, AVG(b.is_win)*100 AS win_rate, AVG(b.net_ret_pct) AS avg_return
FROM mw_signal_daily m
JOIN backtest_results b ON b.stock_code=m.stock_code AND b.signal_date=m.b1_date
WHERE b.entry_method='T+1_O' AND b.hold_days=20 AND b.signal_mask & 1 = 1
GROUP BY b2_status
```

---

## 六、注意事项

1. **`_sentinel_`**：`mw_signal_daily` 中有一条 `stock_code='_sentinel_'` 的哨兵行，所有查询必须排除：`AND stock_code != '_sentinel_'`
2. **退市股**：`daily_kline` 包含已退市股票的历史数据，回测时无需单独处理
3. **前复权**：`daily_kline` 的 OHLC 已是前复权价格
4. **交易成本**：`backtest_results.net_ret_pct` 已扣除 0.3% 成本（买入 0.125% + 卖出 0.175%）
5. **h_rs250 的 NULL**：部分早期信号（2018-2019 年）的 `h_rs250` 为 NULL，因为 `stock_rs_daily` 表可能缺少对应日期的 RS 数据
6. **tech_score 的 0**：相当多历史信号的 `tech_score=0`，因为 tech_score 是 v2.x 才加入的，早期扫描未计算
7. **score_I2 高分稀疏**：`score_i2` 的 20 分和 30 分几乎为空，因为需要 `h_rs250 ≥ 85`，同时满足此条件的 B1 极少
8. **信号位掩码**：MW_B1=`bit0`(值1), MW_B2=`bit1`(值2), PLUS=`bit2`(值4)，用 `signal_mask & N = N` 检测
