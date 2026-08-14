# -*- coding: utf-8 -*-
"""
干净重跑：H00922 250日 vs 年度 回撤口径对比（数据来自入库表，已验证一致）
重点：修正早期回测可能的数据污染，输出可信矩阵
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system')
os.chdir(r'D:\hanako\investment-system')

db = sqlite3.connect(r'data\lixinger.db')
db.row_factory = sqlite3.Row
rows = db.execute("SELECT date, close FROM index_full_return_daily WHERE stock_code='H00922' ORDER BY date").fetchall()
db.close()
dates = [r['date'] for r in rows]
closes = [r['close'] for r in rows]
print('H00922 表数据: %d 条 (%s ~ %s)' % (len(closes), dates[0], dates[-1]))

THRESHOLDS = [0.08, 0.10, 0.12, 0.15, 0.20]
COOLDOWN = 20

def dd250(closes):
    out = []
    for i in range(len(closes)):
        w = closes[max(0, i-249):i+1]
        hi = max(w)
        out.append((hi - closes[i]) / hi)
    return out

def dd_annual(dates, closes):
    out = []
    cur = None; hi = None
    for i in range(len(closes)):
        y = dates[i][:4]
        if y != cur:
            cur = y; hi = closes[i]
        if closes[i] > hi:
            hi = closes[i]
        out.append((hi - closes[i]) / hi)
    return out

def detect(dds, th, start):
    ev = []
    last = -999
    for i in range(start, len(dds)):
        if dds[i] >= th and i - last >= COOLDOWN:
            ev.append(i)
            last = i
    return ev

def fwd(events, w):
    rets = []
    for i in events:
        if i + 1 + w >= len(closes):
            continue
        entry = closes[i+1]
        rets.append((closes[i+1+w] / entry - 1) * 100)
    return rets

def stat(rs):
    if not rs: return None
    n = len(rs); s = sorted(rs)
    return {'n': n, 'win': round(sum(1 for r in rs if r > 0)/n*100, 1),
            'med': round(s[n//2] if n%2 else (s[n//2-1]+s[n//2])/2, 2),
            'avg': round(sum(rs)/n, 2)}

import random
random.seed(42)
valid = list(range(260, len(closes)-60))
samples = random.sample(valid, min(2000, len(valid)))
base = {20: [], 60: []}
for i in samples:
    for w in (20, 60):
        if i+1+w < len(closes):
            base[w].append((closes[i+1+w]/closes[i+1]-1)*100)
print('随机基准: 20日', stat(base[20]), '| 60日', stat(base[60]))

print('\n=== 250日滚动 vs 年度 矩阵（H00922 全收益）===')
print('%-6s%-6s%-6s%-10s%-10s%-10s%-10s' % ('口径', '阈值', '触发', '20日胜率', '20日中位', '60日胜率', '60日中位'))
d250 = dd250(closes)
dan = dd_annual(dates, closes)
for name, dds, start in [('250日', d250, 250), ('年度', dan, 0)]:
    for th in THRESHOLDS:
        ev = detect(dds, th, start)
        s20, s60 = stat(fwd(ev, 20)), stat(fwd(ev, 60))
        if not s20: continue
        print('%-6s%-6d%-6d%-10s%-10s%-10s%-10s' % (
            name, int(th*100), s20['n'],
            str(s20['win'])+'%', str(s20['med'])+'%',
            str(s60['win'])+'%', str(s60['med'])+'%'))
