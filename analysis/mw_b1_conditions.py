# -*- coding: utf-8 -*-
"""MW B1 超额条件探测（2016-2026 全量）
口径：B1 日 T+1 开盘(adj_open)买入、H20 收盘卖出、扣成本 0.3%、前复权 adj
超额 = 信号收益 - 同期 000985；按候选条件分层找正超额组合
"""
import sys, io, os, sqlite3, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\hanako\investment-system\src')
os.chdir(r'D:\hanako\investment-system\src')
DB = r'D:\hanako\investment-system\data\lixinger.db'
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

# 指数
idx = conn.execute("SELECT date, close FROM index_daily_kline WHERE stock_code='000985' ORDER BY date").fetchall()
idx_d = [r['date'] for r in idx]
idx_c = {r['date']: r['close'] for r in idx}
# 指数 MA200 判定（牛熊）
ma200 = {}
for i in range(199, len(idx_d)):
    ma200[idx_d[i]] = sum(idx_c[idx_d[j]] for j in range(i - 199, i + 1)) / 200
def idx_ret(d, off):
    if d not in idx_d:
        return None
    i0 = idx_d.index(d)
    j = i0 + off - 1
    if j < len(idx_d):
        return idx_c[idx_d[j]] / idx_c[d] - 1
    return None
def bull(d):
    m = ma200.get(d)
    return (m is not None and idx_c[d] > m) if d in idx_c else None

# 信号
sigs = conn.execute("""SELECT stock_code, b1_date, b2_date, h_rs250, decline_pct, score, tech_score_v4
    FROM mw_signal_daily WHERE b1_date >= '2016-01-01' AND b1_date <= '2026-12-31'
    AND stock_code != '_sentinel'""").fetchall()
print('B1 样本:', len(sigs))

# 股票 K 线缓存（拉 b1 后 30 行判 H20）
k_cache = {}
def kline_win(code, d):
    if code not in k_cache:
        rows = conn.execute("SELECT date, adj_open, adj_close FROM daily_kline WHERE stock_code=? AND date>='2015-06-01' ORDER BY date", (code,)).fetchall()
        k_cache[code] = rows
    rows = k_cache[code]
    i0 = None
    for i, r in enumerate(rows):
        if r['date'] >= d:
            i0 = i
            break
    if i0 is None or i0 + 1 >= len(rows):
        return None
    # T+1 开盘 = rows[i0+1]（b1 日下一交易日开盘）
    entry = rows[i0 + 1]['adj_open']
    j = i0 + 20  # H20：T+1 买后持有 20 交易日
    if j >= len(rows):
        return None
    exit_ = rows[j]['adj_close']
    if not entry or not exit_:
        return None
    return entry, exit_

out = []
for s in sigs:
    win = kline_win(s['stock_code'], s['b1_date'])
    if not win:
        continue
    entry, exit_ = win
    if not entry or entry <= 0:
        continue
    ret = exit_ / entry - 1 - 0.003
    mkt = idx_ret(s['b1_date'], 20)
    if mkt is None:
        continue
    ex = ret - mkt
    out.append({'code': s['stock_code'], 'b1': s['b1_date'], 'b2': s['b2_date'],
                'h_rs250': s['h_rs250'], 'decline': s['decline_pct'], 'score': s['score'],
                'ts': float(s['tech_score_v4']) if s['tech_score_v4'] else None,
                'ret': ret, 'mkt': mkt, 'ex': ex,
                'bull': bull(s['b1_date'])})
print('有效样本:', len(out))

def stat(rows, name, cond=None):
    sub = [r for r in rows if (cond(r) if cond else True)]
    if len(sub) < 30:
        print(f'  {name}: n={len(sub)} 样本不足')
        return
    exs = [r['ex'] for r in sub]
    win = sum(1 for v in exs if v > 0) / len(exs)
    print(f'  {name}: n={len(sub):6d} 超额胜率={win*100:5.1f}% 平均={statistics.mean(exs)*100:+6.2f}% 中位={statistics.median(exs)*100:+6.2f}%')

print('\n===== 全量基线 =====')
stat(out, '全量 B1')
stat(out, 'B2 确认(事后)', lambda r: r['b2'])

