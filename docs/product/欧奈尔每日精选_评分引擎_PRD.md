# 欧奈尔每日精选 — 评分引擎 PRD v2.0

> 本文档使用 to-prd 技能模板，综合本轮讨论中达成的全部决策，替代 v1.0 临时产品需求书。

## Problem Statement

当前 `src/discipline/screener.py` 第六层（综合评分）使用临时加权公式，权重分配不合理，信号评分采用累加制（一个基部突破仅得 25 分，需 4 个才满分），与实际 O'Neil 体系"一个高质量信号就该拿大头"的理念矛盾。RS 过滤过严（只留双强/龙头），CANSLIM 门槛过高（48 分仅覆盖 12% 股票），信号源包含了已被废弃的形态引擎（双重底、扁平基部等）。用户需要一个精确、可维护、与 O'Neil 方法论一致的评分引擎。

## Solution

对 `src/discipline/screener.py` 的六层筛选引擎进行全面修订：

1. **O'Neil 综合得分** = CANSLIM × 0.30 + RPS250 × 0.30 + 形态信号 × 0.25 + 行业共振 × 0.15
2. **形态信号** 采用"最佳主信号 + 额外加分"模式，基部突破和口袋支点基础分相等（70），取最佳信号分 × 时间衰减
3. **RS 过滤** 放宽：标准精英（RPS250≥80 且 RPS20≥85）、短爆发（RPS20≥95）、长牛回调（RPS250≥90，信号窗口缩至 10 日）
4. **CANSLIM 门槛** 降至 32 分（全 A 前 25%）
5. **信号源** 仅保留 `base_breakout` 和 `pocket_pivot` 作为通过条件；`cdl_engine` 和 `talib_engine` 仅作补充加分
6. **日期基准** 全程使用 `target_date` 替代 `datetime.now()`，支持历史回测

## User Stories

1. 作为交易员，我希望筛选页面的 O'Neil 得分能反映股票的 CANSLIM 质量、相对强度、技术信号和行业共振四个维度，以便快速排序决策
2. 作为交易员，我希望一只在 5 日内出现基部突破的股票能获得接近满分的信号分（约 17.5/25），而不需要它出现 4 个突破信号
3. 作为交易员，我希望基部突破和口袋支点具有相同的信号评分权重，因为两者在 O'Neil 体系中的重要性相当
4. 作为交易员，我希望处于长期强势但短期回调中的股票也能进入候选池，前提是它们近期出现了明确的买入信号
5. 作为交易员，我希望 K 线形态（cdl）和 TA 指标（talib）的看涨信号能作为加分项提升信号分，但不能单独满足通过条件
6. 作为交易员，我希望 CANSLIM 评分优先使用 `batch_canslim_score.py` 最新计算的结果，而非依赖观察池快照的延迟数据
7. 作为交易员，我希望筛选日期可以作为参数传入，以便回溯历史某一天的筛选结果
8. 作为开发者，我希望通过 `python src/discipline/screener.py` 命令行直接运行筛选，输出 TOP 20 股票及得分明细
9. 作为开发者，我希望引擎在运行失败时有明确的回退策略（回退到观察池快照信号），不会因个别股票数据缺失而崩溃
10. 作为开发者，我希望信号窗口基于 `target_date` 而非当前时间，确保离线回测结果可复现

## Implementation Decisions

### ID1: O'Neil 得分公式

```
O'Neil = CANSLIM × 0.30 + RPS250 × 0.30 + 形态信号 × 0.25 + 行业共振 × 0.15
```

- CANSLIM：取 `canslim_scores` 最新评分（MAX(date) GROUP BY stock_code），0-100 线性映射，占 30 分
- RPS250：取观察池 `rps_250`，0-100 线性映射，占 30 分
- 形态信号：见 ID2，占 25 分
- 行业共振：无共振 ×1.0=15 分，模糊命中 ×1.15=17.25 分，精确命中 ×1.25=18.75 分

### ID2: 形态信号评分（25% 组件）

**通过条件**：近 10 日内出现 ≥1 个 `base_breakout` 或 `pocket_pivot` 信号。

**计算公式**：

```
信号分 = 最佳主信号分 + min(额外基部加分, 20) + min(额外形态加分, 10)
信号分封顶 = 100
O'Neil 贡献 = 信号分 / 100 × 25
```

**最佳主信号分**：所有 `base_breakout` / `pocket_pivot` 信号中，取 `基础分 × 时间衰减` 的最大值。

**基础分**：`base_breakout` = `pocket_pivot` = **70**。

**时间衰减**：3 日内 ×1.0，4-6 日 ×0.7，7-10 日 ×0.4，>10 日不计。

**额外基部加分**：除最佳信号外，每个额外的 `base_breakout` / `pocket_pivot`（10 日内）加 5 分，封顶 20 分。

**额外形态加分**：每个 `cdl` 系列或 `talib` 系列看涨信号（10 日内，type='bullish'）加 2 分，封顶 10 分。

**计算示例**：

