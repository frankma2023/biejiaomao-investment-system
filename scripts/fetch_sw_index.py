# -*- coding: utf-8 -*-
"""fetch_sw_index.py — 申万一级行业指数日线入库（industry_rs 申万 RS 基准数据源）
源：akshare index_hist_sw（申万宏源官网，住宅 IP 可用，非东财）
表：sw_index_kline (stock_code 6位如801030, stock_name, date, open, high, low, close, volume, amount)
用法：--full 首次全量(400交易日) ｜ 默认增量(最近10交易日)
"""
import sys, io, os, time, sqlite3, argparse
from datetime import date, timedelta
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DB = r'D:\hanako\investment-system\data\lixinger.db'

def get_sw_first():
    import akshare as ak
    df = ak.sw_index_first_info()
    out = []
    for _, r in df.iterrows():
        code = str(r['行业代码']).replace('.SI', '')
        out.append((code, str(r['行业名称'])))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--full', action='store_true', help='首次全量 400 交易日')
    ap.add_argument('--members', action='store_true', help='拉申万2021一级成分表建 个股→新版行业 映射')
    args = ap.parse_args()
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS sw_index_kline (
        stock_code TEXT, stock_name TEXT, date TEXT,
        open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
        PRIMARY KEY (stock_code, date))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sw2021_members (
        stock_code TEXT, industry_code TEXT, industry_name TEXT, weight REAL,
        PRIMARY KEY (stock_code, industry_code))""")
    conn.commit()
    import akshare as ak
    sws = get_sw_first()
    if args.members:
        print(f'拉取申万 2021 一级成分映射（31 行业）')
        tot = 0
        for code, name in sws:
            try:
                df = ak.index_component_sw(symbol=code)
                rows = []
                for _, r in df.iterrows():
                    sc = str(r['证券代码']).zfill(6)
                    rows.append((sc, code, name, float(r.get('最新权重') or 0)))
                conn.executemany("INSERT OR REPLACE INTO sw2021_members VALUES (?,?,?,?)", rows)
                conn.commit()
                tot += len(rows)
                print(f'  {code} {name}: {len(rows)} 成分')
            except Exception as e:
                print(f'  {code} {name}: ❌ {str(e)[:70]}')
            time.sleep(0.3)
        print(f'成分映射完成: 共 {tot} 条')
        # 抽样验证
        for probe in ['600309', '002648']:
            r = conn.execute("SELECT industry_code, industry_name FROM sw2021_members WHERE stock_code=?", (probe,)).fetchall()
            print(f'  {probe}: {[(x[0], x[1]) for x in r]}')
        conn.close()
        return
    print(f'申万一级行业 {len(sws)} 个')
    ok = 0
    for code, name in sws:
        try:
            df = ak.index_hist_sw(symbol=code, period='day')
            if df is None or len(df) == 0:
                print(f'  {code} {name}: 空')
                continue
            # 列: 代码 日期 收盘 开盘 最高 最低 成交量 成交额
            if args.full:
                df = df.tail(400)
            else:
                df = df.tail(10)
            rows = []
            for _, r in df.iterrows():
                rows.append((code, name, str(r['日期'])[:10],
                             float(r['开盘']), float(r['最高']), float(r['最低']),
                             float(r['收盘']), float(r.get('成交量') or 0), float(r.get('成交额') or 0)))
            conn.executemany("INSERT OR REPLACE INTO sw_index_kline VALUES (?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            ok += 1
        except Exception as e:
            print(f'  {code} {name}: ❌ {str(e)[:80]}')
        time.sleep(0.3)
    print(f'完成: {ok}/{len(sws)} 入库')
    r = conn.execute("SELECT COUNT(DISTINCT stock_code), MAX(date) FROM sw_index_kline").fetchone()
    print('表状态: 行业数', r[0], '最新日期', r[1])
    conn.close()

if __name__ == '__main__':
    main()
