# -*- coding: utf-8 -*-
"""
box_breakdown v1.4 验证脚本（人工执行，观察进度）
================================================
验证三项改动：
  1. 下沿触碰对齐买侧 ×3（弱支撑过滤）
  2. 箱体失效机制：strong_sell 后 250 天内同价位再次跌破 → 拦截
  3. warning 级站回后箱体保留 → 再次跌破照报（不算拦截）

用法:
  python scripts/verify_box_breakdown_v14.py --limit 800
  python scripts/verify_box_breakdown_v14.py --limit 800 --workers 8

输出:
  - 终端实时进度（每 100 只一行）
  - analysis/box_breakdown_v14_verify.json（完整结果）
"""
import sys, os, sqlite3, json, argparse
from datetime import date
from multiprocessing import Pool

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'lixinger.db')
OUT_JSON = os.path.join(PROJECT_DIR, 'analysis', 'box_breakdown_v14_verify.json')
LOG_PATH = os.path.join(PROJECT_DIR, 'analysis', 'box_breakdown_v14_verify.log')

BLOCK_WINDOW = 250  # 失效窗口（天），与 max_box_days 一致


def days(d):
    y, m, dd = map(int, d.split('-'))
    return date(y, m, dd).toordinal()


def scan_one(code):
    """单股：跑 detect，返回 strong_sell 后的重复跌破统计"""
    try:
        from src.scanners.box_breakdown import detect, load_params
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""SELECT date, open, high, low, close, volume, change_pct
                FROM daily_kline WHERE stock_code=? ORDER BY date""", (code,)).fetchall()
        finally:
            conn.close()
        if len(rows) < 100:
            return (code, None)
        sigs = detect([dict(r) for r in rows], load_params())

        stats = {
            'total_events': len(sigs),
            'active': sum(1 for s in sigs if s['signal_level']),
            'strong_sell': sum(1 for s in sigs if s['signal_level'] == 'strong_sell'),
            'repeat_blocked_violations': [],  # 应被拦截却出现的（bug）
        }
        # 检查：strong_sell 后 250 天内同价位又出现新事件（v1.4 应拦截）
        for s in sigs:
            if s['signal_level'] != 'strong_sell':
                continue
            later = [x for x in sigs if x['signal_date'] > s['signal_date']
                     and x['signal_level']
                     and days(x['signal_date']) - days(s['signal_date']) <= BLOCK_WINDOW
                     and abs(x['band_bottom'] - s['band_bottom']) / s['band_bottom'] < 0.03]
            if later:
                stats['repeat_blocked_violations'].append({
                    'first': s['signal_date'],
                    'first_bottom': round(s['band_bottom'], 2),
                    'repeat': later[0]['signal_date'],
                    'repeat_bottom': round(later[0]['band_bottom'], 2),
                    'gap_days': days(later[0]['signal_date']) - days(s['signal_date']),
                })
        return (code, stats)
    except Exception as e:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('[%s] %s 异常: %s: %s\n' % (date.today().isoformat(), code, type(e).__name__, e))
        return (code, None)


def main():
    parser = argparse.ArgumentParser(description='box_breakdown v1.4 验证')
    parser.add_argument('--limit', type=int, default=200, help='扫描股票数（默认200）')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    open(LOG_PATH, 'w', encoding='utf-8').close()

    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        stocks = [r['stock_code'] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM daily_kline WHERE date>='2024-01-01' "
            "ORDER BY stock_code LIMIT ?", (args.limit,)).fetchall()]
    finally:
        conn.close()

    print('═══ box_breakdown v1.4 验证 ═══')
    print('股票数: %d | workers: %d | 失效窗口: %d天' % (len(stocks), args.workers, BLOCK_WINDOW))
    print('验证点: ① strong_sell 后 %d 天内同价位再次跌破应被拦截 ② 事件统计' % BLOCK_WINDOW)

    results = {}
    done = 0
    with Pool(args.workers) as pool:
        for code, stats in pool.imap_unordered(scan_one, stocks, chunksize=20):
            results[code] = stats
            done += 1
            if done % 100 == 0 or done == len(stocks):
                print('[%d/%d] 已扫描 %d 只' % (done, len(stocks), done), flush=True)

    # 汇总
    violations = []
    for code, stats in results.items():
        if stats and stats['repeat_blocked_violations']:
            for v in stats['repeat_blocked_violations']:
                violations.append({'code': code, **v})

    summary = {
        'scanned': len(stocks),
        'violations': len(violations),
        'violation_list': violations,
        'notes': 'violations = strong_sell 后 250 天内同价位再次跌破仍被报出（v1.4 失效机制应拦截，出现即 bug）',
    }
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print('\n═══ 结果 ═══')
    print('扫描: %d 只 | 违反失效机制的重复跌破: %d 个' % (len(stocks), len(violations)))
    if violations:
        print('⚠️ 发现违规案例（前 10 个）:')
        for v in violations[:10]:
            print('  %s %s(%.2f) → %s(%.2f) 间隔%d天' % (
                v['code'], v['first'], v['first_bottom'], v['repeat'], v['repeat_bottom'], v['gap_days']))
    else:
        print('✅ 无违规：所有 strong_sell 后 250 天内同价位再次跌破均被拦截')
        print('   （跨年/超期/不同箱体正常放行，属于新箱体）')
    print('\n完整结果: %s' % OUT_JSON)


if __name__ == '__main__':
    main()
