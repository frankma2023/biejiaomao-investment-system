# -*- coding: utf-8 -*-
"""临时验证：周线笔快照内容检查（检查后删除）"""
import json
import sqlite3

c = sqlite3.connect(r"D:\hanako\investment-system\data\lixinger.db")
# 最新周快照
rows = c.execute(
    "SELECT scan_date, bi_json FROM chanlun_weekly_bi_json "
    "WHERE stock_code='688432' ORDER BY scan_date DESC LIMIT 2").fetchall()
for d, bj in rows:
    bis = json.loads(bj)
    print(d, "笔数", len(bis), "最新笔:", bis[-1]["direction"] if bis else "-")

# 早期周快照（笔应从 0~2 逐步增长，防未来验证）
rows = c.execute(
    "SELECT scan_date, bi_json FROM chanlun_weekly_bi_json "
    "WHERE stock_code='688432' ORDER BY scan_date ASC").fetchall()
early = [(d, len(json.loads(bj))) for d, bj in rows if bj][:12]
print("早期周笔数序列:", early)
assert early[0][1] <= 2, "首周笔数异常（疑似未来数据）"

# 单调性粗查：笔数不应剧烈回落（max_bi_num=50 封顶除外）
cnts = [n for _, n in [(d, len(json.loads(bj))) for d, bj in rows if bj]]
big_drops = sum(1 for a, b in zip(cnts, cnts[1:]) if b < a - 10)
print("周间笔数骤降(>10)次数:", big_drops, "/", len(cnts))
print("OK")
