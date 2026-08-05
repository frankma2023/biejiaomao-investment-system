#!/usr/bin/env python3
"""
每日融资融券更新（旧 API）— 单股票日频历史

旧 API: /api/cn/index/margin-trading-and-securities-lending
  单股票调用，返回指定日期范围的融资融券数据（含买入/偿还/余额/净额）
  使用多线程并行，写入 daily_margin_history 表（标准表）
  同时回填 stock_margin（向后兼容 market_health.py）
  运行频率：每日盘后
"""

import sys
import os
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
os.chdir(PROJECT_DIR)

from common import api_post, get_db, get_all_stock_codes, log

API_PATH = "/company/margin-trading-and-securities-lending"
WORKERS = 2  # 降并发防 429 限流（原 8→2）


def fetch_one(code: str, target_date: str):
    """用旧 API 拉取一只股票指定日期的融资融券数据"""
    try:
        result = api_post(API_PATH, {
            "stockCode": code,
            "startDate": target_date,
            "endDate": target_date,
        })
        if not result:
            return None
        item = result[0]
        d = item.get("date", "")
        date_str = d[:10] if "T" in d else d
        if not date_str or date_str != target_date:
            return None

        fb = item.get("financingBalance") or 0
        sb = item.get("securitiesBalance") or 0
        np = item.get("financingNetPurchaseAmount") or 0
        total = item.get("financingSecuritiesBalance") or (fb + sb)
        return (code, date_str, float(fb), float(sb), float(np), float(total))
    except Exception as e:
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=str, default=None, help='指定单日 YYYY-MM-DD')
    parser.add_argument('--start', type=str, default=None, help='起始日期 YYYY-MM-DD')
    parser.add_argument('--end', type=str, default=None, help='结束日期 YYYY-MM-DD')
    args = parser.parse_args()
    
    start = time.time()
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log.info("🐺 每日融资融券更新（旧API）")
    log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # 确定日期范围
    if args.start and args.end:
        db = get_db()
        dates = [r[0] for r in db.execute(
            "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
            (args.start, args.end)
        ).fetchall()]
        db.close()
        log.info(f"日期范围: {args.start} ~ {args.end}，共 {len(dates)} 个交易日")
    elif args.date:
        dates = [args.date]
    else:
        # 默认取 daily_kline 最新交易日（两融数据 T+1 发布，date.today() 当天拉不到）
        db = get_db()
        r = db.execute("SELECT MAX(date) FROM daily_kline").fetchone()
        latest_trade = r[0] if r and r[0] else date.today().isoformat()
        # 从最新交易日往前取 3 个交易日作为候选（T+1：最新日可能还没有，自动回退）
        probe_dates = db.execute(
            "SELECT DISTINCT date FROM daily_kline WHERE date<=? ORDER BY date DESC LIMIT 3",
            (latest_trade,)
        ).fetchall()
        db.close()
        dates = [row[0] for row in probe_dates]
        log.info(f"候选日期: {dates}（daily_kline最近3个交易日，逐个尝试，跳过已有数据/无数据日）")
    
    # 多日拉取时预加载全量股票代码
    db = get_db()
    codes = get_all_stock_codes(db)
    db.close()
    
    total_success = 0
    for di, today_str in enumerate(dates):
        if len(dates) > 1:
            log.info(f"\n[{di+1}/{len(dates)}] {today_str}")
        else:
            log.info(f"目标日期: {today_str}")
        
        db = get_db()
        existing = set(
            r["stock_code"] for r in db.execute(
                "SELECT stock_code FROM daily_margin_history WHERE date=?", (today_str,)
            ).fetchall()
        )
        db.close()
        todo = [c for c in codes if c not in existing]
        log.info(f"已有: {len(existing)} 只, 待取: {len(todo)} 只")

        if not todo:
            log.info("所有股票已有今日数据，跳过")
            continue

        results, failed = [], 0
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(fetch_one, code, today_str): code for code in todo}
            done = 0
            for f in as_completed(futures):
                done += 1
                r = f.result()
                if r: results.append(r)
                else: failed += 1
                if done % 500 == 0:
                    log.info(f"  {done}/{len(todo)} ({done/len(todo)*100:.0f}%) · 成功 {len(results)} · 失败 {failed}")

        log.info(f"拉取完成: {len(results)} 成功, {failed} 失败/无数据")
        if results:
            db = get_db()
            sql = """INSERT OR REPLACE INTO daily_margin_history
                (stock_code, date, financing_balance, securities_balance, net_purchase)
                VALUES (?, ?, ?, ?, ?)"""
            db.executemany(sql, [(r[0], r[1], r[2], r[3], r[4]) for r in results])
            db.commit()
            db.close()
            log.info(f"→ daily_margin_history: {len(results)} 行")
            total_success += len(results)

    elapsed = time.time() - start
    if len(dates) > 1:
        log.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info(f"🐺 全部完成: {len(dates)} 天, {total_success} 行, {elapsed:.0f}s")
        log.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()
