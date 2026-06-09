"""Update all three PRDs with new PLUS standard"""
import os
base = "D:/hanako/investment-system/docs/product"

updates = {
    "MW信号前端页面_产品需求书.md": [
        ("v2.2 | 日期：2026-06-08 | 状态：✅ 权重重构 + 标签修复 + 默认最新日期",
         "v2.3 | 日期：2026-06-08 | 状态：✅ PLUS标准升级(D+I1+I2满分)"),
        ("PLUS 标准：总分 ≥ 80 且 D满分(score_d=15) 且 I1满分(score_i1=15)",
         "PLUS 标准：总分 ≥ 80 且 D满分(score_d=15) 且 I1满分(score_i1=15) 且 I2满分(score_i2=15)"),
    ],
    "MW信号识别引擎_产品需求书.md": [
        ("v3.1 | 日期：2026-06-08 | 状态：✅ 评分权重重构 (D→15, I2→15, Sig→10)",
         "v3.2 | 日期：2026-06-08 | 状态：✅ 权重重构 + PLUS标准升级(D+I1+I2满分)"),
    ],
    "MW信号评分规则修订_产品需求书.md": [
        ("v2.0 | 日期：2026-06-08 | 状态：✅ 权重重构 (D→15,I2→15,Sig→10累加制)",
         "v2.1 | 日期：2026-06-08 | 状态：✅ 权重重构 + PLUS标准升级(D+I1+I2满分)"),
        ("- [x] 权重重构：D 5→15，I2 10→15（新三阶梯），Sig 25→10（新累加：base/pp→+6, cdl/talib→+1, 封顶10）",
         "- [x] 权重重构：D 5→15，I2 10→15（新三阶梯），Sig 25→10（新累加：base/pp→+6, cdl/talib→+1, 封顶10）\n- [x] PLUS标准升级：D满分+I1满分+I2满分 → 21只精选信号，10d中位翻倍至+16.22%"),
    ],
}

for filename, pairs in updates.items():
    path = os.path.join(base, filename)
    with open(path, "r", encoding="utf-8") as f:
        t = f.read()
    for old, new in pairs:
        t = t.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(t)
    print(f"Updated: {filename}")

print("Done.")
