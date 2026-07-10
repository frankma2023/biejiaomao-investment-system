#!/usr/bin/env python
"""
B1 关注度评分重算脚本 v3.0
============================
对 mw_signal_daily 中所有历史 B1 信号的 tech_score 重算为新版关注度评分。

用法:
  python scripts/recompute_attention.py           # 全量重算
  python scripts/recompute_attention.py --fast     # 前1000条测试
  python scripts/recompute_attention.py --resume   # 续传(跳过tech_score>0且有新格式detail的)

因子:
  1. h_rs250 (35分)    2. 换手率 (25分)    3. 距H天数 (20分)
  4. 回调深度 (15分)    5. B1温和度 (5分)
"""

import sys, os, time, sqlite3, json, argparse
from datetime import date
from collections import defaultdict

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def preload_equity(conn):
    """预加载股本数据到内存"""
    eq = defaultdict(list)
    for r in conn.execute("SELECT stock_code, change_date, outstanding_shares_a FROM stock_equity_change ORDER BY stock_code, change_date"):
        if r['outstanding_shares_a'] and r['outstanding_shares_a'] > 0:
            eq[r['stock_code']].append((r['change_date'], r['outstanding_shares_a']))
    return eq

def preload_close(conn, b1_dates):
    """预加载B1日收盘价。返回 {(stock_code, b1_date): close}"""
    # 取所有需要查询的日期的闭区间
    min_date = min(b1_dates) if b1_dates else '2016-01-01'
    max_date = max(b1_dates) if b1_dates else '2026-12-31'
    
    kl = {}
    for r in conn.execute("""
        SELECT stock_code, date, close FROM daily_kline 
        WHERE date >= ? AND date <= ?
    """, (min_date, max_date)):
        kl[(r['stock_code'], r['date'])] = r['close']
    return kl

