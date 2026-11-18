# 数据库表结构

> 引擎：SQLite，WAL 模式，PRAGMA synchronous=NORMAL  
> 数据库文件：`data/lixinger.db`  
> 更新时间：2026-09-29  
> 说明：各表章节手写；「全表汇总」「数据库统计」两节由 `scripts/regen_schema_summary.py` 依实测行数重建（重建方式见该节末）

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
| open / high / low / close | REAL | **不复权**（`ex_rights`）原始成交价，全表单一语义，见 §2.1 |
| volume | INTEGER | 成交量 |
| amount | REAL | 成交额 |
| change_pct | REAL | 涨跌幅（该列为**除权调整后**收益），存在**单位混用**缺陷，见 §2.2 |
| turnover_rate | REAL | 换手率 |
| complex_factor | REAL | 复权因子（2026-05 起大量 NULL，勿依赖） |
| adj_open / adj_high / adj_low / adj_close | REAL | ⚠️ **已废弃，勿用**。旧写入脚本漏传 `adjustForwardDate`，列内口径混乱，见 §2.2 |
| ex_open / ex_high / ex_low / ex_close | REAL | 不复权（`ex_rights`），取值同 open/high/low/close。2016 前由 close 回填，2016 起 API 拉取 |
| lxr_fc_open / lxr_fc_high / lxr_fc_low / lxr_fc_close | REAL | **理杏仁前复权**（`lxr_fc_rights`，锚点 2026-12-31）。✅ 唯一可用于收益计算的复权口径，**价格比值 = 理杏仁官方投资收益率**（已用 `tr_dri` 验证） |
| fc_open / fc_high / fc_low / fc_close | REAL | 前复权（`fc_rights`）。⚠️ 加法分红口径，非乘法，**可能出现负价**，仅留档 |
| bc_open / bc_high / bc_low / bc_close | REAL | 后复权（`bc_rights`，锚点 1996-01-02）。⚠️ 加法分红口径，价格比值≠真实收益，仅留档 |

**数据量**：1,896 万条（1996-01-02 ~ 2026-09-10；其中 2016-01-01 起 6,167 只 / 1,157 万条）  
**索引**：`idx_daily_kline_date(date)`, `idx_daily_kline_stock(stock_code)`  
**数据来源**：理杏仁 API #4 K线数据  
**写入脚本**：`scripts/fetch_stock_daily_kline.py`（每日增量，`date` 单日全市场，仅不复权）
　　　　　　`scripts/fetch_kline_multi_adjust.py`（四口径全量/增量，2026-09 一次性重建）  
**读取场景**：RS计算、技术指标计算、内部共振验证

### 2.1 价格口径说明

**close 的历史口径变迁（2026-09 已修复）**

| 区间 | 旧口径 | 现状 |
|------|--------|------|
| 1996-01-02 ~ 2016-09-30 | 不复权 | 不复权（未变） |
| 2016-10-10 ~ 2018-12-31 | ❌ 前复权（污染段） | **已重写为不复权** |
| 2019-01-02 ~ | 不复权 | 不复权（未变） |

污染段成因：`scripts/pull_adj_close.py` 调用 `type=lxr_fc_rights` 时未传 `adjustForwardDate`（文档要求复权类必传，不传等同不复权），把不复权结果写进了 `adj_close`；而 `close` 的 2016-2018 段来自另一路前复权来源，两列混用。市场级证据：2016-10-10（2,494 只）与 2019-01-02（2,985 只）出现过整体台阶。

2026-09 由 `scripts/fetch_kline_multi_adjust.py` 重写 2016-01-01 起的 `open/high/low/close` 为 `ex_rights`，并补齐四口径列。**此后 `close` 全表 = 不复权。**

**四种口径的官方定义与实测性质**

理杏仁 K线 API（`/company/candlestick`）的 `type` 取值与官方命名（见 `docs/lixinger_apis_help/4. K线数据API.md`）：

| `type` | 官方名称 | 口径性质 | 实测结论（600309 2016 全年） |
|--------|---------|---------|------------------------------|
| `ex_rights` | 不复权 | 原始成交价 | ground truth |
| `lxr_fc_rights` | **理杏仁前复权** | 分红再投入，**乘法** | `k = lxr_fc/ex` 为阶梯常数，仅除权日跳变 ✅ |
| `fc_rights` | 前复权 | 传统「总价值不变」，**加法** | 复权价 = 原价 − 累计现金分红；比值≠收益；锚点拉远时出负价（600309 2016 年最低 -0.81） |
| `bc_rights` | 后复权 | 同为**加法** | 复权价 = 原价 × 送转因子 + 累计现金分红；其 `change` 字段与自身价格比值不符（0.946% vs 1.13%） |

理杏仁在官方 wiki（`https://www.lixinger.com/wiki/cagr-profit`）中说明：传统前复权「**会导致过去的股价失真，进而导致甚至出现负数的情况**」，后复权「**理论上只能是动态值**」。因此他们另建了**分红再投入**口径，即「理杏仁前复权」：把每次现金分红按除权日价格折算成新增份额，等价于一个乘法因子，不会出负价。

**口径正确性验证：`lxr_fc` 的价格比值 = 理杏仁官方投资收益率**

用官方「分红再投入收益率」接口 `/company/hot/tr_dri` 做三方对照（终点 2026-09-10）：

600309 万华化学

| 期间 | 官方 DRI | ex 不复权 | **lxr_fc** | fc 加法 | bc 加法 |
|------|---------|----------|-----------|---------|---------|
| 近 1 年累计 | 16.10% | 14.23% | **16.10%** ✅ | 16.38% | 12.87% |
| 近 3 年年化 | −4.80% | −6.58% | **−4.83%** ✅ | −5.18% | −4.28% |
| 近 5 年年化 | −4.00% | −6.00% | **−4.00%** ✅ | −4.46% | −3.70% |
| 近 10 年年化 | 19.33% | 14.45% | **19.49%** ✅ | **44.64%** ❌ | 16.89% |

002648 卫星化学

| 期间 | 官方 DRI | ex | **lxr_fc** | fc | bc |
|------|---------|-----|-----------|-----|-----|
| 近 1 年累计 | 44.37% | 41.34% | **44.37%** ✅ | 44.99% | 40.20% |
| 近 5 年年化 | 7.17% | −8.18% | **7.17%** ✅ | 6.92% | 6.29% |
| 近 10 年年化 | 24.92% | 11.41% | **24.94%** ✅ | **35.19%** ❌ | 23.49% |

`lxr_fc` 与官方 DRI 精确吻合（1 年尺度小数点后两位一致）；`fc` 在长周期严重失真。
可复现脚本：`scripts/_verify_dri_vs_qfq.py`。

**使用规范（下游必须遵守）**

| 用途 | 应取列 | 说明 |
|------|--------|------|
| 收益 / 收益率 / 回测 / RS | **`lxr_fc_*`** | 比值即官方收益率，可与 `tr_dri` 接口对拍验证 |
| 真实成交价、涨跌停判断、成交额校验 | `ex_*`（等同于 `open/high/low/close`） | 原始价 |
| `fc_*` / `bc_*` | 仅留档 | **禁止用于任何收益计算**；若使用必须过滤 `<= 0` |
| ~~`adj_*`~~ | **已废弃，勿用** | 见 §2.2 |

⚠️ 历史遗留写法 `COALESCE(adj_close, close)` 实际恒等于不复权价（因 `adj_*` 已失效），全站凡用此写法的引擎都缺失分红收益。迁移清单见 `docs/dev/价格口径统一_迁移待办清单.md`。

**锚点常量**（写在 `fetch_kline_multi_adjust.py` 内）

- `adjustForwardDate = 2026-12-31`：用于 `lxr_fc_rights` / `fc_rights`，须**严格大于** `endDate`（等于会被 403 拒绝，报错文案有误导性）
- `adjustBackwardDate = 1996-01-02`：用于 `bc_rights`，须不大于 `startDate`，固定以保跨窗口同尺度
- 窗口切分：`[2016-01-01, 2025-12-31]` + `[2026-01-01, today]`（API 限制 startDate~endDate 跨度 ≤10 年）
- 单日查询（`date` 参数）时 `stockCode` 不生效，返回全市场；这也是 `fetch_stock_daily_kline.py` 的增量方式

### 2.2 已知数据缺陷

1. **`change_pct` 单位混用（已于 2026-09-10 修复）**
   原状：95 只股票的 285,329 行存成百分数形态（`19.17` 表示 19.17%），其余 1,870 万行为小数量纲。
   修复：由 `scripts/fix_change_pct_unit.py` 统一为小数（整段 `/100`）。清单 `analysis/change_pct_unit_manifest.csv`，原值备份 `analysis/_change_pct_backup.csv`。
   可整段转换的关键依据：这 95 只的百分数形态区间**全部终止于 `2016-09-30` 之前**（与 `close` 口径接缝同日，属同一次数据源切换），且区间内不含小数列，故无需逐行判定。
   该列语义为**除权调整后收益率**（小数），已验证与 `lxr_fc` 日收益率 100% 一致（600309 在 2016-2018 段 605/605、002648 728/728，最大偏差 < 0.02pp）。
   - ⚠️ 除权日的 `change_pct` 与 `close/prev_close-1` 不相等属**正常**（前者是除权调整后收益）。`scripts/audit_close_integrity.py` 的判据把除权日误判为「凭空跳变」，需修正
   - 注：`_ensure_adj_prices` / `box_breakout._adj_prices` 用 `change_pct` 反推前复权价，**逻辑本身正确**（反推出的就是 `lxr_fc` 口径、锚定在窗口末日），但锚点随窗口浮动导致跨窗口不可比。改用 `lxr_fc_*` 可解决
   - 仍有 6,908 行 `|change_pct| > 0.31`（超出创业板 30% 上限），绝大多数是**停牌复牌后的累积涨跌幅**（如 000004 停牌两个月后 -88.77%）与北交所/新股，属正常；少量来自源头价格跳变（000013 在 2003-2004 年价格在 4.97 与 104.75 之间反复跳变）
2. **`adj_*` 四列不可用**：2019-01-02 起 ≡ close；2026-05-08 起 NULL；2016-10 ~ 2018-12 段反而是正确的原始价。替代：用 `ex_*` 或 `lxr_fc_*`。
3. **`complex_factor` 自 2026-05 起大量 NULL**：理杏仁疑似停返。
4. **`fc_*` / `bc_*` 为加法口径**：下游若使用，必须过滤 `<= 0` 的行，且不得用其比值计算收益。
5. **上市初期连续一字板段的前复权缺陷**：`lxr_fc_rights` 在个股上市初期连续一字涨停段会返回常数价（300750 在 2018-06-12 ~ 06-21 七日均为 36.798，真实区间 36.20 → 70.54）。属 API 源头缺陷。
6. **北交所新股上市初期无 `lxr_fc_*`（已回填）**：理杏仁 `lxr_fc_rights` 对上市未满约 2~3 周的股票不返回数据，同期 `ex_rights` 正常（920158 IPO 2025-10-16，ex 返回 12 行而 lxr_fc 返回 0 行）。受影响 15 只 920xxx，共 168 行。
   已由 `scripts/fix_lxr_fc_ipo_gap.py` 回填。依据是可证等式：复权因子 `k(t) = lxr_fc/ex` 仅在除权日跳变，故只要缺口内无除权事件，就可用缺口后同一段的 k 外推。实现取首个有值日后 20 个交易日的中位数（首日存在约 0.5% 的 warm-up 偏差，920158 首日 k=0.7033 而稳定值 0.6999），并用分红 API 校验缺口内无已实施除权日。
