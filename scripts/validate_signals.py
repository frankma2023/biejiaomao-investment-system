"""
信号数据校验脚本 v2 — 智能异常检测
用法：python scripts/validate_signals.py --start 2023-01-01 --end 2025-12-31
"""
import sys, os, argparse, sqlite3

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')

ISSUES = []
TD = 0


def check_days(db, label, table, date_col, start, end, extra_where="", min_pct=0.8):
    """检查信号覆盖天数"""
    where = f"WHERE {date_col} BETWEEN '{start}' AND '{end}'"
    if extra_where:
        where += f" AND {extra_where}"
    cnt = db.execute(f"SELECT COUNT(DISTINCT {date_col}) FROM {table} {where}").fetchone()[0]
    pct = cnt / TD * 100 if TD else 0
    if pct >= 95:
        print(f'  ✅ {label}: {cnt}/{TD}天 ({pct:.0f}%)')
    elif pct >= 50:
        print(f'  ⚠️  {label}: {cnt}/{TD}天 ({pct:.0f}%) — 覆盖不足')
        ISSUES.append(f'{label} 覆盖不足({pct:.0f}%)')
    else:
        print(f'  ❌ {label}: {cnt}/{TD}天 ({pct:.0f}%) — 严重缺失')
        ISSUES.append(f'{label} 严重缺失({pct:.0f}%)')
    return cnt


def check_zero_streak(db, label, table, date_col, start, end, extra_where="", streak_days=10):
    """检查开头连续N天为0（未来数据泄露特征）"""
    where = f"WHERE d.date BETWEEN '{start}' AND '{end}'"
    sql = f"""
        SELECT d.date,
            (SELECT COUNT(*) FROM {table} t WHERE t.{date_col}=d.date {('AND ' + extra_where) if extra_where else ''}) as cnt
        FROM daily_kline d
        {where}
        GROUP BY d.date ORDER BY d.date
    """
    rows = db.execute(sql).fetchall()
    zero_days = 0
    first_nonzero = None
    for date, cnt in rows:
        if cnt == 0:
            zero_days += 1
        else:
            first_nonzero = date
            break
    
    if zero_days >= streak_days:
        print(f'  ❌ {label}: 开头连续 {zero_days} 天为 0（首条: {first_nonzero}）— 疑似未来数据泄露')
        ISSUES.append(f'{label} 开头{zero_days}天为0')
    elif zero_days > 0:
        print(f'  ⚠️  {label}: 开头 {zero_days} 天为 0（正常可能是年末假日）')
    return zero_days


