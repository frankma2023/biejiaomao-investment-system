# -*- coding: utf-8 -*-
"""L5-补 · 入场方式敏感性：T+0收盘 / T+1开盘 / T+2开盘 对比（H10 净收益）
独立重算，验证结论不依赖入场时点。
"""
import sqlite3, sys, statistics as st
from collections import defaultdict

DB=r"D:\hanako\investment-system\data\lixinger.db"
COST=0.3

def pct(x): return f"{x*100:.1f}%"
def stats(vals):
    vals=[v for v in vals if v is not None]
    if not vals: return None
    n=len(vals); w=sum(1 for v in vals if v>0)
    return {"n":n,"win":w/n,"mean":st.mean(vals),"median":st.median(vals)}

def load_kline(con,code):
    rows=con.execute("SELECT date,open,close,change_pct FROM daily_kline WHERE stock_code=? ORDER BY date",(code,)).fetchall()
    out=[]
    for d,o,cl,chg in rows:
        c=chg if chg is not None else 0.0
        if abs(c)>0.5: c=0.0
        out.append({"date":d,"open":o,"close":cl,"chg":c})
    nav=1.0
    for i,b in enumerate(out):
        if i==0: b["nav"]=1.0
        else:
            nav=nav*(1+b["chg"]); b["nav"]=nav
    return out

def main():
    con=sqlite3.connect(DB); con.execute("PRAGMA cache_size=-200000")
    sigs=con.execute("SELECT stock_code,b1_date FROM mw_signal_daily WHERE b1_date!='_sentinel_' ORDER BY stock_code,b1_date").fetchall()
    by=defaultdict(list)
    for code,b1 in sigs: by[code].append(b1)

    res={"T+0_C":[],"T+1_O":[],"T+2_O":[]}
    for code,b1list in by.items():
        bars=load_kline(con,code)
        if len(bars)<30: continue
        d2i={b["date"]:i for i,b in enumerate(bars)}
        H=10
        for b1 in b1list:
            if b1 not in d2i: continue
            i_b1=d2i[b1]
            # 三种入场
            for label,ent_idx,use_open in [("T+0_C",i_b1,False),("T+1_O",i_b1+1,True),("T+2_O",i_b1+2,True)]:
                if ent_idx>=len(bars): continue
                eb=bars[ent_idx]
                if use_open:
                    if eb["chg"]>=0.099 and eb["open"]==eb["close"]: continue
                    pc=bars[ent_idx-1]["close"]
                    if not pc or pc<=0 or not eb["open"] or eb["open"]<=0: continue
                    entry_nav=bars[ent_idx-1]["nav"]*(eb["open"]/pc)
                else:
                    entry_nav=eb["nav"]
                i_exit=ent_idx+H
                if i_exit>=len(bars): continue
                gross=bars[i_exit]["nav"]/entry_nav-1
                res[label].append(gross*100-COST)

    print("="*60)
    print("入场方式敏感性（H10 净收益，扣0.3%）")
    print("="*60)
    for label in ["T+0_C","T+1_O","T+2_O"]:
        s=stats(res[label])
        print(f"  {label:<8} n={s['n']:>6} 胜率={pct(s['win']):>7} 均值={s['mean']:>6.2f}% 中位={s['median']:>6.2f}%")
    con.close()

if __name__=="__main__":
    sys.stdout.reconfigure(encoding='utf-8'); main()
