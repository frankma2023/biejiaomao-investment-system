# -*- coding: utf-8 -*-
"""更新 CPA PRD §11.2 待办清单状态"""
import io, sys
sys.stdout.reconfigure(encoding='utf-8')
P = 'docs/product/CPA阶段判定引擎_产品需求书.md'
t = io.open(P, encoding='utf-8').read()

REPL = [
    ("| 1 | 阶段① 四组对照（①A/①B/①C/①D） | §10.1-10.2 | ⬜ |",
     "| 1 | 阶段① 四组对照（①A/①B/①C/①D） | §10.1-10.2 | ✅ 已完成（结论见 §11.3）｜⚠️ 绝对数值待复权修复后重算 |"),
    ("| 2 | 阶段② 三组对照（②A/②B/②C） | §10.3 | ⬜ |",
     "| 2 | 阶段② 三组对照（②A/②B/②C） | §10.3 | ✅ 已完成（结论见 §11.3）｜⚠️ 绝对数值待复权修复后重算 |"),
    ("| 24 | **全站复权口径统一**（§9.15）：抽 `adj.py` → 改 cpa_stage/stock_rs/watchlist_report/chanlun/server → 全量重算 | §9.15 | ⬜ 新增（阻塞级） |",
     "| 24 | **全站复权口径统一**（§9.15） | §9.15 | ✅ **已完成（2026-09-12）**——改用视图 `daily_kline_adj`（`lxr_fc_*` 为底），"
     "cpa_stage/stock_rs/watchlist_report/chanlun/server 全部迁移并验证；`stock_rs_daily` 与 `cpa_stage_daily` 已全量重算 |"),
]
n = 0
for a, b in REPL:
    if a in t:
        t = t.replace(a, b, 1); n += 1
    else:
        print('未匹配：', a[:60])

# 在 24 行后追加两项新待办
EXTRA = ("| 25 | **回测 1/2 在修复后的口径上重跑**（§11.3 结论 3 已声明「绝对数值待复权修复后重算」） | §11.3 | ⬜ 新增（回测 3 的后续）|\n"
         "| 26 | **缠论笔全量重画**（前复权口径）——§11.3 结论 4：笔可继续用但必须换成前复权重画。"
         "当前 `chanlun_bi_json` 是历史未复权价画的，2026-09-12 起新增的是复权价画的，**表内口径已混合**。"
         "CPA 的笔顶序列判据依赖此表 | §11.3 | ⬜ 新增（阻塞级）|\n")
anchor = "| 24 | **全站复权口径统一**（§9.15） | §9.15 |"
i = t.find(anchor)
j = t.find('\n', t.find('|', t.find('|', t.find('|', i + len(anchor)) + 1) + 1))
add_pos = t.find('\n', t.find('完成', i)) + 1
if '| 25 |' not in t:
    t = t[:add_pos] + EXTRA + t[add_pos:]
    n += 2

io.open(P, 'w', encoding='utf-8').write(t)
print(f'✅ 更新 {n} 处')
