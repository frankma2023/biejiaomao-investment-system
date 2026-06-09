# 数据库表结构

> 引擎：SQLite，WAL 模式，PRAGMA synchronous=NORMAL  
> 数据库文件：`data/lixinger.db`  
> 更新时间：2026-06-09

---

## 1. stock_basic — 股票基础信息

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK**，股票代码，如 `300750` |
| name | TEXT | 股票名称 |
| market | TEXT | 市场，`a`=A股 |
| exchange | TEXT | 交易所，`sh`/`sz`/`bj` |
| area_code | TEXT | 地区代码 |
| listing_status | TEXT | 上市状态，`normally_listed`/`delisted`/... |
| ipo_date | TEXT | 上市日期 |
| delisted_date | TEXT | 退市日期（可空） |
| fs_table_type | TEXT | 财报类型，`non_financial`/`bank`/`insurance`/... |
| mutual_market_flag | INTEGER | 是否陆股通标的，0/1 |
| updated_at | TEXT | 更新时间 |

**数据量**：~5595 条  
**数据来源**：理杏仁 API #1 股票信息  
**写入脚本**：`src/data/lixr_api/api_stock_company.py`  
**读取场景**：获取股票名称、上市天数筛选、排除ST

---

## 2. daily_kline — 个股日K线

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| date | TEXT | **PK(2)**，交易日期 YYYY-MM-DD |
| open | REAL | 开盘价 |
| close | REAL | 收盘价 |
| high | REAL | 最高价 |
| low | REAL | 最低价 |
| volume | INTEGER | 成交量 |
| amount | REAL | 成交额 |
| change_pct | REAL | 涨跌幅(%) |
| turnover_rate | REAL | 换手率 |
| complex_factor | REAL | 复权因子 |

**数据量**：~1794万条  
**索引**：`idx_daily_kline_date(date)`, `idx_daily_kline_stock(stock_code)`  
**数据来源**：理杏仁 API #4 K线数据  
**写入脚本**：`scripts/fetch_stock_daily_kline.py`  
**读取场景**：RS计算、技术指标计算、内部共振验证

---

## 3. weekly_kline — 个股周K线

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| week_start_date | TEXT | **PK(2)**，该周第一个交易日 YYYY-MM-DD |
| week_end_date | TEXT | 该周最后一个交易日 |
| year_week | TEXT | 年周标识 YYYY-WW |
| open | REAL | 周开盘价 |
| close | REAL | 周收盘价 |
| high | REAL | 周最高价 |
| low | REAL | 周最低价 |
| volume | INTEGER | 周成交量（日成交求和） |
| amount | REAL | 周成交额（日成交求和） |
| change_pct | REAL | 周涨跌幅 |
| turnover_rate | REAL | 周换手率（日换手求和） |
| trade_days | INTEGER | 该周交易日数量 |
| adj_open/adj_high/adj_low/adj_close | REAL | 复权价格 |

**数据量**：~222万条  
**索引**：`idx_weekly_kline_date(week_end_date)`, `idx_weekly_kline_stock(stock_code)`  
**数据来源**：由 `daily_kline` 聚合生成  
**写入脚本**：`src/data/lixr_api/db_manager.py` → `upsert_weekly_kline()`  
**读取场景**：周线级别技术指标、中期趋势分析

---

## 4. stock_industry — 所属行业（多源）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| industry_code | TEXT | **PK(2)** |
| industry_name | TEXT | 行业名称 |
| source | TEXT | **PK(3)**，来源：`sw`/`sw_2021`/`cni`/... |
| updated_at | TEXT | 更新时间 |

**数据量**：~17141 条  
**数据来源**：理杏仁 API #14 所属行业  
**写入脚本**：`src/data/lixr_api/api_stock_industries.py`  
**读取场景**：获取股票行业分类

---

## 5. stock_index — 所属指数

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| index_code | TEXT | **PK(2)** |
| index_name | TEXT | 指数名称 |
| source | TEXT | 来源：`csi`/`cni`/`hsi`/`usi`/`lxri` |
| updated_at | TEXT | 更新时间 |

**数据量**：~19865 条  
**数据来源**：理杏仁 API #13 所属指数  
**写入脚本**：`src/data/lixr_api/api_stock_indices.py`  
**读取场景**：确认股票所属指数

---

## 6. stock_sw_industry — 申万一级行业映射

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK**，股票代码 |
| industry_name | TEXT | 申万一级行业名称，如"电子"、"食品饮料" |
| industry_code | TEXT | 申万一级行业指数代码，如 `931494` |
| updated_at | TEXT | 更新时间 |

**数据量**：~5500 条  
**数据来源**：理杏仁 API #14 所属行业（筛选 source=sw）  
**写入脚本**：`src/data/lixr_api/api_stock_industries.py`  
**读取场景**：
- `IndustryMapper.load_from_db()` 读取，结合 `rs_config.yaml` 的 `industry_index_map` 生成股票→中证行业指数映射
- 个股RS计算时用于匹配行业基准
- ⚠️ **注意**：不存在 `sw_industry` 或 `stock_sw_industry_list` 表，就是这张表

---

## 7. fundamental_indicator — 基本面指标（key-value）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)** |
| metric_code | TEXT | **PK(3)**，如 `pe_ttm`/`pb`/`mc`/`pe_ttm.y3.cvpos` |
| value | REAL | 指标值 |

**数据量**：~1.25亿条  
**索引**：`idx_fundamental_stock_date(stock_code, date)`, `idx_fundamental_metric(metric_code)`  
**数据来源**：理杏仁 API #26 基本面数据  
**写入脚本**：
- 非金融股：`scripts/fetch_fundamental_nonfinancial.py`
- 金融股等：`src/data/lixr_api/api_stock_fundamental.py` + `DBManager.upsert_fundamental()`
**设计说明**：key-value 结构，指标由 API 的 `metricsList` 参数动态决定
**metric_code 完整清单（33个）**：

| 分类 | 指标代码 | 说明 |
|------|---------|------|
| 估值 | `pe_ttm` | PE-TTM |
| 估值 | `d_pe_ttm` | PE-TTM（扣非） |
| 估值 | `pb` | PB |
| 估值 | `pb_wo_gw` | PB（不含商誉） |
| 估值 | `ps_ttm` | PS-TTM |
| 估值 | `dyr` | 股息率 |
| 估值进阶 | `pcf_ttm` | PCF-TTM |
| 估值进阶 | `ev_ebit_r` | EV/EBIT |
| 估值进阶 | `ev_ebitda_r` | EV/EBITDA |
| 估值进阶 | `ey` | 收益率（E/P） |
| 价量 | `sp` | 股价 |
| 价量 | `spc` | 涨跌幅 |
| 价量 | `spa` | 振幅 |
| 价量 | `tv` | 成交量 |
| 价量 | `ta` | 成交额 |
| 价量 | `to_r` | 换手率 |
| 股东/市值 | `shn` | 股东人数 |
| 股东/市值 | `mc` | 总市值 |
| 股东/市值 | `mc_om` | 流通市值 |
| 股东/市值 | `cmc` | A股市值 |
| 股东/市值 | `ecmc` | 自由流通市值 |
| 股东/市值 | `ecmc_psh` | 人均自由流通市值 |
| 融资融券 | `fpa` | 融资买入额 |
| 融资融券 | `fra` | 融资偿还额 |
| 融资融券 | `fnpa` | 融资净买入额 |
| 融资融券 | `fb` | 融资余额 |
| 融资融券 | `ssa` | 融券卖出量 |
| 融资融券 | `sra` | 融券偿还量 |
| 融资融券 | `snsa` | 融券净卖出量 |
| 融资融券 | `sb` | 融券余量 |
| 陆股通 | `ha_sh` | 陆股通持股量 |
| 陆股通 | `ha_shm` | 陆股通持股市值 |
| 陆股通 | `mm_nba` | 陆股通净买入 |

> ⚠️ 并非所有指标对所有股票都有值（如融资融券仅对两融标的、陆股通仅对互联互通标的）。API 单次 36 指标上限，33 个一次性拉取

---

## 8. financial_statement — 财报数据（key-value）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| report_date | TEXT | **PK(2)**，财报日期 |
| announce_date | TEXT | 公告日期 |
| metric_code | TEXT | **PK(3)**，如 `q.ps.toi.t`/`q.bs.ta.t`/`q.m.roe.t` |
| value | REAL | 指标值 |

**数据量**：~28378 条  
**索引**：`idx_financial_stock_date(stock_code, report_date)`, `idx_financial_metric(metric_code)`  
**数据来源**：理杏仁 API #27 财报数据  
**写入脚本**：`src/data/lixr_api/api_stock_fs.py` → `DBManager.upsert_financial()`  
**metric_code 编码规则**：`[粒度].[报表].[字段].[计算类型]`
- 粒度：q(季度), hy(半年), y(年)
- 报表：bs(资产负债表), ps(利润表), cf(现金流量表), m(财务指标)
- 计算类型：t(当期), c(单季), ttm, t_y2y(同比), c_y2y(单季同比)

