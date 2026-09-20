# 周K线 CPA 判定引擎 — 开发交接文档

> 2026-09-18 写（晚间更新：T1~T7 已完成，见 §0）。任务：按 PRD `docs/product/周K线CPA判定引擎_产品需求书.md` 开发周K线CPA全流程。
> 本文档自足成立，不依赖会话记忆。重写引擎前必读第 4/5/6 节。

---

## 0. 【2026-09-18 晚更新】T1~T7 完成记录

### 0.1 完成清单

| 步骤 | 状态 | 交付物 / 验证 |
|---|---|---|
| T2 日线参数化 | ✅ | cpa_stage.py CFG 新增 21 键（§5.3 全部点位 + atr_series/ema 写死修复）。回归：scripts/_t2_regression.py，600309+002648 逐字节一致（5507 行/955 迁移） |
| T1 周线引擎重写 | ✅ | cpa_stage_weekly.py v2 全重写（三死罪全修 + CFG 污染防护）。A4 十项修订值全部生效 |
| T3 单股验证 | ✅ | scripts/_t3_weekly_verify.py：①~⑧ 全可达、无单阶段>60%、防未来抽查 6853 笔顶 0 泄露、2024-09-30 单日周四价与日线一致 |
| T4 全量脚本 | ✅ | scripts/backfill_cpa_weekly.py（薄壳+覆盖率预检，<90% 拒跑）。**待用户执行**（先跑数据层全量，见 0.3） |
| T5 API 重建 | ✅ | /api/cpa/stock-weekly 镜像日K版（ISO 周K/EMA/ATR 真值/invalidated 三态周单位）。test_client 200 验证通过，**需重启 8788 生效** |
| T6 前端 | ✅ | cpa-stock-weekly.html 全重写（消费新 API、色带/失效位/迁移三态/折叠 original 展示/双页互跳）；日K页仅加互跳链接（A9） |
| T7 daily_update | ✅ | 步骤 35a（周线笔增量）+ 36（周线CPA增量 v3 判据）。幂等实测：二次运行 0 行重写 |
| T8 收尾 | ⏭ | 删临时脚本 + commit + review skill（待用户确认） |

### 0.2 本轮新决策（对 PRD/交接文档的偏离与补充）

| 决策 | 内容 | 理由 |
|---|---|---|
| D5 增量判据 v3 | 周线CPA增量 = 「快照 max(scan_date) > CPA max(date)」的股票才重算（快照缺失=数据层未回填，跳过） | v1 判定（快照周落后市场周）有漏洞：CPA 行为空的股票永远不被补。v3 与 35a 产出严格衔接、天然幂等 |
| D6 文档漏项修复 | compute_indicators 的 atr_series 写死 20、ema_series 写死 10/20 也参数化了（§5.3 只列了 vr_series） | 周线 atr_win=8 必须吃到；日线默认值不变零行为差异（回归已证） |
| D7 周线⑥w 占比偏高 | 688432 ⑥w 35% / 600309 18%（全表 0.002%，因大多数股票无笔顶快照时 ⑥w 判据恒 False） | warn_tops_count=2 让收敛判定更敏感（PRD 定值），先观察全量重算后分布，偏敏再议（候选：EMA10 斜率连续 2 周转负才 warn） |
| D8 collapse original | 折叠改写的行 metrics_json.original 存原始判定；action 按折叠后 stage 重算 | 折叠 ⑥a→⑥ 会翻转动作语义（清仓→观察） |
| D9 ⑥⑦⑧ detail 的 i-39 | run_state_machine ⑧ 入口 detail 里的 40 日振幅展示字段未参数化（判据已参数化） | 纯展示字段，最小变更原则；周线会显示 39 周振幅，语义可接受 |

### 0.3 数据层状态与全量重算操作手册（重要）

**现状**：
- chanlun_weekly_bi_json：正在全量回填中（8 workers，约 5970 只，预计 15~30 分钟）
- cpa_stage_stock_weekly：**混有旧引擎脏数据**（旧 dow==0 口径 74 万行与新引擎 238 万行并存，日期键不同未互相覆盖）
- 第一次全市场增量跑是「无笔顶快照」状态（tops=[]），5637 只的 CPA 证据不全