def validate(start, end):
    global TD
    db = sqlite3.connect(DB)
    print(f'\n{"="*60}')
    print(f'信号校验 v2  {start} ~ {end}')
    print(f'{"="*60}')

    # ── 基础数据 ──
    print(f'\n📊 基础数据')
    TD = db.execute(f"SELECT COUNT(DISTINCT date) FROM daily_kline WHERE date BETWEEN '{start}' AND '{end}'").fetchone()[0]
    bi_days = db.execute(f"SELECT COUNT(DISTINCT scan_date) FROM chanlun_bi_json WHERE scan_date BETWEEN '{start}' AND '{end}'").fetchone()[0]
    rs_days = db.execute(f"SELECT COUNT(DISTINCT date) FROM stock_rs_daily WHERE date BETWEEN '{start}' AND '{end}'").fetchone()[0]
    print(f'  ✅ 交易日: {TD}  缠论bi: {bi_days}/{TD}  RS: {rs_days}/{TD}')

    # ── 信号覆盖天数 ──
    print(f'\n📅 信号覆盖天数（应有 {TD} 天）')
    check_days(db, 'MW B1', 'mw_signal_daily', 'b1_date', start, end)
    check_days(db, 'MW B2', 'mw_signal_daily', 'b2_date', start, end)
    check_days(db, 'PP V1', 'pocket_pivot_daily', 'date', start, end, "engine_version='V1'")
    check_days(db, 'PP V2', 'pocket_pivot_daily', 'date', start, end, "engine_version='V2'", min_pct=0.5)
    try:
        check_days(db, 'BO V2', 'market_breakout_v2_daily', 'date', start, end)
    except:
        print(f'  ⚠️  BO V2: 表不存在')
    check_days(db, 'Sell', 'pattern_scan_signals', 'date', start, end)
    check_days(db, 'Progress', 'backfill_v2_progress', 'date', start, end, min_pct=0.9)

    # ── 年首零信号检测 ──
    print(f'\n🔍 年首零信号检测（连续≥10天为0=未来数据泄露）')
    check_zero_streak(db, 'MW B1', 'mw_signal_daily', 'b1_date', start, end)
    check_zero_streak(db, 'PP V2', 'pocket_pivot_daily', 'date', start, end, "engine_version='V2'")
    check_zero_streak(db, 'BO V2', 'market_breakout_v2_daily', 'date', start, end)
    check_zero_streak(db, 'Sell', 'pattern_scan_signals', 'date', start, end)

    # ── 信号量级异常 ──
    print(f'\n📈 信号量级')
    b1_total = db.execute(f"SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date BETWEEN '{start}' AND '{end}'").fetchone()[0]
    sell_total = db.execute(f"SELECT COUNT(*) FROM pattern_scan_signals WHERE date BETWEEN '{start}' AND '{end}'").fetchone()[0]
    bo_total = db.execute(f"SELECT COUNT(*) FROM market_breakout_v2_daily WHERE date BETWEEN '{start}' AND '{end}'").fetchone()[0]
    ppv2_total = db.execute(f"SELECT COUNT(*) FROM pocket_pivot_daily WHERE date BETWEEN '{start}' AND '{end}' AND engine_version='V2'").fetchone()[0]
    ppv1_total = db.execute(f"SELECT COUNT(*) FROM pocket_pivot_daily WHERE date BETWEEN '{start}' AND '{end}' AND engine_version='V1'").fetchone()[0]
    
    b1_avg = b1_total / TD if TD else 0
    sell_avg = sell_total / TD if TD else 0
    print(f'  MW B1: {b1_total:,}条 ({b1_avg:.0f}/天)')
    print(f'  PP V1: {ppv1_total:,}条')
    print(f'  PP V2: {ppv2_total:,}条')
    print(f'  BO V2: {bo_total:,}条')
    print(f'  Sell: {sell_total:,}条 ({sell_avg:.0f}/天)')

    # 量级异常检查
    if TD > 30:
        if b1_avg < 5:
            print(f'  ❌ MW B1 日均 {b1_avg:.0f} < 5，异常低')
            ISSUES.append('MW B1 日均过低')
        if sell_avg < 100 and TD > 30:
            print(f'  ❌ Sell 日均 {sell_avg:.0f} < 100，异常低')
            ISSUES.append('Sell 日均过低')
        if bo_total == 0 and TD > 10:
            print(f'  ❌ BO V2 为 0')
            ISSUES.append('BO V2 为0')

    # ── 每日明细（前10天）──
    print(f'\n📅 每日明细（前10天）')
    rows = db.execute(f"""
        SELECT d.date,
            (SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date=d.date) as b1,
            (SELECT COUNT(*) FROM mw_signal_daily WHERE b2_date=d.date) as b2,
            (SELECT COUNT(*) FROM pocket_pivot_daily WHERE date=d.date AND engine_version='V1') as ppv1,
            (SELECT COUNT(*) FROM pocket_pivot_daily WHERE date=d.date AND engine_version='V2') as ppv2,
            (SELECT COUNT(*) FROM pattern_scan_signals WHERE date=d.date) as sell
        FROM daily_kline d
        WHERE d.date BETWEEN '{start}' AND '{end}'
        GROUP BY d.date ORDER BY d.date LIMIT 10
    """).fetchall()
    for r in rows:
        flags = ''
        if r[1] == 0 and r[3] == 0 and r[4] == 0 and r[5] == 0:
            flags = ' ← 全零'
        print(f'  {r[0]}  B1={r[1]:>4}  B2={r[2]:>4}  PPV1={r[3]:>4}  PPV2={r[4]:>3}  Sell={r[5]:>4}{flags}')

    # ── 总结 ──
    print(f'\n{"="*60}')
    if ISSUES:
        print(f'❌ 发现 {len(ISSUES)} 个问题:')
        for i in ISSUES:
            print(f'  - {i}')
    else:
        print(f'✅ 校验通过')
    print(f'{"="*60}\n')
    db.close()
    return len(ISSUES)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='信号数据校验 v2')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    args = parser.parse_args()
    sys.exit(validate(args.start, args.end))