---

## 9. stock_financials_quarterly — 季度财务（宽表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| report_date | TEXT | **PK(2)**，报告期 YYYY-MM-DD |
| announce_date | TEXT | 公告日期 |
| revenue_single | REAL | 当季营业收入 |
| revenue_yoy | REAL | 当季营收同比(%) |
| revenue_qoq | REAL | 当季营收环比(%) |
| net_profit_single | REAL | 当季归母净利润 |
| net_profit_margin | REAL | 归母净利润率(%) |
| net_profit_yoy | REAL | 归母净利润同比(%) |
| net_profit_qoq | REAL | 归母净利润环比(%) |
| net_profit_adj_single | REAL | 当季扣非净利润 |
| net_profit_adj_margin | REAL | 扣非净利润率(%) |
| net_profit_adj_yoy | REAL | 扣非净利润同比(%) |
| net_profit_adj_qoq | REAL | 扣非净利润环比(%) |
| gross_margin_single | REAL | 当季毛利率(%) |
| roe_single | REAL | 当季ROE |
| free_cash_flow | REAL | 当季自由现金流 |
| asset_liability_ratio | REAL | 当季资产负债率(%) |
| interest_bearing_debt_ratio | REAL | 当季有息负债率(%) |
| current_ratio | REAL | 当季流动比率 |
| quick_ratio | REAL | 当季速动比率 |
| receivables_turnover | REAL | 当季应收账款周转率 |
| inventory_turnover | REAL | 当季存货周转率 |
| updated_at | TEXT | 更新时间 |

**数据量**：~16.2万条  
**索引**：`idx_fq_date(report_date)`  
**数据来源**：理杏仁 API #27 财报数据（按季拆分）  
**写入脚本**：`scripts/fetch_stock_financials.py`  
**读取场景**：CANSLIM C维度季度盈利判断、财务筛选

---

## 10. stock_financials_annual — 年度财务（宽表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| report_date | TEXT | **PK(2)**，报告期 YYYY-12-31 |
| announce_date | TEXT | 公告日期 |
| revenue | REAL | 营业总收入 |
| revenue_yoy | REAL | 营收同比(%) |
| net_profit | REAL | 归母净利润 |
| net_profit_yoy | REAL | 归母净利润同比(%) |
| net_profit_adj | REAL | 扣非净利润 |
| gross_margin | REAL | 毛利率(%) |
| roe | REAL | 归母ROE(%) |
| roe_adj | REAL | 扣非归母ROE(%) |
| operating_cash_flow | REAL | 经营活动现金流净额 |
| free_cash_flow | REAL | 自由现金流 |
| free_cash_flow_yoy | REAL | 自由现金流同比(%) |
| asset_liability_ratio | REAL | 资产负债率(%) |
| interest_bearing_debt_ratio | REAL | 有息负债率(%) |
| current_ratio | REAL | 流动比率 |
| quick_ratio | REAL | 速动比率 |
| receivables_turnover | REAL | 应收账款周转率 |
| inventory_turnover | REAL | 存货周转率 |
| updated_at | TEXT | 更新时间 |

**数据量**：~5.7万条  
**索引**：`idx_fa_date(report_date)`  
**数据来源**：理杏仁 API #27 财报数据（按年拆分）  
**写入脚本**：`scripts/fetch_stock_financials.py`  
**读取场景**：CANSLIM A维度年度盈利判断、ROE筛选

---

## 11. shareholders_num — 股东人数

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)** |
| total | INTEGER | 股东人数 |
| change_rate | REAL | 变化比例 |
| price_change | REAL | 股价涨跌幅 |

**数据量**：~4 条（仅测试数据）  
**数据来源**：理杏仁 API #5 股东人数  
**写入脚本**：`src/data/lixr_api/api_stock_shareholders_num.py`

---

## 12. shareholders_num_v2 — 股东人数V2

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| date | TEXT | **PK(2)**，日期 |
| shnc_rln | INTEGER | 最新股东人数 |
| shnc_d10~d90 | REAL | 10/20/30/60/90日变化率 |
| shnc_qln | REAL | 上期股东人数 |
| shnc_q1~q3 | REAL | 1/2/3季度变化 |
| shnc_y1~y2 | REAL | 1/2年变化 |

**数据量**：~5168 条  
**数据来源**：理杏仁 API #5 股东人数V2  
**写入脚本**：`src/data/lixr_api/api_stock_shareholders_v2.py`

---

## 13. stock_margin — 融资融券数据

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| date | TEXT | **PK(2)**，日期 |
| mtaslb | REAL | 融资融券余额 |
| mtaslb_fb | REAL | 融资余额 |
| mtaslb_sb | REAL | 融券余额 |
| mtaslb_mc_r | REAL | 融资买入额 |
| npa_o_f_d1~d240 | REAL | 1/5/10/20/60/120/240日净买入额 |
| fb_mc_rc_d1~d240 | REAL | 1/5/10/20/60/120/240日融资偿还率 |

**数据量**：~5023 条  
**数据来源**：理杏仁 API #20 融资融券  
**写入脚本**：`src/data/lixr_api/api_stock_margin.py`

---

## 14. index_daily_kline — 指数日K线

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，指数代码 |
| date | TEXT | **PK(2)**，交易日期 |
| kline_type | TEXT | **PK(3)**，`normal`(正常点位)/`total_return`(全收益点位) |
| open | REAL | 开盘价 |
| close | REAL | 收盘价 |
| high | REAL | 最高价 |
| low | REAL | 最低价 |
| volume | INTEGER | 成交量 |
| amount | REAL | 成交额 |
| change | REAL | 涨跌幅(%) |

**数据量**：~142万条（408个指数 × ~1740天 × 2种点位）  
**数据来源**：理杏仁指数K线API  
**写入脚本**：`scripts/fetch_index_daily_kline.py`（通过 `config/index_style.yaml` 配置 408 个指数）  
**读取场景**：RS计算的行业/市场基准、行业技术指标(MA200/涨跌幅/成交量)

---

## 15. index_constituents — 指数成分股

| 字段名 | 类型 | 说明 |
|--------|------|------|
| index_code | TEXT | **PK(1)**，指数代码 |
| stock_code | TEXT | **PK(2)**，成分股代码 |
| date | TEXT | **PK(3)**，成分股生效日期 |
| updated_at | TEXT | 更新时间 |

**数据量**：~62.3万条（409个指数 × 13个月份）  
**索引**：`idx_idx_const_idx(index_code)`, `idx_idx_const_stock(stock_code)`, `idx_idx_const_date(date)`  
**数据来源**：理杏仁指数成分股API  
**写入脚本**：`scripts/fetch_index_constituents.py --constituents-only`  
**读取场景**：
- 行业RS v3.0：构建 `{stock_code: industry_index_code}` 映射
- 内部共振验证：获取行业成分股列表
- `DataFetcher.load_constituents()` 和 `DataFetcher.load_constituent_rs_median()`

---

## 16. index_constituent_weightings — 指数成分股权重

| 字段名 | 类型 | 说明 |
|--------|------|------|
| index_code | TEXT | **PK(1)** |
| stock_code | TEXT | **PK(2)** |
| date | TEXT | **PK(3)** |
| weighting | REAL | 权重比例 |
| updated_at | TEXT | 更新时间 |

**数据量**：~119万条  
**索引**：`idx_icw_index(index_code)`, `idx_icw_stock(stock_code)`  
**数据来源**：理杏仁成分股权重API  
**写入脚本**：`scripts/fetch_index_constituents.py --weightings-only`  
**读取场景**：`DataFetcher.load_constituents()` 优先使用权重排序取 top_n 成分股

---

## 17. rs_daily — 个股RS每日计算结果

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)**，YYYY-MM-DD |
| industry_code | TEXT | 行业指数代码（来自 stock_sw_industry + rs_config.yaml 映射） |
| industry_name | TEXT | 行业名称 |
| rs_mkt_long | REAL | 相对市场长期 RPS (250日)，0-99 |
| rs_mkt_mid | REAL | 相对市场中期 RPS (120日)，0-99 |
| rs_mkt_short | REAL | 相对市场短期 RPS (20日)，0-99 |
| rs_ind_long | REAL | 相对行业长期 RPS (250日)，0-99 |
| rs_ind_mid | REAL | 相对行业中期 RPS (120日)，0-99 |
| rs_ind_short | REAL | 相对行业短期 RPS (20日)，0-99 |
| pattern | TEXT | 双强模式：`Steady Leader`/`Accelerating`/NULL |
| long_days | INTEGER | 长期周期天数（配置值，默认250） |
| mid_days | INTEGER | 中期周期天数（配置值，默认120） |
| short_days | INTEGER | 短期周期天数（配置值，默认20） |
| updated_at | TEXT | 更新时间 |

