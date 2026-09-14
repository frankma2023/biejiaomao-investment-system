#!/usr/bin/env python3
"""
【CPA 阶段④ Base 'n Break 专项回测】—— 一次覆盖 #11 / #13 / #14 / #15

PRD §6 的 ④ 判据：
  前置   自最后一次有效入场点起涨幅 ≥20%（**待校准 = #14**）
  箱体   箱顶=最高收盘价，箱底=最低收盘价
  存在性 时长 10-40 日 ｜ 深度 ≤阈值（或 ≤前段涨幅 50%）（**待校准 = #13**）
         后段均量 < 前段均量 ｜ 箱底 ≥ EMA20-0.3×ATR20
  质量   VCP 收缩度（**#11：加了它有没有用**）｜ 触碰次数 ｜ 缠论中枢
  突破   close > 箱顶×1.005 且 VR ≥1.5
  边界   「涨幅<20% 平台突破」→ 独立类别（**#15**）

引擎已记录的字段（metrics_json.detail / transitions.trigger_detail_json）：
  box_high / box_low / win / depth / touch_top / vr
**未记录**（本脚本自算）：
  prior_gain  箱体起点前 120 日内的最低收盘 → 箱底 的涨幅
  vcp_*       箱体前半段 vs 后半段 的区间收缩比与均量收缩比

口径：T+1 复权开盘入场 / H10/20/60 / 扣 0.3% / 超额 = 个股 − 000985 / 同股去重 20 交易日 / 全量事件
用法：python scripts/bt_cpa_04_base.py
"""
import sys, os, sqlite3, statistics, time, argparse, json
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

COST = 0.003


def load_index_ret(conn):
    rows = conn.execute("SELECT date, close FROM index_daily_kline "
                        "WHERE stock_code='000985' ORDER BY date").fetchall()
    ds = [r[0] for r in rows]; cs = {r[0]: r[1] for r in rows}
    idx = {d: k for k, d in enumerate(ds)}

    def ret(d, off):
        k0 = idx.get(d)
        if k0 is None or k0 + off >= len(ds):
            return None
        return cs[ds[k0 + off]] / cs[ds[k0]] - 1
    return ret


