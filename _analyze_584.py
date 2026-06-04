"""分析 600584 长电科技 vs 其它 MW 信号的差异"""
import sqlite3
conn = sqlite3.connect('data/lixinger.db')
conn.row_factory = sqlite3.Row

CODE = '600584'
DATE = '2026-05-08'

# ── 1. 基本面 ──
print('═══ 1. 600584 基本面 ═══')
row = conn.execute("""
    SELECT report_date, revenue_yoy, net_profit_yoy, gross_margin, roe, asset_liability_ratio
    FROM stock_financials_annual
    WHERE stock_code=? ORDER BY report_date DESC LIMIT 1
""", (CODE,)).fetchone()
if row:
    print(f'  报告期: {row[0]}')
    print(f'  营收同比: {row[1]}%')
    print(f'  利润同比: {row[2]}%')
    print(f'  毛利率: {row[3]}%')
    print(f'  ROE: {row[4]}%')
    print(f'  资产负债率: {row[5]}%')

# 季度数据
row = conn.execute("""
    SELECT report_date, revenue_yoy, net_profit_yoy, eps
    FROM stock_financials_quarterly
    WHERE stock_code=? ORDER BY report_date DESC LIMIT 3
""", (CODE,)).fetchall()
print(f'  近3季EPS:')
for r in row:
    print(f'    {r[0]}: 营收同比{r[1]}% 利润同比{r[2]}% EPS={r[3]}')

# ── 2. CANSLIM ──
print('\n═══ 2. CANSLIM ═══')
row = conn.execute("""
    SELECT canslim_total, canslim_c, canslim_a, canslim_n, canslim_s, canslim_l, canslim_i, canslim_m
    FROM stock_canslim_score WHERE stock_code=? ORDER BY date DESC LIMIT 1
""", (CODE,)).fetchone()
if row:
    print(f'  总分={row[0]} C={row[1]} A={row[2]} N={row[3]} S={row[4]} L={row[5]} I={row[6]} M={row[7]}')
else:
    print('  无数据')

# ── 3. RS ──
print('\n═══ 3. RS 动量 ═══')
row = conn.execute("""
    SELECT rps_20, rps_120, rps_250 FROM stock_rs_daily
    WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
""", (CODE, DATE)).fetchone()
print(f'  最新: RPS20={row[0]} RPS120={row[1]} RPS250={row[2]}')

# ── 4. 机构持股 ──
print('\n═══ 4. 机构持股 ═══')
row = conn.execute("""
    SELECT report_date, holder_count, inst_ratio, fund_ratio
    FROM stock_institutional_holdings
    WHERE stock_code=? ORDER BY report_date DESC LIMIT 1
""", (CODE,)).fetchone()
if row:
    print(f'  {row[0]}: 机构数={row[1]} 机构占比={row[2]}% 基金占比={row[3]}%')
else:
    # Try inst_holders_detail
    print('  查详细表...')
    row = conn.execute("""
        SELECT end_date, COUNT(*) as cnt, SUM(hold_ratio) as total_ratio
        FROM stock_inst_holders_detail
        WHERE stock_code=? GROUP BY end_date ORDER BY end_date DESC LIMIT 1
    """, (CODE,)).fetchone()
    if row:
        print(f'  {row[0]}: 机构{row[1]}家 合计占比{row[2]:.1f}%')

# ── 5. 行业 ──
print('\n═══ 5. 行业表现 ═══')
row = conn.execute("""
    SELECT industry_name FROM discipline_observation_pool
    WHERE stock_code=? ORDER BY date DESC LIMIT 1
""", (CODE,)).fetchone()
industry = row[0] if row else '未知'
print(f'  行业: {industry}')

# 观察池
row = conn.execute("""
    SELECT composite_score, rs_category, rps_250, rps_20, canslim_score
    FROM discipline_observation_pool
    WHERE stock_code=? AND date=?
""", (CODE, DATE)).fetchone()
if row:
    print(f'  观察池: 综合分={row[0]} 分类={row[1]} RPS250={row[2]} RPS20={row[3]} CANSLIM={row[4]}')
else:
    print(f'  不在{DATE}观察池')

# ── 6. 形态信号 ──
print('\n═══ 6. B2前后形态信号 ═══')
for d in ['2026-04-08','2026-04-14','2026-05-08']:
    rows = conn.execute("""
        SELECT signal_type, signal_count FROM daily_pattern_scan_results
        WHERE date=? AND stock_code=?
    """, (d, CODE)).fetchall()
    if rows:
        signals = ', '.join([f'{r[0]}x{r[1]}' for r in rows])
        print(f'  {d}: {signals}')

# ── 7. 对比其它高置信度信号 ──
print('\n═══ 7. 对比: 05-08 其它高置信度信号 ═══')
others = conn.execute("""
    SELECT stock_code, stock_name, b2_return_pct, h_rs250, score
    FROM mw_signal_daily
    WHERE b2_date=? AND confidence='高' AND stock_code!=?
""", (DATE, CODE)).fetchall()
for s in others[:8]:
    # RS
    rs = conn.execute("""
        SELECT rps_20, rps_250 FROM stock_rs_daily
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
    """, (s[0], DATE)).fetchone()
    r20 = rs[0] if rs else '—'
    r250 = rs[1] if rs else '—'
    # CANSLIM
    cs = conn.execute("""
        SELECT canslim_total FROM stock_canslim_score
        WHERE stock_code=? ORDER BY date DESC LIMIT 1
    """, (s[0],)).fetchone()
    cst = cs[0] if cs else '—'
    # Pool
    pool = conn.execute("""
        SELECT composite_score FROM discipline_observation_pool
        WHERE stock_code=? AND date=?
    """, (s[0], DATE)).fetchone()
    ps = pool[0] if pool else '—'
    # Industry
    ind = conn.execute("""
        SELECT industry_name FROM discipline_observation_pool
        WHERE stock_code=? ORDER BY date DESC LIMIT 1
    """, (s[0],)).fetchone()
    ind_name = ind[0] if ind else '—'
    
    print(f'  {s[0]} {s[1]} {ind_name}: RS20={r20} RS250={r250} CANSLIM={cst} 池={ps} B2涨={s[2]}%')

conn.close()