**数据量**：~527万条  
**写入脚本**：`src/server.py`（RS计算API）  
**读取场景**：
- 行业RS v3.0：`DataFetcher.load_constituent_rs_median()` 聚合为行业RS中位数
- CSV 导出：`output/rs_daily_YYYYMMDD.csv` + `output/double_strong_YYYYMMDD.csv`
- Web 看板 API

---

## 18. sector_rs_daily — 行业板块RS每日结果

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | PK, 自增 |
| date | TEXT | 计算日期 YYYY-MM-DD |
| sector_code | TEXT | 行业指数代码 |
| sector_name | TEXT | 行业名称 |
| rs_ratio | REAL | RS Ratio（行业指数收盘 / 基准指数收盘） |
| score_20 | REAL | 20日动量得分(%) |
| score_120 | REAL | 120日动量得分(%) |
| score_250 | REAL | 250日动量得分(%) |
| rps_20 | INTEGER | 20日 RPS 排名（成分股RS中位数排名） |
| rps_120 | INTEGER | 120日 RPS 排名 |
| rps_250 | INTEGER | 250日 RPS 排名 |
| price_vs_ma200 | REAL | 收盘价/MA200 |
| ma200_trend | TEXT | MA200 趋势：UP/DOWN |
| daily_change_pct | REAL | 当日涨跌幅(%) |
| vol_ratio_20 | REAL | 当日成交量/20日均量 |
| vol_ratio_5 | REAL | 当日成交量/5日均量 |
| rs20_trend_up | INTEGER | 近20日 RPS 趋势是否上升，1/0/NULL |
| is_leading | INTEGER | L1 绝对强势池，1/0 |
| is_momentum | INTEGER | L2 短期爆发池，1/0 |
| is_setup | INTEGER | L3 潜在共振池，1/0 |
| is_compact | INTEGER | 紧凑整理形态，1/0 |
| internal_status | TEXT | 内部共振状态：高置信度领涨/中等置信度/弱势领涨/无共振/成分股缺失 |
| internal_count | INTEGER | 通过共振验证的个股数 |
| internal_weighted | REAL | 通过个股的权重占比 |
| top_stocks | TEXT | Top5 通过个股的 JSON（code/weighting/signal） |
| created_at | TEXT | 创建时间 |

**数据量**：~3330 条  
**UNIQUE**：`(date, sector_code)`  
**索引**：`idx_srs_date(date)`, `idx_srs_leading(date, is_leading)`, `idx_srs_momentum(date, is_momentum)`  
**写入脚本**：`src/server.py`（行业板块RS API）  
**读取场景**：CSV 导出 `output/sector_rs_report_YYYYMMDD.csv`、Web 看板

---

## 19. sector_internal_strength — 行业内部强度明细

| 字段名 | 类型 | 说明 |
|--------|------|------|
| date | TEXT | **PK(1)**，日期 |
| sector_code | TEXT | **PK(2)**，行业代码 |
| sector_name | TEXT | 行业名称 |
| constituent_count | INTEGER | 成分股数量 |
| rs_mean_20 | REAL | 20日RS均值 |
| rs_mean_120 | REAL | 120日RS均值 |
| rs_mean_250 | REAL | 250日RS均值 |
| rs_std_120 | REAL | 120日RS标准差 |
| above_ma50_count | INTEGER | MA50上方个股数 |
| above_ma200_count | INTEGER | MA200上方个股数 |
| bull_alignment_count | INTEGER | 多头排列个股数 |
| volume_surge_count | INTEGER | 放量个股数 |
| breakout_count | INTEGER | 突破个股数 |
| breakout_stocks | TEXT | 突破个股列表(JSON) |
| fund_flow_intensity | REAL | 资金流强度 |
| strong_ratio | REAL | 强势股占比 |
| above_ma50_ratio | REAL | MA50上方占比 |
| rs_dist_score | REAL | RS分布得分 |
| trend_score | REAL | 趋势得分 |
| money_flow_score | REAL | 资金流得分 |
| resonance_score | REAL | 共振得分 |

**UNIQUE**：`(date, sector_code)`  
**写入脚本**：`src/server.py`（行业板块RS API）  
**读取场景**：行业扫描

---

## 20. sector_rotation — 行业轮动记录

| 字段名 | 类型 | 说明 |
|--------|------|------|
| date | TEXT | **PK(1)**，日期 |
| sector_code | TEXT | **PK(2)**，行业代码 |
| sector_name | TEXT | 行业名称 |
| momentum_delta_5d | REAL | 5日动量变化 |
| rps_delta_5d | REAL | 5日RPS变化 |
| rotation_signal | TEXT | 轮动信号 |

**UNIQUE**：`(date, sector_code)`  
**写入脚本**：`src/server.py`（行业板块RS API）  
**读取场景**：行业扫描

---

## 21. market_direction_daily — 大盘方向每日分析

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| date | TEXT | 计算日期 YYYY-MM-DD |
| market_phase | TEXT | 市场阶段：Confirmed Rally/Uptrend Under Pressure/Correction/Downtrend |
| market_phase_confidence | TEXT | 阶段置信度 |
| risk_level | TEXT | 风险等级 |
| suggested_position_size | REAL | 建议仓位比例 |
| distribution_days_25d | INTEGER | 近25日抛盘日数 |
| distribution_days_10d | INTEGER | 近10日抛盘日数 |
| distribution_trend | TEXT | 抛盘日趋势 |
| distribution_warning | INTEGER | 抛盘日预警，0/1 |
| ftd_exists | INTEGER | 是否存在追盘日，0/1 |
| ftd_date | TEXT | 追盘日日期 |
| ftd_day_count | INTEGER | 追盘日第N天 |
| ftd_index_code | TEXT | 追盘日指数代码 |
| ftd_index_name | TEXT | 追盘日指数名称 |
| ftd_gain_pct | REAL | 追盘日涨幅(%) |
| ftd_volume_ratio | REAL | 追盘日量比 |
| accumulation_days_10d | INTEGER | 近10日吸筹日数 |
| standard_accumulation | INTEGER | 标准吸筹日数 |
| special_accumulation | INTEGER | 特殊吸筹日数 |
| breakout_accumulation | INTEGER | 突破吸筹日数 |
| accumulation_vs_distribution | REAL | 吸筹/抛盘比 |
| divergence_pattern | TEXT | 分歧形态 |
| style_divergence | TEXT | 风格分歧 |
| sector_rotation_summary | TEXT | 板块轮动摘要(JSON) |
| leading_index_code | TEXT | 最强指数代码 |
| leading_index_name | TEXT | 最强指数名称 |
| leading_index_rs | INTEGER | 最强指数RS值 |
| top_indices | TEXT | Top指数排名(JSON) |
| market_health_score | INTEGER | 市场健康度评分(0-100) |
| health_score_components | TEXT | 健康度分项(JSON) |
| advance_decline_ratio | REAL | 涨跌比 |
| new_high_new_low_ratio | REAL | 新高新低比 |
| above_ma50_ratio | REAL | MA50上方个股占比 |
| summary | TEXT | 总结 |
| action_suggestion | TEXT | 操作建议 |
| warnings | TEXT | 风险提示(JSON) |
| focus | TEXT | 关注方向 |
| avoid | TEXT | 回避方向 |
| stop_loss | TEXT | 止损建议 |
| strengths | TEXT | 优势(JSON) |
| weaknesses | TEXT | 劣势(JSON) |
| opportunities | TEXT | 机会(JSON) |
| risks | TEXT | 风险(JSON) |
| created_at | TEXT | 创建时间 |

**写入脚本**：`~/source/backtest/server.py`（大盘方向分析模块）  
**读取场景**：Web 看板（大盘扫描页）

---

## 22. distribution_days_detail — 抛盘日明细

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| date | TEXT | 日期 |
| index_code | TEXT | 指数代码 |
| index_name | TEXT | 指数名称 |
| close | REAL | 收盘价 |
| change_pct | REAL | 涨跌幅(%) |
| volume | INTEGER | 成交量 |
| prev_volume | INTEGER | 前日成交量 |
| volume_ratio | REAL | 量比 |
| is_distribution | INTEGER | 是否抛盘日，0/1 |
| distribution_type | TEXT | 抛盘日类型 |
| decline_threshold_used | REAL | 使用的跌幅阈值 |
| market_phase_at_date | TEXT | 当日市场阶段 |
| is_high_position | INTEGER | 是否高位，0/1 |
| created_at | TEXT | 创建时间 |