7. **2 只 ETF 无四口径**：`513120`（港股创新药ETF，968 行）、`159201`（自由现金流ETF，332 行）存在于 `daily_kline`，但 `/company/candlestick` 只覆盖个股，范围查询对 ETF 返回 0 行，单日全市场查询也不再包含它们。这 1,300 行的 `ex_*`/`lxr_fc_*`/`fc_*`/`bc_*` 全为 NULL，且 K 线停在 `2026-07-09`。待决策：移出 `daily_kline` 或标注保留。
8. **上市初期 warm-up 偏差**：股票首个有 `lxr_fc` 值的那一天，`k` 可能存在约 0.5% 的偏差（见第 6 条）。用 `lxr_fc` 算收益率时，每只股票的绝对首日值需保留该偏差的余地。

### 2.3 每日增量与断点续传

**口径列不会自动维护**，这是本表最容易踩的坑：

| 脚本 | 写入列 |
|------|--------|
| `fetch_stock_daily_kline.py`（默认） | `open/close/high/low/volume/amount/change_pct/turnover_rate/complex_factor`。用 `date` 单日全市场查询，**带不上复权口径**（`type` 被忽略） |
| `--caliber --types lxr_fc`（每日） | `ex_*` + `lxr_fc_*`。已接入 `daily_update.py` 步骤 5b，约 22 分钟 |
| `--caliber --types fc,bc`（每周一） | `fc_*` + `bc_*`（留档）。步骤 5c，约 44 分钟 |

`ex_*` 由本地从 `open/close` 推导（同口径等值），**不耗 API 调用**。

**为何不能一次调用刷全市场**：范围查询（`startDate/endDate`）**强制要求 `stockCode`**，且不支持批量传数组。实测：`date + type` 会忽略 type；`date + type + 锚点` 行为异常；范围查询不带 stockCode 报 400 `"stockCode" is required`。
所以四口径只能逐只调：**调用量 = 股票数 × 口径数，与窗口长度无关**。实测 4.84 次/秒，全市场（约 6,266 只 × 3 口径）≈ 65 分钟/天。

**⚠️ 尺度接缝（关键）**：前复权序列的绝对水平依赖「基准日当时已知的除权事件集合」，**每发生一次新除权，整个历史序列会整体平移一个因子**。
实证：600309 在 2026-09-11 除权派现 0.81 元，当天 API 返回的 2018 年历史值比上午下载的低了 1.065%，恰好等于 0.81/76.34。
因此**不能把新拉的数据裸追加**，否则新旧数据之间会出现一个等于该次分红率的跳变，跨接缝的收益率会凭空多出一次跌幅。

`--caliber` 的默认处理：发现 `scale = 已存(锚点日) / 新拉(锚点日) ≠ 1` 时，
**把该股从最早交易日到今天的整条序列重新下一次（接口原值直写，不做任何缩放）**。
理杏仁自己会回算，直接取它的值是最不会出错的做法。
只有当日发生除权的股票会触发，量很小（2026-09-11 全市场仅 19 只）。

为何不用「常数因子缩放全历史」（曾这么做过，已改）：数学上它在多数情况下等价，
但前提是「理杏仁的新序列是旧序列的等比例缩放」。**配股不满足这个前提**
（理杏仁对配股按「转入资金参与配股、收益率时间加权」处理，引入了新增资金）——
那时因子缩放会静默算出错值。所以改为直接重下。
缩放仍作为**兜底**保留：整条重下失败时才退回因子缩放，并在日志里标注。

加 `--keep-scale` 则保留旧尺度（把新数据缩放回旧序列，历史不动）。
代价是库内绝对价位与理杏仁/通达信存在一个**常数偏移**
（2026-09-11 实测偏移是 1.010654，逐日比值恒定）。

**验证**：600309 在 2026-09-09/10/11 四价与理杏仁 API 逐日比值 = 1.000000；
002648 全序列 3,546 个交易日与 API 逐点一致（相对误差 < 1e-9）。

**断点续传**：`daily_kline_multi_progress(stock_code, adj_type, start_date, end_date, rows, ts)` 按 (股票, 口径, 窗口) 登记完成状态。重跑 `fetch_kline_multi_adjust.py` 自动跳过已完成项；`--reset` 清空进度表后重跑。

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

## 12. shareholders_num_daily — 股东人数时序（2026-08-28 新增，筹码集中度）

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | 股票代码 |
| date | TEXT | 报告期截止日（季度披露） |
| total | INTEGER | 股东人数 |
| change_rate | REAL | 股东人数变化率（理杏仁）或 akshare 推算 |
| price_change | REAL | 股价涨跌幅（理杏仁 spc） |
| source | TEXT | lx=理杏仁 / ak=东财 akshare |
| updated_at | TEXT | 更新时间 |

**数据量**：~2 万条+（2026-08-28 起拉取，全市场进行中）  
**数据来源**：理杏仁 API #5 股东人数（主源，全市场覆盖）+ akshare `stock_zh_a_gdhs_detail_em` 兜底  
**写入脚本**：`scripts/fetch_shareholders_num.py`  
**用途**：stock-valuation「👥 股东人数」tab——筹码集中度观察（人数减少=筹码集中，创新高=分散）

## 13. index_full_return_daily — 指数全收益（2026-08-28 扩展为 8 指数）

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | 指数代码（原代码入库，如 000922/H30269/930914/930839） |
| date | TEXT | 交易日 |
| close | REAL | 全收益收盘点位（含分红再投资） |
| change_pct | REAL | 涨跌幅 |
| pe_ttm | REAL | 市盈率（暂未填） |
| updated_at | TEXT | 更新时间 |

**数据量**：~2.3 万条 / 9 指数（8 全收益 + 旧 H00922）  
**数据来源**：理杏仁 `/index/candlestick type=total_return`（全收益率点位，**2016 前无数据**）  
**写入脚本**：`scripts/fetch_index_full_return.py`（增量，daily_update 5.5）  
**用途**：dividend-advice 回撤基准 + full-return-compare 全收益对比（起点=100）+ 红利 RSI 买卖点的前瞻收益与胜率（见 §85）

---

## 14. shareholders_num_v2 — 股东人数V2

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

## 15. daily_margin_history — 融资融券逐股数据（主表）

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| date | TEXT | **PK(2)**，日期 |
| financing_balance | REAL | 融资余额（元） |
| securities_balance | REAL | 融券余额（元） |
| net_purchase | REAL | 当日净买入（元） |

**数据量**：~498,000 行（2026-01~今，每日约 3,835 只两融标的）
**数据来源**：理杏仁 API 融资融券（旧接口）
**写入脚本**：`scripts/fetch_margin_daily.py`（每日，daily_update 步骤 7）
**读取场景**：`/api/market-scan/margin-history` → 前端融资融券分析页；`src/scanners/market_bottom.py` 的杠杆出清判据；`scripts/gen_sector_flow.py`、`scripts/gen_daily_market_summary.py`、`scripts/daily_review.py` 的两融章节
**数据质量**：部分日期（如 07-17/07-22/07-23）出现仅 2,580 只的问题，需 `--date` 补跑修复

> **口径铁律（2026-11-18）**：全库唯一的「融资余额 / 融券余额 / 融资净买入」来源就是这张表。
> 不要再引入第二张两融表 —— 曾存在的 `daily_review_margin` 因为按抓取日打标签（交易所 T+1 公布），
> 峰值算低 3,143 亿、回撤算浅 8.8pp，已于 2026-11-18 删表，详见 §16。
>
> **自检方法**：任何两融序列，先验「逐日余额变动 ≈ 逐日净买入」。本表 2026 年以来相关系数 0.9991；
> 若某个来源低于 0.99，说明日期标签或数据新鲜度有问题，不要用它做趋势判断。
>
> **T+1 处理**：本表数据日通常比行情日晚一个交易日。做窗口统计时锚到
> `SELECT MAX(date) FROM daily_margin_history`，不要用行情最后一天，否则最后一天取空值。

> ⚠️ 2026-07-25 清理：已删除冗余表 `stock_margin`（同源数据，字段多但停在 07-22）和 `daily_margin_total`（汇总表，已弃用）。融资融券以本表为准。

---

## 16. ~~daily_review_margin~~ — 已废弃（2026-11-18 删表）

**此表已删除，不要再使用、不要重建。** 两融数据一律读 §15 `daily_margin_history`。

原表结构（仅作历史记录）：`date / stock_code / margin_balance / margin_fb / margin_sb /
net_buy_d1 / fb_balance / sb_balance`，主键 `(date, stock_code)`，删除时 798,096 行。

**为什么废弃**（这不是命名问题，是口径错误）：

| 问题 | 实测证据 |
|---|---|
| `date` 是**抓取日**，不是数据日 | 交易所 T+1 才公布两融余额。逐股核对 `review[D] == history[D-1]`，600519 连续 8 个交易日精确相等，全样本 207 天平均绝对差 713 亿元 |
| 2026-05~07 有**未刷新的冻结段** | 600519 在 06-22~06-26 五天都是 182.20 亿 / 净买入 0.78 亿，完全不动 |
| 余额与净买入**不自洽** | 2026 年以来「余额日变动」与「净买入」的相关：`daily_margin_history` **0.9991**（均值 −15.2 vs −15.3，几乎相等），本表只有 **0.9039** |
| 汇总值系统性偏低 | 2026 年 5~7 月高于 history 4.6%~9.0%，其余月份低 0.7%~2.7%；用它算年内峰值把高点算低 3,143 亿（25,719 vs 28,862），回撤算浅 8.8pp（18.8% vs 27.6%） |

**迁移记录**：`scripts/daily_review.py` 的 `fetch_margin()` 改为只读 `daily_margin_history`
（取 `MAX(date) WHERE date<=?`，T+1 自动回退，并把数据日显示在报告里）；建表 DDL 已删，
不会再被重新创建。同期改正的读取方：`src/scanners/market_bottom.py`（杠杆出清）、
`scripts/gen_sector_flow.py`（板块资金）、`scripts/gen_daily_market_summary.py`（大盘总结）。

---

## 17. index_daily_kline — 指数日K线

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
**读取场景**：RS计算的行业/市场基准、行业技术指标(MA200/涨跌幅/成交量)、红利 RSI 买卖点的价格序列（取 `kline_type='normal'`，见 §85）

---

## 18. index_full_return_daily（已废弃，见 §13）— 中证全收益指数日线（红利回撤基准）

