# -*- coding: utf-8 -*-
"""全市场扫描：2026 年跌破箱体下沿的 A 股股票
用 box_breakdown 引擎逐股检测，多进程并行。
输出：当前活跃跌破（strong_sell/warning）+ 2026 年触发过（含已清除）
"""
import sys, os, sqlite3, time, json
from multiprocessing import Pool

PROJ = r'D:\hanako\investment-system'
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, 'src'))
os.chdir(PROJ)

DB = os.path.join(PROJ, 'data', 'lixinger.db')
END_DATE = '2026-08-07'  # 数据截止

def load_kline(code):
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    rows = db.execute("""SELECT date, open, high, low, close, volume, change_pct FROM daily_kline
        WHERE stock_code=? AND date<=? ORDER BY date""", (code, END_DATE)).fetchall()
    db.close()
    return [dict(r) for r in rows]

def scan_one(code):
    """单只股票：跑 box_breakdown，返回 2026 年相关事件"""
    try:
        from src.scanners.box_breakdown import detect, load_params
        kl = load_kline(code)
        if len(kl) < 100:
            return None
        sigs = detect(kl, load_params())
        if not sigs:
            return None
        # 2026 年事件：创建日期 >= 2026-01-01，或当前活跃（signal_level 非空）
        recent = []
        for s in sigs:
            if s['signal_date'] >= '2026-01-01' or s.get('signal_level'):
                recent.append(s)
        if not recent:
            return None
        return (code, recent)
    except Exception:
        return None

def main():
    db = sqlite3.connect(DB)
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT stock_code FROM daily_kline WHERE date=?", (END_DATE,)).fetchall()]
    db.close()
    # 只留 A 股：60/00/30/68 开头（排除 B股 900/200、北交所 8/4/9）
    codes = [c for c in codes if c.startswith(('60', '00', '30', '68'))]
    print(f'扫描 {len(codes)} 只 A 股, workers=8', flush=True)

    t0 = time.time()
    results = []
    with Pool(8) as pool:
        for i, res in enumerate(pool.imap_unordered(scan_one, codes, chunksize=50)):
            if res:
                results.append(res)
            if (i + 1) % 1000 == 0:
                print(f'  进度 {i+1}/{len(codes)} ({time.time()-t0:.0f}s)', flush=True)
    print(f'扫描完成 {time.time()-t0:.0f}s, 命中 {len(results)} 只', flush=True)

    # 查名称
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    name_map = {}
    for r in db.execute("SELECT stock_code, name FROM stock_basic").fetchall():
        name_map[r['stock_code']] = r['name']
    db.close()

    # 分类：当前活跃 vs 2026触发已清除
    active_list = []   # 当前仍在跌破
    triggered_2026 = []  # 2026 年触发过（含活跃）
    for code, sigs in results:
        # 取 2026 年创建的事件 + 活跃事件
        ev2026 = [s for s in sigs if s['signal_date'] >= '2026-01-01']
        act = [s for s in sigs if s.get('signal_level')]
        if act:
            for s in act:
                active_list.append({
                    'code': code, 'name': name_map.get(code, code),
                    'date': s['signal_date'], 'level': s['signal_level'],
                    'bottom': s['band_bottom'], 'close': s['close'],
                    'drop_pct': s.get('drop_pct'), 'max_drop': s.get('max_drop_pct'),
                })
        for s in ev2026:
            triggered_2026.append({
                'code': code, 'name': name_map.get(code, code),
                'date': s['signal_date'],
                'level': s['signal_level'] or ('清除·曾' + (s['details'].get('max_level') or '')),
                'bottom': s['band_bottom'], 'close': s['close'],
                'drop_pct': s.get('drop_pct'), 'max_drop': s.get('max_drop_pct'),
                'status': s['details']['status'],
            })

    # 排序输出
    active_list.sort(key=lambda x: (-(x['max_drop'] or 0), x['code']))
    triggered_2026.sort(key=lambda x: (-(x['max_drop'] or 0), x['code']))

    print(f'\n{"="*90}')
    print(f'📉 当前活跃跌破箱体（{len(active_list)} 只）')
    print(f'{"="*90}')
    print(f'{"代码":<8}{"名称":<10}{"触发日":<12}{"级别":<14}{"下沿":>8}{"收盘":>8}{"最大跌幅":>9}')
    for s in active_list:
        print(f'{s["code"]:<8}{s["name"]:<10}{s["date"]:<12}{s["level"]:<14}{s["bottom"]:>8.2f}{s["close"]:>8.2f}{s["max_drop"]:>8.2f}%')

    print(f'\n{"="*90}')
    print(f'📋 2026 年触发过跌破（含已清除，共 {len(triggered_2026)} 条）')
    print(f'{"="*90}')
    print(f'{"代码":<8}{"名称":<10}{"触发日":<12}{"状态":<18}{"下沿":>8}{"收盘":>8}{"最大跌幅":>9}')
    for s in triggered_2026[:60]:
        print(f'{s["code"]:<8}{s["name"]:<10}{s["date"]:<12}{s["status"]:<18}{s["bottom"]:>8.2f}{s["close"]:>8.2f}{s["max_drop"]:>8.2f}%')

    # 保存
    out = {
        'scan_date': END_DATE,
        'active': active_list,
        'triggered_2026': triggered_2026,
    }
    with open(os.path.join(PROJ, 'analysis', 'box_breakdown_2026_scan.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 已保存 analysis/box_breakdown_2026_scan.json')

if __name__ == '__main__':
    main()
