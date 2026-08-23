"""
缠论笔全市场批量回填 v2.0（CZSC 1.0.1 Rust 版适配）
将全市场市值大于50亿的股票的缠论笔数据从指定起始日期计算至今

依赖：src/scanners/chanlun.py、chanlun_scan.py
输出：chanlun_scan_daily + chanlun_bi_json 表

用法：
    # 全量重算（CZSC 1.0.1 升级后笔算法变化，需重写历史 bi_json）
    # ✅ 推荐 --by-stock：每只股票只加载 1 次 K 线 + CZSC 逐日增量，全量约 40 分钟
    python scripts/backfill_chanlun.py --start 2016-01-01 --end 2026-08-21 --workers 8 --log --by-stock
    # 按季度分片（白天人工跑，断点续跑）
    python scripts/backfill_chanlun.py --quarter 2024Q1 --workers 8 --log --by-stock
    # 增量（跳过已有日期）
    python scripts/backfill_chanlun.py --quarter 2024Q1 --incremental --workers 8 --log --by-stock
    # 只重算指定股票（先验证质量，如自选池）
    python scripts/backfill_chanlun.py --start 2026-08-01 --end 2026-08-21 --codes "300750,002648" --workers 4 --log --by-stock

性能参考（CZSC 1.0.1 Rust 版 + 增量模式）：
    --by-stock: 每只 1 次 K线加载 + 逐根 update（与全量结果一致已验证），
                全量 2535 天约 40 分钟（按日模式约 3.5 小时）

注意：
    - 同一天重跑 = 覆盖该日（DELETE 后重插），天然支持"重算某日"
    - --incremental 跳过已存在的日期，中断后继续用同一命令即可续跑
    - 建议先用 --codes 重算少量股票验证质量，再全量
    - --by-stock 与 --incremental 兼容：中断续跑时已重算日期会被按日跳过（存量模式）
"""
import sys, os, time, argparse, sqlite3, traceback
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')


# ══════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════

def get_trading_dates(start, end):
    db = sqlite3.connect(DB)
    rows = db.execute(
        "SELECT DISTINCT date FROM daily_kline WHERE date>=? AND date<=? ORDER BY date",
        (start, end)
    ).fetchall()
    db.close()
    return [r[0] for r in rows]


def get_existing_dates():
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT DISTINCT scan_date FROM chanlun_scan_daily").fetchall()
    db.close()
    return set(r[0] for r in rows)


def quarter_to_range(q):
    import calendar
    year = int(q[:4]); qnum = int(q[-1])
    sm = (qnum - 1) * 3 + 1; em = sm + 2
    ld = calendar.monthrange(year, em)[1]
    return f"{year}-{sm:02d}-01", f"{year}-{em:02d}-{ld}"


# ══════════════════════════════════════════════════════════
# Worker（进程内执行）
# ══════════════════════════════════════════════════════════

def scan_stock_worker(args):
    """单只股票扫描（worker 进程内调用），返回 scan_stock 结果或 (None, err_msg)"""
    code, scan_date = args
    try:
        from scanners.chanlun_scan import scan_stock
        r = scan_stock(code, scan_date)
        return (r, None)
    except Exception as e:
        return (None, f"{code}: {str(e)[:120]}")


def scan_stock_all_worker(args):
    """单只股票全历史增量扫描（--by-stock 模式）
    args: (code, dates)
    Returns: (code, [(date, summary)]) 或 (code, err)
    """
    code, dates = args
    try:
        from scanners.chanlun_scan import scan_stock_all
        res = scan_stock_all(code, dates)
        return (code, res)
    except Exception as e:
        return (code, f"{code}: {str(e)[:120]}")