> ⚠️ 2026-08-28 已升级：见 §13（8 指数、理杏仁 total_return、2016 起）。本旧条目（H00922 单指数、akshare 中证官网源）仅历史记录，写入脚本已由 daily_update 5.5 替换为 `fetch_index_full_return.py`。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，全收益指数代码（如 H00922 中证红利全收益） |
| date | TEXT | **PK(2)**，交易日期（格式 2026-08-13，与 index_daily_kline 一致） |
| close | REAL | 收盘价（含分红再投资） |
| change_pct | REAL | 涨跌幅（小数，0.01=1%） |
| pe_ttm | REAL | 滚动市盈率 |
| updated_at | TEXT | 更新时间 |

**数据量**：2092条（H00922，2018-01-01 起）  
**数据来源**：akshare `stock_zh_index_hist_csindex`（中证官网）  
**写入脚本**：`scripts/fetch_full_return_index.py`（daily_update 步骤 5.5）  
**读取场景**：红利模块 250日回撤计算（`_dd_from_full_return`，优先全收益降级价格）、红利详情页价格vs全收益对比线、年度回撤、回撤实验室  
**备注**：⚠️ 日期必须带横杠格式（曾踩坑：无横杠导致字符串比较错乱）

---

## 19. bond_yield_daily — 中国国债收益率（红利温度计股债息差）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| date | TEXT | **PK**，日期（中债估值日频，自然日非交易日） |
| y2 | REAL | 中国国债收益率 2 年（%） |
| y5 | REAL | 中国国债收益率 5 年（%） |
| y10 | REAL | 中国国债收益率 10 年（%，主用） |
| y30 | REAL | 中国国债收益率 30 年（%） |
| spread_10_2 | REAL | 10 年−2 年利差（%） |
| updated_at | TEXT | 更新时间 |

**数据量**：2299条（2018-01-02 ~ 2026-08-17）  
**数据来源**：akshare `bond_zh_us_rate`（中债估值口径）  
**写入脚本**：`scripts/fetch_bond_yield.py`（daily_update 步骤 5.6，全量 --full / 增量）  
**读取场景**：红利温度计股债息差（`red_dividend_metrics.compute_spread`，股息率 − 10年国债收益率）  
**备注**：⚠️ 列名模糊匹配防上游改名（改名时中止避免坏数据落库）

---


## 20. dividend_records — 分红记录（红利观察）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| code | TEXT | **PK(1)**，个股/基金代码 |
| kind | TEXT | **PK(2)**，stock / fund_off / fund_etf |
| ex_date | TEXT | **PK(3)**，除权除息日 |
| dividend | REAL | 每股分红（元）|
| payout_ratio | REAL | 派息率（0-1小数，仅个股理杏仁）|
| total_amount | REAL | 分红总额（仅个股）|
| status | TEXT | implemented/plan（仅个股）|
| source | TEXT | lixinger / akshare / tx_reverse |

**数据源**：个股→理杏仁 `/company/dividend`；场外基金→akshare 分红送配；场内ETF→腾讯复权差异反推（估算，送转年份偏差）
**写入脚本**：`scripts/fetch_dividends.py`（daily_update 步骤 5.8）
**读取场景**：分红可持续性（`/api/market-scan/div-sustainability`）

---

## 21. hk_etf_daily / hk_etf_full_return — 港股红利ETF 日线/全收益

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，ETF 代码（513820/159691/513630/159545）|
| date | TEXT | **PK(2)**，交易日期 |
| open/high/low/close | REAL | 价格（hk_etf_daily）|
| volume | REAL | 成交量 |
| close | REAL | 后复权收盘（hk_etf_full_return，全收益口径）|

**数据源**：新浪 fund_etf_hist_sina（日线）+ 腾讯 fqkline hfq（全收益）
**写入脚本**：`scripts/fetch_hk_etf.py`（daily_update 步骤 5.7）
**读取场景**：港股红利观察（`/api/market-scan/hk-etf`）、全收益对比（`/api/market-scan/full-return-compare`）

---
## 22. index_constituents — 指数成分股

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
- **行业RS映射（MW信号用）**：`mw_signal.py` 查此表获取股票归属的 L2/L1 指数代码 → 查 `index_rs_daily` 取该指数 `rs_250` → 选最高值作为行业 RS
- 内部共振验证：获取行业成分股列表
- `DataFetcher.load_constituents()` 和 `DataFetcher.load_constituent_rs_median()`

---

## 23. index_constituent_weightings — 指数成分股权重

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

## 24. rs_daily — 个股RS每日计算结果

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

> ✅ 2026-06-12: 已补齐 2016 年数据，现覆盖 2016-01-04 ~ 2026-06-11（1866 个交易日，~445 万行）

---

## 25. stock_rs_daily — 个股RS每日结果（新版宽表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)** |
| close / adj_close | REAL | 收盘价 / 复权收盘价 |
| ret_20 / ret_60 / ret_120 / ret_250 | REAL | 各周期收益率(%) |
| rps_20 / rps_60 / rps_120 / rps_250 | REAL | 各周期RPS排名(0~99) |
| rs_line | REAL | RS线（归一化值） |
| amount | REAL | 成交额 |

**数据量**：~445 万行（2016-01-04 ~ 2026-06-11，1866 个交易日）  
**写入脚本**：`scripts/backfill_rs.py`（批量） `src/scanners/stock_rs.py`（每日）  
**读取场景**：MW 信号 H 点 RS 取值、口袋支点 RS 过滤、CAN SLIM L 维度评分

---

## 26. index_rs_daily — 指数RS每日结果

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，指数代码（如 000988=全指工业） |
| date | TEXT | **PK(2)** |
| close | REAL | 收盘价 |
| ret_20 / ret_60 / ret_120 / ret_250 | REAL | 各周期收益率(%) |
| rs_20 / rs_60 / rs_120 / rs_250 | INTEGER | 各周期RS排名（分类内百分位，0~99） |
| ma50 / ma150 / ma200 | REAL | 均线值 |
| ad_line | REAL | A/D 线累计值 |
| ad_slope_20d | REAL | A/D 线 20 日斜率 |

**数据量**：~100 万行（2016-01-01 ~ 2026-06-11，2730 个交易日，408 个指数）  
**数据来源**：`config/index_style.yaml`，含 5 大分类（market/sector_l1/sector_l2/thematic/strategy）  
**写入脚本**：`scripts/backfill_index_rs.py`（批量） `src/scanners/index_rs.py`（每日）  
**读取场景**：
- **MW 信号行业 RS 映射**：查 `index_constituents` 获取股票归属的 L2/L1 指数 → 取该指数 `rs_250` 最高值 → 写入 `mw_signal_daily.ind_rs250`
- 最强指数筛选、指数看板

---

## 27. sector_rs_daily — 行业板块RS每日结果

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

## 28. sector_internal_strength — 行业内部强度明细

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

## 29. sector_rotation — 行业轮动记录

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

## 30. market_direction_daily — 大盘方向每日分析

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

## 31. distribution_days_detail — 抛盘日明细

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

## 32. follow_through_days — 追盘日

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

## 33. accumulation_days_detail — 吸筹日明细

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

## 34. distribution_day_features — 分布日特征工程

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

## 35. daily_distribution_summary — 每日分布日汇总

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

## 36. market_distribution_days — 市场分布日（简化版）

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

## 37. market_scan_parameters — 抛盘日扫描参数集

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

## 38. market_scan_results — 抛盘日扫描结果

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

## 39. buy_signals_daily — 每日买点信号

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

## 40. stock_candidates_daily — 个股扫描每日结果

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

## 41. watchlist — 自选池

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

## 42. pattern_scan_signals — 形态扫描信号（简表，完整定义见 §70）

| 字段 | 类型 | 说明 |
|------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)**，扫描日期 |
| signals_json | TEXT | JSON 信号数组 |

**数据量**：~3,678 条/日  
**写入脚本**：`scripts/daily_pattern_scan.py`（Step 3.5）  
**读取场景**：复核详情页信号标记、观察池信号列

---

## 43. discipline_observation_pool — 观察池日快照

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

## 44. discipline_trades — 交易记录

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

## 45. discipline_rules_config — 规则配置

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

## 46. discipline_alerts — 告警记录

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

## 47. discipline_daily_snapshots — 持仓日快照

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

## 48. portfolio_holdings — 组合持仓

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

## 49. portfolio_signals — 持仓信号

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

## 50. seven_week_state — 七周规则状态

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

## 51. sector_strength_results — 行业强度

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

## 52. industry_strength_results — 行业RS强度

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

## 53. index_divergence_results — 指数分化分析

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

## 54. backtest_results — 全信号回测结果

> M0 回测引擎产出。每行为一笔回测交易（信号 × 入场方式 × 持有期）。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| stock_code | TEXT | 股票代码 |
| signal_date | TEXT | 信号日期 |
| signal_mask | INTEGER | 信号位掩码（见下方） |
| combo_label | TEXT | 信号组合标签 |
| signal_count | INTEGER | 当天触发的信号数 |
| entry_method | TEXT | 入场方式：`T+0_C`/`T+1_O`/`T+2_O` |
| hold_days | INTEGER | 持有天数：5/10/20/60 |
| market_regime | TEXT | 市场环境：`bull`/`bear`/`ranging` |
| pool_mode | TEXT | 股票池：`full`/`filtered` |
| entry_price | REAL | 入场价格 |
| exit_price | REAL | 出场价格 |
| ret_pct | REAL | 毛收益（%） |
| net_ret_pct | REAL | 净收益（%，已扣 0.3% 成本） |
| is_win | INTEGER | 是否盈利 0/1 |
| peak_ret_pct | REAL | 持有期内最大浮盈 |
| trough_ret_pct | REAL | 持有期内最大浮亏 |
| index_ret_pct | REAL | 同期基准（000985）收益 |
| excess_ret_pct | REAL | 超额收益 |
| skipped_reason | TEXT | 跳过原因（停牌/涨跌停/无K线） |

**信号位掩码**：bit0=MW_B1, bit1=MW_B2, bit2=MW_PLUS, bit3=PP_V1, bit4=PP_V2, bit5=BO_V2  
**数据量**：~9,500,000 行  
**索引**：`idx_bt_query(signal_mask, entry_method, hold_days, signal_date)`  
**唯一键**：`(stock_code, signal_date, entry_method, hold_days, pool_mode)`  
**写入脚本**：`scripts/backtest_signals.py`  
**读取场景**：赢家因子分析、信号回测统计

**常用查询**：MW B1 H20/T+1_O
```sql
SELECT * FROM backtest_results 
WHERE signal_mask & 1 = 1 AND entry_method='T+1_O' AND hold_days=20
```

---

## 55. signal_events — 信号事件汇总

> 回测引擎的中间表。每行为一个交易日某个股票触发的所有信号聚合。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，股票代码 |
| date | TEXT | **PK(2)**，信号日期 |
| stock_name | TEXT | 股票名称 |
| signal_mask | INTEGER | 位掩码（同 backtest_results） |
| combo_label | TEXT | 如 `MW_B1+PP_V1` |
| signal_count | INTEGER | 当天触发的信号数 |
| mw_b1_decline_pct | REAL | MW B1 调整深度 |
| mw_b1_h_rs250 | INTEGER | MW B1 H 点 RS250 |
| pp_v1_vol_ratio | REAL | PP_V1 量比 |
| pp_v1_rps_250 | INTEGER | PP_V1 RPS250 |
| pp_v2_vol_ratio | REAL | PP_V2 量比 |
| bo_v2_vol_ratio | REAL | BO_V2 量比 |
| bo_v2_decline_pct | REAL | BO_V2 调整深度 |