**待执行（数据层回填完成后）**：
```
# 1. 全量 CPA 重算（purge 清掉混口径脏数据 + 无快照跑的半残数据）
python scripts/backfill_cpa_weekly.py --workers 8

# 2. 重启 8788 加载新 API
# 3. 浏览器验收 cpa-stock-weekly.html（serve_dev.py 8772，勿用裸 http.server）
# 4. git commit + review skill 双轴复核（T8）
```

### 0.4 幂等验证证据

- 增量跑两次：第一次 2385335 行/665053 迁移/5637 只；第二次行数完全一致
- v3 判据下二次运行：0 行重写，0s 退出 ✓（A10）

---

## 1. 任务全景与当前进度

```
① 源码精读                    ✅ 完成
② chanlun_weekly.py 数据层     ✅ 完成并验证（真周线笔+逐周快照防未来）
③ backfill_chanlun_weekly.py   ✅ 完成并冒烟验证（全市场回填进行中，见 §0.3）
④ 重写 cpa_stage_weekly.py     ✅ 完成（v2，§0.1）
⑤ 日线侧硬编码窗口参数化        ✅ 完成（§0.1 T2）
⑥ 全量重算脚本                 ✅ 脚本就绪，待用户执行（§0.3）
⑦ API /api/cpa/stock-weekly    ✅ 完成（需重启 8788）
⑧ 前端页面+双页互跳             ✅ 完成
⑨ daily_update.py 接入          ✅ 完成（步骤35a+36，幂等已验证）
⑩ git commit → review skill    ⏭ 待用户确认
⑪ 验收 PRD §9 A1~A10           ◐ 引擎层 A1/A2/A4 过；A3 待全量重算后复验；A5~A7 待重启+浏览器验收
```

---

## 2. 已交付文件（全部已验证）

### 2.1 `src/scanners/chanlun_weekly.py`（新模块，数据层核心）

| 函数 | 作用 | 关键点 |
|---|---|---|
| `iso_aggregate(df)` | 日线 DataFrame → ISO 周K | group_key=`{ISO年}-W{ISO周:02d}`，**代表日=该周最后交易日**（组内须覆盖更新 date，已修 bug） |
| `load_daily_df(code, end_date, max_weeks)` | 读 daily_kline_adj 复权日线 | end_date 防未来 |
| `week_is_complete(rep_date, data_max, now=None)` | 完整周判定 | 双充分条件：①数据越过周日历周五 ②**现实时间**已过周五（日期级比较，已修盘中误判bug）。长假周五休市周靠②不丢信号 |
| `to_rawbars(code, weeks)` | 周K → RawBar(freq=W) | dt=代表日 |
| `scan_stock_weekly_all(code, target_dates, max_weeks)` | 单股全历史扫描 | n_init=1 逐根 update（每根都有结果）；**快照存个股自己的周代表日**（ISO周键映射，见 3.2 bug#1）；防未来逐周快照 |
| `analyze_weekly(code, limit=260, end_date=None)` | 当前视角快照（API/调试用） | 尾部不完整周丢弃 |
| `verify_vs_w_fri(code)` | ISO vs W-FRI 等价验证 | 验收 A1 工具 |
| `ensure_tables(conn)` | 建 `chanlun_weekly_bi_json` | PK(stock_code,scan_date) + algo_version 列 |

**验证证据（已实测通过）**：
- ISO vs W-FRI：688432 全历史 196/196 周匹配，OHLC 零差异
- 688432 周线笔 16 根 vs 日线笔 50 根（真降尺度，验收 A2）
- 2026 国庆长假周抽查：W40 代表日 09-30、W41 代表日 10-10，无错位
- 早期周快照 0 笔渐增（无未来泄露）；周间笔数骤降 0 次
- 周五盘中跑（12:05），最新快照正确停在 09-11（上一完整周），本周 W38 跳过

### 2.2 `scripts/backfill_chanlun_weekly.py`（回填脚本，已验证）

