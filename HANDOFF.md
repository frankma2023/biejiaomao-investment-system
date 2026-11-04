# 交接文档：A 股投研系统开发

> 写于 2026-11-04。接收方为新的 AI agent。本文档目标是让接手者在 30 分钟内具备独立修改能力，不必重新摸索环境与踩过的坑。

---

## 0. 第一件事：读完这 5 条，再动手

1. **所有指标必须由代码确定性计算，禁止心算或编造数字。** 任何一个数字都要能指向一段可复跑的代码或一个可查询的 SQL。
2. **改完必须实跑验证，不能靠推断。** 本项目历史上多个 bug 就是「改了代码、没重跑、看着像对的」造成的（见 §7）。
3. **`sqlite3.connect()` 遇到不存在的路径会静默创建一个空库，不报错。** 看到「no such table」先查 DB_PATH 解析结果，不要先怀疑数据。
4. **删除任何目录前先 `Get-ChildItem -Recurse` 列出全部内容并逐项确认。** 用 `unlink` 删单个文件，永远不要 `rmtree` 删整个目录。
5. **`docs/` 目录被 gitignore（`.gitignore` 第 90 行）。** 里面是 PRD 与待办，写进去不会进版本库，也不要指望 git 能恢复。

---

## 1. 环境与启动

| 项 | 值 |
|---|---|
| 工作目录 | `D:\hanako\investment-system` |
| Python | `C:\Program Files\Python312\python.exe`（必须用绝对路径，PATH 里的不是这个） |
| SQLite 库 | `data\lixinger.db`（约 180 GB，只读打开请用 `file:...?mode=ro` + `uri=True`） |
| Flask API | `python src/server.py` → `http://localhost:8788` |
| 静态站点 | `python scripts/serve_dev.py 8772 web` → `http://localhost:8772`（代理 `/api/*` 到 8788） |
| Git 远程 | `frankma2023/biejiaomao-investment-system` |
| 数据源 | 理杏仁 API（指数/个股/财务/融资融券）+ 通达信本地文件 |

**启动顺序**：先起 API（8788），再起静态站（8772）。只起静态站页面会全空。

**常用自检**：
```powershell
Invoke-RestMethod 'http://localhost:8788/api/market-health' | ConvertTo-Json -Depth 2
```

---

## 2. 仓库结构（只列会碰到的）

```
src/
  server.py                    Flask 全部 API（约 1400 行，改前先搜路由名）
  scanners/
    market_health.py           大盘健康分（0~100，越高越好）
    market_sell_score.py       大盘卖出评分（≥0，越高越危险）—— 13 个信号
    stock_rs.py                个股相对强度（RS/RPS）全市场计算
    chanlun_weekly.py          周线缠论笔（供周线 CPA 用）
    cpa_stage.py               CPA 九阶段（日线）
    cpa_stage_weekly.py        周线 CPA
    distribution_day.py        抛盘日检测 detect()（纯计算，不写库）
  detectors/
    follow_through_day.py      追盘日检测 detect()（纯计算，不写库）
scripts/
  daily_update.py              每日总入口，约 40 个编号步骤
  backfill_distribution_days.py    抛盘日写库（daily_update 步骤 13b 调用）
  backfill_follow_through_days.py  追盘日写库（步骤 13a）
  incremental_cpa_weekly.py    周线 CPA 增量（步骤 36）
  backfill_chanlun_weekly.py   周线笔（步骤 35a）
  diag_sell_score_thresholds.py    卖出评分阈值校准（可复跑）
config/
  market/
    market_sell_score.yaml     卖出评分全部阈值
    follow_through_day.yaml    追盘日参数
web/
  market-scan/index.html       大盘扫描（健康分 + 卖出评分 + 两张趋势图）
  market-scan/margin.html      两融分析
  stock-valuation/index.html   个股估值（含「市场强度」tab）
  discipline/cpa-stock-weekly.html  个股周线 CPA
  progress.html                开发进度 + 全部教训（到 189 条）
docs/                          被 gitignore
  product/*.md                 各评分卡的 PRD（第七章记录每次修复）
  dev/指数CPA_待办事项.md        待办
```

