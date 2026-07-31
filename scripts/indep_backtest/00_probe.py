# -*- coding: utf-8 -*-
"""独立回测 · 步骤0：数据探查
只读，不写库。确认信号覆盖、时间分布、K线可用性、基准可用性。
"""
import sqlite3
import sys

DB = r"D:\hanako\investment-system\data\lixinger.db"

def main():
    con = sqlite3.connect(DB)
    c = con.cursor()

    print("=" * 60)
    print("1. MW 信号总量与覆盖")
    print("=" * 60)
    total = c.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date!='_sentinel_'").fetchone()[0]
    with_b2 = c.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date!='_sentinel_' AND b2_date IS NOT NULL AND b2_date!='_sentinel_'").fetchone()[0]
    sent = c.execute("SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date='_sentinel_'").fetchone()[0]
    rng = c.execute("SELECT MIN(b1_date), MAX(b1_date) FROM mw_signal_daily WHERE b1_date!='_sentinel_'").fetchone()
    stocks = c.execute("SELECT COUNT(DISTINCT stock_code) FROM mw_signal_daily WHERE b1_date!='_sentinel_'").fetchone()[0]
    print(f"有效 B1 信号: {total}")
    print(f"含 B2 信号:   {with_b2} ({with_b2/total*100:.1f}%)")
    print(f"哨兵污染行:   {sent}")
    print(f"日期范围:     {rng[0]} ~ {rng[1]}")
    print(f"涉及股票数:   {stocks}")

    print("\n" + "=" * 60)
    print("2. 逐年分布 (B1 / 含B2)")
    print("=" * 60)
    rows = c.execute("""
        SELECT substr(b1_date,1,4) y, COUNT(*),
               SUM(CASE WHEN b2_date IS NOT NULL AND b2_date!='_sentinel_' THEN 1 ELSE 0 END)
        FROM mw_signal_daily WHERE b1_date!='_sentinel_'
        GROUP BY y ORDER BY y
    """).fetchall()
    for y, n, b2 in rows:
        print(f"  {y}: B1={n:>6}  B2={b2:>6}  ({b2/n*100:.0f}%)")

    print("\n" + "=" * 60)
    print("3. 基准指数 000985 K线可用性")
    print("=" * 60)
    idx = c.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM index_daily_kline WHERE stock_code='000985'").fetchone()
    print(f"  000985: {idx[0]} ~ {idx[1]}, {idx[2]} 条")

    print("\n" + "=" * 60)
    print("4. daily_kline complex_factor 覆盖 (前视/复权检查)")
    print("=" * 60)
    kl = c.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_kline").fetchone()
    print(f"  daily_kline: {kl[0]} ~ {kl[1]}, {kl[2]} 条")
    null_cf = c.execute("SELECT COUNT(*) FROM daily_kline WHERE complex_factor IS NULL").fetchone()[0]
    tot_cf = c.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    print(f"  complex_factor NULL: {null_cf} / {tot_cf} ({null_cf/tot_cf*100:.2f}%)")
    # 按年看 NULL 率
    print("  complex_factor NULL 率按年:")
    for r in c.execute("""SELECT substr(date,1,4) y, COUNT(*), SUM(CASE WHEN complex_factor IS NULL THEN 1 ELSE 0 END)
                          FROM daily_kline GROUP BY y ORDER BY y""").fetchall():
        y, n, nu = r
        if int(y) >= 2014:
            print(f"    {y}: {nu}/{n} ({nu/n*100:.1f}%)")

    print("\n" + "=" * 60)
    print("5. 退市股在信号里的占比 (幸存者偏差检查)")
    print("=" * 60)
    delisted = c.execute("""
        SELECT COUNT(DISTINCT m.stock_code)
        FROM mw_signal_daily m JOIN stock_basic b ON m.stock_code=b.stock_code
        WHERE b.listing_status='delisted' AND m.b1_date!='_sentinel_'
    """).fetchone()[0]
    print(f"  信号涉及的已退市股票: {delisted} 只")

    print("\n" + "=" * 60)
    print("6. 现有因子字段 非空率 (决定哪些因子可用)")
    print("=" * 60)
    for col in ['h_rs250', 'ind_rs20', 'ind_rs250', 'decline_pct', 'h_pre_rise_pct',
                'b1_vol_ratio', 'tech_score', 'b1_return_pct']:
        nn = c.execute(f"SELECT COUNT(*) FROM mw_signal_daily WHERE b1_date!='_sentinel_' AND {col} IS NOT NULL").fetchone()[0]
        print(f"  {col:<18}: {nn}/{total} ({nn/total*100:.1f}%)")

    con.close()

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    main()
