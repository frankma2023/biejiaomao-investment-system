#!/usr/bin/env python3
"""
【CPA 回测 #22】invalidated N 值校准

引擎逻辑（cpa_stage.py:1123）：
  迁移后 N×1.45 个【日历日】内，若出现 rank 变小的迁移（回退型） → 该迁移标记 invalidated=1
  N 值取自 CFG['inv_n']：{'②→③':15, '②→④':40, '③→④':40, '④→⑤':30, '⑤→⑥':30, '→⑥':12}

要回答的问题：**这些 N 值是否有依据？**
方法：对每个迁移，找出"首次回退型迁移"的实际滞后（日历日），看分布——
  · 若失败集中在 N 以内 → N 合理
  · 若大量失败落在 N 之外 → N 太短，会漏标
  · 同时给出各分位数，作为重新取值参考

口径：800 只随机（seed 42）的 cpa_stage_transitions；只在 120 个日历日内找回退
"""
import sys, os, sqlite3, random, statistics, time, argparse
from collections import defaultdict
from datetime import datetime, timedelta
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

CFG = cpa.CFG
WINDOW = 120          # 观察上限（日历日）


def rank(s):
    s = (s or '').replace('T', '')
    if s.startswith('①'):
        return 1
    if s.startswith('⑥'):
        return 6
    return {'②': 2, '③': 3, '④': 4, '⑤': 5}.get(s, 0)


def norm_key(fs, ts):
    n_to = ts.replace('T', '')
    if n_to.startswith('⑥'):
        n_to = '⑥'
    return '%s→%s' % ((fs or '').replace('T', ''), n_to)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sample', type=int, default=800)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')

    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM cpa_stage_daily WHERE date>='2016-01-01'")]
    random.seed(42)
    codes = sorted(random.sample(codes, min(args.sample, len(codes))))
    print(f'样本 {len(codes)} 只｜回退观察上限 {WINDOW} 日历日\n', flush=True)

    lags = defaultdict(list)          # key -> [滞后日历日]
    n_ev = defaultdict(int)
    n_fail = defaultdict(int)
    for ci, code in enumerate(codes):
        rows = conn.execute("""SELECT transition_date, from_stage, to_stage
            FROM cpa_stage_transitions WHERE stock_code=? ORDER BY transition_date""", (code,)).fetchall()
        if not rows:
            continue
        for i, r in enumerate(rows):
            key = norm_key(r['from_stage'], r['to_stage'])
            n_ev[key] += 1
            r_to = rank(r['to_stage'])
            d0 = datetime.strptime(r['transition_date'], '%Y-%m-%d')
            for j in range(i + 1, len(rows)):
                d1 = datetime.strptime(rows[j]['transition_date'], '%Y-%m-%d')
                gap = (d1 - d0).days
                if gap > WINDOW:
                    break
                if rank(rows[j]['to_stage']) < r_to:
                    lags[key].append(gap)
                    n_fail[key] += 1
                    break
        if ci % 200 == 0:
            print(f'  ...{ci}/{len(codes)} ({time.time() - t0:.0f}s)', flush=True)

    print('\n' + '=' * 100)
    print('【回测 #22】invalidated N 值校准 —— 各迁移类型的「首次回退」滞后分布（日历日）')
    print('=' * 100)
    print(f'{"迁移":<10}{"事件":>8}{"失败":>8}{"失败率":>7}{"P25":>6}{"中位":>6}{"P75":>6}{"P90":>6}'
          f'{"当前N":>7}{"=日历日":>8}{"覆盖率":>8}')
    for key in sorted(lags, key=lambda k: -n_ev[k]):
        a = sorted(lags[key])
        if not a:
            continue
        n = len(a)

        def p(q):
            return a[int(n * q)] if n > 1 else a[0]
        N = CFG['inv_n'].get(key) or CFG['inv_n'].get('→' + key.split('→')[1])
        cal = int(N * 1.45) if N else 0
        cov = sum(1 for x in a if x <= cal) / n * 100 if cal else 0
        mark = '  ←漏标多' if cal and cov < 70 else ''
        print(f'{key:<10}{n_ev[key]:>8,}{n_fail[key]:>8,}{n_fail[key] / n_ev[key] * 100:>6.0f}%'
              f'{p(0.25):>6}{p(0.5):>6}{p(0.75):>6}{p(0.9):>6}{(N if N else "-"):>7}'
              f'{cal:>8}{cov:>7.0f}%{mark}')
    print(f'\n耗时 {time.time() - t0:.0f}s')
    print('\n注：覆盖率 = 实际失败中，滞后 ≤ 当前 N×1.45 的比例（= 会被正确标记为 invalidated 的比例）')
    conn.close()


if __name__ == '__main__':
    main()