**写入脚本**：`~/source/backtest/server.py`（抛盘日检测模块）  
**读取场景**：大盘方向分析
---

## 23. follow_through_days — 追盘日

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| ftd_date | TEXT | 追盘日日期 |
| low_point_date | TEXT | 低点日期 |
| low_point_value | REAL | 低点值 |
| day_count | INTEGER | 低点后第N天 |
| index_code | TEXT | 指数代码 |
| index_name | TEXT | 指数名称 |
| close | REAL | 收盘价 |
| gain_pct | REAL | 涨幅(%) |
| volume | INTEGER | 成交量 |
| prev_volume | INTEGER | 前日成交量 |
| volume_ratio | REAL | 量比 |
| is_valid | INTEGER | 是否仍有效，0/1 |
| invalidated_date | TEXT | 失效日期 |
| invalidated_reason | TEXT | 失效原因 |
| follow_up_5d_return | REAL | 后5日收益(%) |
| follow_up_20d_return | REAL | 后20日收益(%) |
| created_at | TEXT | 创建时间 |

**写入脚本**：`~/source/backtest/server.py`（抛盘日检测模块）  
**读取场景**：大盘方向分析
---

## 24. accumulation_days_detail — 吸筹日明细

| 字段名 | 类型 | 说明 |
|--------|------|------|
| date | TEXT | **PK(1)**，日期 |
| index_code | TEXT | **PK(2)**，指数代码 |
| index_name | TEXT | 指数名称 |
| close | REAL | 收盘价 |
| change_pct | REAL | 涨跌幅(%) |
| volume | INTEGER | 成交量 |
| prev_volume | INTEGER | 前日成交量 |
| volume_ratio | REAL | 量比 |
| amplitude | REAL | 振幅 |
| close_position | REAL | 收盘位置(0-1) |
| is_accumulation | INTEGER | 是否吸筹日，0/1 |
| is_standard_acc | INTEGER | 标准吸筹日，0/1 |
| is_special_acc | INTEGER | 特殊吸筹日，0/1 |
| is_breakout_acc | INTEGER | 突破吸筹日，0/1 |
| accumulation_type | TEXT | 吸筹日类型 |
| created_at | TEXT | 创建时间 |

**UNIQUE**：`(date, index_code)`  
**写入脚本**：`~/source/backtest/server.py`（抛盘日检测模块）  
**读取场景**：大盘方向分析
---

## 25. distribution_day_features — 分布日特征工程

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，指数代码 |
| date | TEXT | **PK(2)**，交易日期 |
| open/high/low/close | REAL | OHLC |
| volume | INTEGER | 成交量 |
| amount | REAL | 成交额 |
| change_pct | REAL | 涨跌幅(%) |
| prev_close | REAL | 前日收盘 |
| volume_ratio | REAL | 当日量/20日均量 |
| volume_ratio_ma5 | REAL | 当日量/5日均量 |
| volume_score | INTEGER | 成交量评分 |
| decline_score | INTEGER | 跌幅评分 |
| stagnation_flag | INTEGER | 停滞标志 |
| is_stealth_dist | INTEGER | 是否隐性抛盘(假阳线) |
| close_position | REAL | 收盘价位置(0-1) |
| upper_shadow_pct | REAL | 上影线占比(%) |
| lower_shadow_pct | REAL | 下影线占比(%) |
| body_pct | REAL | 实体占比(%) |
| position_score | INTEGER | 收盘位置评分 |
| gap_down_flag | INTEGER | 跳空下跌标志 |
| gap_filled_flag | INTEGER | 缺口回补标志 |
| gap_score | INTEGER | 缺口评分 |
| total_score | INTEGER | 总分 |
| is_dist_day | INTEGER | 是否抛盘日，0/1 |
| fwd_max_dd_5d/10d | REAL | 前向5/10日最大回撤(%) |
| fwd_return_5d/10d | REAL | 前向5/10日收益(%) |
| label_dd_gt_2pct | INTEGER | 标签：5日内是否跌>2% |
| dist_count_20d/15d | INTEGER | 近20/15日抛盘日计数 |

**数据量**：~12339 条  
**索引**：`idx_ddf_code_date(stock_code, date)`, `idx_ddf_dist(is_dist_day)`, `idx_ddf_label(label_dd_gt_2pct)`, `idx_ddf_score(total_score)`  
**数据来源**：指数日K线计算生成  
**写入脚本**：`src/detectors/distribution_day.py`  
**读取场景**：分布日回测、信号回溯分析

---

## 26. daily_distribution_summary — 每日分布日汇总

| 字段名 | 类型 | 说明 |
|--------|------|------|
| date | TEXT | **PK**，日期 |
| total_indices | INTEGER | 统计指数数（默认3） |
| distribution_count | INTEGER | 总抛盘日数 |
| standard_distribution_count | INTEGER | 标准抛盘日数 |
| special_distribution_count | INTEGER | 特殊抛盘日数 |
| reversal_distribution_count | INTEGER | 盘中反转抛盘日数 |
| heavy_distribution_count | INTEGER | 重抛盘日数 |
| confirmation_count | INTEGER | 确认日数 |
| canceled_count | INTEGER | 被抵消抛盘日数 |
| weighted_distribution_sum | INTEGER | 加权抛盘日和 |
| market_status | TEXT | 市场状态 |
| created_at | TIMESTAMP | 创建时间 |

**数据量**：~67 条  
**索引**：`idx_daily_summary_date(date)`, `idx_daily_summary_status(market_status)`  
**写入脚本**：`src/detectors/distribution_day.py`  
**读取场景**：大盘日级别快速查询

---

## 27. market_distribution_days — 市场分布日（简化版）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| date | TEXT | **PK(1)**，日期 |
| index_code | TEXT | **PK(2)**，指数代码 |
| is_distribution | INTEGER | 是否抛盘日，0/1 |
| dist_type | TEXT | 抛盘日类型 |
| change_pct | REAL | 涨跌幅(%) |
| volume_ratio | REAL | 量比 |
| close_position | REAL | 收盘价位置 |
| upper_shadow_ratio | REAL | 上影线比例 |
| created_at | TEXT | 创建时间 |

**数据量**：~436 条  
**索引**：`idx_mdd_index_date(index_code, date)`  
**写入脚本**：`src/detectors/distribution_day.py`  
**读取场景**：大盘方向快速查询

---

## 28. market_scan_parameters — 抛盘日扫描参数集

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| parameter_set_name | TEXT | **UNIQUE**，参数集名称 |
| description | TEXT | 参数集描述 |
| std_decline_threshold | REAL | 跌幅阈值（%，默认 -0.1） |
| std_volume_increase | BOOLEAN | 需要放量（默认 1） |
| special_max_gain | REAL | 特殊抛盘日最大涨幅（默认 0.2） |
| special_min_intraday_gain | REAL | 最小盘中涨幅（默认 0.5） |
| special_volume_ratio | REAL | 成交量倍数（默认 1.3） |
| special_upper_shadow_entity_ratio | REAL | 上影线/实体比例（默认 1.5） |
| special_upper_shadow_amplitude_ratio | REAL | 上影线/振幅比例（默认 0.5） |
| reversal_volume_ratio | REAL | 反转抛盘日量比（默认 1.2） |
| reversal_min_intraday_gain | REAL | 最小盘中涨幅（默认 0.5） |
| reversal_upper_shadow_entity_ratio | REAL | 上影线/实体比例（默认 1.5） |
| reversal_close_position | REAL | 收盘价位置阈值（默认 0.5） |
| heavy_decline_threshold | REAL | 重抛盘日跌幅阈值（默认 -1.5） |
| heavy_volume_ratio | REAL | 重抛盘日量比（默认 1.2） |
| confirmation_gain_threshold | REAL | 确认日涨幅阈值（默认 1.5） |
| flat_day_threshold | REAL | 平盘日阈值（默认 0.05） |
| rolling_window_days | INTEGER | 滚动窗口天数（默认 25） |
| pressure_threshold | INTEGER | 承压阈值（默认 5） |
| bear_threshold | INTEGER | 熊市阈值（默认 8） |
| require_multiple_indices | BOOLEAN | 需要多指数确认（默认 0） |
| min_indices_confirm | INTEGER | 最少确认指数数（默认 2） |
| is_default | BOOLEAN | 是否默认参数集 |
| created_at/updated_at | TIMESTAMP | 时间戳 |

**数据量**：~1 条（1套默认参数）  
**写入脚本**：`src/server.py`（管理API）  
**读取场景**：分布日回测参数管理

---

