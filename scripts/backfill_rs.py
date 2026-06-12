"""
RS强度批量回填脚本
用法: python scripts/backfill_rs.py --start 2016-01-01 --end 2026-06-05
"""
import sys, os, sqlite3, argparse
import polars as pl
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default='2026-06-05')
    args = parser.parse_args()

    from scanners.stock_rs import compute

    print(f"区间: {args.start} ~ {args.end}")
    print("计算中 (这一步可能较久，Polars 全量计算)...")
    
    result = compute(target_date=args.end, start_date=args.start, min_amount_20d=0)
    
    # result 包含 start~end 之间所有交易日的数据
    # 按日期分组，逐日写入
    dates = result['date'].unique().sort().to_list()
    total_dates = len(dates)
    print(f"共 {total_dates} 个交易日，开始写入...")
    
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'lixinger.db')
    conn = sqlite3.connect(db_path)
    
    written = 0
    for i, date in enumerate(dates):
        day_data = result.filter(pl.col('date') == date)
        
        # 删除该日旧数据（避免重复）
        conn.execute("DELETE FROM stock_rs_daily WHERE date = ?", (str(date),))
        
        rows = []
        for row in day_data.iter_rows(named=True):
            rows.append((
                row['stock_code'], str(date),
                row['adj_close'], row['adj_close'],
                row['ret_20'], row['ret_60'], row['ret_120'], row['ret_250'],
                row['rps_20'], row['rps_60'], row['rps_120'], row['rps_250'],
                row['rs_line_norm'], row['amount'],
            ))
        
        conn.executemany("""INSERT INTO stock_rs_daily
            (stock_code, date, close, adj_close, ret_20, ret_60, ret_120, ret_250,
             rps_20, rps_60, rps_120, rps_250, rs_line, amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        written += len(rows)
        
        if i % 100 == 0:
            pct = (i+1)/total_dates*100
            print(f"  [{i+1}/{total_dates}] {date} ({pct:.0f}%) - 本日{len(rows)}条, 累计{written}条")
    
    conn.close()
    print(f"\n完成! {total_dates}天, {written}条记录")
    print("现在可以跑口袋支点回填了:")
    print(f"python scripts/backfill_pocket_pivot_v3.py --start {args.start} --end {args.end} --incremental --workers 4")

if __name__ == '__main__':
    main()
