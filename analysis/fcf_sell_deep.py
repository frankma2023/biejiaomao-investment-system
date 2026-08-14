# -*- coding: utf-8 -*-
"""
卖点信号深化验证：
1. PE分位阈值扫描（70/75/80/85）——看 >80% 有效是否稳健
2. 涨幅信号多阈值扫描（20日 8/10/12%，60日 12/15/18%）
3. 组合信号：PE>70% 且 60日涨幅>10%
"""
import sys, os, sqlite3, bisect
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')

DB = r'data\lixinger.db'
COOLDOWN = 20
WINDOWS = [20, 60, 120]

def load():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    tri = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' ORDER BY date").fetchall()
    fund = db.execute("""SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily
        WHERE stock_code='000922' AND pe_ttm_pct IS NOT NULL ORDER BY date""").fetchall()
    db.close()
    return [dict(r) for r in tri], [dict(r) for r in fund]

def stat(rs):
    if not rs: return {'n': 0}
    n = len(rs); s = sorted(rs)
    return {'n': n, 'win': round(sum(1 for r in rs if r > 0)/n*100, 1),
            'neg': round(sum(1 for r in rs if r < 0)/n*100, 1),
            'med': round(s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2, 2),
            'avg': round(sum(rs)/n, 2)}

def main():
    tri, fund = load()
    dates = [r['date'] for r in tri]
    closes = [r['close'] for r in tri]
    fund_dates = [f['date'] for f in fund]
    def nf(d):
        p = bisect.bisect_right(fund_dates, d) - 1
        return fund[p] if p >= 0 else None

    def run(name, cond):
        ev = []
        last = -999
        for i in range(250, len(dates)):
            d = dates[i]
            v = nf(d)
            if v is None: continue
            if cond(v, i) and i - last >= COOLDOWN:
                ev.append(i); last = i
        out = {w: [] for w in WINDOWS}
        for i in ev:
            for w in WINDOWS:
                if i + w < len(closes):
                    out[w].append((closes[i+w]/closes[i]-1)*100)
        s20, s60, s120 = stat(out[20]), stat(out[60]), stat(out[120])
        flag = '🔴' if s60.get('med') is not None and s60['med'] < -1.5 else ('🟡' if s60.get('med') is not None and s60['med'] < 1.86 else '⚪')
        print('%-28s n=%-3d 20日中位=%-6s 60日中位=%-6s 60日下跌=%-5s 120日中位=%-6s %s' % (
            name, len(ev),
            (str(s20.get('med'))+'%' if s20.get('med') is not None else '—'),
            (str(s60.get('med'))+'%' if s60.get('med') is not None else '—'),
            (str(s60.get('neg'))+'%' if s60.get('neg') is not None else '—'),
            (str(s120.get('med'))+'%' if s120.get('med') is not None else '—'),
            flag))

    print('=== PE分位阈值扫描（60日基准中位 1.86%）===')
    for th in [0.70, 0.75, 0.80, 0.85]:
        run('PE分位>%d%%' % (th*100), lambda v, i, th=th: v['pe_ttm_pct'] > th)

    print('\n=== 涨幅阈值扫描 ===')
    for th in [8, 10, 12]:
        run('20日涨幅>%d%%' % th, lambda v, i, th=th: i >= 20 and (closes[i]/closes[i-20]-1)*100 > th)
    for th in [12, 15, 18]:
        run('60日涨幅>%d%%' % th, lambda v, i, th=th: i >= 60 and (closes[i]/closes[i-60]-1)*100 > th)

    print('\n=== 组合信号 ===')
    run('PE>70% 且 20日涨幅>8%', lambda v, i: v['pe_ttm_pct'] > 0.70 and i >= 20 and (closes[i]/closes[i-20]-1)*100 > 8)
    run('PE>70% 且 60日涨幅>12%', lambda v, i: v['pe_ttm_pct'] > 0.70 and i >= 60 and (closes[i]/closes[i-60]-1)*100 > 12)
    run('PE>75% 且 60日涨幅>10%', lambda v, i: v['pe_ttm_pct'] > 0.75 and i >= 60 and (closes[i]/closes[i-60]-1)*100 > 10)

if __name__ == '__main__':
    main()