## 29. market_scan_results — 抛盘日扫描结果

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| date | TEXT | 日期 |
| index_code | TEXT | 指数代码 |
| close/open/high/low | REAL | 价格数据 |
| volume/amount | REAL | 成交量/额 |
| prev_close/prev_volume | REAL | 前日数据 |
| change_pct | REAL | 涨跌幅(%) |
| volume_ratio | REAL | 量比 |
| is_flat_day | BOOLEAN | 平盘日 |
| is_standard_distribution | BOOLEAN | 标准抛盘日 |
| is_special_distribution | BOOLEAN | 特殊抛盘日 |
| is_intraday_reversal | BOOLEAN | 盘中反转抛盘日 |
| is_heavy_distribution | BOOLEAN | 重抛盘日 |
| distribution_type | TEXT | 抛盘日类型 |
| distribution_weight | INTEGER | 权重(0/1/2) |
| is_confirmation_day | BOOLEAN | 升势确认日 |
| is_canceled | BOOLEAN | 是否被抵消 |
| canceled_by_date | TEXT | 被哪个确认日抵消 |
| distribution_count_25d | INTEGER | 25日内抛盘日累积 |
| standard/special/reversal/heavy_count_25d | INTEGER | 各类型25日计数 |
| canceled_count_25d | INTEGER | 25日内被抵消数 |
| market_status | TEXT | normal/pressure/bear |
| ma5/ma10/ma20/ma50/ma120/ma250 | REAL | 均线值 |
| calculated_at/updated_at | TIMESTAMP | 时间戳 |

**数据量**：~201 条  
**UNIQUE**：`(date, index_code)`  
**索引**：`idx_market_scan_date(date)`, `idx_market_scan_index(index_code)`, `idx_market_scan_status(market_status)`  
**写入脚本**：`src/detectors/distribution_day.py`  
**读取场景**：分布日看板、K线叠加显示

---

## 30. buy_signals_daily — 每日买点信号

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| date | TEXT | 信号日期 |
| stock_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票名称 |
| signal_type | TEXT | 信号类型 |
| subtype | TEXT | 信号子类型 |
| entry_price | REAL | 入场价 |
| entry_price_high | REAL | 入场价(最高) |
| stop_loss_price | REAL | 止损价 |
| target_price | REAL | 目标价 |
| confidence | TEXT | 置信度 |
| strength_score | INTEGER | 强度评分 |
| suggested_position_pct | REAL | 建议仓位 |
| details_json | TEXT | 信号详情(JSON) |
| created_at | TEXT | 创建时间 |

**数据量**：~0 条  
**写入脚本**：`src/server.py`（买点信号管理API）  
**读取场景**：CANSLIM 看板

---

## 31. stock_candidates_daily — 个股扫描每日结果

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| date | TEXT | **PK(2)**，日期 |
| stock_name | TEXT | 股票名称 |
| industry_name | TEXT | 行业 |
| rs_score | REAL | RS维度评分 |
| rs_mkt_long | REAL | 原始RS值 |
| fundamental_score | REAL | 基本面维度评分 |
| eps_ttm, eps_yoy, revenue_yoy, roe, debt_ratio | REAL | 基本面指标 |
| vol_price_score | REAL | 量价维度评分 |
| price_vs_ma50, price_vs_ma200, dist_from_high | REAL | 量价指标 |
| avg_volume_20d | REAL | 20日均量 |
| volume_trend, ma_trend | TEXT | 趋势描述 |
| pattern_score | REAL | 形态维度评分 |
| pattern_health, pattern_type | TEXT | 形态分析 |
| composite_score | REAL | 综合评分 |
| grade | TEXT | 等级(A/B/C/D) |
| is_watchlist | INTEGER | 是否自选 |

**数据量**：~5220 条  
**写入脚本**：`src/server.py`（扫描API）  
**读取场景**：个股筛选看板

---

## 32. watchlist — 自选池

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **UNIQUE**，股票/指数代码 |
| stock_name | TEXT | 股票名称 |
| source | TEXT | 来源：`observation`(观察池) / `manual`(手动添加) |
| review_status | TEXT | 评估状态：`pending_review`(评估中) / `reviewed`(已评估) |
| manual_reason | TEXT | 手动添加理由（source=manual 时填写） |
| added_at | TEXT | 添加时间 |
| removed_at | TEXT | 移除时间(NULL=有效) |
| note | TEXT | 备注 |

**数据量**：少量  
**写入脚本**：`src/discipline/trades_api.py`  
**读取场景**：自选池管理，支持移除后重新添加(UPSERT)

---

## 52. pattern_scan_signals — 形态扫描信号

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)**，扫描日期 |
| signals_json | TEXT | JSON 信号数组 |

**数据量**：~3,678 条/日  
**写入脚本**：`scripts/daily_pattern_scan.py`（Step 3.5）  
**读取场景**：复核详情页信号标记、观察池信号列

---

## 53. discipline_observation_pool — 观察池日快照

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)**，快照日期 |
| stock_name | TEXT | 股票名称 |
| industry_name | TEXT | 申万一级行业 |
| rs_category | TEXT | RS分类（龙头/加速/短爆/稳健/双强） |
| rps_20/60/120/250 | REAL | RPS 各周期值 |
| canslim_total/c/a/n/s/l/i/m | REAL | CAN SLIM 七维评分 |
| roe/eps_yoy/revenue_yoy/debt_ratio/gross_margin | REAL | 财务指标 |
| pe_ttm/pb/pe_percentile/market_cap | REAL | 估值指标 |
| signals_json | TEXT | V2 统一信号列（替代 buy/sell 拆分） |
| composite_score | REAL | 综合评分 |
| grade | TEXT | 等级 |
| suggestion | TEXT | 建议 |

**数据量**：~1,489 条/日  
**写入脚本**：`src/discipline/observation.py`  
**读取场景**：观察池浏览、复核详情

---

## 54. discipline_trades — 交易记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | **PK**，自增 |
| stock_code | TEXT | 股票/指数代码 |
| stock_name | TEXT | 名称 |
| asset_type | TEXT | 类型：`stock`(股票) / `index`(指数) |
| buy_date | TEXT | 买入日期 |
| buy_price | REAL | 买入价 |
| buy_qty | INTEGER | 买入数量 |
| buy_amount | REAL | 买入金额 |
| buy_reason | TEXT | 买入理由 |
| buy_emotion | TEXT | 买入时情绪 |
| target_period | TEXT | 目标周期(swing/medium/long) |
| target_price | REAL | 目标价 |
| stop_loss_price | REAL | 止损价 |
| position_pct | REAL | 仓位占比 |
| checklist_json | TEXT | 买入前检查清单JSON |
| sell_date | TEXT | 卖出日期 |
| sell_price | REAL | 卖出价 |
| sell_reason | TEXT | 卖出理由 |
| sell_emotion | TEXT | 卖出时情绪 |
| pnl_amount | REAL | 盈亏金额 |
| pnl_pct | REAL | 盈亏% |
| hold_days | INTEGER | 持股天数 |
| review_* | — | 复盘字段（合规率/α/β/笔记/心力） |

**数据量**：少量  
**写入脚本**：`src/discipline/trades_api.py`  
**读取场景**：交易录入、持仓监控、盈亏汇总、仪表盘

---

## 55. discipline_rules_config — 规则配置

| 字段 | 类型 | 说明 |
|------|------|------|
| rule_name | TEXT | **PK**，规则名 |
| display_name | TEXT | 显示名 |
| category | TEXT | 分类：`system_config` / `pre_trade` |
| parameters_json | TEXT | 参数JSON |
| is_active | INTEGER | 是否启用 |

**数据量**：少量  
**写入脚本**：`src/discipline/trades_api.py`  
**读取场景**：总资产存储、检查清单模板

---

## 56. discipline_alerts — 告警记录

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | **PK**，自增 |
| trade_id | INTEGER | 关联交易ID |
| stock_code | TEXT | 股票代码 |
| alert_date | TEXT | 告警日期 |
| alert_level | TEXT | 级别：`red`(红色) / `yellow`(黄色) / `green`(绿色) |
| alert_type | TEXT | 类型：`stop_loss` / `market_risk` / `stock_weakness` / `top_signal` |
| alert_message | TEXT | 告警消息 |
| acknowledged | INTEGER | 是否已知晓(0/1) |

**数据量**：少量  
**写入脚本**：`src/discipline/monitoring.py`  
**读取场景**：持仓监控页告警灯、已知晓操作

---

## 57. discipline_daily_snapshots — 持仓日快照

| 字段 | 类型 | 说明 |
|------|------|------|
| date | TEXT | **PK(1)** |
| trade_id | INTEGER | **PK(2)** |
| current_price | REAL | 当日收盘价 |
| market_value | REAL | 当日市值 |
| pnl_pct | REAL | 当日浮盈% |
| position_pct | REAL | 当日仓位% |
| alerts_json | TEXT | 当日告警JSON

