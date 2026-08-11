#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
2026 年 A 股跌破箱体扫描（人工执行版）
======================================
用 box_breakdown 引擎扫描全 A 股（60/00/30/68 开头），
检测 2026-01-01 以来触发过"跌破箱体下沿"的股票。

输出：
  analysis/box_breakdown_2026_result.json  — 完整结果
  analysis/box_breakdown_2026.log          — 执行日志（实时进度）

用法：
  python scripts/scan_box_breakdown_2026_result.py
  python scripts/scan_box_breakdown_2026_result.py --workers 8
  python scripts/scan_box_breakdown_2026_result.py --top 20   # 只打印前20条
"""

import sys, os, sqlite3, time, json, argparse
from datetime import datetime
from multiprocessing import Pool

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'src'))
os.chdir(PROJECT_DIR)  # 显式设置 CWD，不依赖引擎模块的导入副作用

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")
OUT_JSON = os.path.join(PROJECT_DIR, "analysis", "box_breakdown_2026_result.json")
LOG_PATH = os.path.join(PROJECT_DIR, "analysis", "box_breakdown_2026.log")

# 2026 年起点（可用 --since 覆盖）
YEAR_START = '2026-01-01'
# K 线回溯窗口：箱体识别需要 lookback_days(300) + box_min_days(40)，近 3 年足够
KLINE_START = '2023-01-01'


def log(msg):
    """同时输出到终端和日志文件"""
    line = f'[{datetime.now().strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def get_all_a_shares():
    """全 A 股：60/00/30/68 开头，取最新交易日有行情的"""
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        rows = conn.execute(
            "SELECT DISTINCT stock_code FROM daily_kline WHERE date=(SELECT MAX(date) FROM daily_kline)").fetchall()
    return [r[0] for r in rows if r[0].startswith(('60', '00', '30', '68'))]


def get_name_map():
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT stock_code, name FROM stock_basic").fetchall()
    return {r['stock_code']: r['name'] for r in rows}


def scan_one(args_tuple):
    """单股：跑 box_breakdown，返回 since 之后触发的事件（含活跃）。args_tuple=(code, since)"""
    code, since = args_tuple
    try:
        from src.scanners.box_breakdown import detect, load_params
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""SELECT date, open, high, low, close, volume, change_pct
                FROM daily_kline WHERE stock_code=? AND date>=? ORDER BY date""", (code, KLINE_START)).fetchall()
        finally:
            conn.close()
        if len(rows) < 100:
            return None
        kl = [dict(r) for r in rows]
        sigs = detect(kl, load_params())
        if not sigs:
            return None
        # 只收 since 之后触发的事件（活跃与否都收；更早的事件直接丢弃，减少传输）
        events = []
        for s in sigs:
            if s['signal_date'] < since:
                continue
            events.append({
                    'date': s['signal_date'],
                    'level': s['signal_level'],          # None = 已清除
                    'max_level': s['details'].get('max_level'),
                    'status': s['details']['status'],     # active/failed
                    'bottom': s['band_bottom'],
                    'top': s['band_top'],                 # 箱体上沿（验证箱体深度用）
                    'top_touches': s['details'].get('top_touches'),
                    'bottom_touches': s['details'].get('bottom_touches'),
                    'box_days': s['details'].get('box_days'),
                    'close': s['close'],
                    'drop_pct': s.get('drop_pct'),
                    'max_drop_pct': s.get('max_drop_pct'),
                    'box_start': s['details'].get('box_start_date'),
                })
        if not events:
            return None
        return (code, events)
    except Exception as e:
        # 不静默吞错：记录到日志，便于区分"无信号"与"扫描崩溃"
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f'[{datetime.now().strftime("%H:%M:%S")}] ⚠️ {code} 扫描异常: {type(e).__name__}: {e}\n')
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--top', type=int, default=0, help='只打印前 N 条（0=全部，JSON 始终输出全部）')
    parser.add_argument('--limit', type=int, default=0, help='只扫前 N 只股票（快速调试，0=全部）')
    parser.add_argument('--since', type=str, default=YEAR_START, help='事件起始日期（默认 2026-01-01）')
    parser.add_argument('--no-st', action='store_true', help='排除 ST/*ST 股')
    args = parser.parse_args()
    since = args.since

    # 清空旧日志 + 确保目录存在
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    open(LOG_PATH, 'w', encoding='utf-8').close()

    # 启动时预加载引擎参数：配置错误立即暴露，而非被 worker 吞掉
    try:
        from src.scanners.box_breakdown import load_params
        params = load_params()
        log(f'引擎参数加载 OK（YAML 配置有效）')
    except Exception as e:
        log(f'❌ 引擎参数加载失败: {type(e).__name__}: {e}')
        sys.exit(1)

    log(f'═══ 2026 年 A 股跌破箱体扫描 ═══')
    log(f'数据截止: 最近交易日 | workers={args.workers}')

    codes = get_all_a_shares()
    if args.limit:
        codes = codes[:args.limit]
    log(f'待扫描: {len(codes)} 只 A 股')
    tasks = [(c, since) for c in codes]

    t0 = time.time()
    results = []
    done = 0
    with Pool(args.workers) as pool:
        for res in pool.imap_unordered(scan_one, tasks, chunksize=50):
            done += 1
            if res:
                results.append(res)
            if done % 200 == 0:
                elapsed = time.time() - t0
                speed = done / elapsed
                remain = (len(codes) - done) / speed if speed > 0 else 0
                log(f'进度 {done}/{len(codes)} ({elapsed:.0f}s, 剩余约{remain:.0f}s) 命中 {len(results)} 只')

    elapsed = time.time() - t0
    log(f'✅ 扫描完成 {elapsed:.0f}s, 命中 {len(results)} 只')

    # 查名称
    name_map = get_name_map()
    if args.no_st:
        before = len(results)
        results = [(c, evs) for c, evs in results if 'ST' not in (name_map.get(c, c) or '').upper()]
        log(f'排除 ST 股: {before} → {len(results)} 只')

    # 分类整理：聚焦 since 之后
    active_now = []     # since 后触发且当前仍活跃
    triggered_since = [] # since 后触发过（含已清除）

    for code, events in results:
        for ev in events:
            if ev['date'] < since:
                continue  # 只看 since 之后触发的事件
            item = {
                'code': code,
                'name': name_map.get(code, code),
                **ev,
            }
            triggered_since.append(item)
            if ev['status'] == 'active':  # 真活跃（signal_level 清除后可能残留）
                active_now.append(item)

    # active_now 每股只保留最新触发的事件（避免同一股票多个活跃事件造成困惑）
    active_now.sort(key=lambda x: x['date'], reverse=True)
    seen = set()
    dedup = []
    for item in active_now:
        if item['code'] in seen:
            continue
        seen.add(item['code'])
        dedup.append(item)
    active_now = dedup

    # 排序：按触发日期倒序（最新在前），同日按代码+级别保证确定性
    triggered_since.sort(key=lambda x: (x['date'], x['code'], x.get('level') or ''), reverse=True)
    active_now.sort(key=lambda x: (x['date'], x['code'], x.get('level') or ''), reverse=True)

    # 输出 JSON
    out = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'scan_universe': len(codes),
        'since': since,
        'active_now_count': len(active_now),
        'triggered_count': len(triggered_since),
        'active_now': active_now,
        'triggered': triggered_since,
    }
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f'📄 结果已写入 {OUT_JSON}')

    # 终端打印
    def show(title, items, limit):
        log(f'\n{"="*110}')
        log(f'{title}（{len(items)} 条）')
        log(f'{"="*110}')
        log(f'{"代码":<8}{"名称":<10}{"触发日":<12}{"级别":<16}{"上沿":>9}{"下沿":>9}{"收盘":>9}{"最大跌幅":>10}  状态')
        for s in items[:limit] if limit else items:
            lv = s['level'] or ('曾' + (s['max_level'] or ''))
            log(f'{s["code"]:<8}{s["name"]:<10}{s["date"]:<12}{lv:<16}'
                f'{s.get("top") or 0:>9.2f}{s["bottom"]:>9.2f}{s["close"]:>9.2f}{s["max_drop_pct"]:>9.2f}%  {s["status"]}')

    show('📉 当前活跃跌破箱体（信号有效中）', active_now, args.top if args.top else 0)
    show('📋 触发过跌破（含已清除）', triggered_since, args.top if args.top else 0)


if __name__ == '__main__':
    main()
