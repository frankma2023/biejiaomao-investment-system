# -*- coding: utf-8 -*-
"""CANSLIM 全量评分 · 性能调试工具包
用法（人工逐步调优）：
  python perf_bench.py --n 200 --offset 0      # 测 200 只基准耗时（分段扫描慢票分布）
  python perf_bench.py --slow-report           # 全量跑并输出慢票报告（>3s 的票与耗时）
  python perf_bench.py --full-timed            # 完整 5452 只计时（目标 <10 分钟）
日志：perf_bench.log（阶段计时 + 慢票清单），Ctrl+C 可中断，已完成的票不会重评（skip 当天）
"""
import sys, io, os, time, sqlite3, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
os.chdir(r'D:\hanako\investment-system')
DB = r'D:\hanako\investment-system\data\lixinger.db'
LOG = r'D:\hanako\investment-system\data\perf_bench.log'

def log(msg):
    line = time.strftime('%H:%M:%S ') + msg
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

def get_all_codes():
    conn = sqlite3.connect(DB)
    rows = conn.execute("SELECT stock_code FROM stock_basic WHERE listing_status='normally_listed' AND name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' ORDER BY stock_code").fetchall()
    conn.close()
    return [r[0] for r in rows]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=200)
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--slow-report', action='store_true')
    ap.add_argument('--full-timed', action='store_true')
    ap.add_argument('--workers', type=int, default=0)
    args = ap.parse_args()

    if not args.full_timed and not args.slow_report:
        # 分段基准：测 [offset, offset+n) 的单票耗时（串行，找慢票分布）
        sys.path.insert(0, r'D:\hanako\investment-system\src')
        os.chdir(r'D:\hanako\investment-system\src')
        from scanners.canslim_score import score_stock
        codes = get_all_codes()[args.offset:args.offset + args.n]
        log(f'=== 分段基准 offset={args.offset} n={args.n} ===')
        times = []
        for code in codes:
            t0 = time.time()
            try:
                score_stock(code, '2026-09-07', save=False)
                dt = time.time() - t0
            except Exception as e:
                dt = time.time() - t0
                log(f'{code} ERR {str(e)[:60]}')
                dt = 999
            times.append(dt)
            if dt > 3:
                log(f'{code}: {dt:.1f}s ⚠️慢')
            if len(times) % 50 == 0:
                ts = sorted(times)
                log(f'  [{len(times)}/{args.n}] 中位{ts[len(ts)//2]:.2f}s 平均{sum(ts)/len(ts):.2f}s 最慢{ts[-1]:.2f}s')
        ts = sorted(times)
        est = sum(ts)/len(ts) if ts else 0
        log(f'=== 段结果: 中位{ts[len(ts)//2]:.2f}s 平均{est:.2f}s 最慢{ts[-1]:.2f}s | 全量估算 {5452*est/60:.0f}分钟(单进程) / {5452*est/(args.workers or 8)/60:.0f}分钟({args.workers or 8}进程) ===')
        return

    # 全量计时（多进程，从 batch_canslim_score 借力：--force 全重评并计时）
    import subprocess
    cmd = [sys.executable, r'D:\hanako\investment-system\scripts\batch_canslim_score.py']
    if args.full_timed:
        cmd += ['--force']
    if args.workers:
        cmd += ['--workers', str(args.workers)]
    log('=== 全量计时开始: ' + ' '.join(cmd) + ' ===')
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    dt = (time.time() - t0) / 60
    out = (r.stdout or '')[-1500:]
    log(f'=== 全量完成: {dt:.1f} 分钟 (rc={r.returncode}) ===')
    log('输出尾部: ' + out.replace('\n', ' | '))

if __name__ == '__main__':
    main()
