# -*- coding: utf-8 -*-
"""
复权数据完整性体检（CPA 回测 3 衍生发现）

背景：
  daily_kline.adj_close 在 2019-2026 全市场等于 close（fac 恒 1.0），2026-05-08 起全市场 NULL。
  CPA load_klines() 直接读该列 → 现代回测期实际跑在【未复权】价上。
  正确做法 = server._ensure_adj_prices：用 change_pct 从最新日反推前复权。

本脚本量化：
  P1 adj_close 列健康度（按年）
  P2 除权日检测（change_pct vs close 环比 不一致）—— 按年 + 幅度分档
  P3 近 250 日窗口内有除权的股票占比
  P4 笔顶收敛「假收敛」率（raw vs 前复权）
  P5 ① 250 日回撤门槛翻转率（raw vs 前复权，门槛 25%）

输出：analysis/cpa_adj_audit.md + .json
"""
import sqlite3, os, json, random, sys
from collections import Counter, defaultdict

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
OUT_MD = os.path.join(PROJECT, 'analysis', 'cpa_adj_audit.md')
OUT_JSON = os.path.join(PROJECT, 'analysis', 'cpa_adj_audit.json')

SAMPLE_N = 800
SEED = 42
FOCUS = ['002648', '600309', '000338', '601012', '300750', '600875']


def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def pct_bucket(x):
    """x 为绝对幅度（小数），返回分档标签"""
    a = abs(x)
    if a < 0.005:
        return '<0.5%'
    if a < 0.01:
        return '0.5-1%'
    if a < 0.02:
        return '1-2%'
    if a < 0.05:
        return '2-5%'
    if a < 0.10:
        return '5-10%'
    if a < 0.30:
        return '10-30%'
    return '>=30%'