- 范式镜像 `backfill_chanlun.py`：by-stock 多进程、单事务写入、busy 重试
- `--codes / --start / --end / --workers / --purge / --stats / --incremental`
- 股票池=流动性过滤（POOL_SQL）；周目标日=市场日历（index_daily_kline 000001 各周最后交易日）
- 增量按 (code, algo_version='czsc101_w') 过滤；`--purge` 清表重跑
- **冒烟实测**：2 只 × 547 周 2 秒；增量重跑 0 周写入（幂等 ✓）；`--purge` 后 716 周重算一致

### 2.3 临时脚本（上线前清理）

- `scripts/_check_weekly_bi.py`（数据层验证用）
- `scripts/_t2_regression.py`（**保留**：日线引擎回归工具，改引擎后复跑）
- `scripts/_t3_weekly_verify.py`（**保留**：周线验收工具，A1/A4 复验用）
- `scripts/_check_weekly_cpa_quick.py`（一次性快查，可删）

### 2.4 数据库现状

- `chanlun_weekly_bi_json` 全量回填中（§0.3）
- 旧表 `cpa_stage_stock_weekly` / `cpa_stage_stock_weekly_transitions`：**新旧口径混存**，待 T4 purge 清干净
- 库最大日期 2026-09-17；688432 数据起点 2022-11-10

---

## 3. 修掉的 bug（都在已交付文件里，列此备查）

1. **ISO聚合组内date未覆盖更新**：iso_aggregate 初版只写组首日，已改为组内持续覆盖为代表日
2. **周五停牌股丢整周**：初版按市场代表日精确匹配快照日期，个股周内部分停牌（如周五停牌）→ 整周丢失。改为 ISO 周键映射：目标市场周 → ISO键 → 个股自身聚合 bar，快照落在**个股自己的周代表日**。与日线引擎「按自身日期查快照」的消费契约对称
3. **长假周提前收市误判不完整**：只有「数据越过周五」一个条件，周五休市的节前周会被永久跳过。加墙钟规则（现实时间过周五即完整）
4. **墙钟 datetime 比较错误**：`now > friday` 用 datetime 比，周五中午 12:05 > 00:00 会把进行中的周误判完整。改 `now.date() > friday.date()`（日期级）

## 3b. 【本轮新增】T1/T2 修掉的 bug

5. **atr_series/ema_series 写死窗口**：compute_indicators 里 `atr_series(highs, lows, 20)` / `ema_series(closes, 10/20)` 均未读 CFG（交接文档只发现 vr_series 一处）。已全部参数化（atr_win/ema_fast/ema_slow），日线默认值不变零行为差异
6. **增量判据 v1 漏洞**：CPA 行为空的股票（如新上快照的股）永远不会被增量补算。v3 判据改为「快照领先 CPA 行才重算」（§0.2 D5）
7. **index_daily_kline 列名**：指数代码列是 `stock_code` 不是 `index_code`（kline_type 区分 normal/total_return）

---

## 4. 关键设计决策（含对 PRD 的偏离记录）

| 决策 | 内容 | 理由 |
|---|---|---|
| D1 聚合口径 | 引擎与缠论统一用 **ISO week**（group_key 同款），W-FRI resample 仅作交叉验证 | W-FRI label=周五日历日，长假周五休市时标签非交易日会错位；ISO 分组实测与 W-FRI 196/196 等价 |
| D2 快照归属 | 周线笔快照存**个股自己的周代表日**（非市场代表日） | 停牌对称性；消费方用个股自身日期查表 |
| D3 表结构 | `chanlun_weekly_bi_json` 镜像日线 `chanlun_bi_json` + algo_version 列（'czsc101_w'） | 多版本共存防串写 |
| D4 引擎架构 | 周线引擎 `import scanners.cpa_stage as daily`，worker 进程内 `daily.CFG.update(WEEKLY_CFG)` 后调用 daily.run_state_machine | 判据逻辑零改动，参数集隔离（PRD §5 关键约束）。⚠ 单进程串行跑多只时此法会污染日线 CFG——全量重算走多进程 worker（每进程独立副本），单股调试路径 run_weekly 入口统一 _ensure_cfg()（幂等 update，v2 已修死罪3） |
| D5 增量判据 | 周线CPA增量 = 快照领先 CPA 行（§0.2） | 幂等 + 与 35a 衔接 |
| D6 硬编码全参数化 | atr/ema/vr 全部读 CFG（§0.2） | 周线覆盖的通道完整性 |

