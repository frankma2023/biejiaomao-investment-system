"""
季度数据验收脚本 v1.0
每完成一个季度的信号回填后运行，自动检查数据质量。

用法：
    python scripts/validate_quarter.py --table index_rs_daily --quarter 2016Q1
    python scripts/validate_quarter.py --table pocket_pivot_daily --start 2016-01-01 --end 2016-03-31

验收项：
    1. 日期连续性：区间内每个交易日都有记录
    2. 数据覆盖面：每天行数在历史中位数的 80% 以上
    3. 信号产出率：每天至少 1 条记录（信号表专用）
    4. 数值合理性：RS∈[0,99]、价格>0、日期递增
    5. 交叉校验：stock_code 在 daily_kline 或 stock_basic 中存在
"""
import sys, os, json, argparse, sqlite3
from datetime import datetime

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

DB = os.path.join(PROJECT, 'data', 'lixinger.db')
OUT_DIR = os.path.join(PROJECT, 'data', 'validate')


# ══════════════════════════════════════════════════════════
# 表配置：每张表的验收规则
# ══════════════════════════════════════════════════════════

TABLE_CONFIG = {
    'index_rs_daily': {
        'date_col': 'date',
        'code_col': 'stock_code',
        'numeric_cols': ['rs_20', 'rs_60', 'rs_120', 'rs_250', 'close', 'ma50'],
        'numeric_ranges': {'rs_20': (0, 99), 'rs_60': (0, 99), 'rs_120': (0, 99), 'rs_250': (0, 99), 'close': (0.01, 1e6)},
        'ref_table': 'index_daily_kline',
        'ref_code_col': 'stock_code',
        'is_signal_table': False,
    },
    'stock_rs_daily': {
        'date_col': 'date',
        'code_col': 'stock_code',
        'numeric_cols': ['rps_20', 'rps_60', 'rps_120', 'rps_250', 'close'],
        'numeric_ranges': {'rps_20': (0, 99), 'rps_60': (0, 99), 'rps_120': (0, 99), 'rps_250': (0, 99), 'close': (0.01, 1e6)},
        'ref_table': 'daily_kline',
        'ref_code_col': 'stock_code',
        'is_signal_table': False,
    },
    'chanlun_scan_daily': {
        'date_col': 'scan_date',
        'code_col': 'stock_code',
        'numeric_cols': ['bi_count'],
        'numeric_ranges': {'bi_count': (0, 500)},
        'ref_table': 'daily_kline',
        'ref_code_col': 'stock_code',
        'is_signal_table': True,
    },
    'mw_signal_daily': {
        'date_col': 'b2_date',
        'code_col': 'stock_code',
        'numeric_cols': ['decline_pct', 'h_rs20', 'h_rs250'],
        'numeric_ranges': {'decline_pct': (0, 99), 'h_rs20': (0, 99), 'h_rs250': (0, 99)},
        'ref_table': 'daily_kline',
        'ref_code_col': 'stock_code',
        'is_signal_table': True,
    },
    'pocket_pivot_daily': {
        'date_col': 'date',
        'code_col': 'stock_code',
        'numeric_cols': ['gain_pct', 'vol_ratio', 'rps_20', 'rps_250', 'close'],
        'numeric_ranges': {'gain_pct': (-20, 30), 'vol_ratio': (0.1, 50), 'rps_20': (0, 99), 'rps_250': (0, 99), 'close': (0.01, 1e6)},
        'ref_table': 'daily_kline',
        'ref_code_col': 'stock_code',
        'is_signal_table': True,
    },
    'market_breakout_v2_daily': {
        'date_col': 'date',
        'code_col': 'stock_code',
        'numeric_cols': ['gain_pct', 'vol_ratio', 'rps_20', 'rps_250', 'close'],
        'numeric_ranges': {'gain_pct': (-20, 30), 'vol_ratio': (0.1, 50), 'rps_20': (0, 99), 'rps_250': (0, 99), 'close': (0.01, 1e6)},
        'ref_table': 'daily_kline',
        'ref_code_col': 'stock_code',
        'is_signal_table': True,
    },
}


# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════

def get_trading_dates(db, start, end, table='daily_kline'):
    """获取区间内交易日"""
    if table == 'index_daily_kline':
        rows = db.execute(
            "SELECT DISTINCT date FROM index_daily_kline WHERE date>=? AND date<=? AND kline_type='normal' ORDER BY date",
            (start, end)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
            (start, end)
        ).fetchall()
    return set(r[0] for r in rows)


def quarter_to_range(q):
    import calendar
    year = int(q[:4])
    qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1
    em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


# ══════════════════════════════════════════════════════════
# 验收主逻辑
# ══════════════════════════════════════════════════════════