---

## 3. 每日数据流水线

`scripts/daily_update.py` 是唯一入口，步骤编号即执行顺序。与本次交接强相关的步骤：

| 步骤 | 内容 | 说明 |
|---|---|---|
| 13a | 追盘日明细 | **本次新增**，每日增量回补 120 天 |
| 13b | 抛盘日明细 | **本次新增**，每日增量回补 45 天 |
| 13c | 指数成分股 | **本次新增**，季度执行（首月首个周一） |
| 14 | 大盘卖出评分 | 依赖 13a/13b |
| 27a/27c | 日线缠论笔 | |
| 35a | 周线缠论笔 | 增量，`backfill_chanlun_weekly.py` |
| 36 | 周线 CPA | 依赖 35a |

**入口有交易日守卫**：若 `index_daily_kline` 里 `000985` 当天无日 K 线，直接 `sys.exit(0)` 跳过全部步骤。补算历史请加 `--force`。

---

## 4. 两个大盘评分体系（别混淆）

| | 健康分 | 卖出评分 |
|---|---|---|
| 表 | `market_health_daily` | `market_sell_score_daily` |
| 值域 | 0~100，越高越好 | ≥0，越高越危险 |
| 回答 | 市场**好不好** | 危险**累积多少** |
| 信号数 | 4 技术 + 3 全市场 | **13** |
| 档位 | A≥80/B≥65/C≥50/D≥35/E≥20 | <60正常 / 60~100减仓50% / 100~140减仓25% / ≥140清仓 |

**两者互为镜像，相关约 −0.59，这是设计意图，不是 bug。**

**卖出评分 13 个信号**（分值见 PRD §5.1，阈值全在 `config/market/market_sell_score.yaml`）：

抛盘日、追盘日失效、龙头见顶、垃圾补涨、指数背离、破50日线、破200日线、均线死叉、AD低位、AD崩盘、NH/NL、上涨无量、融资余额恶化。

**熔断规则**（与评分并行）：单个清仓级信号（破200日线/死叉/AD崩盘）→ 仓位≤25%；两个同时 → 强制清仓。

---

## 5. 最近一轮工作的现状（2026-11-02 ~ 11-04）

### 5.1 卖出评分卡从「半瘫痪」修到「全部可用」

接手前它是这样的：**12 个信号里 6 个从未触发**，评分卡实际只有一半在看市场。

修完后的触发率（2026 年 200 个交易日）：

| 信号 | 触发 | 日均贡献 |
|---|---|---|
| 破 200 日线 | 138 天(68%) | 27.2 |
| 破 50 日线 | 122 天(61%) | 18.3 |
| 抛盘日 | 139 天(70%) | 12.0 |
| 追盘日失效 | 65 天(32%) | 9.8 |
| NH/NL | 50 天(25%) | 7.5 |
| 垃圾补涨 | 40 天(20%) | 5.0 |
| AD 崩盘 | 13 天(6%) | 3.9 |
| 融资余额恶化 | 20 天(10%) | 1.4 |
| AD 低位 | 13 天(6%) | 1.3 |
| 龙头见顶 | 10 天(5%) | 1.2 |
| 均线死叉 | 3 天(2%) | 0.9 |
| 指数背离 | 3 天(2%) | 0.6 |
| 上涨无量 | 1 天(0%) | 0.1 |

总分均值 88.6，档位分布：<60 正常 26% / 60~100 减仓50% 24% / 100~140 减仓25% 32% / ≥140 清仓 18.5%。

**关键结论：档位 60/100/140 不需要改。**「档位不对」其实是「权重不对」的症状——把破200日线从 80 降到 40、AD崩盘阈值从 0.5 收紧到 0.15 之后，原档位分布自然健康了。

### 5.2 6 个失效信号的根因（都是不同类型，别当成同一类问题修）

