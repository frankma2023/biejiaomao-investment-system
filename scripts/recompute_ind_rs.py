"""
重算 MW 信号行业 RS + 关注分（不重扫，只修存量）
用法:
  python scripts/recompute_ind_rs.py
  python scripts/recompute_ind_rs.py --start 2016-01-01 --end 2016-12-31
"""
import sqlite3, yaml, json, os, argparse
from datetime import date, datetime

DB = 'D:/hanako/investment-system/data/lixinger.db'
SW_MAP = 'D:/hanako/investment-system/config/sw_to_index.yaml'
IDX_STYLE = 'D:/hanako/investment-system/config/index_style.yaml'

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=30000")
t0 = datetime.now()
print("=" * 60)
print("重算行业 RS + 关注分")

# ── 参数 ──
parser = argparse.ArgumentParser()
parser.add_argument('--start', type=str, default='2016-01-01')
parser.add_argument('--end', type=str, default='2026-07-21')
args = parser.parse_args()

with open(SW_MAP, 'r', encoding='utf-8') as f:
    sw_map = yaml.safe_load(f)

# ── 2. 加载 L2/L1 指数名称 ──
with open(IDX_STYLE, 'r', encoding='utf-8') as f:
    idx_cfg = yaml.safe_load(f)
l2_names = {str(i['code']): i['name'] for i in idx_cfg['categories']['sector_l2']}
l1_names = {str(i['code']): i['name'] for i in idx_cfg['categories']['sector_l1']}

# ── 3. 遍历信号 ──
signals = conn.execute("""
    SELECT id, stock_code, b1_date, h_date, h_rs250, decline_pct, 
           b1_return_pct, c_amount_avg, tech_score, ind_rs20, ind_rs250
    FROM mw_signal_daily
    WHERE b1_date >= ? AND b1_date <= ? AND b1_date != '_sentinel_'
    ORDER BY id
""", (args.start, args.end)).fetchall()
print(f"信号数: {len(signals):,}")

updated_rs = 0
updated_score = 0
skipped = 0

# ── 缓存：SW行业 → 指数映射 ──
sw_to_idx_cache = {}

for i, sig in enumerate(signals):
    if i % 10000 == 0:
        print(f"  {i//1000}k...", end=' ', flush=True)
    
    code = sig['stock_code']
    b1_date = sig['b1_date']
    h_date = sig['h_date']
    
    # ── 查 stock_sw_industry ──
    sw_row = conn.execute(
        "SELECT industry_name FROM stock_sw_industry WHERE stock_code=? ORDER BY updated_at DESC LIMIT 1",
        (code,)
    ).fetchone()
    if not sw_row or not sw_row[0]:
        skipped += 1
        continue
    
    sw_name = sw_row[0]
    
    # ── SW → L1/L2 指数 ──
    if sw_name not in sw_to_idx_cache:
        mapped = sw_map.get(sw_name, {})
        l1_code = str(mapped.get('l1', ''))
        l2_code = str(mapped.get('l2', ''))
        # 优先 L2
        idx_code = l2_code if l2_code else l1_code
        idx_name = l2_names.get(idx_code) or l1_names.get(idx_code, '')
        sw_to_idx_cache[sw_name] = (idx_code, idx_name)
    else:
        idx_code, idx_name = sw_to_idx_cache[sw_name]
    
    if not idx_code:
        skipped += 1
        continue
    
    # ── 查 index_rs_daily @ H 日期 ──
    rs_row = conn.execute(
        "SELECT rs_20, rs_250 FROM index_rs_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (idx_code, h_date)
    ).fetchone()
    if not rs_row:
        # 兜底：查最早可用
        rs_row = conn.execute(
            "SELECT rs_20, rs_250 FROM index_rs_daily WHERE stock_code=? ORDER BY date ASC LIMIT 1",
            (idx_code,)
        ).fetchone()
    
    if not rs_row or rs_row[0] is None:
        skipped += 1
        continue
    
    ind_rs20 = rs_row[0]
    ind_rs250 = rs_row[1]
    
    # ── 更新行业 RS ──
    conn.execute("""
        UPDATE mw_signal_daily SET ind_rs20=?, ind_rs250=?, ind_code=?, ind_name=?
        WHERE id=?
    """, (ind_rs20, ind_rs250, idx_code, idx_name, sig['id']))
    updated_rs += 1
    
    # ── 重算 score_i1（I1: 行业RS250）──
    score_i1 = 0
    if ind_rs250 is not None and ind_rs250 >= 85:
        score_i1 = 20
    elif ind_rs250 is not None and ind_rs250 >= 80:
        score_i1 = 10
    conn.execute("UPDATE mw_signal_daily SET score_i1=? WHERE id=?", (score_i1, sig['id']))
    
    # ── 重算关注分（tech_score）──
    sc = 0
    # h_rs250 (50分)
    rs_val = sig['h_rs250'] or 0
    if rs_val >= 90: sc += 50
    elif rs_val >= 80: sc += 40
    elif rs_val >= 70: sc += 30
    elif rs_val >= 60: sc += 15
    
    # 换手率 (15分) - 从已有 tech_score_detail 读取，不重查DB
    detail = conn.execute("SELECT tech_score_detail FROM mw_signal_daily WHERE id=?", (sig['id'],)).fetchone()
    to_v = 0
    if detail and detail[0]:
        try:
            d = json.loads(detail[0])
            to_v = d.get('turnover', 0)
        except: pass
    sc += to_v
    
    # 距H天数 (22分)
    dh = 0
    if h_date and h_date > '2000-01-01':
        dh = (date.fromisoformat(b1_date) - date.fromisoformat(h_date)).days
        if 40 <= dh <= 60: sc += 22
        elif 30 <= dh < 40: sc += 18
        elif (20 <= dh < 30) or (60 < dh <= 80): sc += 12
        elif dh > 80: sc += 7
    
    # 回调深度 (5分)
    dec = sig['decline_pct'] or 0
    if dec > 35: sc += 5
    elif dec >= 25: sc += 4
    elif dec >= 20: sc += 3
    elif dec >= 15: sc += 2
    
    # 行业 RS_20 (8分) - 用新算的
    if ind_rs20 is not None and ind_rs20 >= 80: sc += 8
    
    # 更新详情（各因子得分）
    h_rs250_sc = 50 if rs_val>=90 else (40 if rs_val>=80 else (30 if rs_val>=70 else (15 if rs_val>=60 else 0)))
    dh_sc = 22 if (dh and 40 <= dh <= 60) else (18 if (dh and 30 <= dh < 40) else (12 if (dh and ((20 <= dh < 30) or (60 < dh <= 80))) else (7 if (dh and dh > 80) else 0)))
    dec_sc = 5 if dec>35 else (4 if dec>=25 else (3 if dec>=20 else (2 if dec>=15 else 0)))
    ind_sc = 8 if (ind_rs20 is not None and ind_rs20 >= 80) else 0
    new_detail = {'h_rs250': h_rs250_sc, 'turnover': to_v, 'days_since_h': dh_sc,
                  'decline': dec_sc, 'ind_rs20': ind_sc}
    
    conn.execute("UPDATE mw_signal_daily SET tech_score=?, tech_score_detail=? WHERE id=?",
                 (sc, json.dumps(new_detail, ensure_ascii=False), sig['id']))
    updated_score += 1

conn.commit()
print(f"\n完成: 更新RS {updated_rs:,}, 更新关注分 {updated_score:,}, 跳过 {skipped:,}")
print(f"耗时: {(datetime.now()-t0).total_seconds():.0f}s")
conn.close()