def save_day_results(db_path, scan_date, results):
    """将一天的结果写入 chanlun_scan_daily + chanlun_bi_json（带重试）"""
    import time as _time
    for attempt in range(5):
        try:
            db = sqlite3.connect(db_path, timeout=30)
            try:
                db.execute("DELETE FROM chanlun_scan_daily WHERE scan_date=?", (scan_date,))
                db.execute("DELETE FROM chanlun_bi_json WHERE scan_date=?", (scan_date,))
                
                rows = []
                bi_rows = []
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                for r in results:
                    if r is None:
                        continue
                    rows.append((
                        scan_date, r['stock_code'], '',
                        r.get('bi_count', 0), r.get('zs_count', 0), r.get('segment_count', 0),
                        r.get('latest_bi_dir', ''), r.get('latest_bi_power', 0),
                        r.get('divergence_count', 0), r.get('latest_div_type', ''),
                        r.get('trade_signal_count', 0), r.get('latest_trade_type', ''),
                        r.get('latest_trade_side', ''), r.get('latest_trade_price', 0),
                        r.get('resonance_strength', ''), now
                    ))
                    bi = r.get('bi_json')
                    if bi:
                        bi_rows.append((r['stock_code'], scan_date, bi))
                
                if rows:
                    db.executemany("""
                        INSERT INTO chanlun_scan_daily
                        (scan_date, stock_code, stock_name, bi_count, zs_count, segment_count,
                         latest_bi_dir, latest_bi_power, divergence_count, latest_div_type,
                         trade_signal_count, latest_trade_type, latest_trade_side, latest_trade_price,
                         resonance_strength, created_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, rows)
                if bi_rows:
                    db.executemany(
                        "INSERT OR REPLACE INTO chanlun_bi_json (stock_code, scan_date, bi_json) VALUES (?,?,?)",
                        bi_rows
                    )
                db.commit()
                return len(rows)
            finally:
                db.close()
        except sqlite3.OperationalError as e:
            if 'locked' in str(e).lower() and attempt < 4:
                _time.sleep(3 * (attempt + 1))
            else:
                raise


def get_candidates(date, codes=None):
    """获取当天可扫描的股票列表（codes 过滤时只取指定股票）"""
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    if codes:
        placeholders = ','.join('?' * len(codes))
        stocks = db.execute(f"""
            SELECT DISTINCT k.stock_code, b.name
            FROM daily_kline k
            JOIN stock_basic b ON k.stock_code = b.stock_code
            WHERE k.date = ? AND k.stock_code IN ({placeholders})
        """, (date,) + tuple(codes)).fetchall()
    else:
        stocks = db.execute("""
            SELECT DISTINCT k.stock_code, b.name
            FROM daily_kline k
            JOIN stock_basic b ON k.stock_code = b.stock_code
            WHERE k.date = ?
        """, (date,)).fetchall()
    db.close()
    return [(r['stock_code'], r['name']) for r in stocks]


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='缠论批量回填（CZSC 1.0.1 全量重算版）')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--start', help='起始日期（需同时指定 --end）')
    group.add_argument('--quarter', help='按季度，如 2016Q1')
    parser.add_argument('--end', help='结束日期')
    parser.add_argument('--workers', type=int, default=8, help='并行进程数（默认8）')
    parser.add_argument('--incremental', action='store_true', help='增量模式（跳过已有日期）')
    parser.add_argument('--codes', help='只处理指定股票，逗号分隔（如 300750,002648）')
    parser.add_argument('--by-stock', action='store_true', help='按股票分片增量扫描（每只1次加载K线+逐日增量，全量重算推荐）')
    parser.add_argument('--log', action='store_true', help='同时输出到日志文件（logs/backfill_chanlun.log）')
    parser.add_argument('--backup', action='store_true', help='重算前备份 chanlun 两表到 data/backup/')
    args = parser.parse_args()

    if args.quarter:
        start_date, end_date = quarter_to_range(args.quarter)
    else:
        start_date, end_date = args.start, args.end

    codes = [c.strip() for c in (args.codes or '').split(',') if c.strip()] or None
    # 防御：前导零丢失（PowerShell 数组解析 002648→2648）
    if codes:
        codes = [c.zfill(6) if c.isdigit() and len(c) < 6 else c for c in codes]
        print(f"股票过滤: {len(codes)} 只 {codes[:5]}{'...' if len(codes) > 5 else ''}")

    # 日志
    if args.log:
        import logging
        os.makedirs(os.path.join(PROJECT, 'logs'), exist_ok=True)
        log_path = os.path.join(PROJECT, 'logs', 'backfill_chanlun.log')
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S',
                            handlers=[logging.FileHandler(log_path, encoding='utf-8'), logging.StreamHandler()])
        log = logging
        print(f'日志: {log_path}')
    else:
        import logging
        logging.basicConfig(level=logging.INFO, format='%(message)s')
        log = logging

    # 备份提示
    if args.backup:
        os.makedirs(os.path.join(PROJECT, 'data', 'backup'), exist_ok=True)
        for t in ('chanlun_scan_daily', 'chanlun_bi_json'):
            bp = os.path.join(PROJECT, 'data', 'backup', f'{t}_pre_czsc101.db')
            if not os.path.exists(bp):
                db = sqlite3.connect(DB)
                db.execute(f"ATTACH DATABASE ? AS bak", (bp,))
                db.execute(f"CREATE TABLE bak.{t} AS SELECT * FROM {t}")
                db.commit(); db.close()
                log.info(f'已备份 {t} → {bp}')
            else:
                log.info(f'备份已存在，跳过: {bp}')

    all_dates = get_trading_dates(start_date, end_date)
    if not all_dates:
        print(f"区间 {start_date} ~ {end_date} 无交易日")
        sys.exit(0)

    # 增量过滤
    if args.incremental:
        existing = get_existing_dates()
        dates = [d for d in all_dates if d not in existing]
        print(f"增量模式: 已完成 {len(all_dates)-len(dates)} 天, 剩余 {len(dates)} 天")
    else:
        dates = all_dates
        print(f"全量模式: {len(dates)} 天 ({dates[0]} ~ {dates[-1]})")

    if not dates:
        print("无需处理")
        sys.exit(0)

    workers = args.workers
    print(f"并行: {workers} 进程")
    print()

    if args.by_stock:
        # ── 按股票分片增量扫描（每只 1 次加载 + 逐日增量）──
        # 股票列表：重算窗口内出现过交易的全部股票
        db = sqlite3.connect(DB)
        if codes:
            ph = ','.join('?' * len(codes))
            stock_rows = db.execute(f"SELECT DISTINCT stock_code FROM daily_kline WHERE date>=? AND date<=? AND stock_code IN ({ph})", (start_date, end_date) + tuple(codes)).fetchall()
        else:
            stock_rows = db.execute("SELECT DISTINCT stock_code FROM daily_kline WHERE date>=? AND date<=?", (start_date, end_date)).fetchall()
        db.close()
        stock_codes = [r[0] for r in stock_rows]
        log.info(f"按股票分片: {len(stock_codes)} 只股票 × {len(dates)} 天")

        t_start = time.time()
        completed = 0
        err_codes = []
        # 并行按股票处理
        task_args = [(code, dates) for code in stock_codes]
        results_by_code = []
        if workers == 1 or len(task_args) < 8:
            for a in task_args:
                results_by_code.append(scan_stock_all_worker(a))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(scan_stock_all_worker, a): a for a in task_args}
                for f in as_completed(futures):
                    results_by_code.append(f.result())
                    completed += 1
                    if completed % 100 == 0 or completed == len(task_args):
                        eta = (time.time() - t_start) / completed * (len(task_args) - completed)
                        log.info(f"  进度 {completed}/{len(task_args)} ETA {eta/60:.0f}min")

        # 按日重组并写库
        day_map = {d: [] for d in dates}
        for code, res in results_by_code:
            if isinstance(res, str):
                err_codes.append(res)
                continue
            for d, summary in res:
                if d in day_map and summary:
                    day_map[d].append(summary)
        for d in dates:
            n = save_day_results(DB, d, day_map[d])
            log.info(f"  ✓ {d} 保存 {n} 只")

        total_elapsed = time.time() - t_start
        log.info(f"\n=== 完成 ===")
        log.info(f"股票 {len(stock_codes)} 只 × {len(dates)} 天")
        log.info(f"失败股票: {len(err_codes)}")
        for e in err_codes[:5]:
            log.warning(f"  {e}")
        log.info(f"总耗时: {total_elapsed/60:.1f}min")
        sys.exit(0)

    t_start = time.time()
    completed = 0
    total_stocks = 0
    total_bi = 0
    errors = []

    for date in dates:
        t0 = time.time()
        stocks = get_candidates(date, codes)
        
        if completed > 0:
            eta = (time.time() - t_start) / completed * (len(dates) - completed)
            eta_str = f"ETA {eta/3600:.1f}h"
        else:
            eta_str = ""

        log.info(f"[{completed+1}/{len(dates)}] {date} ({len(stocks)}只) ...")

        # 并行扫描
        task_args = [(code, date) for code, name in stocks]
        results = []
        if workers == 1 or len(stocks) < 10:
            for a in task_args:
                results.append(scan_stock_worker(a))
        else:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(scan_stock_worker, a): a for a in task_args}
                for f in as_completed(futures):
                    results.append(f.result())

        # 保存结果
        # 解析结果：成功返回 (dict, None)，失败返回 (None, err_msg)
        saved = save_day_results(DB, date, [r[0] for r in results])
        day_bi = sum(r[0].get('bi_count', 0) for r in results if r[0])
        day_errs = sum(1 for r in results if r[1])
        
        elapsed = time.time() - t0
        completed += 1
        total_stocks += len(stocks)
        total_bi += day_bi

        log.info(f"✓ {date} 保存{saved}只, {day_bi}笔 ({elapsed:.1f}s) {eta_str}")

        if day_errs:
            err_samples = [r[1] for r in results if r[1]][:3]
            log.warning(f"  ⚠ {day_errs}错: {'; '.join(err_samples)}")

    # 汇总
    total_elapsed = time.time() - t_start
    log.info(f"\n=== 完成 ===")
    log.info(f"成功: {completed}/{len(dates)} 天")
    log.info(f"总股票次: {total_stocks}")
    log.info(f"总笔数: {total_bi}")
    log.info(f"总耗时: {total_elapsed/3600:.1f}h ({total_elapsed/60:.0f}min)")
    log.info(f"平均: {total_elapsed/max(completed,1):.1f}s/天")
