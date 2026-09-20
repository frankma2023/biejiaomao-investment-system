# -*- coding: utf-8 -*-
"""T3 单股验证（交接文档 §7 T3 / 验收 A1-A4 引擎层部分）：
1. WEEKLY_CFG 十项修订值生效核对（A4）
2. 阶段序列 sanity：①~⑧ 可达、无单阶段退化（A3 局部）
3. 笔顶日期 ≤ 快照日期（防未来抽查，A2 引擎层）
4. 长假周周K OHLC 抽查（A1 引擎层）
5. 688432 阶段序列明细
"""
import sys, os, json, sqlite3

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, 'src'))

import scanners.cpa_stage as daily
import scanners.cpa_stage_weekly as wk

def main():
    conn = sqlite3.connect(wk.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row

    print('═' * 60)
    print('[1] WEEKLY_CFG 十项修订值核对（A4）')
    rev = {'inv_n': "{'②→③': 3, '②→④': 8, '③→④': 8, '④→⑤': 6, '⑤→⑥': 6, '→⑥': 3}",
           'w_first_lookback': 4, 'b_depth_max': 0.10, 'e_peak_gain_min': 0.60,
           'cb_tol_atr': 0.15, 'cb_hold_days': 1, 'w_amp_tol': 0.15,
           'ftd_ret_min': 0.05, 'r_d20_max_pct': -0.18, 'r_panic_lookback': 4}
    ok4 = True
    for k, expect in rev.items():
        got = wk.WEEKLY_CFG.get(k)
        got_s = str(got)
        hit = (got_s == expect) if isinstance(expect, str) else (got == expect)
        print('  %-18s = %s  %s' % (k, got_s, 'OK' if hit else '✗ 期望 ' + str(expect)))
        ok4 = ok4 and hit
    # 硬编码覆盖键抽查
    for k, expect in [('ftd_min_history', 52), ('pause_recent_win', 2), ('win_floor', 4),
                      ('warn_tops_count', 2), ('crossback_low_win', 0), ('data_min_ratio', 0.7),
                      ('vr_win', 8), ('atr_win', 8), ('ftd_min_history', 52)]:
        got = wk.WEEKLY_CFG.get(k)
        print('  %-18s = %s  %s' % (k, got, 'OK' if got == expect else '✗ 期望 ' + str(expect)))
        ok4 = ok4 and (got == expect)
    # 日线 CFG 未被污染（模块加载时只 build 了 WEEKLY_CFG，未 update daily.CFG）
    print('  日线 CFG.r_panic_lookback = %s（应为 20，未污染）' % daily.CFG['r_panic_lookback'])
    print('  日线 CFG.vr_win = %s（应为 20，未污染）' % daily.CFG['vr_win'])

    print('═' * 60)
    print('[2] 三只股票全历史 run_weekly + 阶段分布（A1/A2/A3 局部）')
    for code in ['688432', '600309', '300323']:
        rows, trans, nwk = wk.run_weekly(code, conn=conn)
        if nwk < wk.MIN_WEEKS:
            print('  %s: 跳过（%d 周 < %d）' % (code, nwk, wk.MIN_WEEKS))
            continue
        stages = {}
        for r in rows:
            s = r[2].rstrip('T')
            stages[s] = stages.get(s, 0) + 1
        n_stage = sum(stages.values())
        dist = ', '.join('%s:%d(%.0f%%)' % (s, c, c / n_stage * 100) for s, c in sorted(stages.items()))
        dominant = max(stages.values()) / n_stage * 100
        print('  %s: %d周 → %d行 %d迁移 | %s%s' % (
            code, nwk, len(rows), len(trans), dist,
            '  [⚠单阶段>60%%]' if dominant > 60 else ''))

    print('═' * 60)
    print('[3] 笔顶防未来抽查（每快照笔顶日期 ≤ 快照日）')
    bad = 0
    checked = 0
    for code in ['688432', '600309']:
        for row in conn.execute(
                'SELECT scan_date, bi_json FROM chanlun_weekly_bi_json WHERE stock_code=? ORDER BY scan_date',
                (code,)):
            tops = daily._parse_tops(row[1])
            for t in tops:
                checked += 1
                if t['date'] > row[0]:
                    bad += 1
                    if bad <= 3:
                        print('  ✗ %s 快照 %s 含未来笔顶 %s' % (code, row[0], t['date']))
    print('  抽查 %d 个笔顶，未来泄露 %d 个 %s' % (checked, bad, 'OK' if bad == 0 else '✗FAIL'))

    print('═' * 60)
    print('[4] 长假周周K抽查（A1 引擎层：2024 国庆 09-30 单日周 + 2024 春节 02-05~02-09 休市）')
    wkl = wk.load_weekly_klines(conn, '688432')
    for w in wkl:
        if w['date'] in ('2024-09-30', '2024-02-02', '2024-02-08'):
            print('  %s O=%s H=%s L=%s C=%s' % (w['date'], w['open_adj'], w['high_adj'], w['low_adj'], w['close']))
    # 单日周核验：09-30 是 2024 国庆前最后交易日，该周只有一根日K → OHLC 应全等
    daily_row = conn.execute("SELECT date, close FROM daily_kline_adj WHERE stock_code='688432' AND date='2024-09-30'").fetchone()
    if daily_row:
        print('  日线 2024-09-30 close=%s（周K 四价应与其一致）' % daily_row[1])

    print('═' * 60)
    print('[5] 688432 全阶段序列（折叠后，仅阶段变化处）')
    rows, trans, nwk = wk.run_weekly('688432', conn=conn)
    prev = None
    for r in rows:
        if r[2] != prev:
            print('  %s → %s (days_in=%d)' % (r[1], r[2], r[5]))
            prev = r[2]
    # original 标记抽样
    n_orig = sum(1 for r in rows if '"original"' in (r[10] or ''))
    print('  折叠改写并保留 original 的行: %d / %d' % (n_orig, len(rows)))
    conn.close()

    print('═' * 60)
    print('A4:', 'PASS' if ok4 else 'FAIL')

if __name__ == '__main__':
    main()