| 信号 | 根因 | 性质 |
|---|---|---|
| 抛盘日 | `distribution_day.py` 的 `detect()` 算得对，**CLI 只打印不写库**，全库没有写入脚本 | 缺管道 |
| 追盘日 | 同上（`follow_through_day.py`） | 缺管道 |
| 龙头见顶 | `WHERE date <= pool_date` 无日期下界，股池累积到 1067 只（当日实际 27 只） | SQL bug |
| 指数背离 | ① `WHERE date=?` 让 `LAG()` 拿不到前一日 → `prev_close` 恒 NULL ② 阈值 `0.5` 当成了 50%（`change` 存的是小数） | 两个 bug |
| AD 低位 | 读 `ad_ratio_value`（已是 5 日均值）再要求「连续 5 天」= 9 天窗口 | 阈值未校准 |
| 上涨无量 | 要求宽基指数连续 5 日收红，2026 年 0 次 | 阈值未校准 |

### 5.3 同期修的页面/数据问题

- 行业分组健康分全 NULL（全局 `MAX(date)` 缺陷）
- `margin.html` 融资余额图 Y 轴 `min:24000` 裁切（余额跌破后折线消失）
- 周线 CPA 停在 2026-09-18（`DB_PATH` 少一层目录 + `INSERT` 列名写成常量名）
- `stock-valuation` 市场强度 tab 无数据（`rs-line` 接口每次全量重算导致超时）

---

## 6. 待办（按优先级）

### P0 结构性隐患

1. **`stock_rs_daily.rs_line` 列有归一化断层**：2026-04-10 附近部分股票的基准从 2014-01-02 变成了当日（历史重算用了截断窗口），断层前旧值未同步。实测 600519：2026-04-09 `869.67` → 2026-04-10 `66.86`（−92%），不是真实市场变动。
   - `/api/stock-rs/rs-line` 已改为用 `adj_close ÷ 指数收盘` 现算绕开该列
   - **但其他读 `rs_line` 的地方（筛选、排序、报表）需要逐一排查**
   - 根治方案：全量重算一次 `rs_line` 并统一基准日

2. **增量脚本的「两表对比」盲区**：`incremental_cpa_weekly.py` 比较的是「CPA 表 vs 周线笔表」的最新日期。两表一起停更时会一致判定「已是最新」——这次僵死两周无人发现就是这么来的。
   - 建议加外部哨兵：对比 `daily_kline` 的最新日期，若周线笔落后超过 N 天则告警

3. **`INSERT` 里把常量名当列名**：`backfill_chanlun_weekly.py` 曾写成 `(stock_code, scan_date, bi_json, WEEKLY_ALGO_VERSION)`，应为 `algo_version`。建议全库 grep 一遍这类写法。

### P1 数据补齐

4. **指数成分股快照 2026-06~10 有 5 个月缺口**：背离信号在这段时间用 2026-05-01 的快照。
   - 补法：`python scripts/fetch_index_constituents.py --months 6 --category=market`
   - **用户已明确决定暂不补**（会再次改变历史结果，背离只占日均 0.6 分）

5. **`market_distribution_days` 是孤儿表**：有 000985 的 235 行数据（2022-02 ~ 2026-04），但全库无任何代码读写，只在 `docs/DATABASE_SCHEMA.md` 提到。建议确认后清理。

### P2 代码质量

6. **图表代码重复**：`market-scan` 的健康分图与卖出评分图已抽出 `makeTrendChart(spec)` 工厂（净减 67 行）。其他页面若有同形图表可套用同一模式。

7. **分值返回风格不统一**：`score_ma_break` / `score_market_width` 返回各分量（推荐模式），但 `score_ftd_failure` / `score_junk_rally` / `score_vol_dry` 仍返回 `(触发标识, 分值)` 二元组。

### P3 已知数据缺失（非缺陷）

8. 中远海控缺 SCFI/CCFI 运价数据
9. 旧报告未迁移到 hanako-glass：`600309_20261027.html`、`300308_20260908.html`、`002648_20260907.html`、`600309_20260907.html`

---

## 7. 必须知道的 6 个坑（每条都真实踩过）

### 坑 1：函数算了正确值，调用方硬编码覆盖 —— 改函数不生效且不报错

`compute_all` 里有两处硬编码分量值，覆盖了函数的返回值：

