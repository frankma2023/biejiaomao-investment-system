# -*- coding: utf-8 -*-
"""四只股票分红再投 vs 现金（不复权原始价，与文章同口径）"""
import sys, os
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')
import akshare as ak
import time

TARGETS = {'600519': '茅台', '000651': '格力', '601318': '平安', '600887': '伊利'}
START, END = '2016-08-15', '2026-08-19'

# 1. 分红记录
divs = {c: [] for c in TARGETS}
for year in range(2016, 2027):
    try:
        df = ak.stock_fhps_em(date=f'{year}1231')
        if df is None or len(df) == 0:
            continue
        for c in TARGETS:
            hit = df[df['代码'] == c]
            for _, r in hit.iterrows():
                ex_date = str(r.get('除权除息日') or '')[:10]
                pay = r.get('现金分红-现金分红比例') or 0
                if ex_date and pay and START <= ex_date <= END:
                    divs[c].append((ex_date, float(pay)))
    except Exception:
        pass
for c in TARGETS:
    divs[c] = sorted(set(divs[c]))

# 2. 不复权日线
def load_raw(code):
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=START.replace('-', ''),
                                    end_date=END.replace('-', ''), adjust="")
            return {str(r['日期']): float(r['收盘']) for _, r in df.iterrows()}, [str(r['日期']) for _, r in df.iterrows()]
        except Exception as e:
            print(f'{code} 拉取失败({attempt}): {str(e)[:60]}')
            time.sleep(5)
    return {}, []

def sim(code, reinvest):
    price_map, dates = load_raw(code)
    if not price_map:
        return None
    shares = 10000.0
    cash = 0.0
    for ex_date, pay10 in divs.get(code, []):
        cash += shares * (pay10 / 10)
        if reinvest:
            # 除权日之后第一个有价格的交易日按收盘价再投
            buy_price = None
            for d in dates:
                if d > ex_date and d in price_map:
                    buy_price = price_map[d]
                    break
            if buy_price:
                shares += cash / buy_price
                cash = 0.0
    # 期末
    last_d = dates[-1] if dates else None
    total = shares * price_map.get(last_d, 0) + cash
    # 期初价
    first_d = dates[0] if dates else None
    p0 = price_map.get(first_d, 0)
    return total, 10000 * p0, dates[0] if dates else '?'

print(f'=== 分红再投 vs 拿现金（不复权价，2016-08 ~ 2026-08，100手）===')
print(f'{"股票":<6}{"再投年化":>10}{"现金年化":>10}{"再投-现金":>10}{"分红次数":>8}')
for c, name in TARGETS.items():
    r_r = sim(c, True)
    r_c = sim(c, False)
    if not r_r or not r_c:
        print(f'{name:<6} 数据不足'); continue
    t_r, invest, d0 = r_r
    t_c, _, _ = r_c
    years = 10.0
    ann_r = (t_r / invest) ** (1 / years) - 1
    ann_c = (t_c / invest) ** (1 / years) - 1
    print(f'{name:<6}{ann_r*100:>9.2f}%{ann_c*100:>9.2f}%{(ann_r-ann_c)*100:>+9.2f}pp{len(divs[c]):>8}次')
