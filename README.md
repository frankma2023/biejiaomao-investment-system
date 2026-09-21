# 蹩脚猫 A 股量化投资系统 (biejiaomao)

基于威廉·欧奈尔 CAN SLIM 投资体系的 A 股全流程量化投资系统，集成缠论技术分析（日线 + 周线双尺度）与 Oliver Kell CPA 阶段判定作为平行维度。覆盖大盘环境判断、指数扫描、个股基本面分析、买入/卖出形态识别、缠论分析、CAN SLIM 评分、阶段判定、投资决策驾驶舱、持仓管理与 LLM 深度分析的完整投资决策链。

## 核心功能

| 模块 | 功能 | 页面 |
|------|------|------|
| **大盘环境** | 抛盘日/追盘日/吸筹日检测 + 大盘健康度评分卡 + 涨跌停计数 + 融资融券 + 微盘水温 | `market-scan` |
| **指数扫描** | 407个指数多周期RS强度 + 最强指数 + 拥挤度 + 机构吸筹出货 + 估值分位 | `index-scan`、`index-valuation`、`strongest-index` |
| **个股基本面** | 估值仪表/盈利质量/财务健康/DCF估值/三表联动/股东人数/基本面恶化检测 | `stock-valuation` |
| **买入形态** | 基部突破V2 + 口袋支点V3 + 双重底 + 扁平基部 + 碟形基部 + 杯柄形态 + 放量箱体突破 | `pattern-scan`、`base-breakout`、`breakouts` |
| **卖出信号** | 高潮见顶/铁轨线/头部形态/量价背离/突破失败/跌破箱体/基本面恶化 | 各独立回测看板 |
| **MW 信号** | 缠论笔结构驱动的 B1/B2 双信号引擎 + 技术置信度评分（TS 七因子回归标定） | `mw-signals`、`pattern-structure` |
| **缠论分析** | 分型→笔→中枢→背驰→买卖信号 + 日/周/月多周期联立 + 区间套级联 + 共振评分 | `chanlun-backtest`、`chanlun-scan` |
| **CPA 阶段判定** | Oliver Kell 八阶段状态机（日线 v3.0 + 周线 v2），事件触发区间语义 + 失效位 + 数据层折叠 | `cpa-stages`、`cpa-stock-detail`、`cpa-stock-weekly` |
| **CAN SLIM 评分** | 七维评分卡（C/A/N/S/L/I/M）v3.6，全市场 5300+ 只每日评分（8 进程 10 分钟） | `canslim-scores`、`canslim-scorecard` |
| **投资决策驾驶舱** | 五级硬过滤管道 → 简报卡 → 五关检查单 → 仓位/止损建议 → LLM 深度分析 | `cockpit`、`deep-analysis` |
| **回测体系** | 21+ 个回测看板，YAML 配置持久化，左参数卡+右信号表统一布局 | 各回测看板 |
| **知行系统** | 每日精选 + 观察池 + 自选池 + 交易记录 + 持仓监控 + 买入前检查清单 + 每日复盘 | `discipline` |
| **全市场扫描** | 每日双强股形态扫描（内嵌 4000+ 股票数据，客户端分页筛选） | `daily-pattern-scan` |
| **每日更新** | 36 步自动化流水线：K线→财务→RS→缠论→MW→CPA→CANSLIM→驾驶舱→复盘 | `scripts/daily_update.py` |

## 技术架构

```
python src/server.py                              # Flask API (端口 8788)
python scripts/serve_dev.py 8772 web               # 静态前端 (端口 8772，强制 no-cache)
```

> ⚠ 前端必须用 `serve_dev.py`（带 Cache-Control 头），裸 `http.server` 会触发浏览器启发式缓存，表现为"改了代码页面不变"。

- **后端**: Python Flask + SQLite（20GB，WAL 模式，1900 万行日K）
- **前端**: 原生 JavaScript + ECharts 5.5.1（hanako-glass 玻璃拟态设计系统，深浅双模式）
- **数据源多轨**:
  - 理杏仁开放 API（主源：K线/财务/RS/两融/股东人数）
  - 通达信本地文件（分钟线：9003 只 .lc1 → 15/60 分钟 K 线）
  - 新浪财经（机构研报，165 家机构/128 个申万二级行业）
  - akshare（兜底）