| 场景 | 最佳主信号 | +额外基部 | +额外形态 | 信号分 | O'Neil 贡献 |
|------|-----------|----------|----------|--------|------------|
| 1 个 3 日内基部突破 | 70×1.0=70 | 0 | 0 | 70 | 17.5 / 25 |
| 1 个 3 日内口袋支点 | 70×1.0=70 | 0 | 0 | 70 | 17.5 / 25 |
| 基部突破 + 口袋支点（均 3 日） | 70 | 5 | 0 | 75 | 18.75 / 25 |
| 1 个 7 日内口袋支点 + 3 个 cdl | 49 | 0 | min(6,10)=6 | 55 | 13.75 / 25 |
| 1 个 10 日内基部突破 + 2 个 talib | 28 | 0 | min(4,10)=4 | 32 | 8.0 / 25 |
| 3 个基部突破（距今天 3/6/10 日） | 70 | min(10,20)=10 | 0 | 80 | 20.0 / 25 |

### ID3: RS 精英过滤（Layer 2）

三档通行：
- **标准精英**：RPS250 ≥ 80 且 RPS20 ≥ 85（覆盖稳健龙头 / 加速爆发 / 双强）
- **短爆发**：RPS20 ≥ 95（不卡 RPS250）
- **长牛回调**：RPS250 ≥ 90 但 RPS20 < 85，信号窗口收紧至 10 日

### ID4: CANSLIM 质量过滤（Layer 3）

- CANSLIM 总分 ≥ 32（全 A 前 25% 分位）
- ROE ≥ 5%
- 营收增速 ≥ 5%
- 资产负债率 ≤ 70%
- 不检查 C/A 子维度单独门槛（总分已包含）

### ID5: 信号源限定

- **通过条件信号**：仅 `base_breakout`、`pocket_pivot`
- **补充加分信号**：`cdl`、`cdl_*`、`talib`、`talib_*`
- **一票否决信号**：`top_pattern`、`climax_top`、`breakout_failure`、`distribution_day`
- 其他引擎信号不计入评分

### ID6: 日期基准统一

所有日期计算（信号窗口、衰减天数）使用 `target_date` 而非 `datetime.now()`。`target_date` 默认为 `discipline_observation_pool` 的最新日期，API 可通过 `?date=YYYY-MM-DD` 覆盖。

### ID7: 数据回退策略

- CANSLIM 评分：优先 `canslim_scores` → 回退 `discipline_observation_pool`
- 形态信号：优先 `run_all_engines(klines, indicators)` 实时计算 → 回退 `discipline_observation_pool.signals_json`
- 名称/K线：通过 `_lookup_name` / `_lookup_kline` 统一查询，支持股票/指数双模式

### ID8: 一票否决

20 日内出现 `top_pattern` / `climax_top` / `breakout_failure` / `distribution_day` 信号直接淘汰。

### ID9: 最终输出

按 O'Neil 得分降序排列，取 TOP 20。输出字段含 `oneil_score`、`canslim_total`、`signal_count`、`signal_summary`、`ideal_buy`、`resonance_name`、`correction_stock` 等。

## Testing Decisions

### 测试策略

- 只测试外部行为：给定输入（股票代码 + 日期），验证输出（得分、排名、过滤结果）
- 不测试引擎内部实现细节

### 验收标准

1. `python src/discipline/screener.py` 命令行输出 TOP 20，得分分布合理，无崩溃
2. 一个 5 日内的基部突破贡献 ≈ 17.5 分（信号分 70 / 100 × 25）
3. `base_breakout` 和 `pocket_pivot` 信号分贡献完全相同
4. 仅有 cdl/talib 信号、没有 base/pocket 信号的股票被 Layer 4 淘汰
5. 长牛回调股（RPS250≥90 且 RPS20<85）的信号窗口为 10 日，标准股为 20 日
6. 指定 `target_date` 参数时，信号窗口相对于该日期而非当前时间计算

### 测试方法

- 先运行 `python scripts/batch_canslim_score.py --force` 更新评分
- 再运行 `python src/discipline/screener.py` 验证输出
- 通过 `http://localhost:8788/api/discipline/screening?date=2026-05-20` 验证 API
- 筛选页面 `http://localhost:8772/discipline/screening.html` 端到端验证

## Out of Scope

- 不修改 `src/scanners/base_breakout.py` 和 `src/scanners/pocket_pivot.py` 引擎逻辑
- 不修改 `src/scanners/cdl_engine.py` 和 `src/scanners/talib_engine.py` 引擎逻辑
- 不新增数据库表或字段
- 不修改筛选页面 UI（仅后端评分引擎）
- cdl/talib 信号的具体识别规则不在本 PRD 范围内（由引擎自行处理）
- 不实现实时推送或定时自动筛选（依赖 `daily_update.py` 调用）

## Further Notes

- 本 PRD 替代 `docs/欧奈尔每日精选_评分引擎产品需求书.md`（v1.0）
- 六层筛选的完整规则文档见 `docs/欧奈尔每日精选_六层筛选规则.html`
- 信号引擎注册机制见 `src/engine_registry.py`
- CANSLIM 评分引擎见 `src/scanners/canslim_score.py`