def main():
    conn = connect()
    R = {}

    # ─────────── P1 adj_close 健康度 ───────────
    print('P1 adj_close 健康度 ...', flush=True)
    p1 = []
    for r in conn.execute("""
        SELECT substr(date,1,4) y, COUNT(*) n,
               SUM(CASE WHEN adj_close IS NULL THEN 1 ELSE 0 END) nul,
               SUM(CASE WHEN adj_close IS NOT NULL AND close>0 AND abs(adj_close/close-1)>0.001 THEN 1 ELSE 0 END) diff
        FROM daily_kline GROUP BY y ORDER BY y"""):
        p1.append(dict(r))
    R['p1'] = p1
    r = conn.execute("SELECT MIN(date) a, MAX(date) b, COUNT(DISTINCT date) n FROM daily_kline WHERE adj_close IS NULL").fetchone()
    R['p1_null_span'] = dict(r)
    r = conn.execute("SELECT COUNT(*) n FROM daily_kline WHERE date=(SELECT MAX(date) FROM daily_kline) AND adj_close IS NOT NULL").fetchone()
    R['p1_lastday_adj_alive'] = r['n']

    # ─────────── P2 除权日检测（全市场，2019 起） ───────────
    print('P2 除权日检测（全市场 2019+）...', flush=True)
    by_year = defaultdict(Counter)
    mag = Counter()
    ex_by_stock = defaultdict(int)
    cur = conn.execute("SELECT stock_code, date, close, change_pct FROM daily_kline "
                       "WHERE date>='2019-01-01' AND close IS NOT NULL AND change_pct IS NOT NULL ORDER BY stock_code, date")
    prev_code, prev_close = None, None
    tot = 0
    while True:
        rows = cur.fetchmany(50000)
        if not rows:
            break
        for r in rows:
            code, d, cl, chg = r['stock_code'], r['date'], r['close'], r['change_pct']
            if code != prev_code:
                prev_code, prev_close = code, cl
                continue
            if prev_close and prev_close > 0 and cl and cl > 0:
                raw = cl / prev_close - 1
                c = chg / 100 if abs(chg) > 1 else chg
                gap = raw - c          # 非 0 即除权缺口
                tot += 1
                if abs(gap) > 0.005:
                    by_year[d[:4]][pct_bucket(gap)] += 1
                    ex_by_stock[code] += 1
            prev_close = cl
    R['p2_total_pairs'] = tot
    R['p2_by_year'] = {k: dict(v) for k, v in sorted(by_year.items())}
    R['p2_stocks_with_ex'] = len(ex_by_stock)
    ex_all = sum(sum(v.values()) for v in by_year.values())
    R['p2_ex_days'] = ex_all
    m = Counter()
    for v in by_year.values():
        for k, n in v.items():
            m[k] += n
    R['p2_mag'] = dict(m)
    print('   除权日 %d 个，涉及股票 %d 只' % (ex_all, len(ex_by_stock)), flush=True)

    # ─────────── P3/P4/P5 抽样深度检查 ───────────
    print('P3-P5 抽样 %d 只 ...' % SAMPLE_N, flush=True)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT stock_code FROM chanlun_bi_json")]
    random.seed(SEED)
    samp = random.sample(codes, min(SAMPLE_N, len(codes)))
    for f in FOCUS:
        if f not in samp and f in codes:
            samp.append(f)

    p3 = Counter()
    p4 = Counter()
    p5 = Counter()
    p4_ex = []
    p5_ex = []

    for code in samp:
        row = conn.execute("SELECT bi_json FROM chanlun_bi_json WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1",
                           (code,)).fetchone()
        if not row or not row[0]:
            continue
        try:
            bis = json.loads(row[0])
        except Exception:
            continue
        krows = conn.execute(
            "SELECT date, close, change_pct, high, low FROM daily_kline WHERE stock_code=? ORDER BY date", (code,)).fetchall()
        if len(krows) < 300:
            continue
        dates = [r['date'] for r in krows]
        closes = [r['close'] for r in krows]
        n = len(krows)
        # 前复权：锚定最新日，用 change_pct 反推
        fac = [1.0] * n
        for i in range(n - 2, -1, -1):
            chg = krows[i + 1]['change_pct']
            if chg is None or closes[i + 1] is None or closes[i + 1] == 0:
                fac[i] = fac[i + 1]
                continue
            c = chg / 100 if abs(chg) > 1 else chg
            if 1 + c <= 0:
                fac[i] = fac[i + 1]
                continue
            # adj[i] = adj[i+1] / (1+c)  → fac[i] = fac[i+1]*close[i+1]/((1+c)*close[i])
            fac[i] = fac[i + 1] * closes[i + 1] / ((1 + c) * closes[i]) if closes[i] else fac[i + 1]
        fmap = {dates[i]: fac[i] for i in range(n)}

        # P3 近 250 日窗口内是否有除权
        win0 = max(0, n - 250)
        has_ex = any(abs(fac[i] / fac[i + 1] - 1) > 0.005 for i in range(win0, n - 1)) if n > 1 else False
        p3['stocks'] += 1
        if has_ex:
            p3['has_ex_250d'] += 1

        # P4 笔顶收敛：raw vs 前复权
        tops = [(b['sdt'][:10], b['high']) for b in bis
                if b.get('direction') == '向下' and b.get('high') and (b.get('sdt') or '')[:10] in fmap]
        for j in range(len(tops) - 1):
            (d1, h1), (d2, h2) = tops[j], tops[j + 1]
            if d1 > d2:
                continue
            # 窗口内是否有除权：用 fac 比值
            idx1, idx2 = dates.index(d1), dates.index(d2) if d2 in fmap else (None, None)
            if idx2 is None:
                continue
            crossed = abs(fac[idx2] / fac[idx1] - 1) > 0.005
            p4['pairs'] += 1
            raw_conv = h2 <= h1 * 1.01
            a1, a2 = h1 * fac[idx1], h2 * fac[idx2]
            adj_conv = a2 <= a1 * 1.01
            if crossed:
                p4['pairs_crossing_ex'] += 1
                if raw_conv != adj_conv:
                    p4['flip'] += 1
                    if raw_conv and not adj_conv:
                        p4['false_conv'] += 1
                    elif adj_conv and not raw_conv:
                        p4['missed_conv'] += 1
                    if len(p4_ex) < 12:
                        p4_ex.append(dict(code=code, d1=d1, h1=round(h1, 2), d2=d2, h2=round(h2, 2),
                                          a1=round(a1, 2), a2=round(a2, 2),
                                          raw='收敛' if raw_conv else '不收敛',
                                          adj='收敛' if adj_conv else '不收敛',
                                          fdiff=round(fac[idx2] / fac[idx1] - 1, 4)))

        # P5 250 日回撤门槛（① 用 ≥25%）
        if n >= 260:
            hi = max(closes[n - 250:n])
            cur_close = closes[-1]
            raw_dd = (hi - cur_close) / hi if hi else 0
            # 前复权口径：窗口内用 adj
            adj_hi = max(closes[i] * fac[i] for i in range(n - 250, n))
            adj_cur = closes[-1] * fac[-1]
            adj_dd = (adj_hi - adj_cur) / adj_hi if adj_hi else 0
            p5['stocks'] += 1
            if (raw_dd >= 0.25) != (adj_dd >= 0.25):
                p5['flip_25'] += 1
                if len(p5_ex) < 12:
                    p5_ex.append(dict(code=code, raw_dd=round(raw_dd, 3), adj_dd=round(adj_dd, 3)))
            if abs(raw_dd - adj_dd) > 0.01:
                p5['diff_gt_1pp'] += 1

    R['p3'] = dict(p3)
    R['p4'] = dict(p4)
    R['p5'] = dict(p5)
    R['p4_ex'] = p4_ex
    R['p5_ex'] = p5_ex
    print('   P4 笔顶对 %d｜跨除权 %d｜翻转 %d｜假收敛 %d' % (
        p4['pairs'], p4['pairs_crossing_ex'], p4['flip'], p4['false_conv']), flush=True)
    print('   P5 回撤门槛翻转 %d/%d' % (p5['flip_25'], p5['stocks']), flush=True)

    # ─────────── 报告 ───────────
    L = []
    L.append('# CPA 复权数据完整性体检（回测 3 衍生发现）\n')
    L.append('结论先行：`daily_kline.adj_close` 列**自 2019-01-01 起全市场失效**（值恒等于 `close`），')
    L.append('且 **2026-05-08 起全市场为 NULL**。CPA `load_klines()` 直接读该列并令 `fac = 1.0` 兜底，')
    L.append('因此 2019-2026 全部 CPA 判据（EMA/回撤/箱体/涨幅）实际跑在**未复权价**上，')
    L.append('`chanlun.py` 画笔同样用未复权 OHLC。此前 Spec review「复权口径统一」只统一了字段名，没有统一数据。\n')

    L.append('## P1 `adj_close` 列健康度（全市场，按年）\n')
    L.append('| 年 | 行数 | NULL | NULL% | 与 close 不同(>0.1%) |')
    L.append('|---|---|---|---|---|')
    for r in p1:
        n = r['n'] or 1
        L.append('| %s | %d | %d | %.1f%% | %d |' % (r['y'], r['n'], r['nul'], r['nul'] / n * 100, r['diff']))
    L.append('')
    L.append('NULL 区间：%s ~ %s（%d 个交易日）｜最新交易日 adj_close 非空行数：**%d**\n' % (
        R['p1_null_span']['a'], R['p1_null_span']['b'], R['p1_null_span']['n'], R['p1_lastday_adj_alive']))

    L.append('## P2 除权日检测（`change_pct` 与 close 环比不一致）\n')
    L.append('判据：`close/prev_close-1 - change_pct > 0.5%`（理杏仁 `change_pct` 为复权收益率）\n')
    L.append('| 年 | 除权日数 | 幅度分档 |')
    L.append('|---|---|---|')
    for y, v in sorted(R['p2_by_year'].items()):
        L.append('| %s | %d | %s |' % (y, sum(v.values()), ' '.join('%s:%d' % (k, n) for k, n in sorted(v.items()))))
    L.append('')
    L.append('近 %d 个交易日总配对 %d，检出除权日 **%d**，涉及股票 **%d** 只\n' % (
        len(R['p2_by_year']), R['p2_total_pairs'], R['p2_ex_days'], R['p2_stocks_with_ex']))
    L.append('幅度分档合计：%s\n' % R['p2_mag'])

    L.append('## P3 近 250 日窗口内含除权的股票\n')
    L.append('- 抽样 %d 只，有除权 **%d** 只（%.1f%%）\n' % (
        p3['stocks'], p3['has_ex_250d'], p3['has_ex_250d'] / max(1, p3['stocks']) * 100))

    L.append('## P4 笔顶收敛「假收敛」率（raw vs 前复权）\n')
    L.append('| 项 | 数量 |')
    L.append('|---|---|')
    L.append('| 相邻笔顶对 | %d |' % p4['pairs'])
    L.append('| 跨越除权的对 | %d |' % p4['pairs_crossing_ex'])
    L.append('| 结论翻转 | %d |' % p4['flip'])
    L.append('| **假收敛（raw 收敛 / adj 不收敛）** | **%d** |' % p4['false_conv'])
    L.append('| 漏收敛（raw 不收敛 / adj 收敛） | %d |' % p4['missed_conv'])
    L.append('')
    if p4['pairs_crossing_ex']:
        L.append('跨越除权对中翻转率 **%.1f%%**，假收敛率 **%.1f%%**\n' % (
            p4['flip'] / p4['pairs_crossing_ex'] * 100,
            p4['false_conv'] / p4['pairs_crossing_ex'] * 100))
    if p4_ex:
        L.append('| code | 笔顶1 | h1 | 笔顶2 | h2 | adj1 | adj2 | raw | adj |')
        L.append('|---|---|---|---|---|---|---|---|---|')
        for e in p4_ex:
            L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                e['code'], e['d1'], e['h1'], e['d2'], e['h2'], e['a1'], e['a2'], e['raw'], e['adj']))
        L.append('')

    L.append('## P5 ① 250 日回撤门槛翻转（≥25%）\n')
    L.append('- 抽样 %d 只｜门槛翻转 **%d** 只（%.1f%%）｜回撤差 >1pp 的 %d 只\n' % (
        p5['stocks'], p5['flip_25'], p5['flip_25'] / max(1, p5['stocks']) * 100, p5['diff_gt_1pp']))
    if p5_ex:
        L.append('')
        for e in p5_ex:
            L.append('- `%s` raw_dd=%.1f%% adj_dd=%.1f%%' % (e['code'], e['raw_dd'] * 100, e['adj_dd'] * 100))

    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(R, f, ensure_ascii=False, indent=1, default=str)
    print('报告 ->', OUT_MD, flush=True)
    conn.close()


if __name__ == '__main__':
    main()