**数据量**：~841,000 行  
**写入脚本**：`scripts/backtest_signals.py`（step1）  
**读取场景**：回测引擎遍历信号

---

## 56. stock_equity_change — 股本变动历史

> 从理杏仁股本变动 API 拉取。用于计算换手率。

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| stock_code | TEXT | 股票代码 |
| change_date | TEXT | 变动日期 |
| declaration_date | TEXT | 公告日期 |
| change_reason | TEXT | 变动原因 |
| capitalization | REAL | 总股本（股） |
| outstanding_shares_a | REAL | 流通A股（股） |
| limited_shares_a | REAL | 限售A股（股） |
| cap_change_ratio | REAL | 总股本变动比例 |
| outstanding_a_change_ratio | REAL | 流通A股变动比例 |

**数据量**：~156,000 行（5,388 只股票，2016-2026）  
**索引**：`idx_sec_code(stock_code)`, `idx_sec_date(change_date)`  
**写入脚本**：`scripts/backfill_equity_change.py`  
**API**：`src/data/lixr_api/api_stock_equity_change.py`  
**读取场景**：计算换手率 = `c_amount_avg / (outstanding_shares_a × close)` × 100%

**按日期查流通股本**：
```sql
SELECT outstanding_shares_a FROM stock_equity_change 
WHERE stock_code=? AND change_date<=? 
ORDER BY change_date DESC LIMIT 1
```
| reversal_count | INTEGER | 盘中反转数 |
| weighted_count | INTEGER | 加权合计 |
| avg_vol_10d | REAL | 平均10日波动率 |
| avg_volume_ratio | REAL | 平均量比 |

**数据量**：~0 条  
**写入脚本**：`src/server.py`（回测API）  
**读取场景**：回测统计汇总

---

## 57. canslim_quarterly_eps — CANSLIM季度EPS

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

## 58. canslim_annual_eps — CANSLIM年度EPS

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

## 59. canslim_institutional — CANSLIM基金持股

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

## 60. canslim_scores — CANSLIM七维度评分

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

## 61. stock_institutional_holdings — 机构持股汇总

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

## 62. stock_inst_holders_detail — 机构持股明细

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

## 63. stock_analyst_reports — 研报覆盖统计

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

## 64. stock_report_raw — 研报原始记录

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

## 65. stock_buyback — 股票回购

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

## 66. cansim_scores — CAN SLIM评分结果

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

## 67. market_breakout_daily — 放量突破个股明细

| 字段 | 类型 | 说明 |
|------|------|------|
| date | TEXT | **PK(1)**，交易日 |
| stock_code | TEXT | **PK(2)**，股票代码 |
| close | REAL | 收盘价 |
| change_pct | REAL | 涨跌幅（小数） |
| volume | REAL | 成交量 |
| amount | REAL | 成交金额 |
| vol_ma50 | REAL | MA50成交量均额 |
| vol_ratio | REAL | 成交量倍数（volume / vol_ma50） |
| break_ma | TEXT | 突破的最高级别均线（MA5/MA10/MA20/MA50/MA120/MA200） |

**数据量**：~110 条/天  
**数据来源**：`src/scanners/market_health.py` → `compute_vol_breakout()`  
**写入时机**：`python src/scanners/market_health.py --date YYYY-MM-DD`  
**读取场景**：
- `/api/market-health/breakouts` → `web/market-scan/breakouts.html` 独立页面
- `market-scan/index.html` 辅助指标卡片点击跳转

**计算规则**：
- 放量突破条件：`volume > MA50(volume) × 1.5` 且 `close > 前20日最高价`
- `vol_ratio` = `volume / vol_ma50`
- `break_ma`：从 MA200→MA120→MA50→MA20→MA10→MA5 逐级检查，取 close 突破的最高级别

---

## 全表汇总

| # | 表名 | 行数 | 说明 |
|---|------|------|------|
| 1 | stock_basic | 5,712 | 股票基础信息 |
| 2 | daily_kline | 1902.3万 | 个股日K线 |
| 4 | stock_industry | 1.7万 | 所属行业 |
| 5 | stock_index | 2.0万 | 所属指数 |
| 6 | stock_sw_industry | 5,500 | 申万一级行业映射 |
| 7 | fundamental_indicator | 2.86亿 | 基本面指标 |
| 8 | financial_statement | 2.8万 | 财报数据 |
| 9 | stock_financials_quarterly | 23.5万 | 季度财务 |
| 10 | stock_financials_annual | 6.8万 | 年度财务 |
| 11 | shareholders_num | 4 | 股东人数 |
| 12 | shareholders_num_daily | 20.9万 | 股东人数时序 |
| 13 | index_full_return_daily | 2.9万 | 指数全收益 |
| 14 | shareholders_num_v2 | 5,168 | 股东人数V2 |
| 15 | daily_margin_history | 67.4万 | 融资融券逐股数据 |
| 17 | index_daily_kline | 149.0万 | 指数日K线 |
| 19 | bond_yield_daily | 2,330 | 中国国债收益率 |
| 20 | dividend_records | 297 | 分红记录 |
| 21 | hk_etf_daily | 5,106 | 港股红利ETF 日线/全收益 |
| 21 | hk_etf_full_return | 3,078 | 港股红利ETF 日线/全收益 |
| 22 | index_constituents | 62.3万 | 指数成分股 |
| 23 | index_constituent_weightings | 119.5万 | 成分股权重 |
| 25 | stock_rs_daily | 1148.4万 | 个股RS每日结果 |
| 26 | index_rs_daily | 103.2万 | 指数RS |
| 29 | sector_rotation | 3,330 | 行业轮动记录 |
| 30 | market_direction_daily | 313 | 大盘方向分析 |
| 31 | distribution_days_detail | 330 | 抛盘日明细 |
| 32 | follow_through_days | 13 | 追盘日 |
| 33 | accumulation_days_detail | 10 | 吸筹日明细 |
| 34 | distribution_day_features | 1.2万 | 分布日特征工程 |
| 35 | daily_distribution_summary | 67 | 每日分布日汇总 |
| 36 | market_distribution_days | 436 | 市场分布日 |
| 37 | market_scan_parameters | 1 | 扫描参数集 |
| 38 | market_scan_results | 201 | 扫描结果 |
| 40 | stock_candidates_daily | 5,220 | 个股扫描结果 |
| 41 | watchlist | 37 | 自选池 |
| 43 | discipline_observation_pool | 7.6万 | 观察池日快照 |
| 44 | discipline_trades | 7 | 交易记录 |
| 45 | discipline_rules_config | 0 | 规则配置 |
| 46 | discipline_alerts | 546 | 告警记录 |
| 47 | discipline_daily_snapshots | 5 | 持仓日快照 |
| 48 | portfolio_holdings | 12 | 组合持仓 |
| 49 | portfolio_signals | 44 | 持仓信号 |
| 50 | seven_week_state | 7 | 七周规则状态 |
| 51 | sector_strength_results | 5 | 行业强度 |
| 52 | industry_strength_results | 1 | 行业RS强度 |
| 53 | index_divergence_results | 1 | 指数分化分析 |
| 54 | backtest_results | 952.3万 | 全信号回测结果 |
| 55 | signal_events | 82.8万 | 信号事件汇总 |
| 56 | stock_equity_change | 15.6万 | 股本变动历史 |
| 61 | stock_institutional_holdings | 4.7万 | 机构持股汇总 |
| 62 | stock_inst_holders_detail | 177.2万 | 机构持股明细 |
| 63 | stock_analyst_reports | 3.2万 | 研报覆盖统计 |
| 64 | stock_report_raw | 7,106 | 研报原始记录 |
| 65 | stock_buyback | 338 | 股票回购 |
| 66 | cansim_scores | 13.4万 | CAN SLIM评分 |
| 67 | market_breakout_daily | 2.0万 | 放量突破个股明细 |
| 68 | mw_signal_daily | 5.8万 | MW信号 |
| 69 | chanlun_scan_daily | 1281.6万 | 缠论笔缓存 |
| 69 | chanlun_bi_json | 1281.5万 | 缠论笔缓存 |
| 70 | pattern_scan_signals | 217.0万 | 形态扫描信号 |
| 71 | cockpit_daily | 661 | 投资决策驾驶舱每日结果 |
| 72 | daily_review_lhb | 1.2万 | 每日龙虎榜 |
| 73 | daily_review_block_trade | 1.7万 | 大宗交易 |
| 74 | ~~daily_review_margin~~ | 已删 | **已废弃**，两融读 daily_margin_history |
| 75 | daily_review_summary | 179 | 每日市场汇总 |
| 76 | market_health_daily | 97 | 大盘健康度 |
| 77 | sw_index_kline | 1.3万 | 申万一级行业指数日线 |
| 78 | sw2021_members | 5,211 | 申万一级行业成分映射 |
| 79 | microcap_index_daily | 5,220 | 自建微盘股指数 |
| 79 | microcap_index_component | 6.5万 | 自建微盘股指数 |
| 80 | cpa_stage_daily | 1123.3万 | CPA 阶段判定 |
| 80 | cpa_stage_transitions | 227.7万 | CPA 阶段判定 |
| 81 | chanlun_weekly_bi_json | 175.2万 |  |
| 81 | cpa_stage_stock_weekly | 183.1万 |  |
| 82 | bearish_signal_events | 149.7万 | 看跌信号事件流 |
| 83 | box_signals | 1,507 | 箱体突破/跌破信号 |
| 84 | index_fundamental_daily | 83.8万 | 指数基本面 |
| — | backfill_v2_progress | 2,600 | **尚无独立章节** |
| — | backtest_runs | 0 | **尚无独立章节** |
| — | backtest_signals | 0 | **尚无独立章节** |
| — | backtest_stats | 0 | **尚无独立章节** |
| — | chanlun_recalc_progress | 1.3万 | **尚无独立章节** |
| — | cpa_stage_stock_weekly_transitions | 52.1万 | **尚无独立章节** |
| — | cup_handle_v2_daily | 165 | **尚无独立章节** |
| — | daily_kline_caliber_progress | 8.2万 | **尚无独立章节** |
| — | daily_kline_multi_progress | 5.6万 | **尚无独立章节** |
| — | deep_analysis_jobs | 14 | **尚无独立章节** |
| — | discipline_screening_daily | 1,343 | **尚无独立章节** |
| — | discipline_screening_daily_index | 520 | **尚无独立章节** |
| — | discipline_trade_adjustments | 4 | **尚无独立章节** |
| — | index_capital_flow_daily | 1.8万 | **尚无独立章节** |
| — | index_crowding_daily | 83.7万 | **尚无独立章节** |
| — | index_divergence_daily | 5,437 | **尚无独立章节** |
| — | market_breakout_v2_daily | 32.2万 | **尚无独立章节** |
| — | market_cap_snapshot | 2,048 | **尚无独立章节** |
| — | market_health_sector_daily | 507 | 行业分组健康分 **尚无独立章节** |
| — | market_rotation_daily | 291 | **尚无独立章节** |
| — | market_sell_score_daily | 81 | 大盘卖出评分 **尚无独立章节** |
| — | market_snapshot_daily | 99 | **尚无独立章节** |
| — | mw_signal_daily_old | 10.4万 | **尚无独立章节** |
| — | pocket_pivot_daily | 49.8万 | 口袋支点 **尚无独立章节** |
| — | shareholder_low_scan | 212 | **尚无独立章节** |
| — | stock_dri_metrics | 5,631 | **尚无独立章节** |
| — | stock_financials_annual_ext | 2.8万 | **尚无独立章节** |
| — | stock_kline_15min | 1375.4万 | **尚无独立章节** |
| — | stock_kline_60min | 445.4万 | **尚无独立章节** |
| — | stock_kline_adj | 9.4万 | **尚无独立章节** |
| — | stock_kline_adj_progress | 32 | **尚无独立章节** |
| — | watchlist_report_daily | 26 | **尚无独立章节** |
| — | watchlist_review_state | 1 | **尚无独立章节** |