def stat(vals):
    if len(vals) < 20:
        return None
    b = sorted(vals); n = len(b)
    return (n, sum(1 for x in b if x > 0) / n * 100,
            statistics.mean(b) * 100, b[n // 2] * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dedup', type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=60000')
    idx_ret = load_index_ret(conn)

    # ── 取全部「进入④」的迁移事件 ──
    evs = []
    for r in conn.execute("""SELECT stock_code, transition_date, from_stage, trigger_detail_json
                             FROM cpa_stage_transitions WHERE to_stage='④' ORDER BY stock_code, transition_date"""):
        try:
            d = json.loads(r['trigger_detail_json'] or '{}')
        except Exception:
            continue
        if not d.get('box_high') or not d.get('box_low'):
            continue
        evs.append({'code': r['stock_code'], 'date': r['transition_date'],
                    'from': r['from_stage'], 'box_high': d['box_high'],
                    'box_low': d['box_low'], 'win': d.get('win'),
                    'depth': d.get('depth'), 'touch': d.get('touch_top'),
                    'vr': d.get('vr')})
    print(f'进入④事件 {len(evs):,} 个（去重 {args.dedup} 日）', flush=True)

    by_code = defaultdict(list)
    for e in evs:
        by_code[e['code']].append(e)

    grp_gain = defaultdict(list)      # #14 前置涨幅分桶
    grp_depth = defaultdict(list)     # #13 箱体深度分桶
    grp_vcp = defaultdict(list)       # #11 VCP 开关
    grp_from = defaultdict(list)      # ②→④ vs ③→④
    allvals = defaultdict(list)
    kept = 0
    for code, lst in by_code.items():
        kl = cpa.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            continue
        pos = {k['date']: j for j, k in enumerate(kl)}
        last_j = -10 ** 9
        lst.sort(key=lambda x: x['date'])
        for e in lst:
            j = pos.get(e['date'])
            if j is None or j + 1 + 20 >= len(kl):
                continue
            if j - last_j < args.dedup:
                continue
            last_j = j
            kept += 1
            win = int(e['win'] or 15)
            b0 = max(0, j - win + 1)          # 箱体起点
            lb0 = max(0, b0 - 120)            # 前置回看窗口起点
            # 前置涨幅：箱体起点前 120 日内最低收盘 → 箱底
            lows = [kl[i]['low_adj'] for i in range(lb0, b0) if kl[i].get('low_adj')]
            prior_gain = (e['box_low'] / min(lows) - 1) if lows and min(lows) > 0 else None
            # 箱体深度
            depth = e['box_high'] / e['box_low'] - 1 if e['box_low'] else None
            # VCP：前半段 vs 后半段
            mid = b0 + win // 2
            h1 = [kl[i]['high_adj'] for i in range(b0, mid) if kl[i].get('high_adj')]
            l1 = [kl[i]['low_adj'] for i in range(b0, mid) if kl[i].get('low_adj')]
            h2 = [kl[i]['high_adj'] for i in range(mid, j + 1) if kl[i].get('high_adj')]
            l2 = [kl[i]['low_adj'] for i in range(mid, j + 1) if kl[i].get('low_adj')]
            v1 = [kl[i]['volume'] for i in range(b0, mid) if kl[i].get('volume')]
            v2 = [kl[i]['volume'] for i in range(mid, j + 1) if kl[i].get('volume')]
            r1 = (max(h1) - min(l1)) / min(l1) if h1 and l1 and min(l1) > 0 else None
            r2 = (max(h2) - min(l2)) / min(l2) if h2 and l2 and min(l2) > 0 else None
            vr2 = (statistics.mean(v2) / statistics.mean(v1)) if v1 and v2 and statistics.mean(v1) > 0 else None
            # 前瞻
            ent = kl[j + 1]['open_adj']
            if not ent:
                continue
            ex20 = None
            for H in (10, 20, 60):
                if j + 1 + H >= len(kl):
                    continue
                ext = kl[j + 1 + H]['adj_close']
                ir = idx_ret(e['date'], H)
                if not ext or ir is None:
                    continue
                v = (ext / ent - 1 - COST) - ir
                if H == 20:
                    ex20 = v
                    allvals[20].append(v)
                    if prior_gain is not None:
                        g = prior_gain
                        b = ('<20%' if g < 0.20 else '20~30%' if g < 0.30 else
                             '30~50%' if g < 0.50 else '50~100%' if g < 1.00 else '>=100%')
                        grp_gain[b].append(v)
                    if depth is not None:
                        b = ('<=3%' if depth <= 0.03 else '3~5%' if depth <= 0.05 else
                             '5~8%' if depth <= 0.08 else '8~12%' if depth <= 0.12 else '>12%')
                        grp_depth[b].append(v)
                    if r1 and r2 and vr2 is not None:
                        if r2 < r1 and vr2 < 1.0:
                            grp_vcp['VCP(双收缩)'].append(v)
                        elif r2 < r1:
                            grp_vcp['仅深度收缩'].append(v)
                        elif vr2 < 1.0:
                            grp_vcp['仅量能收缩'].append(v)
                        else:
                            grp_vcp['无收缩'].append(v)
                    grp_from[e['from']].append(v)

    def show(title, d, order=None):
        print('\n' + '=' * 96)
        print(title)
        print('=' * 96)
        print(f'{"分组":<16}{"n":>8}{"胜率":>8}{"均值":>10}{"中位":>10}')
        keys = order or sorted(d.keys())
        for k in keys:
            if k not in d:
                continue
            s = stat(d[k])
            if not s:
                print(f'{k:<16}{len(d[k]):>8}  (样本不足)')
                continue
            print(f'{k:<16}{s[0]:>8,}{s[1]:>7.0f}%{s[2]:>+9.2f}%{s[3]:>+9.2f}%')

    s = stat(allvals[20])
    print(f'\n④ 全部事件基准: n={s[0]:,}  胜率 {s[1]:.0f}%  均值 {s[2]:+.2f}%  中位 {s[3]:+.2f}%')
    show('#14 前置涨幅分桶（PRD 门槛 20%）', grp_gain,
         ['<20%', '20~30%', '30~50%', '50~100%', '>=100%'])
    show('#13 箱体深度分桶', grp_depth, ['<=3%', '3~5%', '5~8%', '8~12%', '>12%'])
    show('#11 VCP 收缩度分组', grp_vcp, ['VCP(双收缩)', '仅深度收缩', '仅量能收缩', '无收缩'])
    show('对照组：来自 ② 还是 ③', grp_from, ['②', '③'])
    print(f'\n总耗时 {time.time()-t0:.0f}s｜保留事件 {kept:,}')
    conn.close()


if __name__ == '__main__':
    main()