print('\n===== 单因子分层 =====')
print('--- h_rs250（前高 RS）---')
for lo, hi in [(95, 999), (90, 95), (85, 90), (80, 85), (70, 80), (60, 70), (0, 60)]:
    stat(out, 'h_rs250 %d-%d' % (lo, hi), lambda r, a=lo, b=hi: r['h_rs250'] is not None and a <= r['h_rs250'] < b)
print('--- decline_pct（回调深度）---')
for lo, hi in [(40, 999), (30, 40), (25, 30), (20, 25), (15, 20), (0, 15)]:
    stat(out, 'decline %d-%d%%' % (lo, hi), lambda r, a=lo, b=hi: r['decline'] is not None and a <= abs(r['decline'] or 0) < b)
print('--- score（旧 HDC）---')
for lo, hi in [(80, 999), (70, 80), (60, 70), (50, 60), (0, 50)]:
    stat(out, 'score %d-%d' % (lo, hi), lambda r, a=lo, b=hi: r['score'] is not None and a <= r['score'] < b)
print('--- TS（tech_score_v4, 2018+ 数据足）---')
ts_out = [r for r in out if r['ts']]
for lo, hi in [(85, 999), (75, 85), (65, 75), (55, 65), (0, 55)]:
    stat(ts_out, 'TS %d-%d' % (lo, hi), lambda r, a=lo, b=hi: r['ts'] is not None and a <= r['ts'] < b)

print('\n===== 市场环境 =====')
stat(out, '牛市(000985>MA200)', lambda r: r['bull'] is True)
stat(out, '熊市(000985<MA200)', lambda r: r['bull'] is False)
print('--- 牛熊 × h_rs250 ---')
stat(out, '牛×h_rs250>=80', lambda r: r['bull'] is True and r['h_rs250'] is not None and r['h_rs250'] >= 80)
stat(out, '熊×h_rs250>=80', lambda r: r['bull'] is False and r['h_rs250'] is not None and r['h_rs250'] >= 80)

print('\n===== 组合探测 =====')
stat(out, 'h_rs250>=80 × decline>=25', lambda r: r['h_rs250'] is not None and r['h_rs250'] >= 80 and r['decline'] is not None and abs(r['decline'] or 0) >= 25)
stat(out, 'h_rs250>=80 × decline>=25 × 牛', lambda r: r['bull'] is True and r['h_rs250'] is not None and r['h_rs250'] >= 80 and r['decline'] is not None and abs(r['decline'] or 0) >= 25)
stat(out, 'score>=70 × 牛', lambda r: r['bull'] is True and r['score'] is not None and r['score'] >= 70)
stat(ts_out, 'TS>=75 × 牛', lambda r: r['bull'] is True and r['ts'] is not None and r['ts'] >= 75)

print('\n===== 最强可交易组合（熊市 × 深回调）=====')
stat(out, '熊 × decline>=40', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40)
stat(out, '熊 × decline>=40 × TS55-85', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40 and r['ts'] is not None and 55 <= r['ts'] <= 85)
stat(out, '熊 × decline>=40 × h_rs250>=70', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40 and r['h_rs250'] is not None and r['h_rs250'] >= 70)
stat(out, '牛 × decline>=40(对照)', lambda r: r['bull'] is True and r['decline'] is not None and abs(r['decline'] or 0) >= 40)

print('\n===== 门槛定准（熊市内 decline 细档 + 深度上限）=====')
for lo, hi in [(20, 25), (25, 30), (30, 35), (35, 40), (40, 45), (45, 50), (50, 60), (60, 999)]:
    stat(out, '熊 × decline %d-%d%%' % (lo, hi), lambda r, a=lo, b=hi: r['bull'] is False and r['decline'] is not None and a <= abs(r['decline'] or 0) < b)
stat(out, '熊 × decline>=35', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 35)
stat(out, '熊 × decline>=40 (H20 持有变体对照)', lambda r: r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 40)
print('\n===== 分年稳定性: 熊×decline>=35 =====')
sub2 = [r for r in out if r['bull'] is False and r['decline'] is not None and abs(r['decline'] or 0) >= 35]
by_y = {}
for r in sub2:
    by_y.setdefault(r['b1'][:4], []).append(r['ex'])
for y in sorted(by_y):
    v = by_y[y]
    win = sum(1 for x in v if x > 0) / len(v)
    print('  %s: n=%d 胜率=%.0f%% 平均=%+.2f%%' % (y, len(v), win * 100, statistics.mean(v) * 100))
conn.close()
print('\n完成')