```python
# 修复前（score_ma_break 内部算的是 80，这里又写死 80）
ma_total, ma50_b, ma200_b, death_c, ma_detail = score_ma_break(...)
ma50_score  = 30 if ma50_b else 0
ma200_score = 80 if ma200_b else 0    # ← 把函数里的修改盖掉了
```

**这是「遮蔽」（shadowing），比普通重复危险得多：改了没反应、不报错，只能通过「改前改后数据是否变化」发现。**

已修：函数返回各分量，调用方直接用。**新增打分函数时请沿用这个模式。**

### 坑 2：共享维表用「取全局最新」的写法

```sql
-- 错
AND date = (SELECT MAX(date) FROM index_constituents)
-- 对
AND date = (SELECT MAX(date) FROM index_constituents c2
            WHERE c2.index_code = ic.index_code)
```

外层虽然已按 `index_code` 过滤，但子查询取的是整表最新日期。一旦只刷新了某一类指数，其他类别在那天没有行 → 全部查空 → 静默产出 NULL。

### 坑 3：硬编码坐标轴 min/max 会裁掉数据，看起来像「数据缺失」

```js
yAxis: { min: 24000 }   // 余额跌破 24000 后，折线整段消失在可视区外
```

**判据：断点位置若正好等于坐标轴整数值，就是裁切；否则才是真缺数据。**

已扫全站 360 个 HTML，15 处字面量 min/max，2 处有风险已改动态。其余是天然有界指标（RPS 0-100、百分比）或静态数据。

**新写图表时用 `min: function(v){ ... }` 动态计算，不要写字面量。**

### 坑 4：写完库但下游查不到 —— 查询条件里的字段没填

`score_ftd_failure` 的查询条件是：

```sql
AND (invalidated_date IS NOT NULL OR is_valid = 1)
```

补数据时只写了 `is_valid=0` 和 `invalidated_reason`，**没写 `invalidated_date`**，失效的 FTD 全被过滤掉，信号仍为 0。

**规则：往已有表补数据前，先读下游的 WHERE 条件，确认哪些字段参与过滤。写入端正确 ≠ 读取端能看见。**

### 坑 5：`DB_PATH` 层级错一层，sqlite 会静默建空库

```python
# 错：停在 src/
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 对：三层到仓库根
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

`sqlite3.connect()` 发现文件不存在会**直接创建一个 0 字节空库，不报错**。所有查询随后报 `no such table`，很容易误判成数据问题。

**新增脚本时，路径拼接后请断言目标文件存在。**

### 坑 6：`rmtree` 整个目录

为清理一个 0 字节空库，执行了 `shutil.rmtree('src/data')`，那里除了空库还有 26 个正当源码文件（`src/data/lixr_api/`）。已从 git 恢复。

**规则：先列出目录全部内容逐项确认；能 `unlink` 单文件就不要 `rmtree` 整目录。**

---

## 8. 工作约定

### 8.1 阈值必须校准，不能凭直觉定

2026 年实测：`AD < 0.7 连续 5 天` 的全历史最长连续只有 3 天，等于永假；`宽基指数连续 5 日上涨` 全年 0 次。

**定阈值前先跑一遍分布**，确认目标触发率（一般 5%~30%），并留下可复跑脚本（参考 `scripts/diag_sell_score_thresholds.py`）。

**注意「涨家数÷跌家数」是右偏比率**：上界无限（几乎没下跌股时可达 6+），下界为 0。长期均值必然 >1，中位数 1.29 才是中性水平。按「接近 1 为中性」的直觉定阈值会偏低。

### 8.2 阈值放在 config，不要内联

`config/market/market_sell_score.yaml` 是范例：10 个顶层键，代码里用 `cfg()['xxx']` 取值，带硬编码兜底。

**改阈值只改 YAML，不改代码。**

### 8.3 颜色语义

- **红涨绿跌**：上涨/买入 `#ef4444`，下跌/卖出 `#10b981`
- **价格方向色不得用于分值、比率、估值差**。CANSLIM 77 分不是「涨」，DCF 溢价 58% 不是「涨」，不要染红。
- `hanako-glass` 共享 CSS 里 `--rise` 是绿、`--fall` 是红（与中文习惯相反），需要语义反转：`.rise{color:var(--fall)}`。