---

## 5. WEEKLY_CFG v2 完整参数表（已全部落地于 cpa_stage_weekly.py 头部）

（原 §5 参数表保留备查——**实现时逐键核对无遗漏**，且实际键集比文档多：ema_fast/ema_slow/atr_win 显式列出防漂移。）

### 5.1 保留项（14项）
```python
'atr_win': 8, 'atr_win_slow': 20, 'vr_win': 8, 'pctile_win': 52,
'w_win_min': 2, 'w_win_max': 12, 'w_win_long': 4,
'cb_window_min': 1, 'cb_window_max': 4,
'b_win_min': 4, 'b_win_max': 12,
'r_high_recency': 12,
'e_nd10_min': 3.0, 'e_d10_pct_min': 0.12, 'e_pctile_min': 90,
'ftd_win_min': 1, 'ftd_win_max': 3,
't_in_days': 1,
```

### 5.2 修订项（10项，日线值→周线值）
```python
'inv_n': {'②→③': 3, '②→④': 8, '③→④': 8, '④→⑤': 6, '⑤→⑥': 6, '→⑥': 3},  # 单位=周
'w_first_lookback': 4, 'b_depth_max': 0.10, 'e_peak_gain_min': 0.60,
'cb_tol_atr': 0.15, 'cb_hold_days': 1, 'w_amp_tol': 0.15,
'ftd_ret_min': 0.05, 'r_d20_max_pct': -0.18, 'r_panic_lookback': 4,
```

### 5.3 硬编码窗口覆盖参数（已全部在日线侧参数化，T2 完成）

（原表保留：r_panic_lookback 20→4 / ftd_min_history 260→52 / ftd_low_lookback 120→24 / pause_recent_win 5→2 / pause_prior_win 20→5 / pause_prior_gap 5→1 / pause_vol_min 3,10→1,2 / pause_shrink_min 3,8→1,2 / pause_mid_min_seg 8→2 / win_floor 20→4 / b_floor_atr 0.3→0.15 / bd_win 15→3 / bd_amp_max 0.08→0.12 / bd_min_points 8→3 / support_lookback 40→8 / six_dir_lookback 5→1 / crossback_low_win 2→0 / warn_tops_count 3→2 / data_min_ratio 0.8→0.7 / vr_win 20→8）

---

## 6. 精读结论（v2 重写已完成，保留作 review 对照）

### 6.1 日线引擎关键签名（`src/scanners/cpa_stage.py`，T2 后 CFG=78+21 键）

```
CFG                        # judge_* 全部直接读它
ACTIONS                    # stage→动作枚举；'⑥w':'减仓'
run_state_machine(conn, code, kl, ind, tops, warmup=260) → (daily_rows, trans_rows)
load_klines(conn, code, min_date)   # daily_kline_adj raw_*列→open/high/low_adj
_parse_tops(bi_json)                # direction='向下'的笔：sdt=顶日期 high=顶价
load_bi_tops_by_date(conn, code, dates)  # {date: 笔顶}，每日期查当日快照（防未来）
compute_indicators(kl)              # 全部窗口读 CFG（T2 后）
collapse_states(daily_rows, min_days=5)  # 返回 [(date, stage, orig_stage)]
mark_invalidated(conn, code=None)   # 日线专用（表名/inv_n/N*1.45天硬编码）→ 周线用自己的变体
ensure_tables(conn) / _backfill_worker(codes) / backfill(start, end, workers, purge)
```

### 6.2 run_state_machine 内部结构（v2 周线复用已验证）

