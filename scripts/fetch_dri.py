# -*- coding: utf-8 -*-
"""
scripts/fetch_dri.py — 理杏仁分红再投收益率全量拉取（tr_dri）
================================================================
表：stock_dri_metrics（个股分红再投年化，1/3/5/10/20年 + 上市以来）
价值：分红再投标的筛选（高 cagr_p_r_y10 = 再投长期有效的标的）
全量：全市场分批（≤100/次），限流器控制
"""
import sys, os, sqlite3
sys.path.insert(0, r'D:\hanako\investment-system\scripts')
import common

CREATE_SQL = """CREATE TABLE IF NOT EXISTS stock_dri_metrics (
    stock_code    TEXT PRIMARY KEY,
    last_data_date TEXT,
    period_date    TEXT,
    p_r            REAL,   -- 指定时间段投资收益率
    cagr_fys       REAL,   -- 今年以来
    cagr_d30       REAL,
    cagr_d60       REAL,
    cagr_d90       REAL,
    cagr_y1        REAL,
    cagr_y3        REAL,
    cagr_y5        REAL,
    cagr_y10       REAL,
    cagr_y20       REAL,
    cagr_fs        REAL,   -- 上市至今年化
    p_r_fs         REAL,   -- 上市以来总收益率
    updated_at     TEXT DEFAULT (datetime('now','localtime'))
)"""


def fetch_batch(codes):
    r = common.api_post('/company/hot/tr_dri', {'stockCodes': codes}, timeout=60)
    return r


def main():
    db = common.get_db()
    codes = common.get_all_stock_codes(db)
    print(f'全市场 {len(codes)} 只')
    db.execute(CREATE_SQL)
    total, fail = 0, 0
    for i in range(0, len(codes), 100):
        batch = codes[i:i + 100]
        try:
            data = fetch_batch(batch)
        except Exception as e:
            fail += len(batch)
            print(f'批次 {i // 100} 失败: {str(e)[:60]}')
            continue
        rows = []
        for d in data:
            rows.append((
                d.get('stockCode'), d.get('last_data_date'), str(d.get('period_date'))[:10] if d.get('period_date') else None,
                d.get('p_r'), d.get('cagr_p_r_fys'), d.get('cagr_p_r_d30'), d.get('cagr_p_r_d60'),
                d.get('cagr_p_r_d90'), d.get('cagr_p_r_y1'), d.get('cagr_p_r_y3'), d.get('cagr_p_r_y5'),
                d.get('cagr_p_r_y10'), d.get('cagr_p_r_y20'), d.get('cagr_p_r_fs'), d.get('p_r_fs'),
            ))
        db.executemany("""INSERT OR REPLACE INTO stock_dri_metrics
            (stock_code, last_data_date, period_date, p_r, cagr_fys, cagr_d30, cagr_d60, cagr_d90,
             cagr_y1, cagr_y3, cagr_y5, cagr_y10, cagr_y20, cagr_fs, p_r_fs)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        db.commit()
        total += len(rows)
        if i % 500 == 0:
            print(f'进度 {i}/{len(codes)} 入库 {total}')
    cnt = db.execute("SELECT COUNT(*) FROM stock_dri_metrics").fetchone()[0]
    print(f'完成: 入库 {total} 条（表内共 {cnt}）| 失败 {fail}')
    db.close()


if __name__ == '__main__':
    main()
