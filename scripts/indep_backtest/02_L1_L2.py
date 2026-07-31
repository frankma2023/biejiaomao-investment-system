# -*- coding: utf-8 -*-
"""独立回测 · L1 画像 + L2 单信号基准表现
只读宽表 JSON，做统计。分年 + 分市场环境 + 入场日健康核查。
"""
import json, sqlite3, sys, statistics as st
from collections import defaultdict

WIDE = r"D:\hanako\investment-system\docs\analysis\mw_indep_wide.json"
DB = r"D:\hanako\investment-system\data\lixinger.db"
HOLDS = [5, 10, 20, 60]


def pct(x): return f"{x*100:.1f}%"


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    n = len(vals)
    wins = sum(1 for v in vals if v > 0)
    return {
        "n": n, "win": wins / n, "mean": st.mean(vals),
        "median": st.median(vals),
        "std": st.pstdev(vals) if n > 1 else 0.0,
    }


def print_stats(label, s):
    if not s:
        print(f"  {label:<22} 无样本"); return
    print(f"  {label:<22} n={s['n']:>6}  胜率={pct(s['win']):>7}  "
          f"均值={s['mean']:>7.2f}%  中位={s['median']:>7.2f}%  std={s['std']:>6.2f}")


def build_market_regime(con):
    """用 000985 收盘 vs MA60/MA120 定义牛/熊/震荡。"""
    rows = con.execute("SELECT date, close FROM index_daily_kline "
                       "WHERE stock_code='000985' AND kline_type='normal' ORDER BY date").fetchall()
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    regime = {}
    for i in range(len(dates)):
        if i < 120:
            regime[dates[i]] = "unknown"; continue
        ma60 = sum(closes[i-59:i+1]) / 60
        ma120 = sum(closes[i-119:i+1]) / 120
        c = closes[i]
        if c > ma60 > ma120:
            regime[dates[i]] = "bull"
        elif c < ma60 < ma120:
            regime[dates[i]] = "bear"
        else:
            regime[dates[i]] = "ranging"
    return regime


def main():
    data = json.load(open(WIDE, encoding='utf-8'))
    recs = data["records"]
    con = sqlite3.connect(DB)
    regime = build_market_regime(con)

    print("=" * 70)
    print("L1 · 信号画像")
    print("=" * 70)
    print(f"总记录: {len(recs)}")
    with_b2 = sum(1 for r in recs if r.get('b2_date') and r['b2_date'] != '_sentinel_')
    print(f"含 B2:  {with_b2} ({pct(with_b2/len(recs))})")

    # 入场日健康核查：T+1 涨幅分布（有没有大量涨停入场污染）
    print("\n入场日 T+1 相对 B1 的 gross_5 只是持有期，核查入场质量用另法。")
    # tech_score 分布
    ts = [r['tech_score'] for r in recs if r.get('tech_score') is not None]
    print(f"tech_score: min={min(ts)} max={max(ts)} mean={st.mean(ts):.1f} median={st.median(ts)}")

    print("\n" + "=" * 70)
    print("L2 · 单信号基准表现（全量 B1，T+1 开盘入场，扣 0.3% 双边成本）")
    print("=" * 70)
    for h in HOLDS:
        s = stats([r.get(f'ret_{h}') for r in recs])
        print_stats(f"持有 {h} 日 (净)", s)
    print("\n  [对照] 毛收益（未扣成本）:")
    for h in HOLDS:
        s = stats([r.get(f'gross_{h}') for r in recs])
        print_stats(f"持有 {h} 日 (毛)", s)

    print("\n  [超额] 相对 000985 同期:")
    for h in HOLDS:
        exc = []
        for r in recs:
            rt, ix = r.get(f'ret_{h}'), r.get(f'idxret_{h}')
            if rt is not None and ix is not None:
                exc.append(rt - ix)
        s = stats(exc)
        print_stats(f"持有 {h} 日 超额", s)

    print("\n" + "=" * 70)
    print("L2-A · 分年拆分（H10 净收益）")
    print("=" * 70)
    by_year = defaultdict(list)
    for r in recs:
        y = r['b1_date'][:4]
        by_year[y].append(r.get('ret_10'))
    for y in sorted(by_year):
        print_stats(f"{y}", stats(by_year[y]))

    print("\n" + "=" * 70)
    print("L2-B · 分市场环境拆分（H10 净收益，以 B1 日 000985 状态定义）")
    print("=" * 70)
    by_reg = defaultdict(list)
    for r in recs:
        reg = regime.get(r['b1_date'], 'unknown')
        by_reg[reg].append(r.get('ret_10'))
    for reg in ['bull', 'ranging', 'bear', 'unknown']:
        if reg in by_reg:
            print_stats(f"{reg}", stats(by_reg[reg]))

    con.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    main()