分支顺序：①a/①b→(reversal细分子阶段+wedge_pop+出口兜底gate=recency_fail) → ②→(below_ma_confirmed→⑥ / 跌破fail_low→①a / exhaustion④crossback竞争) → ③→(破EMA20→⑥ / 破cb_low→② / exhaustion④) → ④→(回箱内→⑥或③ / exhaustion / 连续箱体find_box+b_prior_gain_max上界) → ⑤→(跌破EMA10→⑥) → ⑥⑦⑧→(above_ma_confirmed→②复活 / reversal→① / ⑦下行回踩 / ⑧窄幅平台 / ⑥延续)。

注意点：
- `ctx['cb_low']` 用 `lows[i-crossback_low_win:i+1]`（周线=当周）
- ④ 失效回 ③ 的条件 `c < box['high']`（不是 box low）
- ⑥ 复活优先级最高（在 reversal 之前）
- metrics 固定键：nd10/nd10_slow/nd20/vr/ema10/ema20 + 条件键 entry_path/b1_overlap/b2_overlap/boundary_sample/init_flag/six_dir/six_dir_pct/six_dd/detail
- ⑥w/warn_from、T/transition_zone 是输出层标签，不改内部 stage
- b1/b2 重叠窗口 i-2..i+2（周线保留，PRD 未要求改）

### 6.3 旧周线引擎的三个死罪（v2 已全修）

1. `load_weekly_klines` 按 `dow==0 or not week_bars` 分组 → **周一停牌股整周错位**（dow==0 bug）→ v2 用 chanlun_weekly.iso_aggregate
2. 笔顶借日线 `chanlun_bi_json` 最新快照 → **尺度错配 + 未来函数** → v2 查 chanlun_weekly_bi_json 按扫描周快照，缺失用前一周可见集兜底
3. CFG 暂替在主进程做、单股路径漏 update → v2 run_weekly 入口统一 `_ensure_cfg()`（幂等），mark_invalidated_weekly 用 try/finally 保存恢复

### 6.4 server.py 现状（T5 后）

- `/api/cpa/stock-weekly`（~7698行）：v2 全重写——ISO 周K现算（load_weekly_klines+compute_indicators）、metrics 展开 entry_path/six_dir/original、invalidated 三态（inv_n 周×7天窗口）
- 日线 `/api/cpa/stock` 未动（A9）
- ⚠ **需重启 8788 才生效**（当前进程是 9/17 启动的旧代码）

### 6.5 前端（T6 后）

- `web/discipline/cpa-stock-weekly.html` v2 同名覆盖：消费新 API、阶段色带（≥3周）、失效位阶梯线、迁移三态（周单位文案）、折叠 original 提示、双页互跳
- 日K页 `cpa-stock-detail.html` 仅加互跳链接 + .hop 样式（A9 负约束达成）
- 服务用 `python scripts/serve_dev.py 8772 web`（当前 8772 跑的是裸 http.server——老问题，待换）

---

## 7. 待办清单（按执行顺序）

### ~~T1~T7~~ ✅ 全部完成（见 §0.1）

### T8. 收尾（待用户确认后执行）

1. 数据层全量回填完成后：`python scripts/backfill_cpa_weekly.py --workers 8`（purge 清混口径数据）
2. 重启 8788（新 API 生效）
3. 删 `scripts/_check_weekly_bi.py`、`scripts/_check_weekly_cpa_quick.py`
4. git add -A + commit + push → review skill 双轴复核（Standards + Spec 对照 PRD）
5. 验收 PRD §9：A3（全量重算后复验分布）/ A5~A7（浏览器验收）/ A8（覆盖数确认）/ A10（已验）

### 后续观察项（PRD 校准候选，不阻塞验收）

- ⑥w 占比（D7）：全量重算后若 >20% 明显偏敏，候选方案「EMA10 斜率连续 2 周转负才 warn」
- ⑧ 入口 detail 的 40 周振幅展示字段（D9）：语义可接受，如需周化再参数化
- t_out_win/t_out_need/slope_lag 周线沿用日线值（4中3周/3周）：PRD 未要求改，T3 观察无异常

---

## 8. 运行环境