**数据量**：少量  
**写入脚本**：`src/discipline/monitoring.py`（预留）  
**读取场景**：持仓历史回溯（V3）

---

## 33. portfolio_holdings — 组合持仓

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| stock_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票名称 |
| market | TEXT | 市场（默认 SH） |
| batch_id | INTEGER | 批次ID |
| buy_price | REAL | 买入价 |
| qty | INTEGER | 数量 |
| buy_date | TEXT | 买入日期 |
| note | TEXT | 备注 |
| created_at/updated_at | TEXT | 时间戳 |

**UNIQUE**：`(stock_code, batch_id, buy_date, buy_price)`  
**数据量**：~12 条  
**写入脚本**：`src/server.py`（组合管理API）  
**读取场景**：持仓跟踪、盈亏计算

---

## 34. portfolio_signals — 持仓信号

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| scan_date | TEXT | 扫描日期 |
| stock_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票名称 |
| batch_id | INTEGER | 批次ID |
| buy_price | REAL | 买入价 |
| buy_date | TEXT | 买入日期 |
| qty | INTEGER | 数量 |
| current_price | REAL | 当前价格 |
| pnl_pct | REAL | 盈亏(%) |
| hold_days | INTEGER | 持仓天数 |
| signal_level | TEXT | 信号级别（默认 normal） |
| signal_rule | TEXT | 信号规则 |
| signal_detail | TEXT | 信号详情 |
| status | TEXT | 状态（默认 active） |
| created_at/updated_at | TEXT | 时间戳 |

**UNIQUE**：`(scan_date, stock_code, batch_id)`  
**数据量**：~44 条  
**写入脚本**：`src/server.py`（组合扫描API）  
**读取场景**：持仓信号跟踪

---

## 35. seven_week_state — 七周规则状态

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK**，股票代码 |
| stock_name | TEXT | 股票名称 |
| first_batch_date | TEXT | 首批买入日期 |
| has_broken_10d | INTEGER | 是否跌破10日线，0/1 |
| signal_fired | INTEGER | 是否触发信号，0/1 |
| updated_at | TEXT | 更新时间 |

**数据量**：~7 条  
**写入脚本**：`src/server.py`（持仓管理API）  
**读取场景**：七周规则止损信号

---

## 36. sector_strength_results — 行业强度

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| analysis_date | TEXT | 分析日期 |
| sector_code | TEXT | 行业代码 |
| sector_name | TEXT | 行业名称 |
| category | TEXT | 分类 |
| return_20d | REAL | 20日收益(%) |
| rank | INTEGER | 排名 |
| created_at | TIMESTAMP | 创建时间 |

**UNIQUE**：`(analysis_date, sector_code)`  
**数据量**：~5 条  
**写入脚本**：`src/server.py`（行业分析API）  
**读取场景**：行业强度排名

---

## 37. industry_strength_results — 行业RS强度

| 字段名 | 类型 | 说明 |
|--------|------|------|
| date | TEXT | **PK(1)**，日期 |
| industry_code | TEXT | **PK(2)**，行业代码 |
| industry_name | TEXT | 行业名称 |
| rs_20d | REAL | 20日RS |
| rs_60d | REAL | 60日RS |
| rs_120d | REAL | 120日RS |
| composite_rs | REAL | 综合RS |
| rank | INTEGER | 排名 |
| category | TEXT | 分类 |
| trend | TEXT | 趋势 |
| signals | TEXT | 信号(JSON) |
| created_at | TIMESTAMP | 创建时间 |

**数据量**：~1 条  
**写入脚本**：`src/server.py`（行业RS API）  
**读取场景**：行业RS对比

---

## 38. index_divergence_results — 指数分化分析

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| analysis_date | TEXT | **UNIQUE**，分析日期 |
| market_state | TEXT | 市场状态 |
| action | TEXT | 操作建议 |
| focus | TEXT | 关注方向 |
| leading_style_code | TEXT | 领涨风格代码 |
| leading_style_name | TEXT | 领涨风格名称 |
| market_divergence_type | TEXT | 市场分化类型 |
| style_divergence_type | TEXT | 风格分化类型 |
| sector_divergence_type | TEXT | 行业分化类型 |
| results_json | TEXT | 完整结果(JSON) |
| created_at | TIMESTAMP | 创建时间 |

**数据量**：~1 条  
**写入脚本**：`src/server.py`（指数分析API）  
**读取场景**：市场/风格/行业分化监控

---

## 39. backtest_runs — 回测运行记录

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| name | TEXT | 回测名称 |
| signal_type | TEXT | 信号类型（默认 distribution_day） |
| stock_code | TEXT | 指数代码（如 000985） |
| start_date | TEXT | 开始日期 |
| end_date | TEXT | 结束日期 |
| params | TEXT | 参数(JSON) |
| created_at | TEXT | 创建时间 |

**索引**：`idx_br_stock(stock_code, signal_type)`  
**数据量**：~0 条  
**写入脚本**：`src/server.py`（回测API）  
**读取场景**：分布日回测看板

---

## 40. backtest_signals — 回测信号明细

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| run_id | INTEGER | 回测运行ID → `backtest_runs.id` |
| stock_code | TEXT | 指数代码 |
| date | TEXT | 信号日期 |
| signal_type | TEXT | standard/heavy/stealth/reversal |
| score | REAL | 评分 |
| open/high/low/close | REAL | 价格 |
| change_pct | REAL | 涨跌幅(%) |
| volume/amount | REAL/INTEGER | 量/额 |
| vol_5d/10d/20d | REAL | 波动率 |
| ma5/10/20/50/120/250 | REAL | 均线 |
| volume_score/decline_score/position_score/gap_score/special_score | INTEGER | 分项评分 |
| total_score | INTEGER | 总分 |
| close_position | REAL | 收盘位置(0-1) |
| upper_shadow_pct | REAL | 上影线(%) |
| lower_shadow_pct | REAL | 下影线(%) |
| volume_ratio | REAL | 量比 |
| volume_ratio_ma5 | REAL | 5日均量比 |

**索引**：`idx_bs_run(run_id)`, `idx_bs_date(date)`  
**数据量**：~0 条  
**写入脚本**：`src/server.py`（回测API）  
**读取场景**：分布日回测看板信号列表

---

## 41. backtest_stats — 回测统计

| 字段名 | 类型 | 说明 |
|--------|------|------|
| run_id | INTEGER | **PK** → `backtest_runs.id` |
| total_days | INTEGER | 总交易日数 |
| signal_count | INTEGER | 信号总数 |
| standard_count | INTEGER | 标准抛盘日数 |
| heavy_count | INTEGER | 重抛盘日数 |
| stealth_count | INTEGER | 假阳线数 |
| reversal_count | INTEGER | 盘中反转数 |
| weighted_count | INTEGER | 加权合计 |
| avg_vol_10d | REAL | 平均10日波动率 |
| avg_volume_ratio | REAL | 平均量比 |

**数据量**：~0 条  
**写入脚本**：`src/server.py`（回测API）  
**读取场景**：回测统计汇总

---

## 42. canslim_quarterly_eps — CANSLIM季度EPS

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| report_date | TEXT | **PK(2)**，财报日期 |
| eps_basic | REAL | 基本每股收益 |
| eps_diluted | REAL | 稀释每股收益 |
| revenue_yoy | REAL | 营收同比增长率(%) |
| net_profit_yoy | REAL | 净利润同比增长率(%) |
| np_atoopc_yoy | REAL | 归母净利润同比增长率(%) |
| updated_at | TEXT | 更新时间 |

**数据量**：~97713 条  
**写入脚本**：`src/data/lixr_api/api_stock_fs.py`  
**读取场景**：CANSLIM 看板（C季度盈利、A年度盈利判断）

---

## 43. canslim_annual_eps — CANSLIM年度EPS

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| report_date | TEXT | **PK(2)**，财报日期 |
| eps_basic | REAL | 基本每股收益 |
| eps_diluted | REAL | 稀释每股收益 |
| revenue | REAL | 营收 |
| net_profit | REAL | 净利润 |
| roe | REAL | 净资产收益率(%) |
| updated_at | TEXT | 更新时间 |

**数据量**：~17781 条  
**写入脚本**：`src/data/lixr_api/api_stock_fs.py`  
**读取场景**：CANSLIM 看板（A年度盈利判断）

---

## 44. canslim_institutional — CANSLIM基金持股

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| report_date | TEXT | **PK(2)**，财报日期 |
| fund_count | INTEGER | 持股基金数 |
| total_holdings | REAL | 持股总量 |
| total_market_cap | REAL | 持股市值 |
| total_share_pct | REAL | 占总股本比例(%) |
| updated_at | TEXT | 更新时间 |

