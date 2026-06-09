# 策略参数库

回测数据体系化存储，供仓位管理模块加载使用。

## 文件结构

```
config/strategy/
├── README.md                    # 本文件
├── high_conf_pocket_pivot.yaml  # 高置信度口袋支点策略
├── plus_b2p2.yaml               # MW PLUS B2+2策略
└── (未来) base_breakout.yaml    # 基部突破V2策略
```

## 使用方式

```python
import yaml

# 加载策略参数
with open('config/strategy/high_conf_pocket_pivot.yaml') as f:
    cfg = yaml.safe_load(f)

# 取凯利参数
k = cfg['kelly']
half_kelly = k['kelly_half']  # 0.199 → 19.9%仓位

# 取当月胜率
month = '2026-04'
monthly_wr = cfg['monthly'][month]['win_rate_10d']  # 76%

# 根据市场状态调整
market = 'bear'
market_wr = cfg['subgroups']['by_market'][market]['win_rate_10d']  # 63.8%
```

## 字段说明

| 字段 | 含义 | 用途 |
|------|------|------|
| `performance.*.win_rate` | 该持有期的胜率% | 贝叶斯先验概率 |
| `kelly.kelly_half` | 半凯利仓位比例 | 单笔仓位上限 |
| `kelly.avg_win_pct` | 盈利交易平均收益% | 凯利公式 b 参数 |
| `kelly.avg_loss_pct` | 亏损交易平均损失% | 凯利公式分母 |
| `monthly.*.win_rate_10d` | 各月10d胜率 | 贝叶斯月度折扣 |
| `subgroups.by_market.*` | 按市场状态分组胜率 | 贝叶斯市场折扣 |
| `subgroups.by_depth.*` | 按调整深度分组胜率 | 信号质量分层 |

## 更新流程

1. 跑完回测脚本 → 得到原始数据
2. 人工确认数据合理 → 更新对应 YAML
3. git commit → 版本可追溯
4. 仓位管理模块自动读取最新参数
