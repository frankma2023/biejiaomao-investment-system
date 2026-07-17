#!/usr/bin/env python
"""
B1 关注度评分重算脚本 v3.2
============================
对历史信号的 tech_score 重算为 v3.2 关注度评分（含振幅收缩+下影线）。

用法:
  python scripts/recompute_attention.py
  python scripts/recompute_attention.py --fast
  python scripts/recompute_attention.py --start 2026-01-01 --end 2026-07-07
"""
import sys, os, time, sqlite3, json, argparse
from datetime import date
from collections import defaultdict

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
sys.path.insert(0, os.path.join(PROJECT, 'src'))
from scanners.mw_signal import compute_attention_score

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def preload_equity(conn):
    eq = defaultdict(list)
    for r in conn.execute("SELECT stock_code, change_date, outstanding_shares_a FROM stock_equity_change ORDER BY stock_code, change_date"):
        if r['outstanding_shares_a'] and r['outstanding_shares_a'] > 0:
            eq[r['stock_code']].append((r['change_date'], r['outstanding_shares_a']))
    return eq

def preload_klines(conn, codes, min_date):
    """预加载K线，返回 {stock_code: [dicts sorted by date]}"""
    kls = defaultdict(list)
    for bs in range(0, len(codes), 500):
        batch = codes[bs:bs+500]
        ph = ','.join('?'*len(batch))
        for r in conn.execute(f"SELECT stock_code,date,open,high,low,close FROM daily_kline WHERE date>=? AND stock_code IN ({ph}) ORDER BY stock_code,date", [min_date]+batch):
            kls[r['stock_code']].append(dict(r))
    return kls

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fast', action='store_true')
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default='2026-12-31')
    args = parser.parse_args()
    
    conn = get_db()
    
    rows = conn.execute("""
        SELECT * FROM mw_signal_daily 
        WHERE b1_date >= ? AND b1_date <= ? AND stock_code != '_sentinel_'
        ORDER BY b1_date
    """, (args.start, args.end)).fetchall()
    
    if args.fast: rows = rows[:1000]
    print(f"信号: {len(rows)} 条")
    
    # 预加载
    print("预加载股本...")
    eq = preload_equity(conn)
    print(f"  {len(eq)}只")
    
    codes = list(set(r['stock_code'] for r in rows))
    print("预加载K线...")
    kls = preload_klines(conn, codes, '2015-01-01')
    print(f"  {len(kls)}只")
    
    # 重算
    print("重算评分...")
    t0 = time.time(); updated = 0
    update_sql = "UPDATE mw_signal_daily SET tech_score=?, tech_score_detail=? WHERE stock_code=? AND b1_date=?"
    batch = []
    
    for i, r in enumerate(rows):
        # 提取 B1 日及之前的 K 线
        all_kl = kls.get(r['stock_code'], [])
        b1_klines = []
        for k in all_kl:
            if k['date'] <= r['b1_date']:
                b1_klines.append(k)
            else:
                break
        
        score, detail = compute_attention_score(
            r['stock_code'], r['b1_date'], b1_klines,
            r['decline_pct'], r['h_rs250'], r['b1_return_pct'],
            r['h_date'], r['c_amount_avg'],
            ind_rs20=r['ind_rs20'],
            conn=conn, return_detail=True
        )
        detail_json = json.dumps(detail, ensure_ascii=False)
        batch.append((score, detail_json, r['stock_code'], r['b1_date']))
        
        if len(batch) >= 2000:
            conn.executemany(update_sql, batch)
            conn.commit()
            updated += len(batch)
            batch = []
            elapsed = time.time()-t0
            rate = (i+1)/elapsed
            remain = (len(rows)-i-1)/rate if rate>0 else 0
            print(f"  进度: {i+1}/{len(rows)} {rate:.0f}条/s 剩余{remain:.0f}s")
    
    if batch:
        conn.executemany(update_sql, batch)
        conn.commit()
        updated += len(batch)
    
    elapsed = time.time()-t0
    print(f"\n完成: {updated}条  耗时{elapsed:.0f}s")
    
    # 验证
    r = conn.execute("""
        SELECT COUNT(*) tot, AVG(tech_score) avg_s,
               SUM(CASE WHEN tech_score>=80 THEN 1 ELSE 0 END) hi
        FROM mw_signal_daily WHERE b1_date>=? AND b1_date<=? AND stock_code!='_sentinel_'
    """, (args.start, args.end)).fetchone()
    print(f"验证: {r[0]}条 均分{r[1]:.0f} 极高≥80={r[2]}条")
    
    conn.close()

if __name__ == '__main__':
    main()