```
项目：D:\hanako\investment-system
环境变量：$env:PYTHONPATH='D:\hanako\investment-system\src'（PowerShell）
Python 命令：python（非 python3）；文件读写 encoding='utf-8'
DB：data/lixinger.db（20G WAL）
API：python src/server.py → 8788（T5 后需重启）
前端：python scripts/serve_dev.py 8772 web（强制no-cache）
冒烟命令：
  # 数据层全量（先跑）
  python scripts/backfill_chanlun_weekly.py --start 2016-01-01 --workers 8
  # CPA 全量（后跑，purge）
  python scripts/backfill_cpa_weekly.py --workers 8
  # 单股调试
  python src/scanners/cpa_stage_weekly.py --code 600309
  # 回归（改日线引擎后必跑）
  python scripts/_t2_regression.py snapshot && python scripts/_t2_regression.py compare
```

---

## 9. 验收标准（PRD §9 A1~A10）

| # | 标准 | 状态 |
|---|---|---|
| A1 | 抽查3只含长假周周K OHLC与手动一致 | ✅ 数据层+引擎层已验（2024-09-30 单日周四价=日线） |
| A2 | 周线笔数<日线笔数、笔顶对齐合成周K高低点 | ✅ 数据层已验（16 vs 50）+ 防未来 6853 笔顶 0 泄露 |
| A3 | 全市场最新周阶段分布非退化（①-⑥可达，无单阶段>60%） | ◐ 无快照版 2.4M 行：⑧26%/⑥20%/②20% 达标；待全量重算后复验 |
| A4 | WEEKLY_CFG v2 十项修订值生效（debug打印核对） | ✅ 全部核对通过（含硬编码覆盖键） |
| A5 | API镜像日K版；weekly条数=周K数；transitions日期=各周最后交易日 | ✅ test_client 验证；待重启 8788 后浏览器复验 |
| A6 | 新页面色带/失效位/迁移标记/tooltip/互跳全工作 | ✅ 代码完成；待浏览器验收 |
| A7 | 浅色模式全覆盖；红涨绿跌硬编码 | ✅ #ef4444/#10b981 硬编码 + tv() 主题适配 |
| A8 | 全量重算覆盖≥5900只，最新周快照非空 | ⏭ 待 T4 执行 |
| A9 | 负约束：analyze()未动；日K页仅加互跳；列表页未动 | ✅ 已自查 |
| A10 | daily_update含两增量步骤且幂等 | ✅ 步骤35a+36，v3 判据二次运行 0 行 |

---

## 10. 陷阱备忘（血泪清单）

1. **周线语境单位**：1日=1根周K、1周≈5交易日。所有「天数」参数落 CFG 时想清楚单位（inv_n 是周、days_in_stage 是周数）
2. **快照日期=个股自身代表日**：周线笔快照、周K序列、CPA落库行三者的 date 必须同源（个股自身聚合），消费方按个股日期查
3. **warmup=26 周**：分位窗 52 周意味着 pctile 类指标前 52 周为 None——但状态机从第 26 周开始推进，pctile_of 对 None 有保护（返回 None→判据 False），可接受；如遇 ⑤ 全程无信号先查这里
4. **mark_invalidated 别忘周线版**：日线版硬编码表名，直接调会错标日线表（v2 已实现 mark_invalidated_weekly，try/finally 恢复日线 CFG）
5. **W-FRI 仅验证用**：正式数据链路禁止混入 W-FRI resample（口径决策 D1）
6. **serve_dev.py**：改完前端必用 8772 serve_dev，裸 http.server 有缓存坑（当前 8772 就是裸的，待换）
7. **CFG 污染**：run_weekly 内 update daily.CFG 后如需在同进程跑日线引擎，必须重载模块（多进程回算天然隔离）
8. **无快照跑 CPA 是半残数据**：tops=[] 时 ⑥w/笔顶收敛恒 False，能跑但证据不全。数据层回填没完成前不要把无快照 CPA 行当真（本轮已实证：2.4M 行待 purge 重算）
9. **旧引擎口径残留**：dow==0 旧数据与新数据日期键不同，INSERT OR REPLACE 不会覆盖（日期不同），必须 purge