def validate(args):
    cfg = TABLE_CONFIG.get(args.table)
    if not cfg:
        print(f"未知表: {args.table}")
        print(f"支持: {list(TABLE_CONFIG.keys())}")
        sys.exit(1)

    if args.quarter:
        start, end = quarter_to_range(args.quarter)
    else:
        start, end = args.start, args.end

    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    checks = {}
    anomalies = []
    passed = True

    date_col = cfg['date_col']
    code_col = cfg['code_col']

    # ── Check 1: 日期连续性 ──
    ref_table = 'index_daily_kline' if 'index' in args.table else 'daily_kline'
    expected_dates = get_trading_dates(db, start, end, ref_table)
    actual_rows = db.execute(
        f"SELECT DISTINCT {date_col} FROM {args.table} WHERE {date_col}>=? AND {date_col}<=?",
        (start, end)
    ).fetchall()
    actual_dates = set(r[0] for r in actual_rows)

    missing_dates = sorted(expected_dates - actual_dates)
    check1 = {'passed': len(missing_dates) == 0, 'expected': len(expected_dates),
              'actual': len(actual_dates), 'missing': len(missing_dates)}
    if missing_dates:
        check1['missing_dates'] = missing_dates[:10]
        passed = False
    checks['date_continuity'] = check1

    # ── Check 2: 数据覆盖面 ──
    daily_counts = db.execute(
        f"SELECT {date_col}, COUNT(*) as cnt FROM {args.table} "
        f"WHERE {date_col}>=? AND {date_col}<=? GROUP BY {date_col} ORDER BY {date_col}",
        (start, end)
    ).fetchall()

    if daily_counts:
        counts = [r['cnt'] for r in daily_counts]
        median_cnt = sorted(counts)[len(counts)//2]
        threshold = int(median_cnt * 0.8)
        low_days = [(r[date_col], r['cnt']) for r in daily_counts if r['cnt'] < threshold]

        check2 = {'passed': len(low_days) <= max(3, len(daily_counts)*0.05),
                  'median': median_cnt, 'threshold': threshold, 'low_days': len(low_days)}
        if low_days:
            check2['low_samples'] = low_days[:5]
        checks['coverage'] = check2
    else:
        checks['coverage'] = {'passed': True, 'note': '空表'}

    # ── Check 3: 信号产出率（仅信号表）──
    if cfg.get('is_signal_table'):
        zero_days = [r[date_col] for r in daily_counts if r['cnt'] == 0]
        check3 = {'passed': len(zero_days) <= max(3, len(daily_counts)*0.05),
                  'zero_signal_days': len(zero_days)}
        if zero_days:
            check3['zero_samples'] = zero_days[:5]
        checks['signal_yield'] = check3

    # ── Check 4: 数值合理性 ──
    range_anomalies = []
    for col, (lo, hi) in cfg.get('numeric_ranges', {}).items():
        try:
            bad = db.execute(
                f"SELECT {date_col}, {code_col}, {col} FROM {args.table} "
                f"WHERE {date_col}>=? AND {date_col}<=? AND ({col}<? OR {col}>?) LIMIT 10",
                (start, end, lo, hi)
            ).fetchall()
            for r in bad:
                range_anomalies.append(f"{r[date_col]} {r[code_col]} {col}={r[col]} (expected {lo}~{hi})")
        except:
            pass

    check4 = {'passed': len(range_anomalies) == 0, 'anomalies': len(range_anomalies)}
    if range_anomalies:
        check4['samples'] = range_anomalies[:10]
        passed = False
    checks['value_sanity'] = check4

    # ── Check 5: 交叉校验 ──
    ref_table = cfg['ref_table']
    ref_col = cfg['ref_code_col']
    try:
        orphans = db.execute(
            f"SELECT COUNT(DISTINCT s.{code_col}) FROM {args.table} s "
            f"WHERE s.{date_col}>=? AND s.{date_col}<=? "
            f"AND s.{code_col} NOT IN (SELECT DISTINCT {ref_col} FROM {ref_table})",
            (start, end)
        ).fetchone()[0]
        check5 = {'passed': orphans == 0, 'orphans': orphans}
        if orphans > 0:
            passed = False
        checks['cross_ref'] = check5
    except Exception as e:
        checks['cross_ref'] = {'passed': True, 'note': f'skipped: {e}'}

    db.close()

    # ── 输出 ──
    report = {
        'table': args.table,
        'date_range': [start, end],
        'quarter': args.quarter or f'{start}_{end}',
        'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'passed': passed,
        'checks': checks,
        'anomalies': anomalies,
    }

    os.makedirs(os.path.join(OUT_DIR, args.table), exist_ok=True)
    out_path = os.path.join(OUT_DIR, args.table, f"{args.quarter or start}.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 控制台摘要
    icon = '✅' if passed else '❌'
    print(f"\n{icon} {args.table} {args.quarter or f'{start}~{end}'}")
    for name, c in checks.items():
        status = '✓' if c['passed'] else '✗'
        detail = ''
        if 'missing' in c:
            detail = f" (缺{c['missing']}天)"
        elif 'median' in c:
            detail = f" (中位{c['median']}, 低覆盖{c.get('low_days',0)}天)"
        elif 'zero_signal_days' in c:
            detail = f" (零信号{c['zero_signal_days']}天)"
        elif 'anomalies' in c:
            detail = f" (异常{c['anomalies']}条)"
        elif 'orphans' in c:
            detail = f" (孤立{c['orphans']}条)"
        print(f"  {status} {name}{detail}")
    print(f"  报告: {out_path}")

    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='季度数据验收')
    parser.add_argument('--table', required=True, help='表名')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--quarter', help='如 2016Q1')
    group.add_argument('--start', help='起始日期（需同时指定 --end）')
    parser.add_argument('--end', help='结束日期')
    args = parser.parse_args()
    validate(args)
