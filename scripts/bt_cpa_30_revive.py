#!/usr/bin/env python3
"""
#30 影响的对照实测（300 只，不写库）
对比「3中2」（旧）与「4中3」（新）下：
  1. 迁移次数、⑥→② 复活次数（whipsaw 减少多少）
  2. 每个阶段的总天数分布变化
  3. 002648 那段 04-29~05-07 的短 ② 是否消失
"""
import sys, os, sqlite3, random, statistics
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
conn.execute('PRAGMA busy_timeout=60000')
allcodes = [r[0] for r in conn.execute(
    "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
random.seed(42)
codes = random.sample(allcodes, 300)


def run(code):
    kl = cpa.load_klines(conn, code, '2014-01-01')
    if len(kl) < 320:
        return None
    ind = cpa.compute_indicators(kl)
    tops = cpa.load_bi_tops_by_date(conn, code, [k['date'] for k in kl])
    return cpa.run_state_machine(conn, code, kl, ind, tops)


def summarize(daily, trans):
    st = Counter(); runs = 0; prev = None
    for row in daily:
        s = row[2]
        base = row[2][:-1] if row[2].endswith('T') else row[2]
        st[base] += 1
        if base != prev:
            runs += 1
        prev = base
    rev = sum(1 for t in trans if t[3] in ('②',) and t[2] in ('⑥a', '⑥b', '⑥c'))
    return st, runs, rev, len(trans)


CFG = cpa.CFG
print('=== 旧参数（3中2） vs 新参数（4中3），300 只 ===')
res = {}
for label, (win, need) in (('旧 3中2', (3, 2)), ('新 4中3', (4, 3))):
    CFG['t_out_win'], CFG['t_out_need'] = win, need
    tot_st = Counter(); tot_runs = 0; tot_rev = 0; tot_trans = 0; n = 0
    d002648 = None
    for code in codes:
        r = run(code)
        if not r:
            continue
        daily, trans = r
        st, runs, rev, ntr = summarize(daily, trans)
        tot_st += st; tot_runs += runs; tot_rev += rev; tot_trans += ntr
        n += 1
        if code == '002648':
            d002648 = (daily, trans)
    res[label] = (tot_st, tot_runs, tot_rev, tot_trans, n)
    print(f'\n【{label}】{n} 只')
    print(f'  迁移总次数 {tot_trans:,}｜状态段数 {tot_runs:,}｜⑥→② 复活 {tot_rev:,}')
    top = tot_st.most_common(8)
    print('  阶段天数占比: ' + '  '.join(f'{k} {v / sum(tot_st.values()) * 100:.1f}%' for k, v in top))

o = res['旧 3中2']; nw = res['新 4中3']
print()
print('=== 差异 ===')
print(f'  ⑥→② 复活：{o[2]:,} → {nw[2]:,}（{nw[2] - o[2]:+,}，{(nw[2] / o[2] - 1) * 100 if o[2] else 0:+.1f}%）')
print(f'  迁移总次数：{o[3]:,} → {nw[3]:,}（{nw[3] - o[3]:+,}）')
print(f'  状态段数（抖动指标）：{o[1]:,} → {nw[1]:,}（{nw[1] - o[1]:+,}）')
print()
print('  各阶段天数变化（新-旧，占比百分点）：')
tot_o = sum(o[0].values()); tot_n = sum(nw[0].values())
for k in sorted(set(o[0]) | set(nw[0])):
    do = o[0].get(k, 0) / tot_o * 100; dn = nw[0].get(k, 0) / tot_n * 100
    if abs(dn - do) > 0.3:
        print(f'    {k:<5} {do:>5.2f}% → {dn:>5.2f}%  （{dn - do:+.2f}pp）')

if d002648:
    daily, trans = d002648
    print()
    print('=== 002648 在【新参数】下的段切分（2016 起）===')
    segs = []
    for row in daily:
        b = row[2][:-1] if row[2].endswith('T') else row[2]
        if not segs or segs[-1][0] != b:
            segs.append([b, row[1], row[1], 1])
        else:
            segs[-1][2] = row[1]; segs[-1][3] += 1
    for st, a, b, k in segs:
        if '2026-04' <= a <= '2026-07':
            print(f'  {st:<5} {a} ~ {b}  {k} 个交易日')
conn.close()