---

## 数据库统计

| 指标 | 数值 |
|------|------|
| 统计时间 | 2026-09-29 17:56:49 |
| 总表数 | 110 |
| 视图 | 1（daily_kline_adj） |
| 总行数 | 404,031,802（约 4.04 亿） |
| 第1大表 | fundamental_indicator（2.86亿 行） |
| 第2大表 | daily_kline（1902.3万 行） |
| 第3大表 | stock_kline_15min（1375.4万 行） |
| 第4大表 | chanlun_scan_daily（1281.6万 行） |
| 第5大表 | chanlun_bi_json（1281.5万 行） |
| DB 文件大小 | 176.8 GB |
| 数据来源 | `scripts/db_inventory.py` 对 `data/lixinger.db` 实测（只读 COUNT） |
| 重建方式 | `python scripts/db_inventory.py` → `python scripts/regen_schema_summary.py` |

---

## 68. mw_signal_daily — MW信号

> v4.0 更新：2026-07-07 基于 10 年全周期回测重标定评分体系

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | **PK**，自增 |
| stock_code | TEXT | **UNIQUE(1)**，股票代码 |
| b1_date | TEXT | **UNIQUE(1)**，B1 突破日 |
| stock_name | TEXT | 股票名称 |
| b2_date | TEXT | B2 突破日（可为 NULL） |
| confidence | TEXT | 置信度：高/中/低 |
| score | INTEGER | HDC v4.0 总分（0~100） |
| confidence_v2 | TEXT | 体系2 置信度（扩展指标） |
| score_v2 | INTEGER | 体系2 总分 |
| h_date / h_price | TEXT/REAL | H 点前高日期/价格 |
| l_date / l_price | TEXT/REAL | L 点最低日期/价格 |
| c_start / c_end | TEXT/TEXT | C 横盘区起止日期 |
| b1_return_pct | REAL | B1 日涨幅（%） |
| b1_vol_ratio | REAL | B1 日量比（vs 20日均量） |
| b2_return_pct | REAL | B2 日涨幅（%） |
| b2_close_pos | REAL | B2 收盘位置（%） |
| b2_is_gap | INTEGER | B2 是否跳空 0/1 |
| b2_ma_count | INTEGER | B2 突破均线条数 |
| decline_pct | REAL | H→L 回调深度（%） |
| c_amplitude_pct | REAL | C 横盘振幅（%） |
| h_rs20 / h_rs250 | INTEGER | H 点个股 RS20/RS250（0-99） |
| c_amount_avg | REAL | 横盘期日均成交额（元） |
| h_pre_rise_pct | REAL | H 点前 60 日涨幅（%） |
| p_max_dd_pct | REAL | P 段最大回撤（%） |
| p_vol_ratio | REAL | P 段缩量率 |
| score_h | INTEGER | H 子项：前高趋势（0/15） |
| score_d | INTEGER | D 子项：调整深度（0/5/15/25） |
| score_c | INTEGER | C 子项：v4.0 已删除，恒为 0 |
| score_p | INTEGER | P 子项：整理回撤（0/15，不参与主评分） |
| score_i1 | INTEGER | I1 子项：行业 RS（0/10/20） |
| score_i2 | INTEGER | I2 子项：个股 RS（0/10/20/30） |
| score_o1 / score_o2 | INTEGER | 已废弃 |
| score_ma | INTEGER | 已废弃 |
| score_sig | INTEGER | Sig 子项：信号共振（0-10） |
| score_gap | INTEGER | Gap 子项：跳空（0/10，不参与主评分） |
| score_m1 / score_m2 / score_m3 | INTEGER | 体系2 扩展指标 |
| is_plus | INTEGER | PLUS 标志（总分≥80 且 D=25 且 I1=20） |
| tech_score | INTEGER | B1 技术置信度：9 因子评分（0-100） |
| tech_score_detail | TEXT | 技术置信度 9 因子明细（JSON） |
| ind_rs20 / ind_rs250 | INTEGER | 行业指数 RS20/RS250 |
| ind_code | TEXT | L2/L1 行业指数代码 |
| ind_name | TEXT | L2/L1 行业指数名称 |
| scan_date | TEXT | 扫描日期 |
| created_at | TEXT | 创建时间 |

**数据量**：~103,000 行  
**索引**：`idx_mw_b1date(b1_date)`, `idx_mw_code(stock_code)`  
**写入脚本**：`src/scanners/mw_signal.py`  
**读取场景**：MW 信号看板、回测分析

### 评分体系（HDC v4.0）

| 子项 | 满分 | 规则 |
|------|:---:|------|
| I2（个股RS） | 30 | `h_rs250≥90`(30) / `≥85`(20) / `≥75`(10) |
| D（调整深度） | 25 | 跌幅 `25%~40%`(25) / `20%~25%`(15) / `15%~20%`(5) |
| I1（行业RS） | 20 | `ind_rs250≥85`(20) / `≥80`(10) |
| H（前高趋势） | 15 | SMA50 斜率>0 且价格>MA200 |
| Sig（信号共振） | 10 | PP_V1(+5)+BO_V2(+3)+缠论(+2)+蜡烛(+1)，上限 10 |

**硬门禁**：`h_rs250 ≥ 60`，不满足则不出 B1  
**置信度**：高 ≥ 70 / 中 50~69 / 低 < 50  
**PLUS**：总分 ≥ 80 且 D=25 且 I1=20  
**已删除**：C（横盘质量），全周期负相关 r=-0.036

---

## 69. chanlun_scan_daily / chanlun_bi_json — 缠论笔缓存

### chanlun_scan_daily

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER | 自增主键 |
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
| created_at | TEXT | 写入时间 |
| bi_json | TEXT | 历史遗留列（新数据写 `chanlun_bi_json`，本列不再使用） |
| **algo_version** | TEXT | **算法/口径版本标记**，见下方 |

### chanlun_bi_json

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| scan_date | TEXT | **PK(2)**，扫描日（= 该日快照） |
| bi_json | TEXT | 笔序列 JSON，每笔含 `direction` / `sdt` / `edt` / `high` / `low` |

**数据量**：两表各约 1,250 万行（12,499,076 / 12,499,747）  
**写入脚本**：`src/scanners/chanlun_scan.py`（每日增量）、`scripts/backfill_chanlun.py`（历史回填）、`scripts/recalc_chanlun_bi.py`（全量重算）

### `algo_version` 语义

| 值 | 含义 |
|---|---|
| `czsc101` | CZSC 1.0.1 引擎 + **未复权价**（除权缺口会被当成真实 K 线造出假笔） |
| `czsc010` | 更早的引擎 |
| `czsc101_adj` | CZSC 1.0.1 + **前复权价**（`daily_kline_adj`），**目标版本** |

重算命令：`python scripts\recalc_chanlun_bi.py --mode latest`（约 16 分钟）/ `--mode full`（约 60 小时）。

### ⚠️ 消费方的实际读法（决定重算范围的关键依据）

| 消费方 | 读法 | 需要历史逐日？ |
|---|---|---|
| `src/scanners/cpa_stage.py::load_bi_tops` | `ORDER BY scan_date DESC LIMIT 1` | ❌ 只要最新 |
| `src/scanners/mw_signal.py:105` | `ORDER BY scan_date DESC LIMIT 1` | ❌ 只要最新 |
| `src/scanners/mw_signal.py:908` | `GROUP BY stock_code HAVING scan_date=MAX(scan_date)` | ❌ 只要最新 |
| `src/scanners/mw_signal.py:1049` | `WHERE scan_date=?` | ✅ 按日期 |
| `scripts/audit_cpa_bi_quality.py` | JOIN `MAX(scan_date)` | ❌ 只要最新 |
| `scripts/backfill_mw.py` 等历史回填 | 按日期 | ✅ 按日期 |

**实盘路径（CPA + MW 主流程）只读最新一条快照**，只有历史回填需要逐日快照。
最新快照与逐日行必须同口径，否则按日期读的消费方会拿到混合数据。

**读取场景**：MW 信号 H/L 检测、CPA 笔顶序列判据、缠论每日精选

---

## 70. pattern_scan_signals — 形态扫描信号（完整定义）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)** |
| date | TEXT | **PK(2)** |
| signals_json | TEXT | 信号JSON数组，每条含 `source`（引擎名）/ `type`（bullish/bearish）/ `details` |

**已注册引擎（17个）**：

| 类别 | 引擎 | source |
|------|------|------|
| 买入-突破 | 基部突破 V1/V2 | `base_breakout`, `base_breakout_v2` |
| 买入-形态 | 口袋支点 V1/V3 | `pocket_pivot`, `pocket_pivot_v3` |
| 买入-形态 | 杯柄/双底/平底/碟形 | `cup_handle`, `double_bottom`, `flat_base`, `saucer_base` |
| 买入-K线 | CDL形态（吞没/锤子等） | `cdl` |
| 买入-指标 | TA-Lib 指标 | `talib` |
| 买入-信号 | MW B1/B2 | `mw_signal` |
| 卖出-形态 | 头肩顶/双重顶/三重顶 | `top_pattern` |
| 卖出-形态 | 高潮见顶 | `climax_top` |
| 卖出-形态 | 铁轨线 | `railroad_tracks` |
| 卖出-指标 | 量价背离 | `volume_divergence` |
| 卖出-失败 | 基部突破失败 | `breakout_failure` |
| 卖出-大盘 | 抛盘日 | `distribution_day` |

> ✅ 2026-06-12: `railroad_tracks` 引擎已修复 `detect()` 注册和 `type=bearish` 字段，`climax_top` 已确认正常工作（条件严格，100只中约5只有信号）

---

## 71. cockpit_daily — 投资决策驾驶舱每日结果

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

