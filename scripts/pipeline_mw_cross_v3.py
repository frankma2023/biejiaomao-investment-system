"""
管道 × MW 信号 交叉决策 v3 · 改进版
━━━━━━━━━━━━━━━━━━━━━━━━━━
改进:
  ① 板块扩展: L2 + 主题（thematic），覆盖半导体/集成电路等MW信号集中板块
  ② RPS分级: MW高置信→RPS≥60, MW中置信→RPS≥80
  ③ 行业RS兜底: ind_rs250≥75 替代板块成员要求
"""
import sqlite3, yaml, os
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
PROJECT = 'D:/hanako/investment-system'
TODAY = '2026-06-30'

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 85)
print(f'管道 × MW 信号 整合决策 v3 · {TODAY}')
print('改进: L2+主题板块 | 分级RPS | 行业RS兜底')
print('=' * 85)

# ═══ 步骤一：大盘环境 ═══
r = c.execute("SELECT total_score, rating FROM market_health_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
health, health_r = (r['total_score'], r['rating']) if r else (50, '—')
r = c.execute("SELECT total_score, position_advice FROM market_sell_score_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
sell, sell_adv = (r['total_score'], r['position_advice']) if r else (0, '—')

c.execute("SELECT close FROM index_daily_kline WHERE stock_code='000985' AND date<=? ORDER BY date DESC LIMIT 200", (TODAY,))
cl = [r['close'] for r in c.fetchall()]; cl.reverse()
lc = cl[-1] if cl else 0
ma50 = sum(cl[-50:])/50 if len(cl)>=50 else 0
ma200 = sum(cl[-200:])/200 if len(cl)>=200 else 0
ma50_20d = sum(cl[-70:-20])/50 if len(cl)>=70 else ma50
slope = (ma50-ma50_20d)/ma50_20d if ma50_20d>0 else 0
if slope>0.005 and lc>ma200: regime='牛市'
elif slope<-0.005 and lc<ma200: regime='熊市'
else: regime='震荡市'

pos = 100
if health<50 or sell>=100: pos=30
elif health<65 or sell>=60: pos=50
if regime=='熊市': pos=min(pos,50)
elif regime=='震荡市': pos=min(pos,80)

print(f'\n步骤一: 健康分{health}({health_r}) | 卖出{sell}({sell_adv}) | {regime} | MA50斜率{slope*100:+.1f}%')
print(f'  → 仓位上限 {pos}%')

# ═══ 步骤二：行业 ═══
with open(os.path.join(PROJECT, 'config/index_style.yaml'), 'r', encoding='utf-8') as f:
    idx_cfg = yaml.safe_load(f)

sector_indices = []
cats = idx_cfg.get('categories', {})
for key in ['sector_l2', 'thematic']:
    if key in cats:
        for item in cats[key]:
            if isinstance(item, dict) and 'code' in item:
                sector_indices.append(item['code'])

all_sector_stocks = set()
if sector_indices:
    ph = ','.join('?'*len(sector_indices))
    c.execute(f"SELECT DISTINCT stock_code FROM index_constituents WHERE index_code IN ({ph}) AND date=(SELECT MAX(date) FROM index_constituents)", sector_indices)
    all_sector_stocks = {r['stock_code'] for r in c.fetchall()}

print(f'\n步骤二: L2+主题 {len(sector_indices)}指数 → {len(all_sector_stocks)}只成分股')

# ═══ 步骤三：基本面 ═══
c.execute("SELECT stock_code FROM stock_basic WHERE listing_status IN ('special_treatment','delisting_risk_warning')")
st_set = {r['stock_code'] for r in c.fetchall()}
c.execute("SELECT stock_code, rps_20, rps_250, close FROM stock_rs_daily WHERE date=?", (TODAY,))
rs_dict = {r['stock_code']: {'rps20': r['rps_20'] or 0, 'rps250': r['rps_250'] or 0, 'close': r['close']} for r in c.fetchall()}
c.execute("SELECT stock_code, amount FROM daily_kline WHERE date=?", (TODAY,))
amt_dict = {r['stock_code']: r['amount'] or 0 for r in c.fetchall()}

# MW信号
c.execute("SELECT * FROM mw_signal_daily WHERE b1_date=? AND stock_code!='_sentinel_'", (TODAY,))
mw_today = {r['stock_code']: dict(r) for r in c.fetchall()}

# 共现
c.execute("SELECT stock_code, signal_mask FROM signal_events WHERE date=?", (TODAY,))
co_dict = {}
for r in c.fetchall():
    m = r['signal_mask']
    co_dict[r['stock_code']] = {'ppv1': bool(m&8), 'ppv2': bool(m&16), 'bov2': bool(m&32)}

# PP
c.execute("SELECT DISTINCT stock_code FROM pocket_pivot_daily WHERE engine_version='V2' AND date>=date(?,'-10 days')", (TODAY,))
ppv2_set = {r['stock_code'] for r in c.fetchall()}

# -- 改进后的过滤逻辑 --
def pipeline_pass(code, mw_entry):
    """改进③: 行业RS≥75兜底 + 改进①: 扩展板块 + 改进②: 分级RPS"""
    rs = rs_dict.get(code)
    if not rs: return False, '无RPS'
    if code in st_set: return False, 'ST'

    rps = rs['rps250']
    in_sector = code in all_sector_stocks
    ind_rs = mw_entry.get('ind_rs250') if mw_entry else None
    b1_only = 0
    if mw_entry:
        b1_only = (mw_entry['score_h'] or 0)+(mw_entry['score_d'] or 0)+(mw_entry['score_c'] or 0)+\
                  (mw_entry['score_i1'] or 0)+(mw_entry['score_i2'] or 0)+(mw_entry['score_sig'] or 0)

    # 行业RS≥75 → 行业过滤通过
    sector_ok = in_sector or (ind_rs is not None and ind_rs >= 75)

    # 分级RPS: 高置信≥60, 中置信≥80
    if mw_entry and b1_only >= 55:
        rps_ok = rps >= 60 and sector_ok
        reason = f'MW高置信+RPS{rps}≥60' + ('+行业RS兜底' if not in_sector and ind_rs and ind_rs>=75 else '')
    elif mw_entry and b1_only >= 40:
        rps_ok = rps >= 80 and sector_ok
        reason = f'MW中置信+RPS{rps}≥80' + ('+行业RS兜底' if not in_sector and ind_rs and ind_rs>=75 else '')
    else:
        rps_ok = rps >= 80 and sector_ok
        reason = f'RPS{rps}≥80'

    return rps_ok, reason

# 应用过滤
pipeline_stocks = []
for code in set(list(all_sector_stocks) + list(mw_today.keys())):
    mw = mw_today.get(code)
    ok, reason = pipeline_pass(code, mw)
    if ok:
        rs = rs_dict.get(code, {})
        b1_only = 0
        if mw:
            b1_only = (mw['score_h'] or 0)+(mw['score_d'] or 0)+(mw['score_c'] or 0)+\
                      (mw['score_i1'] or 0)+(mw['score_i2'] or 0)+(mw['score_sig'] or 0)
        pipeline_stocks.append({
            'code': code, 'rps250': rs.get('rps250', 0),
            'amt': amt_dict.get(code, 0), 'reason': reason,
            'has_mw': mw is not None, 'b1_only': b1_only,
        })

pipeline_stocks.sort(key=lambda x: -x['rps250'])
pipeline_codes = {s['code'] for s in pipeline_stocks}

print(f'\n步骤三: 管道通过 {len(pipeline_stocks)} 只 (分级RPS + 行业RS兜底)')
mw_in_pipe = sum(1 for s in pipeline_stocks if s['has_mw'])
print(f'  其中含MW信号: {mw_in_pipe} 只')

# ═══ 交叉分类 ═══
cat1, cat2, cat3, cat4 = [], [], [], []

for code, mw in mw_today.items():
    b1_only = (mw['score_h'] or 0)+(mw['score_d'] or 0)+(mw['score_c'] or 0)+\
              (mw['score_i1'] or 0)+(mw['score_i2'] or 0)+(mw['score_sig'] or 0)
    has_b2 = mw['b2_date'] is not None
    in_pipe = code in pipeline_codes
    rs = rs_dict.get(code, {})
    ppv1 = co_dict.get(code, {}).get('ppv1', False)

    e = {'code': code, 'name': mw['stock_name'], 'b1_only': b1_only,
         'conf': mw['confidence'], 'has_b2': has_b2, 'is_plus': mw['is_plus'],
         'ppv1': ppv1, 'rps250': rs.get('rps250', 0),
         'ind_name': mw.get('ind_name',''), 'ind_rs': mw.get('ind_rs250'),
         'amt_m': (amt_dict.get(code,0) or 0)/10000,
         'in_ppv2': code in ppv2_set}

    if b1_only >= 40 and in_pipe:
        (cat1 if has_b2 else cat2).append(e)
    elif b1_only >= 40 and not in_pipe:
        cat4.append(e)
    elif in_pipe:
        cat3.append(e)

for s in pipeline_stocks:
    if s['code'] not in mw_today and s['has_mw']:
        continue
    if s['code'] not in mw_today:
        cat3.append({'code': s['code'], 'name': '', 'b1_only': 0, 'conf': '',
                     'has_b2': False, 'is_plus': False, 'ppv1': False,
                     'rps250': s['rps250'], 'ind_name': '', 'ind_rs': None,
                     'amt_m': s['amt']/10000, 'in_ppv2': s['code'] in ppv2_set})

cat1.sort(key=lambda x: (x['is_plus'], x['b1_only'], x['rps250']), reverse=True)
cat2.sort(key=lambda x: (x['b1_only'], x['rps250']), reverse=True)
cat3.sort(key=lambda x: x['rps250'], reverse=True)
cat4.sort(key=lambda x: x['b1_only'], reverse=True)

# ═══ 输出 ═══
print('\n' + '-' * 85)
print(f'🔴 类型1: 立即买入 (管道+MW高分+B2确认): {len(cat1)}只')
if cat1:
    for e in cat1:
        print(f'  🏆 {e[\"code\"]} {e[\"name\"]} B1={e[\"b1_only\"]}分 {e[\"conf\"]} RPS={e[\"rps250\"]} {\"✦PLUS\" if e[\"is_plus\"] else \"\"}')

print(f'\n🟡 类型2: 等待B2 (管道+MW高分+无B2): {len(cat2)}只')
if cat2:
    print(f'  {\"代码\":<8s} {\"名称\":<8s} {\"B1\":>4s} {\"置信\":<4s} {\"PPV1\":<5s} {\"RPS\":>4s} {\"行业RS\":>6s} {\"PP_V2\":<6s} {\"行业\":<14s} {\"通过原因\":<20s}')
    for e in cat2:
        reason = next((s['reason'] for s in pipeline_stocks if s['code']==e['code']), '')
        print(f'  {e[\"code\"]:<8s} {e[\"name\"]:<8s} {e[\"b1_only\"]:>4d} {e[\"conf\"]:<4s} {\"✅\" if e[\"ppv1\"] else \"—\":<5s} {e[\"rps250\"]:>4d} {str(e[\"ind_rs\"] or \"—\"):>6s} {\"✅\" if e[\"in_ppv2\"] else \"—\":<6s} {(e[\"ind_name\"] or \"\")[:14]:<14s} {reason:<20s}')

print(f'\n🟢 类型3: 管道候选 (无MW高分): {len(cat3)}只 (Top10)')
if cat3:
    for e in cat3[:10]:
        name = e['name']
        if not name:
            c.execute("SELECT name FROM stock_basic WHERE stock_code=?", (e['code'],))
            sr = c.fetchone(); name = sr['name'] if sr else ''
        reason = next((s['reason'] for s in pipeline_stocks if s['code']==e['code']), '')
        print(f'  {e[\"code\"]} {name:<8s} RPS={e[\"rps250\"]} {\"MW✅\" if e[\"b1_only\"]>0 else \"\"} {reason}')

print(f'\n🟣 类型4: MW高分但管道不通过: {len(cat4)}只')
for e in cat4:
    rs = rs_dict.get(e['code'], {})
    issues = []
    if e['code'] not in all_sector_stocks and (e['ind_rs'] is None or e['ind_rs'] < 75):
        issues.append(f'不在L2/主题板块且行业RS={e[\"ind_rs\"] or \"—\"}<75')
    if rs.get('rps250', 0) < 60:
        issues.append(f'RPS={e[\"rps250\"]}<60(高置信最低门槛)')
    print(f'  {e[\"code\"]} {e[\"name\"]} B1={e[\"b1_only\"]}分 {e[\"conf\"]} {\"·\".join(issues)}')

# ═══ 最终建议 ═══
print(f'\n{\"=\" * 85}')
print('整合决策')
print('=' * 85)
print(f'''
┌- 环境 ------------------------------
│ 健康分:{health}({health_r}) | 卖出:{sell} | {regime} | MA50斜率:{slope*100:+.1f}%
│ 仓位上限: {pos}% | 板块指数: {len(sector_indices)}个 | 成分股: {len(all_sector_stocks)}只
│ 管道通过: {len(pipeline_stocks)}只 (分级RPS + 行业RS兜底)
├- 信号 ------------------------------
│ MW B1总计: {len(mw_today)}只 | B1≥40: {len(cat1)+len(cat2)+len(cat4)}只
│ 立即买入: {len(cat1)}只 | 等待B2: {len(cat2)}只 | 管道剔除: {len(cat4)}只
└- 建议 ------------------------------''')

if cat1:
    t = cat1[0]
    print(f'\n  🏆 {t[\"code\"]} {t[\"name\"]} | B1={t[\"b1_only\"]}分/{t[\"conf\"]} | 仓位{pos}%×72%≈{pos*0.72:.0f}%')
elif cat2:
    t = cat2[0]
    print(f'\n  📋 观察池首位: {t[\"code\"]} {t[\"name\"]} B1={t[\"b1_only\"]}分/{t[\"conf\"]} RPS={t[\"rps250\"]}')
    print(f'     等待B2确认 → 仓位{pos}%×72%≈{pos*0.72:.0f}%')
    if regime == '熊市': print(f'     ⚠ 熊市环境,MW样本极少,建议轻仓')

print(f'\n改进效果:')
print(f'  v2(仅L2+RPS≥80): 管道0只MW → v3(分级RPS+行业RS兜底): 管道{len(cat1)+len(cat2)}只MW')
if cat4: print(f'  仍被剔除{len(cat4)}只, 主因: 行业RS<75 + RPS<60')

db.close()