### 8.4 文档与代码同源

PRD 里的数字必须在代码/脚本里可复现（`AGENTS.md` §三.4）。

- 每张评分卡的 PRD 在 `docs/product/`，**第七章记录每次修复的根因与校准依据**
- 改阈值时同步更新 PRD 第七章
- 不要只改 PRD §5.1 的正表而不改第七章，反之亦然——这会导致文档内部矛盾

### 8.5 收尾流程

每次完成一轮工作：

1. `web/progress.html` 追加卡片（问题 + 根因 + 修法 + 日期）
2. 若有可复用的教训，追加 `lesson-card`（编号递增，当前到 **189**）
3. `docs/dev/指数CPA_待办事项.md` 更新已完成项与新增待办
4. `git add -A && git commit && git push`
5. 验证：`git rev-parse HEAD origin/main` 两个哈希应一致（push 的 stderr 噪音是正常的）

### 8.6 验证习惯

- **改完必须实跑。** 不要用「我以为」代替「我跑过」。
- 页面改动用 Playwright 截图确认渲染，不要只看接口返回。
- 数据改动后抽查具体日期，确认数值合理（如融资余额 5 日变化率不应出现 ±100%+）。
- 跨表改动后，把「谁会读它」列一遍逐个看结果。

---

## 9. 关键数据表速查

| 表 | 内容 | 最新 |
|---|---|---|
| `market_health_daily` | 大盘健康分（200 行） | 2026-11-03 |
| `market_sell_score_daily` | 卖出评分（200 行，13 信号分项） | 2026-11-03 |
| `market_health_sector_daily` | 行业分组健康分（9 组/日） | 2026-11-03 |
| `cpa_stage_daily` | 个股 CPA 日线阶段 | 2026-11-03 |
| `cpa_stage_stock_weekly` | 个股 CPA 周线阶段（199 万行） | 2026-11-03 |
| `chanlun_weekly_bi_json` | 周线缠论笔（191 万周） | 2026-10-30 |
| `stock_rs_daily` | 个股 RS/RPS（1158 万行） | 2026-11-03 |
| `daily_margin_history` | 个股融资余额（75 万行） | 2026-11-02 |
| `index_constituents` | 指数成分股（月度快照） | 2026-11-01 |
| `distribution_days_detail` | 抛盘日明细（000985 有 441 行） | 2026-11-03 |
| `follow_through_days` | 追盘日（000985 有 19 行） | 2026-10-23 |

**指数代码**：中证全指 `000985`、上证 `000001`、深证成指 `399001`、创业板指 `399006`、科创50 `000688`、沪深300 `000300`（`kline_type='normal'`）。

**注意 `000001` 在 `stock_rs_daily` 里是平安银行，在 `index_daily_kline` 里是上证指数。**

---

## 10. 与本项目无关但会影响判断的噪音

- `git push` 经常输出 stderr 警告（LF→CRLF 等），**这不是失败**。用 `git rev-parse HEAD origin/main` 验证两个哈希一致。
- `Select-String -NotMatch 'AD|NH|...'` 这类过滤会误杀含 `ad_low5_score` 的行（PowerShell 大小写不敏感）。读日志时注意。
- 数据库 180 GB，全表扫描很慢。诊断脚本请带日期范围。
- `daily_update.py` 跑完约 40 步、可能几十分钟。日常验证用单步脚本，不要为此跑全流程。

---

## 11. 交接时点的可信状态

| 项 | 状态 |
|---|---|
| Git | `4b70dae`，本地与远程一致，工作区干净 |
| 卖出评分 | 13 信号全部可用，档位 60/100/140 经两轮验证仍适用 |
| 健康分 | 204 行 2026-01-05 ~ 11-03，趋势图与卖出评分同起点 |
| 行业分组 | 9 组正常 |
| 页面 | market-scan / margin / stock-valuation / cpa-stock-weekly 均已验证渲染正常 |
| 教训 | progress.html 至 189 条 |

**接手后建议先做的第一件事**：跑一遍 `python scripts/diag_sell_score_thresholds.py`，确认当前数据下阈值行为与文档一致 —— 这是最快建立「数据可信」认知的方式。