## 72. daily_review_lhb — 每日龙虎榜
| 列 | 类型 | 说明 |
|---|------|------|
| date | TEXT | 交易日期 |
| stock_code | TEXT | 股票代码 |
| stock_name | TEXT | 股票名称 |
| reason | TEXT | 披露原因 |
| total_buy/total_sell/net_amount | REAL | 买卖总额/净额 |
| inst_buy/inst_sell/inst_net | REAL | 机构买卖/净额 |

**数据量**：~10,700 行（2026-01~今；2026-08-05 补齐 7/23·7/27·7/30·7/31·8/4 缺失日）
**数据来源**：理杏仁 `/api/cn/company/trading-abnormal`（龙虎榜，T+1 发布）
**写入脚本**：`scripts/daily_review.py`（fetch 带重试 + T+1 自动回退，2026-08-05 起）

## 73. daily_review_block_trade — 大宗交易
| 列 | 类型 | 说明 |
|---|------|------|
| date | TEXT | 交易日期 |
| stock_code | TEXT | 股票代码 |
| trading_price/volume/amount | REAL | 成交价/量/额 |
| discount_rate | REAL | 折价率 |
| buy_branch/sell_branch | TEXT | 营业部 |

**数据量**：~14,600 行（2026-01~今；2026-08-05 补齐缺失日）
**数据来源**：理杏仁 `/api/cn/company/block-deal`（大宗，T+1 发布）
**写入脚本**：`scripts/daily_review.py`（fetch 带重试 + T+1 自动回退，2026-08-05 起）
**注意**：大宗为 T+1 披露，当日拉取可能为空，脚本自动回退前一交易日并标注实际数据日

## 74. ~~daily_review_margin~~ — 已废弃（已删表，见 §16）

两融只读 §15 `daily_margin_history`。

## 75. daily_review_summary — 每日市场汇总
| 列 | 类型 | 说明 |
|---|------|------|
| date | TEXT PRIMARY KEY | 交易日期 |
| up_count/down_count | INT | 上涨/下跌家数 |
| total_amount | REAL | 全市场成交额(亿) |
| sh_idx_close/sh_idx_chg | REAL | 上证收盘/涨跌 |
| hs300_close/hs300_chg | REAL | 沪深300收盘/涨跌 |

---

## 76. market_health_daily — 大盘健康度
| 列 | 类型 | 说明 |
|---|------|------|
| date | TEXT PRIMARY KEY | 交易日期 |
| total_score | REAL | 总分（100分制） |
| rating | TEXT | 评级 A~F |
| ma50_above_value/score | REAL/INT | MA50上方占比（%） |
| hl_ratio_value/score | REAL/INT | 新高新低比 |
| ad_ratio_value/today/score | REAL/INT | 涨跌家数比（5日均/当日） |
| limit_up_count | INT | 当日涨停家数（2026-08-05新增） |
| limit_down_count | INT | 当日跌停家数（2026-08-05新增） |
| vol_breakout_value/score | REAL/INT | 放量突破数 |
| margin_5d_value/score | REAL/INT | 融资余额5日变化（%） |
| sector_rot_score | INT | 板块轮动稳定性 |
| fear_greed_value/score | REAL/INT | 恐慌贪婪指数 |

**数据量**：~365 行（每日一条）  
**数据来源**：全A daily_kline + index_daily_kline + 两融表聚合  
**写入脚本**：`src/scanners/market_health.py`（daily_update 步骤 9）  
**读取场景**：`/api/market-health` → 大盘扫描页辅助指标卡  
**涨跌停口径**（2026-08-05 起）：主板 ≥9.9%，创业板/科创板 ≥19.9%，不含 ST（5% 档）、北交所（30% 档）、新股无涨跌幅限制；不计入健康分


---

## 77. sw_index_kline — 申万 2021 一级行业指数日线（2026-09-07 新增）

**用途**：CANSLIM L 因子 industry_rs 的申万行业 RS 基准（替代旧"所属指数成分 MAX"虚高口径）

| 列 | 类型 | 说明 |
|---|---|---|
| stock_code | TEXT | 申万一级行业指数代码（6位，如 801030 基础化工） |
| stock_name | TEXT | 行业名（基础化工/石油石化/食品饮料…31 个） |
| date | TEXT | 交易日 |
| open/high/low/close | REAL | OHLC（申万宏源官网源） |
| volume/amount | REAL | 量额 |

**主键**：(stock_code, date)
**数据量**：31 行业 × ~400 交易日（--full 全量；每日增量拉最近 10 日覆盖周末缺口）
**数据来源**：akshare index_hist_sw（申万宏源官网，住宅 IP 可用，非东财）
**写入脚本**：`scripts/fetch_sw_index.py`（daily_update 步骤 19b，每日增量）
**读取场景**：CANSLIM score_l industry_rs（行业指数 20 日涨幅 ≥3% → 4 分 / 0-3% → 2 分 / <0 → 0 分；阈值 yaml industry_ret20_*）

## 78. sw2021_members — 申万 2021 一级行业成分映射（2026-09-07 新增）

**用途**：个股 → 申万 2021 新版一级行业归属（stock_sw_industry 是旧版分类——旧版"化工"在 2021 版拆为基础化工+石油石化，无法自动推断个股归属，故用官方成分表精确映射）

| 列 | 类型 | 说明 |
|---|---|---|
| stock_code | TEXT | 股票代码 |
| industry_code | TEXT | 申万 2021 一级指数代码（801030 等） |
| industry_name | TEXT | 新版行业名 |
| weight | REAL | 指数内权重 |

**主键**：(stock_code, industry_code)
**数据量**：5211 条（31 行业成分并集）
**数据来源**：akshare index_component_sw（申万官网成分表）
**写入脚本**：`scripts/fetch_sw_index.py --members`（低频——成分定期调样时重拉）
**读取场景**：CANSLIM score_l industry_rs 行业归属查询；个股体检 skill 行业卡

> 验证：万华 600309 / 卫星 002648 → 基础化工(801030)（官方成分表权威）
> 注意：stock_sw_industry（旧版申万，industry_code 220000 类）与 sw2021_members（2021 新版）并存——行业口径引用以 2021 新版为准

## CANSLIM 规则变更记录（v3.4-v3.6，2026-09-07）

- **A 因子**：新增 TTM 盈利趋势子项（0-4 分，滚动 4 季 vs 再前 4 季同比）；A 满分 15→19；yaml ttm_yoy_tiers/scores
- **N 因子**：形态信号四档分级（19 引擎全接入 engine_scores）+ MW 质量分段（mw_quality：tech_score_v4/score_v2 ≥85→6/70→5/55→3/<55→2，空白→0 分+note）+ 族内去重（signal_families base/pivot/mw/misc）+ 跨族共振（day_cap 10）；注意引擎输出契约：部分引擎信号无 type 字段，计分靠"显式 bearish 过滤 + 配分表白名单"
- **I 因子**：删首次覆盖；新构成 机构持股 5 + 机构增减 8（inst_count_score [8,4]）+ 研报覆盖 5（analyst_coverage_score 5/低档 2）；rating_up 降展示项（新浪无评级恒 0）；研报唯一源=新浪（东财退役 2026-09-07）；负债率已移出（v3.2）
- **L 因子**：industry_rs 改申万 2021 一级行业指数 20 日涨幅口径（yaml industry_ret20_threshold 3.0/high 4/mid 2）
- 相关表：stock_analyst_reports（source='sina' 每日重点池 + 周一全市场；source='lx' 历史遗留不再读取）

---

## 79. microcap_index_daily / microcap_index_component — 自建微盘股指数（2026-09-09 新增）

万得口径复刻（月频调仓/等权/总市值最小 400 + 100 双序列），用于市场风格/流动性水温监测。

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| **microcap_index_daily** | (index_type, date) | point / pct_change / avg_mkt_cap / n | 指数日线；2016-01-04 = 1000 基点；index_type ∈ {'400','100'} |
| **microcap_index_component** | (index_type, eff_date, stock_code) | name / mkt_cap / weight | 每月月末定池的快照（次月首个交易日起生效） |

**编制规则**：选池市值 = 真实收盘 × 当日总股本（stock_equity_change，change_date 生效，首条向前回填）；点位 = 成分前复权收益等权连乘；剔除北交所（4/8/92 前缀）/次新（上市<60 自然日）/B 股；ST 历史段不剔（无历史状态，避免前视）。
**数据量**：2,598 日 × 2 序列 + 129 月成分快照（约 6.5 万行）。
**更新**：`scripts/build_microcap_index.py`（全量回算 ~180s / `--incremental` 增量），daily_update 步骤 33。
**验证**：2024-01 崩盘 -45.5%（万得 -49%）；2026 回撤 -28.2%；与 932000 相关 0.87、跑赢 4.2 倍；平均市值 22 亿（万得中位 22 亿）。

---

## 80. cpa_stage_daily / cpa_stage_transitions — CPA 阶段判定（2026-09-10 新增）

Oliver Kell CPA（Cycle of Price Action）六阶段状态机，输出每只股票的当前阶段/失效位/动作方向。

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| **cpa_stage_daily** | (stock_code, date) | stage / prior_stage / stage_start_date / days_in_stage / structure_support / invalid_level / action / close / metrics_json | 每日状态快照 |
| **cpa_stage_transitions** | (stock_code, transition_date) | from_stage / to_stage / trigger_detail_json / invalidated / invalidated_date | 变迁事件（稀疏） |

**stage 取值**：`①a/①b`（跌到位/衰竭迹象）｜ `②`（Wedge Pop）｜ `③`（EMA Crossback）｜ `④`（Base 'n Break）｜ `⑤`（Exhaustion）｜ `⑥a/⑥b/⑥c`（Wedge Drop 三子段）｜ **带 `T` 后缀 = 过渡态**（②T/⑥bT/⑥cT，EMA20 [-0.3,+0.5]×ATR 模糊带）｜ **`⑥w` = 预警态**（衰竭迹象出现但未破位，动作=减仓，止损 EMA20 下方）。
**action 枚举**：观察 / 入场 / 加仓 / 持有 / 减仓 / 保护利润 / 清仓。
**关键机制**：
- 阶段 = **事件触发的区间**（无空档）；②③④ 是事件、⑤⑥① 是状态/段
- **非对称阈值**：进 ⑥b `close < EMA20-0.3×ATR`（连2日，防守快）；出 ⑥b `close > EMA20+0.5×ATR` 且 3中2（进攻慢）
- **路径B（FTD 快速通道）**：深回撤≥25% + 低点后 4-15 日 + 涨幅≥2% + VR≥1.3 + 站上 EMA20 → 直接进 ②
- **entry_path**（metrics_json 内）：`pause`（慢速）/ `ftd_4_7` / `ftd_8_15`（溯源，回测分层用）
- **失效闭环**：② 的失效线 = 入口结构低点（FTD 前低点/停顿区低点）
- **invalidated**：迁移后 N 日内出现反向迁移 → 标记（N 按迁移类型分设，待校准）
- **数据层折叠**：`cpa_stage.collapse_states(min_days=5)`（回测/门禁消费前折叠短命段，实测段数 -61%）
**更新**：`src/scanners/cpa_stage.py`（待建回算主流程 + daily_update 接入）。
**PRD**：docs/product/CPA阶段判定引擎_产品需求书.md（v1.2）
**v1.2 review 修复要点（2026-09-10）**：复权口径统一（涨跌幅全用 adj_close）｜invalidated 重构（⑥ 归一化键匹配 / 去 1.5 倍窗口 / 只标回退型迁移）｜阶段① 闸门 a 必要 + b/c 至少一条（RPS250 由状态机预加载传入）｜笔顶收敛参与停顿区判定｜③ 深度上限用停顿区最高点（FTD 入口用前低点）｜⑥ 预警态 `⑥w` 落地｜①b 两日确认｜③ 缩量双口径｜交叉验证字段 b1_overlap/b2_overlap（±2 日）。
**metrics_json 字段**：`entry_path`（pause/ftd_4_7/ftd_8_15/revive）、`transition_zone`、`warn_from`、`b1_overlap`、`b2_overlap`、`boundary_sample`、`init_flag`、`nd10_slow`、`six_dir`、`six_dir_pct`、`six_dd`。

