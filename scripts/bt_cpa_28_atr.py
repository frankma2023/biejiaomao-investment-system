#!/usr/bin/env python3
"""
【待办 #28】ATR 口径对照：引擎版（H-L 均值） vs Wilder 经典版（三选一取最大）

不比较"哪个信号多"，而是问两个可判定问题：
  T1 预测力：ATR 的用途是度量"典型日波动幅度"→ 哪个口径更能预测未来的实际波动？
     目标量用与定义无关的口径：未来 20 日 |收盘-前收盘| 的均值（价格单位，与 ATR 同量纲）
  T2 跳空分辨力：方法论源头用 ATR 判定"跳空 ≥0.75×ATR"→ 哪个口径能把真实跳空正确识别出来？
     真跳空定义：当日最低 > 前日最高（向上跳空缺口）
  T3 横截面一致性：两口径之比在股票间的离散度（离散大 = 判据跨股票不等价）
"""
import sys, os, sqlite3, random, statistics, math
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

N = 20
conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
conn.execute('PRAGMA busy_timeout=60000')
allcodes = [r[0] for r in conn.execute(
    "SELECT DISTINCT stock_code FROM daily_kline_adj WHERE date>='2016-01-01'")]
random.seed(42)
codes = random.sample(allcodes, 800)
print(f'样本 {len(codes)} 只｜ATR 窗口 {N} 日｜未来波动窗口 {N} 日\n', flush=True)


def wilder_atr(kl, n=N):
    trs = [None] * len(kl)
    for i, k in enumerate(kl):
        h, l = k['high_adj'], k['low_adj']
        pc = kl[i - 1]['adj_close'] if i > 0 else None
        if h is None or l is None:
            continue
        trs[i] = (h - l) if pc is None else max(h - l, abs(h - pc), abs(l - pc))
    out = [None] * len(kl)
    seed = [t for t in trs[:n] if t is not None]
    if len(seed) == n:
        out[n - 1] = sum(seed) / n
        for i in range(n, len(kl)):
            if trs[i] is None or out[i - 1] is None:
                continue
            out[i] = (out[i - 1] * (n - 1) + trs[i]) / n
    return out


# T1: 预测力（相关性 / 偏差）
import itertools
pa_e, pa_w, act = [], [], []
# T2: 跳空
gap_tot = 0; hit_e = 0; hit_w = 0; gap_size = []
# T3: 比值
ratios = []
n_stock = 0
for ci, code in enumerate(codes):
    kl = cpa.load_klines(conn, code, '2014-01-01')
    if len(kl) < 120:
        continue
    ind = cpa.compute_indicators(kl)
    atr_e = ind['atr20']
    atr_w = wilder_atr(kl, N)
    cl = [k['adj_close'] for k in kl]
    n_stock += 1
    for i in range(60, len(kl) - N - 1):
        a_e, a_w = atr_e[i], atr_w[i]
        if not a_e or not a_w:
            continue
        # 未来 N 日实际波动（与定义无关）
        ch = [abs(cl[j] - cl[j - 1]) for j in range(i + 1, i + 1 + N) if cl[j] and cl[j - 1]]
        if len(ch) < N:
            continue
        real = sum(ch) / len(ch)
        pa_e.append((a_e, real)); pa_w.append((a_w, real))
        act.append(real); ratios.append(a_e / a_w)
        # 跳空检测
        h, l = kl[i]['high_adj'], kl[i]['low_adj']
        ph = kl[i - 1]['high_adj'] if i > 0 else None
        if l and ph and l > ph:
            gap_tot += 1
            size = l - ph
            gap_size.append(size)
            if size >= 0.75 * a_e:
                hit_e += 1
            if size >= 0.75 * a_w:
                hit_w += 1
    if ci % 200 == 0:
        print(f'  ...{ci}/{len(codes)}', flush=True)


def corr(xs, ys):
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n)
    if not sx or not sy:
        return 0
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / n / (sx * sy)


print(f'\n股票数 {n_stock}，样本点 {len(act):,}')
print('=' * 80)
print('T1 预测力：ATR20 预估 vs 未来 20 日实际日均波动')
e_est = [a for a, _ in pa_e]; w_est = [a for a, _ in pa_w]
print(f'  引擎版   r = {corr(e_est, act):.4f}   平均 ATR {statistics.mean(e_est):.4f} / 实际 {statistics.mean(act):.4f}'
      f'   平均低估 {(1 - statistics.mean(e_est) / statistics.mean(act)) * 100:+.2f}%')
print(f'  Wilder版 r = {corr(w_est, act):.4f}   平均 ATR {statistics.mean(w_est):.4f} / 实际 {statistics.mean(act):.4f}'
      f'   平均低估 {(1 - statistics.mean(w_est) / statistics.mean(act)) * 100:+.2f}%')
print(f'  比值（引擎/Wilder）：中位 {statistics.median(ratios):.4f}｜'
      f'10分位 {sorted(ratios)[len(ratios) // 10]:.4f}｜90分位 {sorted(ratios)[len(ratios) * 9 // 10]:.4f}')

print()
print('T2 跳空分辨力：真跳空日（当日最低 > 前日最高）共 %d 个' % gap_tot)
if gap_tot:
    print(f'  引擎版  把 {hit_e:,} 个判为「≥0.75×ATR 的大跳空」（占 {hit_e / gap_tot * 100:.1f}%）')
    print(f'  Wilder版 把 {hit_w:,} 个判为「≥0.75×ATR 的大跳空」（占 {hit_w / gap_tot * 100:.1f}%）')

print()
print('T3 横截面一致性：两口径之比在股票间是否一致')
by_stock = []
print('  （离散越大 = 判据在不同股票间越不等价）')
conn.close()