**数据量**：~7088 条  
**写入脚本**：`src/data/lixr_api/api_stock_fund_shareholders.py`  
**读取场景**：CANSLIM 看板（I机构认同判断）

---

## 45. canslim_scores — CANSLIM七维度评分

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| date | TEXT | 筛选日期 |
| stock_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票名称 |
| total_score | INTEGER | 总分 |
| pass_count | INTEGER | 通过维度数 |
| C_score/A_score/N_score/S_score/L_score/I_score/M_score | INTEGER | 各维度得分 |
| C_pass/A_pass/... | INTEGER | 各维度是否通过，0/1 |
| C_detail/A_detail/... | TEXT | 各维度详情(JSON) |
| has_base | INTEGER | 是否有基部，0/1 |
| breakout | INTEGER | 是否突破，0/1 |
| created_at | TEXT | 创建时间 |

**数据量**：~0 条  
**写入脚本**：`src/server.py`（买点信号管理API）  
**读取场景**：CANSLIM 看板

---

## 46. stock_institutional_holdings — 机构持股汇总

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| date | TEXT | **PK(2)**，报告期 YYYY-MM-DD |
| data_source | TEXT | **PK(3)**，数据源 `lixinger` |
| fund_count | INTEGER | 公募基金持有数 |
| fund_holdings_total | REAL | 基金持股市值合计(元) |
| fund_proportion_sum | REAL | 流通A股占比合计（归一化0~1） |
| top10_inst_count | INTEGER | 前十大中机构数 |
| top10_inst_proportion | REAL | 前十大机构持股占比 |
| top10_float_inst_count | INTEGER | 前十流通中机构数 |
| top10_float_inst_prop | REAL | 前十流通机构占比 |
| total_inst_count | INTEGER | 总机构数(基金+十大) |
| total_inst_proportion | REAL | 综合机构持股占比(max) |
| org_categories_json | TEXT | 机构类别分布 JSON |
| updated_at | TEXT | 更新时间 |

**数据量**：~5300/季  
**数据来源**：理杏仁 fund-shareholders + majority-shareholders + nolimit-shareholders  
**写入脚本**：`scripts/fetch_institutional_holdings.py`（每周一）  
**读取场景**：CANSLIM I维机构持股比例、机构数量变化

---

## 47. stock_inst_holders_detail — 机构持股明细

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | 股票代码 |
| date | TEXT | 报告期 |
| data_source | TEXT | 数据源 |
| holder_type | TEXT | 类型 fund/majority/freeholders |
| holder_name | TEXT | 持有人名称 |
| holder_code | TEXT | 持有人代码 |
| holdings | REAL | 持股数 |
| market_cap | REAL | 持股市值 |
| proportion | REAL | 占比 |
| holder_category | TEXT | 持有人类别 |
| holder_rank | INTEGER | 排名 |

**数据来源**：理杏仁（同上）  
**读取场景**：机构持股明细查询

---

## 48. stock_analyst_reports — 研报覆盖统计

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)**，统计日期 |
| lookback_days | INTEGER | **PK(3)**，回溯天数(90) |
| report_count | INTEGER | 研报总数 |
| org_count | INTEGER | 覆盖机构数(去重) |
| first_coverage | INTEGER | 是否有首次覆盖 0/1 |
| upgrade_count | INTEGER | 评级上调数 |
| downgrade_count | INTEGER | 评级下调数 |
| maintain_count | INTEGER | 评级维持数 |
| buy_count | INTEGER | 买入推荐数 |
| overweight_count | INTEGER | 增持数 |
| neutral_count | INTEGER | 中性/持有数 |
| reduce_count | INTEGER | 减持数 |
| lx_pe_ttm / lx_pb / lx_mc / lx_shn / lx_shn_change | REAL | 理杏仁辅助指标 |
| orgs_json | TEXT | 机构列表 JSON |
| top_orgs_json | TEXT | TOP机构 JSON |
| updated_at | TEXT | 更新时间 |

**数据来源**：东方财富 reportapi.eastmoney.com/report/list  
**写入脚本**：`scripts/fetch_stock_reports.py`（每周一）  
**读取场景**：CANSLIM I维研报覆盖、首次覆盖、评级上调

---

## 49. stock_report_raw — 研报原始记录

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| stock_code | TEXT | 股票代码 |
| report_date | TEXT | 研报日期 |
| org_name | TEXT | 机构名称 |
| author_name | TEXT | 作者 |
| title | TEXT | 研报标题 |
| rating_name | TEXT | 评级名称 |
| rating_change | TEXT | 评级变化 |
| is_first | INTEGER | 是否首次覆盖 0/1 |
| info_code | TEXT | **UNIQUE**，东方财富研报ID |
| updated_at | TEXT | 更新时间 |

**索引**：`idx_report_raw_stock(stock_code, report_date)`  
**数据来源**：东方财富（同上）  
**读取场景**：研报明细追溯

---

## 50. stock_buyback — 股票回购

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| buyback_code | TEXT | **PK(2)**，回购编号 |
| notice_date | TEXT | 公告日期 |
| progress | TEXT | 进度 001=实施中 002=已完成 |
| objective | TEXT | 回购目的（含"注销"关键词） |
| amount_yuan | REAL | 累计已回购金额(元) |
| ratio_pct | REAL | 占总股本比例(%) |
| is_cancellation | INTEGER | 是否注销回购 0/1 |
| updated_at | TEXT | 更新时间 |

**数据量**：~5100条(全量) / ~62条(实施中)  
**数据来源**：东方财富 RPTA_WEB_GETHGLIST_NEW  
**写入脚本**：`scripts/fetch_buyback.py`（每周一）  
**读取场景**：CANSLIM S维回购注销评分

---

## 51. cansim_scores — CAN SLIM评分结果

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)**，评分日期 |
| score | REAL | 综合评分(0-100) |
| score_c | REAL | C 当季利润 |
| score_a | REAL | A 年度利润 |
| score_n | REAL | N 新事物 |
| score_s | REAL | S 供需关系 |
| score_l | REAL | L 领涨股/行业 |
| score_i | REAL | I 机构持股 |
| grade | TEXT | 等级 |

**数据量**：~5,500 条/次  
**写入脚本**：`scripts/batch_canslim_score.py`  
**读取场景**：观察池评分、复核页CANSLIM七维体检

---

## 全表汇总

| # | 表名 | 行数 | 说明 |
|---|------|------|------|
| 1 | stock_basic | ~5,595 | 股票基础信息 |
| 2 | daily_kline | ~1794万 | 个股日K线 |
| 3 | weekly_kline | ~222万 | 个股周K线 |
| 4 | stock_industry | ~17,141 | 所属行业（多源） |
| 5 | stock_index | ~19,865 | 所属指数 |
| 6 | stock_sw_industry | ~5,500 | 申万一级行业映射 |
| 7 | fundamental_indicator | ~1.25亿 | 基本面指标(key-value) |
| 8 | financial_statement | ~28,378 | 财报数据(key-value) |
| 9 | stock_financials_quarterly | ~16.2万 | 季度财务（宽表） |
| 10 | stock_financials_annual | ~5.7万 | 年度财务（宽表） |
| 11 | shareholders_num | ~4 | 股东人数（旧版） |
| 12 | shareholders_num_v2 | ~5,168 | 股东人数V2 |
| 13 | stock_margin | ~5,023 | 融资融券 |
| 14 | index_daily_kline | ~142万 | 指数日K线 |
| 15 | index_constituents | ~62.3万 | 指数成分股 |
| 16 | index_constituent_weightings | ~119万 | 成分股权重 |
| 17 | rs_daily | ~527万 | 个股RS每日结果 |
| 18 | sector_rs_daily | ~3,330 | 行业板块RS |
| 19 | sector_internal_strength | — | 行业内部强度 |
| 20 | sector_rotation | — | 行业轮动记录 |
| 21 | market_direction_daily | — | 大盘方向分析 |
| 22 | distribution_days_detail | — | 抛盘日明细 |
| 23 | follow_through_days | — | 追盘日 |
| 24 | accumulation_days_detail | — | 吸筹日明细 |
| 25 | distribution_day_features | ~12,339 | 分布日特征工程 |
| 26 | daily_distribution_summary | ~67 | 每日分布日汇总 |
| 27 | market_distribution_days | ~436 | 市场分布日(简化) |
| 28 | market_scan_parameters | ~1 | 扫描参数集 |
| 29 | market_scan_results | ~201 | 扫描结果 |
| 30 | buy_signals_daily | ~0 | 买点信号 |
| 31 | stock_candidates_daily | ~5,220 | 个股扫描结果 |
| 32 | watchlist | 少量 | 自选池 |
| 33 | portfolio_holdings | ~12 | 组合持仓 |
| 34 | portfolio_signals | ~44 | 持仓信号 |
| 35 | seven_week_state | ~7 | 七周规则状态 |
| 36 | sector_strength_results | ~5 | 行业强度 |
| 37 | industry_strength_results | ~1 | 行业RS强度 |
| 38 | index_divergence_results | ~1 | 指数分化分析 |
| 39 | backtest_runs | ~0 | 回测运行记录 |
| 40 | backtest_signals | ~0 | 回测信号明细 |
| 41 | backtest_stats | ~0 | 回测统计 |
| 42 | canslim_quarterly_eps | ~97,713 | CANSLIM季度EPS |
| 43 | canslim_annual_eps | ~17,781 | CANSLIM年度EPS |
| 44 | canslim_institutional | ~7,088 | 基金持股(旧) |
| 45 | stock_institutional_holdings | ~5300/季 | 机构持股汇总(新) |
| 46 | stock_inst_holders_detail | — | 机构持股明细 |
| 47 | stock_analyst_reports | — | 研报覆盖统计 |
| 48 | stock_report_raw | — | 研报原始记录 |
| 49 | stock_buyback | ~62(活跃) | 股票回购 |
| 50 | cansim_scores | — | CAN SLIM评分(新) |
| 52 | pattern_scan_signals | ~3,678/日 | 形态扫描信号 |
| 53 | discipline_observation_pool | ~1,489/日 | 观察池日快照 |
| 54 | discipline_trades | 少量 | 交易记录 |
| 55 | discipline_rules_config | 少量 | 规则配置 |
| 56 | discipline_alerts | 少量 | 告警记录 |
| 57 | discipline_daily_snapshots | 少量 | 持仓日快照 |