**v1.4 / v1.5 新增字段说明（2026-09-14）**：

| 字段 | 版本 | 含义 |
|---|---|---|
| `nd10_slow` | v1.4 | `(close-EMA10)/ATR60`。⑤ 的判据专用（ATR20 会被延伸行情自身的大阳线抬高→漏报）；`nd10` 仍是 ATR20 口径，① 的下影线、③ 的容差继续用它。 |
| `six_dir` | v1.5 | ⑥ 阶段的方向分档：`rebound`（近 5 日上涨）/ `decline`（下跌）。实测方向胜率差 6pp、中位差 1.55pp，而回撤分档不单调。前端 ⑥ 标签按此分档。 |
| `six_dir_pct` | v1.5 | 近 5 日涨跌幅（小数）。 |
| `six_dd` | v1.5 | 自 250 日高点的回撤（负数）。保留为结构标注，不驱动展示分档。 |

⚠️ `six_*` 三字段仅 ⑥a/⑥b/⑥bT/⑥c/⑥cT 有值；**⑥w 不带**（它是输出层标签，底层为 ②③④⑤，前端靠 `baseStage()` 还原）。

⚠️ v1.5 已废弃两个 CFG 参数（置为 `None`）：`r_strong_gain`（涨幅≥40%）与 `r_h_rps250`（RPS250≥70）。
二者实测对 ① 无增量（−0.02pp / −0.04pp），现只在 `detail` 里记录。前置闸门只剩 `r_high_recency`（已由 120 收到 **60** 日）。

### 日线引擎 T2 参数化（2026-09-18）

CFG 新增 21 个窗口键（原硬编码改读 CFG，**默认值 = 原硬编码值，日线行为零变化**，回归 `scripts/_t2_regression.py` 逐字节一致）：
`r_panic_lookback` / `ftd_min_history` / `ftd_low_lookback` / `pause_recent_win` / `pause_prior_win` / `pause_prior_gap` / `pause_vol_min_recent` / `pause_vol_min_prior` / `pause_shrink_min_recent` / `pause_shrink_min_prior` / `pause_mid_min_seg` / `win_floor` / `b_floor_atr` / `bd_win` / `bd_amp_max` / `bd_min_points` / `support_lookback` / `six_dir_lookback` / `crossback_low_win` / `warn_tops_count`；另有 `atr_series`/`ema_series`/`vr_series` 写死窗口改读 `atr_win`/`ema_fast`/`ema_slow`/`vr_win`。

---

## 81. 周K线 CPA：chanlun_weekly_bi_json + cpa_stage_stock_weekly 双表（2026-09-18 新增）

Oliver Kell CPA 的**真周K**版本：日线 → ISO 周K（代表日=该周最后交易日）→ 真周线笔（CZSC Freq.W）→ 同一状态机（WEEKLY_CFG 参数集）→ 独立周线表。

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| **chanlun_weekly_bi_json** | (stock_code, scan_date) | bi_json / algo_version | 周线笔逐周快照（防未来）；scan_date = **个股自己的周代表日**（停牌对称，非市场代表日）；algo_version='czsc101_w' |
| **cpa_stage_stock_weekly** | (stock_code, date) | 结构与日线 cpa_stage_daily 一致 | 每周状态快照；date=周代表日，days_in_stage 单位=**周** |
| **cpa_stage_stock_weekly_transitions** | (stock_code, transition_date) | 与日线 transitions 一致 | 周迁移事件；invalidated 窗口 = N **周**×7 天 |

**数据量**（2026-09-18 全量重算）：chanlun_weekly_bi_json 173.9 万周 / 4,109 只；cpa_stage_stock_weekly 182.7 万行 / 4,000 只（128 只 <78 周跳过）；迁移 52.0 万 / 失效标记 16.7 万。
**股票池**：流动性池（非ST+日均成交额≥5000万，4,128 只）——CPA 池与数据层池同源，无快照的股票不写（半残数据）。
**聚合口径**：ISO week（group_key={ISO年}-W{ISO周:02d}），代表日=组内最后交易日；与 W-FRI resample 实测 196/196 等价，长假周五休市周靠墙钟规则不丢周。
**WEEKLY_CFG**：`src/scanners/cpa_stage_weekly.py` 头部完整参数集（保留 14 + 修订 10 + 硬编码覆盖 ~20 键，含 ema/atr/vr 窗口覆盖）。关键差异：warmup=26 周、分位窗 52 周、inv_n 单位周、warn_tops_count=2。
**更新**：daily_update 步骤 35a（`backfill_chanlun_weekly.py --incremental`）+ 36（`incremental_cpa_weekly.py`，判据=快照 max(scan_date) > CPA max(date) 才重算，幂等实测 0 行重写）；全量重算 `scripts/backfill_cpa_weekly.py --workers 8`（purge，241s）。
**已知边界**：最新一根周K的笔顶证据可能落后一周（数据层回填在收盘前跑时本周快照缺失→前周可见集兜底），下周增量自愈；历史行不受影响。
**已知校准候选**：⑥w 占比 26.5%（日线约 0.5%），warn_tops_count=2 在周线语境过敏，候选：EMA10 斜率连续 2 周转负才 warn 或恢复 3 顶。
**API**：`/api/cpa/stock-weekly?code=`（ISO 周K + EMA/ATR 现算 + invalidated 三态）；前端 `web/discipline/cpa-stock-weekly.html`（双页互跳）。
**PRD**：docs/product/周K线CPA判定引擎_产品需求书.md；**交接**：docs/dev/HANDOVER-weekly-cpa.md（含 WEEKLY_CFG 全表 + 三死罪 + 陷阱清单）。

---

## 82. bearish_signal_events — 看跌信号事件流（2026-09-14 新增）

**为什么需要它**：`pattern_scan_signals` 的 `date` 是**扫描日**，`signals_json` 是**截至该扫描日已知的全部信号**（累积写入）；而且卖出引擎有预筛选（近 60 日涨 >15% 或距 60 日高点 10% 以内）。
实测后果：**96% 的行都含 `bearish`**，所以「某行含 bearish」实际在测「该股近期是否强势」，**不能当信号用**。

**做法**：扫一遍 `pattern_scan_signals`，从 `signals_json` 里提取每条信号**自带的 `signal_date`**（每条信号都有，四个家族的字段名不同），
按 `(source, signal_type/pattern, signal_date)` 去重，落到一张干净的稀疏事件表。

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| **bearish_signal_events** | (stock_code, source, pattern, signal_date) | detected_date / signal_level / lp_date / rp_date / tr_date / neckline / detail_json | 1,497,431 条 / 5,562 只 |

**家族与子类型**（`source` + `pattern`）：

| source | pattern / signal_type | 条数 | 信号日覆盖 |
|---|---|---|---|
| `top_pattern` | double_top | 1,261,127 | 2016-01 起（**每只 232 次，太频繁**） |
| `top_pattern` | triple_top | 94,602 | 2016-01 起 |
| `top_pattern` | head_shoulders | 10,166 | 2016-01 起 |
| `railroad_tracks` | railroad_daily_single / weekly / daily_double | 81,575 | 2015-03 起 |
| `talib` | macd_death_cross / price_cross_sma50_down / rsi_overbought / death_cross_50_200 | 24,561 | **仅 2026-04 起** |
| `cdl` | — | 7,000 | 仅 2026-04 起 |
| `volume_divergence` | rally_dry / rising_shrink / drop_surge / … | ~8,000 | 仅 2026-06 起 |
| `distribution_day` / `breakout_failure` / `pivot` | — | ~10,500 | 2026-04/06 起 |

**回测推荐子集**（`--bear-src strong`）：`triple_top` + `head_shoulders` + `railroad_tracks` = 181,108 条 / 5,423 只。
排除 double_top（饱和）与 talib/cdl/volume_divergence（历史太短）。

**已知缺陷**：`signal_date` 是形态自身完成日，而回填时检测日可能晚于它（000001 有滞后 9 个月的例子）→ 存在**轻微前视风险**，待用 `detected_date` 做敏感性验证。

**生成**：`python scripts/build_bearish_events.py --rebuild`（57s）。

---

## 83. box_signals — 箱体突破/跌破信号（2026-09-14 新增）

| 表 | 主键 | 关键字段 | 说明 |
|---|---|---|---|
| **box_signals** | (stock_code, signal_date, engine, sig_level) | engine / sig_type / band_top / band_bottom / detail_json | 引擎 `box_breakout`(v1.3, bullish) / `box_breakdown`(v1.5, bearish) |

**特点**：箱体引擎是**对整条 K 线序列做事件扫描**的纯函数（`detect(daily, params)`，信号自带 `signal_date`），
所以**按股票跑一次**比按日跑高效得多（按股票 6.15s/只，全市场 8 进程约 1.2 小时；按日则约 10 小时）。

**生成**：`python scripts/backfill_all_signals_v2.py --box-only --box-workers 8`（已接入该脚本，`--box` 则按日回填后再跑）。
**当前状态**：仅 20 只试跑数据（1,507 行），全量待跑。

---

## 84. index_fundamental_daily — 指数基本面（PE/PB/股息率及十年分位）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| stock_code | TEXT | **PK(1)**，指数代码 |
| date | TEXT | **PK(2)**，交易日 |
| mc | REAL | 总市值 |
| tv | REAL | 成交量 |
| ta | REAL | 成交额 |
| to_r | REAL | 换手率（%） |
| pe_ttm | REAL | PE-TTM（市值加权，`pe_ttm.mcw`） |
| pe_ttm_pct | REAL | PE 十年分位点（0~1） |
| pb | REAL | PB（市值加权，`pb.mcw`） |
| pb_pct | REAL | PB 十年分位点（0~1） |
| dyr | REAL | 股息率（%，市值加权，`dyr.mcw`） |
| dyr_pct | REAL | 股息率十年分位点（0~1） |
| fpa | REAL | 融资买入金额 |
| fb | REAL | 融资余额 |
| ecmc | REAL | 自由流通市值 |
| updated_at | TEXT | 更新时间 |

