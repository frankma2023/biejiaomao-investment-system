"""600584 vs 其它 MW 信号 差异分析"""
import sqlite3
conn = sqlite3.connect('data/lixinger.db')
conn.row_factory = sqlite3.Row

CODE = '600584'; DATE = '2026-05-08'

def v(x, fmt=''):
    if x is None: return '—'
    if fmt=='.1f': return f'{x:.1f}'
    return str(x)

print(f'{"="*60}')
print(f'  {CODE} 长电科技')
print(f'{"="*60}')

# ── 基本面 ──
r = conn.execute("SELECT report_date, revenue_yoy, net_profit_yoy, gross_margin, roe, asset_liability_ratio FROM stock_financials_annual WHERE stock_code=? ORDER BY report_date DESC LIMIT 1", (CODE,)).fetchone()
print(f'  年报{r[0]}: 营收+{v(r[1],".1f")}% 利润{v(r[2],".1f")}% 毛利率{v(r[3],".1f")}% ROE{v(r[4],".1f")}% 负债率{v(r[5],".1f")}%')

qr = conn.execute("SELECT report_date, roe_single, net_profit_yoy FROM stock_financials_quarterly WHERE stock_code=? ORDER BY report_date DESC LIMIT 2", (CODE,)).fetchall()
for q in qr:
    print(f'  季度{q[0]}: ROE{v(q[1],".1f")}% 利润同比{v(q[2],".1f")}%')

# ── RS ──
r = conn.execute("SELECT rps_20,rps_120,rps_250 FROM stock_rs_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1", (CODE, DATE)).fetchone()
print(f'  RS@B2: 20={r[0]} 120={r[1]} 250={r[2]}')

# ── 机构 ──
try:
    r = conn.execute("SELECT end_date, COUNT(*) as n, SUM(hold_ratio) as t FROM stock_inst_holders_detail WHERE stock_code=? GROUP BY end_date ORDER BY end_date DESC LIMIT 1", (CODE,)).fetchone()
    if r and r[0]:
        print(f'  机构({r[0]}): {r[1]}家 合计{r[2]:.1f}%')
    else:
        print(f'  机构: 无数据')
except:
    print(f'  机构: 无数据')

# ── 观察池 ──
r = conn.execute("SELECT composite_score,rs_category,rps_250,rps_20,canslim_score,industry_name FROM discipline_observation_pool WHERE stock_code=? AND date=?", (CODE, DATE)).fetchone()
if r:
    print(f'  观察池: 综合分{r[0]} 分类{r[1]} RS250={r[2]} RS20={r[3]} CANSLIM={r[4]} 行业={r[5]}')
    ind = r[5]
else:
    print(f'  不在观察池')
    ind = '—'

# ── 形态信号 ──
for d in ['2026-04-14','2026-04-29','2026-05-06']:
    rows = conn.execute("SELECT signal_type,signal_count FROM daily_pattern_scan_results WHERE date=? AND stock_code=?", (d, CODE)).fetchall()
    if rows:
        sigs = ' '.join([f'{r[0]}x{r[1]}' for r in rows])
        print(f'  形态({d}): {sigs}')

# ═══ 横向对比 ═══
print(f'\n{"="*70}')
print(f'  {DATE} 所有MW信号对比 (高置信度 + 部分中置信度)')
print(f'{"="*70}')
print(f'  {"代码":<8} {"名称":<8} {"行业":<10} {"RS20":>5} {"RS250":>6} {"池分":>4} {"ROE":>6} {"B2涨":>6}')
print(f'  {"—"*60}')

# 600584
r = conn.execute("SELECT roe FROM stock_financials_annual WHERE stock_code=? ORDER BY report_date DESC LIMIT 1", (CODE,)).fetchone()
roe584 = r[0] if r else None
r = conn.execute("SELECT rps_20,rps_250 FROM stock_rs_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1", (CODE, DATE)).fetchone()
r20_584=r[0]; r250_584=r[1]
r = conn.execute("SELECT composite_score FROM discipline_observation_pool WHERE stock_code=? AND date=?", (CODE, DATE)).fetchone()
pool_584 = r[0] if r else None
r = conn.execute("SELECT b2_return_pct FROM mw_signal_daily WHERE stock_code=? AND b2_date=?", (CODE, DATE)).fetchone()
b2r_584 = r[0] if r else None
print(f'  {CODE:<8} {"长电科技":<8} {ind:<10} {v(r20_584):>5} {v(r250_584):>6} {v(pool_584):>4} {v(roe584,".1f"):>5}% {v(b2r_584,".1f"):>5}%')

# Others
others = conn.execute("""
    SELECT stock_code,stock_name,b2_return_pct FROM mw_signal_daily
    WHERE b2_date=? AND (confidence='高' OR (confidence='中' AND score>=30))
    AND stock_code!=? ORDER BY score DESC LIMIT 12
""", (DATE, CODE)).fetchall()

for s in others:
    sc = s[0]; sn = s[1]
    r = conn.execute("SELECT roe FROM stock_financials_annual WHERE stock_code=? ORDER BY report_date DESC LIMIT 1", (sc,)).fetchone()
    roe_v = r[0] if r else None
    r = conn.execute("SELECT rps_20,rps_250 FROM stock_rs_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1", (sc, DATE)).fetchone()
    rs20_v = r[0] if r else None; rs250_v = r[1] if r else None
    r = conn.execute("SELECT composite_score FROM discipline_observation_pool WHERE stock_code=? AND date=?", (sc, DATE)).fetchone()
    pool_v = r[0] if r else None
    r = conn.execute("SELECT industry_name FROM discipline_observation_pool WHERE stock_code=? ORDER BY date DESC LIMIT 1", (sc,)).fetchone()
    ind_v = r[0][:8] if r and r[0] else '—'
    print(f'  {sc:<8} {sn:<8} {ind_v:<10} {v(rs20_v):>5} {v(rs250_v):>6} {v(pool_v):>4} {v(roe_v,".1f"):>5}% {v(s[2],".1f"):>5}%')

conn.close()
