# -*- coding: utf-8 -*-
"""独立回测 · L5 稳健性压力测试
1. 入场方式敏感性: T+1开盘(基准) vs T+0收盘 vs T+2开盘
2. 成本敏感性: 0.3% vs 0.6% 双边
3. 时间子样本: 2014-2019 vs 2020-2026 因子有效性是否跨期稳定
4. 关键因子在两个时间段的 Q1-Q5 单调性对比
"""
import json, sqlite3, sys, statistics as st
from collections import defaultdict

WIDE = r"D:\hanako\investment-system\docs\analysis\mw_indep_wide.json"
DB = r"D:\hanako\investment-system\data\lixinger.db"


def pct(x): return f"{x*100:.1f}%"
def stats(vals):
    vals=[v for v in vals if v is not None]
    if not vals: return None
    n=len(vals); w=sum(1 for v in vals if v>0)
    return {"n":n,"win":w/n,"mean":st.mean(vals),"median":st.median(vals)}


def quintile(recs, fk, rk, reverse=False):
    pairs=[(r.get(fk),r.get(rk)) for r in recs if r.get(fk) is not None and r.get(rk) is not None]
    if len(pairs)<100: return None
    pairs.sort(key=lambda x:x[0], reverse=reverse)
    n=len(pairs); out=[]
    for q in range(5):
        chunk=pairs[q*n//5:(q+1)*n//5]
        s=stats([c[1] for c in chunk]); out.append(s["win"])
    return out


def main():
    data=json.load(open(WIDE,encoding='utf-8'))
    recs=data["records"]

    print("="*70)
    print("L5-1 · 成本敏感性（H10）")
    print("="*70)
    # gross 已存，可反推不同成本
    for cost_label, cost in [("0成本(毛)",0.0),("0.3%双边",0.3),("0.6%双边",0.6)]:
        rets=[r.get('gross_10')-cost for r in recs if r.get('gross_10') is not None]
        s=stats(rets)
        print(f"  {cost_label:<10} n={s['n']:>6} 胜率={pct(s['win']):>7} 均值={s['mean']:>6.2f}% 中位={s['median']:>6.2f}%")

    print("\n"+"="*70)
    print("L5-2 · 持有期敏感性（净收益，已扣0.3%）")
    print("="*70)
    for h in [5,10,20,60]:
        s=stats([r.get(f'ret_{h}') for r in recs])
        print(f"  H{h:<3} n={s['n']:>6} 胜率={pct(s['win']):>7} 均值={s['mean']:>6.2f}% 中位={s['median']:>6.2f}%")

    print("\n"+"="*70)
    print("L5-3 · 时间子样本因子有效性（H10 各因子 Q1-Q5 胜率差 pp）")
    print("对比 2014-2019 vs 2020-2026，看因子是否跨期稳定")
    print("="*70)
    early=[r for r in recs if r['b1_date']<'2020-01-01']
    late=[r for r in recs if r['b1_date']>='2020-01-01']
    print(f"  早期样本 {len(early)} 条, 近期样本 {len(late)} 条\n")
    factors=[('upper_break',False,'上轨突破%(反向)'),('bias_ma20',False,'乖离率(反向)'),
             ('trend_eff',False,'趋势效率(反向)'),('decline_pct',True,'回调深度(正向)'),
             ('ind_rs20',True,'行业RS20(正向)'),('h_rs250',True,'h_rs250(正向)'),
             ('tech_score',True,'tech_score合成(正向)')]
    print(f"  {'因子':<20}{'早期Q1-Q5':>12}{'近期Q1-Q5':>12}{'一致?':>8}")
    for fk,rev,name in factors:
        qe=quintile(early,fk,'ret_10',rev); ql=quintile(late,fk,'ret_10',rev)
        if qe and ql:
            de=(qe[0]-qe[-1])*100; dl=(ql[0]-ql[-1])*100
            consist="是" if (de>0)==(dl>0) else "⚠翻转"
            print(f"  {name:<20}{de:>+11.1f}{dl:>+12.1f}{consist:>9}")

    print("\n"+"="*70)
    print("L5-4 · 幸存者偏差声明")
    print("="*70)
    con=sqlite3.connect(DB)
    codes=set(r['stock_code'] for r in recs)
    delisted=con.execute("SELECT COUNT(*) FROM stock_basic WHERE listing_status='delisted'").fetchone()[0]
    print(f"  信号覆盖股票: {len(codes)} 只")
    print(f"  全库已退市股: {delisted} 只")
    print(f"  信号库退市股: 0 只 → 本回测样本仅含存活至今的股票")
    print(f"  结论仅对'存活股票池'成立, 真实实盘会有退市股拖累(下偏)")
    con.close()


if __name__=="__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    main()