- **价格口径铁律**: 全站统一 `daily_kline_adj` 视图（理杏仁前复权 `lxr_fc_*` 优先），`lxr_fc_*` = 分红再投口径，禁止用加法复权算收益
- **缠论核心**: CZSC 1.0.1（Rust 实现）+ 自研中枢/背驰/信号引擎 + 周线独立数据层（ISO 周聚合真周线笔）
- **LLM 通道**: DeepSeek CLI 深度分析（驾驶舱候选股自动生成调研报告）
- **引擎自动发现**: `src/` 下实现 `detect()` + `ENGINE_META` 的模块自动注册
- **共享基础设施**: `nav.js`（全站导航+主题） / `kline-chart.js`（通用 K 线图） / `hanako-glass.css`（全站设计系统）

## 项目结构

```
investment-system/
├── config/              ← YAML 配置（引擎参数/回测/策略回测结果）
├── src/
│   ├── server.py        ← Flask API 入口（80+ 端点）
│   ├── engine_registry.py ← 引擎自动发现
│   ├── scanners/        ← 检测引擎（形态/MW/CPA/缠论日线+周线/卖出引擎）
│   ├── cockpit/         ← 投资决策驾驶舱（五级过滤管道/仓位止损）
│   ├── discipline/      ← 知行系统（观察池/交易/持仓/精选）
│   ├── analysis/        ← 财务分析（基本面恶化/DCF/可比公司）
│   ├── analytics/       ← 回测分析（MW 全维度回测等）
│   └── backtest/        ← 回测引擎
├── web/                 ← 前端（55+ 页面）
│   ├── shared/          ← 共享 CSS/JS（hanako-glass.css / nav.js / kline-chart.js）
│   ├── cockpit/         ← 投资决策驾驶舱
│   ├── deep-analysis/   ← LLM 深度分析
│   ├── market-scan/     ← 大盘扫描（含微盘水温）
│   ├── stock-valuation/ ← 个股全维度分析
│   ├── pattern-scan/    ← 统一形态扫描（缠论K线 + 多引擎标记）
│   ├── mw-signals/      ← MW 信号看板
│   ├── canslim-scores/  ← CAN SLIM 全市场评分
│   ├── daily-pattern-scan/ ← 全市场形态扫描
│   ├── discipline/      ← 知行系统（含 CPA 详情双页）
│   └── */               ← 21+ 回测看板
├── scripts/             ← 数据拉取 + 批量计算 + 回填（daily_update.py 为调度入口）
├── docs/
│   ├── product/         ← 产品需求文档（20+ 篇，本地维护不入库）
│   ├── dev/             ← 开发标准 + 交接文档
│   └── DATABASE_SCHEMA.md ← 数据库 Schema（本地维护）
└── data/                ← SQLite 数据库（lixinger.db）
```

## 快速开始

```bash
cd investment-system

# 1. 启动 API（80+ 端点）
python src/server.py

# 2. 启动前端（必须 serve_dev，勿用裸 http.server）
python scripts/serve_dev.py 8772 web

# 3. 访问
# http://localhost:8772

# 4. 每日盘后更新（36 步流水线，收盘后运行）
python scripts/daily_update.py
```

## 开发约定

- Python 命令用 `python`（非 `python3`），文件 `encoding='utf-8'`
- 开发前 → `to-prd` 出需求文档；开发后 → `review` 双轴复核（Standards + Spec）
- `git commit` = `add -A + commit + push` 一条龙
- 回测看板铁律：保存 → YAML 配置 + 加载 → 填充控件
- 引擎规范：`ENGINE_META` + `detect()` + `load_params()`
- 数据铁律：严禁兜底/估算值，缺失数据须通过 API 拉取
- 样式铁律：`hanako-glass.css` 是唯一真相源；红涨绿跌 JS 硬编码 `#ef4444`/`#10b981`，禁用 `var(--rise)/var(--fall)`（语义反直觉）
- 价格口径：复权数据统一走 `daily_kline_adj` 视图，新引擎禁止自行拼复权因子
- CFG 污染防护：长驻进程（server）内调跨参数集引擎必须 apply/restore（参照 `cpa_stage_weekly.compute_weekly_indicators`），禁止裸 update 模块级 CFG
- 增量判据：比较「上游产出 vs 下游落库」，幂等验收 = 二次运行 0 行写入
- 池口径：下游管道的股票池必须与数据生产方同源（如周线 CPA 与周线笔回填同用流动性池）

## 相关文档

- [数据库 Schema](./docs/DATABASE_SCHEMA.md)（本地维护）
- [工作交接](./docs/dev/HANDOVER.md)
- [周K线 CPA 交接](./docs/dev/HANDOVER-weekly-cpa.md)
- [产品需求文档](./docs/product/)（本地维护）
- [项目进度](./web/progress.html)