**数据量**：~83.8 万条 / 409 个代码（2016-01-01 ~ 2026-09-29）  
**数据来源**：理杏仁 `/index/fundamental`，指数清单取自 `config/index_style.yaml`  
**写入脚本**：`scripts/fetch_index_fundamental.py`（`--incremental` 增量，`--dry-run` 仅统计）  
**读取场景**：红利观察估值分位（`red_dividend_metrics`）、指数拥挤度五维（`index_crowding`）、红利温度计股债息差（股息率 − 10 年国债收益率）、红利 RSI 买卖点的估值对照（见 §85）、watchlist 指数估值  
**备注**：三个 `*_pct` 均为 `y10`（十年滚动窗口）分位。数据自 2016 年起，因此早期日期的分位是在不足十年的样本上算出来的。

---

## 85. 红利 RSI 买卖点 — 无独立数据表

红利详情页「操作建议」tab 的 RSI 买卖点**不落库**：RSI 序列、分档、买/卖点标注与胜率统计全部在请求 `/api/market-scan/dividend-advice-detail` 时实时计算。
因此该功能**没有新增任何表或字段**，本节只登记它的数据来源与参数位置。

| 用途 | 数据来源 | 口径 |
|------|----------|------|
| RSI 序列 | `index_daily_kline`（§17） | 取 `kline_type='normal'` 的 `close`，近 10 年，Wilder 平滑 |
| 前瞻收益 / 胜率 | `index_full_return_daily`（§13） | 全收益 `close`，前瞻 20 / 60 日；与信号同用全收益，故不含分红偏差 |
| 估值分位对照 | `index_fundamental_daily`（§84） | `pe_ttm_pct` / `pb_pct` / `dyr_pct` |

**参数不存库**：默认周期、各周期买卖阈值（`buy_tiers`/`sell_tiers`）、按指数代码的 `overrides` 均在
`config/market/red_dividend_rsi.yaml`。Flask 按文件 mtime 比对缓存自动重载，改阈值不必重启（配置文件读失败时回落到 `src/server.py` 的内置默认值）。
**买卖点定义**：状态 + 深度分档（不是「下穿阈值」事件）。买点 k 档覆盖 `[deeper, threshold)`，卖点 k 档覆盖 `(threshold, deeper]`，档与档不重叠；
标注取每一段连续超买/超卖行情的极值点（买点取最低点，卖点取最高点）。
**接口字段**：`rsi_basis` / `rsi_default_period` / `rsi_series[]`（含 `values`、`buy_tiers`、`sell_tiers`、`tier_stats`、`sell_tier_stats`、`buy_marks`、`sell_marks`）。  
**产品说明**：`docs/product/红利观察买卖点模块_产品需求书.md` §10–§11。



---

## 指数 CPA 阶段相关表

### index_cpa_daily — 指数 CPA 八阶段快照

由 `scripts/scan_index_cpa_daily.py` 写入（日更步骤 38）。每个交易日每个指数一行。

| 字段 | 类型 | 说明 |
|---|---|---|
| stock_code | TEXT | 指数代码，主键之一 |
| date | TEXT | 数据日期，主键之一（`PRIMARY KEY (stock_code, date)`） |
| stage | INTEGER | 阶段 0~8（0 = 待确认突破） |
| stage_name | TEXT | 阶段中文名 |
| action | TEXT | 绑定动作 |
| regime | TEXT | 环境层：`UP` / `DOWN` / `RANGE` |
| close | REAL | 当日收盘 |
| days_in_stage | INTEGER | 当前阶段已持续交易日数 |
| since_date | TEXT | 本阶段起始日 |
| dev20 | REAL | `(收盘 − EMA20) / ATR20`，正为高于均线 |
| index_name | TEXT | 指数名称，来自 `stock_index`（该表是「个股→所属指数」映射，名称须取 `DISTINCT index_code, index_name`） |
| updated_at | TEXT | 写入时间 |

索引：`idx_cpa_date(date)`、`idx_cpa_stage(stage)`。幂等：同日同指数覆盖写。

**覆盖范围**：394 个指数（有 `index_daily_kline` 且行数 ≥ 400）。

**口径**：环境层由均线收敛度与 EMA50 20 日斜率决定，每日重算；事件层在环境内记忆，环境一变即重置。
判定只使用截至当日的数据，无未来视角（截断检验 570 个时点 0 处不一致）。

### index_chanlun_bi — 指数缠论笔

由 `scripts/compute_index_chanlun_bi.py` 计算。**与个股的 `chanlun_bi_json` 完全分开**——
后者里出现的 6 位代码是股票，与指数代码撞号（例如 `000985` 的笔数据 high=18.17，是中证全指点位的百分之一）。

| 字段 | 类型 | 说明 |
|---|---|---|
| stock_code | TEXT | 指数代码 |
| start_date / end_date | TEXT | 笔的起止日期，联合主键 |
| direction | TEXT | `up` / `down` |
| start_price / end_price | REAL | 笔的端点价 |
| high / low | REAL | 笔区间极值 |
| length | INTEGER | 合并 K 线跨度（最少 4） |
| power | REAL | 涨跌幅 % |
| slope | REAL | 每根 K 线的平均变动 |
| updated_at | TEXT | 写入时间 |

**现状**：目前只算了 980092（259 笔）。指数 CPA 引擎当前**不依赖**笔数据，
`fractal_highs` 也已不再使用；是否引入需重新评估。


---

## 指数级财报与全收益

### index_financials_annual — 指数级年度财报

由 `scripts/fetch_index_financials.py` 写入。**端点按指数所属行业选择**：

| 行业 | 端点 |
|---|---|
| 产业类（煤炭等） | `/index/fs/non_financial` |
| 证券公司 | `/index/fs/security` |
| 银行 / 保险 / 其他金融 | `/index/fs/bank` / `/insurance` / `/other_financial` |

| 字段 | 说明 |
|---|---|
| stock_code, date | 联合主键，date 为报告期（YYYY-12-31） |
| revenue | 营业总收入（元） |
| operating_profit | 营业利润（元） |
| net_profit | 归母净利润（元） |
| roe | 净资产收益率（小数，展示需 ×100） |
| eps | 每股收益（元/股） |
| net_margin | 净利润率（小数） |
| op_margin | 营业利润率（小数） |
| dividend_amount | A 股分红金额（元） |
| updated_at | 写入时间 |

**覆盖现状**：399998 中证煤炭（2014~2025，12 期）、399975 证券公司（2013~2025，13 期）。
**注意**：写入时按 stock_code 先删后插，不 DROP 整表——早期版本用 DROP TABLE 会清空其它指数数据。

指标代码（年频 `y.` 前缀）：`y.ps.toi.t` / `y.ps.op.t` / `y.ps.npatoshopc.t` / `y.m.roe.t` /
`y.m.npatoshopc_ps.t` / `y.m.np_s_r.t` / `y.ps.op_s_r.t` / `y.ps.da_om.t`。

### index_full_return_daily — 指数全收益

由 `scripts/fetch_index_full_return.py` 写入（理杏仁 `/index/candlestick` type=total_return）。
**覆盖扩充**：000510 / 000300 / 000905 / 000985 / 399998 / 399975 均已补齐至 2012~2013 起。

**用途**：区分「价格口径」与「全收益口径」。全收益 − 价格 = 分红贡献，
这是研究高股息类指数的核心分解量。缺少该序列时所有收益数字都必须标注为价格口径。

### 报告自动校验器

`docs/report/validate_report.py <html路径>`：检查章节齐全、嵌入数据无 NaN、页面无 JS 错误、
所有 SVG 曲线包围盒非零、路径以 M 起笔、图表窗口与正文样本区间一致。退出码 0/1。


### 报告自动校验器（13 项检查）

`docs/report/validate_report.py <html路径>`，退出码 0 通过 / 1 失败。

| 类 | 检查项 |
|---|---|
| 结构 | 章节数 ≥6；含「结论」「本质总结」必需章节 |
| 数据 | 嵌入 JSON 无 NaN/Infinity；可解析 |
| 运行时 | 页面无 JS 错误 |
| 渲染 | 每条 path 包围盒非零；路径以 M 起笔（合法 SVG）；**包围盒落在自身 viewBox 内** |
| 一致性 | **开关按钮数 / 曲线数 / 图例项数匹配**；KPI 卡片数；**图表窗口与正文样本区间一致** |

**后三项是 2026-10-23 新增**，起因：加第二个基准时漏了按钮与 Y 轴范围，
曲线被裁到图外但原有检查（只看包围盒非零）全部通过。

**注意**：统计图形元素需同时计 `path` 与 `rect`——成分重叠图用 rect 绘制，
只计 path 会把它误判为「有图例无图形」。


### index_constituents_cni — 国证系列成分（akshare 补）

国证（98xxxx）系列指数在理杏仁无成分覆盖，改用 `ak.index_detail_cni(symbol=...)`。
由 `scripts/fetch_cni_constituents.py` 写入。

| 字段 | 说明 |
|---|---|
| index_code, date, stock_code | 联合主键 |
| stock_name, industry | 成分名称、所属行业 |
| mcap, weight | 总市值（亿）、权重（%） |

**已知覆盖**：980081 国证价值100（100 只，2026-09-30）。
**用途**：成分重叠分析、按权重把个股财报聚合为指数级财务（存在幸存者偏差，须声明）。

### index_fundamental_daily 补拉

多数指数的该表从 2016/2018 起，个别新指数（如 000510 中证A500）只有发布日起（2024-09-23）的数据。
`scripts/fetch_index_fundamental_range.py` 可按指定区间补拉 PE/PB/股息率及 10 年分位。

**注意**：`/index/fundamental` 返回的是**扁平键**（`"pe_ttm.mcw"`），不是嵌套字典。
按嵌套解析会静默把所有字段写成 NULL（踩过：把 000510 的 503 行股息率全部覆盖成空）。


### stock_entry_conditions — 自选股「入条件」

个股体检后写入的可量化买入条件，由 `scripts/eval_entry_conditions.py --add` 维护。

| 字段 | 说明 |
| :--- | :--- |
| stock_code | 主键 |
| stock_name | 名称 |
| rules | JSON 数组，规则见下表 |
| summary | 人读的一句话条件总结 |
| source | 条件来源（哪份报告/哪次分析） |
| enabled | 是否启用 |

**规则 DSL：** `price_above_ma(n)` / `price_below_ma(n)` / `vol_ratio(op,v)` /
`canslim(op,v)` / `pe_pct(op,v)` / `rsi(period,op,v)` / `dist_high(op,v)` /
`pos_250(op,v)` / `turnover(op,v)`

### stock_entry_alerts — 每日评估结果

| 字段 | 说明 |
| :--- | :--- |
| stock_code + date | 联合主键，幂等覆盖 |
| satisfied / total | 满足条数 / 总条数 |
| detail | JSON，逐条 {rule, ok, desc} |
| triggered | 是否全部满足（= 驾驶舱提醒条件） |

**调用链**：`daily_update.py 第39步` → 写本表 → `GET /api/cockpit/entry-alerts`
→ 驾驶舱「🔔 自选提醒」tab。