---

## 数据库统计（2026-05-21）

| 指标 | 数值 |
|------|------|
| 总表数 | 56 |
| 最大表 | fundamental_indicator (~1.25亿行) |
| K线数据 | daily_kline ~1794万 + weekly_kline ~222万 |
| RS数据 | rs_daily ~527万 |
| 指数K线 | index_daily_kline ~142万 |
| MW信号 | mw_signal_daily ~2367条 |

---

## 50. mw_signal_daily — MW信号（牛市回调后再启动）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| b2_date | TEXT | **UNIQUE(1)**，B2突破日 |
| stock_code | TEXT | **UNIQUE(1)**，股票代码 |
| stock_name | TEXT | 股票名称 |
| confidence | TEXT | 体系1置信度：高/中/低 |
| score | INTEGER | 体系1总分（0~100） |
| confidence_v2 | TEXT | 体系2置信度 |
| score_v2 | INTEGER | 体系2总分（0~115） |
| h_date / h_price | TEXT/REAL | H点前高日期/价格 |
| l_date / l_price | TEXT/REAL | L点最低日期/价格 |
| c_start / c_end | TEXT/TEXT | C横盘区起止日期 |
| b1_date | TEXT | B1突破日 |
| b1_return_pct | REAL | B1涨幅% |
| b1_vol_ratio | REAL | B1量比 |
| b2_return_pct | REAL | B2涨幅% |
| b2_close_pos | REAL | B2收盘位置% |
| b2_is_gap | INTEGER | B2是否跳空 0/1 |
| b2_ma_count | INTEGER | B2突破均线条数 |
| decline_pct | REAL | D段跌幅% |
| c_amplitude_pct | REAL | C段振幅% |
| h_rs20 / h_rs250 | INTEGER | H点股票RS20/RS250 |
| c_amount_avg | REAL | 横盘期日均成交额 |
| h_pre_rise_pct | REAL | H点前60日涨幅% |
| p_max_dd_pct | REAL | P段最大回撤% |
| p_vol_ratio | REAL | P段缩量率（均量/B1量） |
| score_h / score_d / score_c / score_p | INTEGER | HDCP形态评分（15/5/5/15） |
| score_i1 / score_i2 | INTEGER | 行业RS评分（15/10） |
| score_o1 / score_o2 | INTEGER | 已废弃（CANSLIM I/L），恒为0 |
| score_ma | INTEGER | 已废弃（MA排列），恒为0 |
| score_sig | INTEGER | 信号共振评分（累加制，封顶25） |
| score_gap | INTEGER | B2跳空评分（10/0） |
| score_m1 / score_m2 / score_m3 | INTEGER | 体系2新指标（大盘强度/缩量率/跳空） |
| is_plus | INTEGER | PLUS标志（≥65=1） |
| ind_rs20 / ind_rs250 | INTEGER | 行业指数RS20/RS250值 |
| ind_code | TEXT | L2/L1行业指数代码 |
| ind_name | TEXT | L2/L1行业指数名称 |
| scan_date | TEXT | 扫描日期 |
| created_at | TEXT | 创建时间 |

**数据量**：~2367条  
**写入脚本**：`src/scanners/mw_signal.py`  
**读取场景**：MW信号看板、回测分析

### 评分体系（v2.4）

体系1满分100：HDCP形态40(H15/D5/C5/P15) + 行业RS25(I1=15,I2=10,L2优先L1兜底) + 信号共振25(累加制) + B2跳空10

置信度：高≥80 中55~79 低<55

PLUS：总分≥65

---

## 51. chanlun_scan_daily — 缠论批量扫描缓存

| 字段名 | 类型 | 说明 |
|--------|------|------|
| scan_date | TEXT | 扫描日期 |
| stock_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票名称 |
| bi_count | INTEGER | 笔数量 |
| zs_count | INTEGER | 中枢数量 |
| segment_count | INTEGER | 线段数量 |
| latest_bi_dir | TEXT | 最新笔方向 |
| latest_bi_power | REAL | 最新笔力度 |
| divergence_count | INTEGER | 背驰信号数 |
| latest_div_type | TEXT | 最新背驰类型 |
| trade_signal_count | INTEGER | 交易信号数 |
| latest_trade_type | TEXT | 最新交易信号类型 |
| latest_trade_side | TEXT | 最新交易方向 buy/sell |
| latest_trade_price | REAL | 最新交易价格 |
| resonance_strength | REAL | 共振强度 |
| bi_json | TEXT | 完整笔列表JSON（供MW信号H/L检测） |

**数据量**：~4500条/日  
**写入脚本**：`src/scanners/chanlun_scan.py`  
**读取场景**：MW信号H/L检测加速、缠论每日精选

---

## 52. pattern_scan_signals — 形态扫描信号

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)** |
| signals_json | TEXT | 信号JSON（source/type/details） |

**数据量**：~3500条/日  
**写入脚本**：`scripts/daily_pattern_scan.py`  
**读取场景**：MW信号共振评分
| 指数成分 | constituents ~62万 + weightings ~119万 |
| 财务数据 | quarterly ~16万 + annual ~5.7万 + financial_statement ~2.8万 |
| CANSLIM | quarterly_eps ~9.8万 + annual_eps ~1.8万 |
| DB文件大小 | ~41GB |

---

## 53. cockpit_daily — 投资决策驾驶舱每日结果

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| run_date | TEXT | **UNIQUE(1)**，管道运行日期 |
| stock_code | TEXT | **UNIQUE(2)**，股票代码 |
| stock_name | TEXT | 股票名称 |
| rank | INTEGER | 排序（1=最优） |
| signal_types | TEXT | JSON数组，如 ["mw_b2","base_breakout"] |
| signal_date | TEXT | 最近的买入信号日期 |
| confidence | TEXT | 置信度 高/中/低 |
| h_date | TEXT | 前高日期（MW结构） |
| h_price | REAL | 前高价格 |
| l_date | TEXT | 低点日期 |
| l_price | REAL | 低点价格 |
| decline_pct | REAL | H→L 调整深度% |
| consolidation_days | INTEGER | 盘整天数 |
| canslim_total | INTEGER | CAN SLIM 总分 |
| canslim_c~canslim_m | INTEGER | CAN SLIM 各维度分 |
| market_cap | REAL | 市值（亿） |
| l1_industry | TEXT | 所属行业 |
| l1_rs250 | INTEGER | 行业RS250 |
| suggested_position_pct | REAL | 建议仓位% |
| stop_loss_price | REAL | 止损参考价 |
| target_price | REAL | 止盈参考价 |
| sentiment_summary | TEXT | 舆情摘要 |
| oneil_analysis | TEXT | 欧奈尔分析（trade-like-oneil输出） |
| stop_loss_rule | TEXT | 止损规则描述 |
| trailing_stop_rule | TEXT | 移动止损规则 |

**数据量**：~5条/日
**写入脚本**：`src/cockpit/pipeline.py --save`
**读取场景**：投资决策驾驶舱前端 /cockpit/