#!/usr/bin/env python3
"""
重建看跌形态信号事件表。

背景：pattern_scan_signals 的 date 是「扫描日」，signals_json 是「截至该扫描日已知的
全部信号」（累积写入）。所以：
  · 行的 date ≠ 信号的产生日
  · 且卖出引擎有预筛选（近60日涨>15% 或距60日高点10%内），
    因此"某行含 bearish" 实际在测「该股近期是否强势」，不能用。

本脚本按股票时间序扫一遍，用**形态自身的结构日期**做去重键
（pattern + left_peak.date + right_peak.date + trough.date，不含会随窗口漂移的 idx），
每条形态只在其**首次出现的扫描日**记一次 —— 那个日期就是它的「检测日」。

产出 bearish_signal_events：
  stock_code / signal_date（检测日）/ pattern / structural_date（右峰日）
  + 左峰/右峰/谷/颈线 的价格与日期 + 展示名
"""
import sys, os, sqlite3, json, time, argparse
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from common import DB_PATH

DDL = """
CREATE TABLE IF NOT EXISTS bearish_signal_events (
    stock_code      TEXT NOT NULL,
    signal_date     TEXT NOT NULL,
    source          TEXT,
    pattern         TEXT NOT NULL,
    detected_date   TEXT,
    signal_level    TEXT,
    lp_date         TEXT, lp_price REAL,
    rp_date         TEXT, rp_price REAL,
    tr_date         TEXT, tr_price REAL,
    neckline        REAL,
    detail_json     TEXT,
    PRIMARY KEY (stock_code, source, pattern, signal_date)
)
"""

DISPLAY = {
    'double_top': '双重顶', 'triple_top': '三重顶', 'head_shoulders': '头肩顶',
    'railroad_tracks': '铁轨线', 'climax_top': '高潮见顶',
    'talib': '技术指标', 'pivot': '枢轴',
}


def key_of(s):
    """去重键：家族 + 子类型 + 信号自身日期。

    pattern_scan_signals 里混了 4 个家族，命名各异：
      · top_pattern（双顶/三顶/头肩顶）—— 有 pattern + signal_date
      · railroad_tracks（铁轨线）—— 有 signal_type + signal_date
      · talib（MACD 死叉等技术指标）—— 有 details.signal_type + date
      · pivot —— 有 pivot_type + date
    每条信号都自带日期，所以不需要靠行的扫描日猜测。
    """
    src = s.get('source') or ''
    sub = (s.get('pattern') or s.get('signal_type')
           or (s.get('details') or {}).get('signal_type')
           or s.get('pivot_type') or '')
    sub = str(sub)
    if src == 'top_pattern' and not s.get('pattern'):
        sub = sub or 'top_pattern'
    sd = s.get('signal_date') or s.get('date')
    return (src, sub, sd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rebuild', action='store_true', help='先清表')
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH), timeout=120)
    conn.execute('PRAGMA busy_timeout=120000')
    if args.rebuild:
        conn.execute('DROP TABLE IF EXISTS bearish_signal_events')
        conn.commit()
    conn.executescript(DDL)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bse_date ON bearish_signal_events(signal_date)')
    conn.commit()

    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM pattern_scan_signals ORDER BY stock_code")]
    if args.limit:
        codes = codes[:args.limit]
    print(f'扫描 {len(codes)} 只', flush=True)

    INS = ("INSERT OR REPLACE INTO bearish_signal_events (stock_code, signal_date, source, "
           "pattern, detected_date, signal_level, lp_date, lp_price, rp_date, rp_price, "
           "tr_date, tr_price, neckline, detail_json) "
           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

    n_ev = n_skip = 0
    buf = []
    for ci, code in enumerate(codes, 1):
        rows = conn.execute("SELECT date, signals_json FROM pattern_scan_signals "
                            "WHERE stock_code=? ORDER BY date", (code,)).fetchall()
        seen = set()
        for d, js in rows:
            try:
                sigs = json.loads(js or '[]')
            except Exception:
                n_skip += 1
                continue
            for s in sigs:
                if (s.get('type') or '') != 'bearish':
                    continue
                k = key_of(s)
                if k in seen:
                    continue
                seen.add(k)
                lp = s.get('left_peak') or {}
                rp = s.get('right_peak') or {}
                tr = s.get('trough') or {}
                sd = k[2]
                if not sd:
                    continue
                buf.append((code, str(sd)[:10], k[0], k[1], d,
                            s.get('signal_level'),
                            lp.get('date'), lp.get('price'),
                            rp.get('date'), rp.get('price'),
                            tr.get('date'), tr.get('price'),
                            s.get('neckline'),
                            json.dumps(s, ensure_ascii=False, default=str)))
                n_ev += 1
        if len(buf) >= 20000:
            conn.executemany(INS, buf)
            conn.commit()
            buf = []
        if ci % 500 == 0:
            print(f'  {ci}/{len(codes)}  事件={n_ev:,}  {time.time()-t0:.0f}s', flush=True)
    if buf:
        conn.executemany(INS, buf)
        conn.commit()

    print(f'\n完成: {n_ev:,} 条看跌信号事件 / {len(codes)} 只 / 耗时 {time.time()-t0:.0f}s')
    print('\n按家族×子类型统计:')
    for r in conn.execute("""SELECT source, pattern, COUNT(*), COUNT(DISTINCT stock_code),
                             MIN(signal_date), MAX(signal_date)
                             FROM bearish_signal_events
                             GROUP BY source, pattern ORDER BY 3 DESC LIMIT 20"""):
        disp = DISPLAY.get(r[0], r[0])
        print(f'  {disp:<8} {str(r[1])[:26]:<27} {r[2]:>8,} 条 / {r[3]:>5,} 只  {r[4]} ~ {r[5]}')
    conn.close()


if __name__ == '__main__':
    main()
