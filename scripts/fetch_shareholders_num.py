# -*- coding: utf-8 -*-
"""
scripts/fetch_shareholders_num.py — 理杏仁股东人数时序拉取
================================================================
API: POST https://open.lixinger.com/api/cn/company/shareholders-num
     {token, stockCode, startDate, endDate}
返回: date / total(股东人数) / shareholdersNumberChangeRate / spc
表: shareholders_num_daily (stock_code, date, total, change_rate, price_change)

用法:
  python fetch_shareholders_num.py --watchlist   # 自选池(未移除) + 观察池
  python fetch_shareholders_num.py 600309,000858 # 指定
  python fetch_shareholders_num.py --all --limit 100  # 全市场前100
"""
import sys, os, json, time, sqlite3, argparse
from pathlib import Path
from datetime import datetime, timedelta

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DB_PATH = SCRIPT_DIR.parent / "data" / "lixinger.db"
LIXINGER_URL = "https://open.lixinger.com/api/cn/company/shareholders-num"

ENV_CANDIDATES = [Path.home() / ".hermes" / ".env", SCRIPT_DIR.parent.parent / ".env"]


def load_token():
    for env in ENV_CANDIDATES:
        if env.exists():
            for line in env.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line.startswith("LIXINGER_TOKEN="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    raise FileNotFoundError("LIXINGER_TOKEN 未找到")


def fetch_one(token, code, start, end):
    for attempt in range(3):
        try:
            r = requests.post(LIXINGER_URL, json={
                "token": token, "stockCode": code,
                "startDate": start, "endDate": end,
            }, timeout=60)
            d = r.json()
            if d.get('code') == 1 and d.get('data'):
                return d['data']
            if d.get('code') == 2:  # 积分不足/无权限
                print(f'  ⚠️ {code}: 理杏仁积分不足/无权限，静默切换 akshare')
                return None
            if d.get('message'):
                print(f'  ⚠️ {code}: {str(d.get("message"))[:60]}')
                return None
        except Exception as e:
            if attempt == 2:
                print(f'  ❌ {code}: {str(e)[:60]}')
                return None
            time.sleep(2 * (attempt + 1))
    return None


def fetch_lx_all(token, code, start, end):
    """分 9 年窗口拉取合并（理杏仁 API 约束 ≤10 年间隔，实测容忍但防御性分窗）
    B1 修复：seg_end 减 1 天 + cur 从 seg_end+1 续——原实现 2016~2025-01-01 后直接跳 2026-01-01，
    2025 全年丢失（review-standards-tri-hk B1）"""
    from datetime import date as _date, timedelta as _td
    s = _date.fromisoformat(start)
    e = _date.fromisoformat(end)
    all_rows = []
    cur = s
    while cur <= e:
        raw_end = _date(min(cur.year + 9, 2100), cur.month, cur.day)
        seg_end = min(e, raw_end - _td(days=1))
        if seg_end < cur:
            seg_end = e  # 单窗兜底（极端起点）
        data = fetch_one(token, code, cur.isoformat(), seg_end.isoformat())
        if data:
            all_rows.extend(data)
        if seg_end >= e:
            break
        cur = seg_end + _td(days=1)
    return all_rows


def save_rows(rows, src='lx'):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS shareholders_num_daily (
        stock_code TEXT, date TEXT, total INTEGER,
        change_rate REAL, price_change REAL, source TEXT DEFAULT 'lx',
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        PRIMARY KEY (stock_code, date))""")
    cols = [c[1] for c in conn.execute('PRAGMA table_info(shareholders_num_daily)').fetchall()]
    if 'source' not in cols:
        conn.execute("ALTER TABLE shareholders_num_daily ADD COLUMN source TEXT DEFAULT 'lx'")
    # W4：理杏仁优先——已存在 lx 行不允许被 ak 覆盖；仅 lx 源直接 REPLACE
    if src == 'lx':
        conn.executemany(
            "INSERT OR REPLACE INTO shareholders_num_daily (stock_code, date, total, change_rate, price_change, source) VALUES (?,?,?,?,?,?)",
            [(*r, src) for r in rows])
    else:
        # ak 兜底：同 (code,date) 已有 lx 行则跳过，否则写入
        for r in rows:
            ex = conn.execute("SELECT 1 FROM shareholders_num_daily WHERE stock_code=? AND date=? AND source='lx'",
                              (r[0], r[1])).fetchone()
            if ex:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO shareholders_num_daily (stock_code, date, total, change_rate, price_change, source) VALUES (?,?,?,?,?,?)",
                (*r, src))
    conn.commit()
    conn.close()


def fetch_akshare(code):
    """akshare 东财股东户数明细（覆盖全市场，理杏仁无深市主板）"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        if df is None or len(df) == 0:
            return None
        rows = []
        for _, r in df.iterrows():
            d = str(r.get('股东户数统计截止日', ''))[:10]
            total = r.get('股东户数-本次')
            prev = r.get('股东户数-上次')
            if not d or not total:
                continue
            try:
                total = int(float(total))
                chg = (total / float(prev) - 1) if prev else None
            except Exception:
                continue  # W5：脏值整行跳过，不存半成品
            # 过滤股本变动等异常（绝对值 >5 置空；O4：两侧口径统一说明）
            if chg is not None and abs(chg) > 5:
                chg = None
            rows.append({'date': d, 'total': total, 'change_rate': chg, 'price_change': None})
        return rows
    except Exception as e:
        print(f'  ⚠️ akshare {code}: {str(e)[:60]}')
        return None


def run(code, token):
    start = '2016-01-01'
    end = datetime.now().strftime('%Y-%m-%d')
    data = fetch_lx_all(token, code, start, end)
    src = 'lx'
    if not data:
        data = fetch_akshare(code)
        src = 'ak'
    if not data:
        return 0
    if src == 'lx':
        # O1：显式 None 判断（铁律禁 x or y）；spc=理杏仁披露期附近股价变动（O6 注释）
        rows = [(code, d['date'][:10], d.get('total') if d.get('total') is not None else d.get('num'),
                 d.get('shareholdersNumberChangeRate'), d.get('spc')) for d in data]
    else:
        rows = [(code, d['date'], d['total'], d['change_rate'], d['price_change']) for d in data]
    rows.sort(key=lambda x: x[1])  # 统一升序（理杏仁返回降序）
    save_rows(rows, src)
    first, last = rows[0][1], rows[-1][1]
    latest = rows[-1][2]
    print(f'{code} [{src}]: {len(rows)} 条 ({first} ~ {last}) | 最新股东人数 {latest}')
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('codes', nargs='*', help='股票代码')
    ap.add_argument('--watchlist', action='store_true')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--sleep', type=float, default=1.2)
    args = ap.parse_args()

    token = load_token()
    codes = []
    if args.codes:
        codes = [c for c in args.codes[0].split(',') if c]
    elif args.watchlist:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        codes = [r['stock_code'] for r in conn.execute(
            "SELECT stock_code FROM watchlist WHERE removed_at IS NULL").fetchall()]
        obs = [r[0] for r in conn.execute(
            "SELECT stock_code FROM discipline_observation_pool").fetchall()]
        conn.close()
        codes = list(dict.fromkeys(codes + obs))
    elif args.all:
        conn = sqlite3.connect(DB_PATH)
        codes = [r[0] for r in conn.execute(
            "SELECT DISTINCT stock_code FROM daily_kline WHERE stock_code NOT LIKE '9%'").fetchall()]
        conn.close()
        if args.limit:
            codes = codes[:args.limit]
        else:
            # O9：全市场无上限约 80 分钟+，强制确认
            print(f'⚠️ --all 全市场 {len(codes)} 只，预计 {(len(codes) * args.sleep) / 60:.0f} 分钟。建议 --limit 分批。继续？Ctrl+C 取消，5 秒后开始...')
            time.sleep(5)
    if not codes:
        print('无标的。用法见文件头')
        return

    print(f'开始拉取 {len(codes)} 只股东人数...')
    ok = fail = 0
    for i, c in enumerate(codes):
        try:
            n = run(c, token)
            if n: ok += 1
            else: fail += 1
        except Exception as e:
            print(f'{c}: 异常 {str(e)[:50]}')
            fail += 1
        if (i + 1) % 50 == 0:
            print(f'  进度 {i+1}/{len(codes)} (成功{ok} 失败{fail})')
        time.sleep(args.sleep)
    print(f'完成: 成功 {ok} 只, 失败 {fail} 只')


if __name__ == '__main__':
    main()