def get_turnover(conn, eq, kl, stock_code, b1_date, c_amount_avg):
    """计算换手率"""
    if not c_amount_avg or c_amount_avg <= 0:
        return None
    
    eq_list = eq.get(stock_code, [])
    if not eq_list:
        return None
    
    # 二分查找 B1 日期之前最近的股本
    lo, hi = 0, len(eq_list) - 1
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if eq_list[mid][0] <= b1_date:
            best = eq_list[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    
    if not best:
        return None
    
    shares = best[1]
    close_p = kl.get((stock_code, b1_date), 0)
    if shares <= 0 or close_p <= 0:
        return None
    
    return c_amount_avg / (shares * close_p) * 100

def compute_score(row, to_rate):
    """计算关注度评分，返回 (total, detail_dict)"""
    sc = 0
    detail = {}
    
    # 1. h_rs250 (35分)
    rs = row['h_rs250'] or 0
    if rs >= 90: v = 35
    elif rs >= 80: v = 28
    elif rs >= 70: v = 18
    elif rs >= 60: v = 10
    else: v = 0
    sc += v; detail['h_rs250'] = v
    
    # 2. 换手率 (25分)
    if to_rate is not None:
        if to_rate < 0.5: to_v = 25
        elif to_rate < 1.0: to_v = 20
        elif to_rate < 1.5: to_v = 15
        elif to_rate < 2.0: to_v = 10
        elif to_rate < 3.0: to_v = 5
        else: to_v = 0
    else:
        to_v = 0
    sc += to_v; detail['turnover'] = to_v
    
    # 3. 距H天数 (20分)
    dh_v = 0
    hd = row['h_date']
    bd = row['b1_date']
    if hd and bd and hd > '2000-01-01':
        dh = (date.fromisoformat(bd) - date.fromisoformat(hd)).days
        if 40 <= dh <= 60: dh_v = 20
        elif 30 <= dh < 40: dh_v = 15
        elif (20 <= dh < 30) or (60 < dh <= 80): dh_v = 10
        elif dh > 80: dh_v = 5
    sc += dh_v; detail['days_since_h'] = dh_v
    
    # 4. 回调深度 (15分)
    dec = row['decline_pct'] or 0
    if dec > 35: dec_v = 15
    elif dec >= 25: dec_v = 12
    elif dec >= 20: dec_v = 8
    elif dec >= 15: dec_v = 4
    else: dec_v = 0
    sc += dec_v; detail['decline'] = dec_v
    
    # 5. B1 温和度 (5分)
    b1r = row['b1_return_pct'] or 0
    if b1r < 3: b1r_v = 5
    elif b1r < 5: b1r_v = 3
    elif b1r < 8: b1r_v = 1
    else: b1r_v = 0
    sc += b1r_v; detail['b1_moderation'] = b1r_v
    
    return sc, detail

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fast', action='store_true', help='仅处理前1000条')
    parser.add_argument('--resume', action='store_true', help='跳过已有新格式detail的信号')
    parser.add_argument('--start', type=str, default='2016-01-01', help='起始日期')
    parser.add_argument('--end', type=str, default='2026-12-31', help='结束日期')
    args = parser.parse_args()
    
    conn = get_db()
    
    # ── 加载信号 ──
    rows = conn.execute("""
        SELECT * FROM mw_signal_daily 
        WHERE b1_date >= ? AND b1_date <= ? AND stock_code != '_sentinel_'
        ORDER BY b1_date
    """, (args.start, args.end)).fetchall()
    
    if args.fast:
        rows = rows[:1000]
    
    if args.resume:
        # 跳过已有新格式detail的信号
        old_count = len(rows)
        rows_to_process = []
        for r in rows:
            detail = r['tech_score_detail']
            if detail:
                try:
                    d = json.loads(detail)
                    if 'h_rs250' in d or 'turnover' in d:
                        continue  # 已有新格式，跳过
                except:
                    pass
            rows_to_process.append(r)
        print(f"续传: {len(rows_to_process)} 条待处理 (跳过 {old_count - len(rows_to_process)} 条)")
        rows = rows_to_process
    else:
        print(f"全量: {len(rows)} 条")
    
    # ── 预加载 ──
    print("预加载股本数据...")
    eq = preload_equity(conn)
    print(f"  {len(eq)} 只股票有股本数据")
    
    print("预加载收盘价...")
    b1_dates = list(set(r['b1_date'] for r in rows))
    kl = preload_close(conn, b1_dates)
    print(f"  {len(kl)} 条收盘价")
    
    # ── 重算 ──
    print(f"\n重算关注度评分...")
    t0 = time.time()
    updated = 0
    no_turnover = 0
    
    # 批量更新
    update_sql = "UPDATE mw_signal_daily SET tech_score=?, tech_score_detail=? WHERE stock_code=? AND b1_date=?"
    batch = []
    
    for i, r in enumerate(rows):
        to_rate = get_turnover(conn, eq, kl, r['stock_code'], r['b1_date'], r['c_amount_avg'])
        if to_rate is None:
            no_turnover += 1
        
        score, detail = compute_score(r, to_rate)
        detail_json = json.dumps(detail, ensure_ascii=False)
        
        batch.append((score, detail_json, r['stock_code'], r['b1_date']))
        
        if len(batch) >= 5000:
            conn.executemany(update_sql, batch)
            conn.commit()
            updated += len(batch)
            batch = []
        
        if (i + 1) % 5000 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            remain = (len(rows) - i - 1) / rate if rate > 0 else 0
            print(f"  进度: {i+1}/{len(rows)} ({updated}已写) {rate:.0f}条/s 剩余{remain:.0f}s")
    
    # 写入剩余
    if batch:
        conn.executemany(update_sql, batch)
        conn.commit()
        updated += len(batch)
    
    elapsed = time.time() - t0
    print(f"\n完成: {updated}条更新, {no_turnover}条无换手率数据")
    print(f"耗时: {elapsed:.0f}s")
    
    # ── 验证 ──
    r = conn.execute("""
        SELECT COUNT(*) tot, AVG(tech_score) avg_s,
               SUM(CASE WHEN tech_score>=80 THEN 1 ELSE 0 END) hi,
               SUM(CASE WHEN tech_score>=65 AND tech_score<80 THEN 1 ELSE 0 END) mid
        FROM mw_signal_daily WHERE b1_date>=? AND b1_date<=? AND stock_code!='_sentinel_'
    """, (args.start, args.end)).fetchone()
    print(f"\n验证: {r[0]}条, 均分{r[1]:.0f}, 极高≥80={r[2]}条, 高65~79={r[3]}条")
    
    # 样例
    sample = conn.execute("""
        SELECT stock_code,stock_name,b1_date,tech_score,tech_score_detail 
        FROM mw_signal_daily WHERE tech_score>=80 AND b1_date>=? AND b1_date<=?
        ORDER BY tech_score DESC LIMIT 3
    """, (args.start, args.end)).fetchall()
    print("\n极高关注样例:")
    for s in sample:
        d = json.loads(s['tech_score_detail'])
        print(f"  {s['stock_code']} {s['stock_name']} {s['b1_date']}: {s['tech_score']}分 {d}")
    
    conn.close()

if __name__ == '__main__':
    main()
