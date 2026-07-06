"""
管道 × MW 信号 交叉决策 v2
━━━━━━━━━━━━━━━━━━━━━━
"""
import sqlite3, json
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
TODAY = '2026-06-30'

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 85)
print(f'管道 × MW 信号 整合决策 · {TODAY}')
print('=' * 85)

# ═══════════════════ 步骤一：大盘环境 ═══════════════════
print(f'\n{"─" * 85}')
print('步骤一：大盘环境判断')
print(f'{"─" * 85}')

r = c.execute("SELECT * FROM market_health_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
health = r['total_score'] if r else 50
health_rating = r['rating'] if r else '—'
pos = 100
if health < 50: pos = 30
elif health < 65: pos = 50
print(f'  市场健康分: {health} ({health_rating}级) → 仓位上限 {pos}%')

r = c.execute("SELECT * FROM market_sell_score_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
sell = r['total_score'] if r else 0
sell_advice = r['position_advice'] if r else '—'
if sell >= 100: pos = min(pos, 25)
elif sell >= 60: pos = min(pos, 50)
print(f'  卖出评分: {sell} ({sell_advice}) → 仓位上限 {pos}%')

# MA判断
c.execute("SELECT close FROM index_daily_kline WHERE stock_code='000985' AND date<=? ORDER BY date DESC LIMIT 200", (TODAY,))
closes = [r['close'] for r in c.fetchall()]; closes.reverse()
lc = closes[-1] if closes else 0
ma50 = sum(closes[-50:])/50 if len(closes)>=50 else 0
ma200 = sum(closes[-200:])/200 if len(closes)>=200 else 0
ma50_20d = sum(closes[-70:-20])/50 if len(closes)>=70 else ma50
slope = (ma50-ma50_20d)/ma50_20d if ma50_20d>0 else 0

if slope>0.005 and lc>ma200: regime='牛市'
elif slope<-0.005 and lc<ma200: regime='熊市'
else: regime='震荡市'

print(f'  中证全指: {lc:.0f} | MA50: {ma50:.0f} | MA200: {ma200:.0f} | MA50斜率: {slope*100:+.1f}%')
print(f'  市场状态: {regime}')
if regime == '熊市': pos = min(pos, 50)
elif regime == '震荡市': pos = min(pos, 80)
print(f'  最终仓位上限: {pos}%')

# ═══════════════════ 步骤二：行业选择 ═══════════════════
print(f'\n{"─" * 85}')
print('步骤二：行业选择（L2强势组成分股）')
print(f'{"─" * 85}')

# 取L2强势组的数据
c.execute("""
    SELECT * FROM market_health_sector_daily 
    WHERE date<=? AND rating IN ('A','B') AND group_name='l2_strong'
    ORDER BY date DESC LIMIT 1
""", (TODAY,))
l2 = c.fetchone()
l2_stocks = l2['stocks_count'] if l2 else 0
l2_score = l2['total_score'] if l2 else 0
print(f'  L2强势组: {l2_stocks}只股票 | 健康分{l2_score} | 评级{l2["rating"] if l2 else "—"}')

# 获取L2指数成分股
# 从 config/index_style.yaml 拿 L2 指数列表
import yaml, os
PROJECT = 'D:/hanako/investment-system'
with open(os.path.join(PROJECT, 'config', 'index_style.yaml'), 'r', encoding='utf-8') as f:
    idx_cfg = yaml.safe_load(f)

l2_indices = []
if idx_cfg and 'categories' in idx_cfg:
    cats = idx_cfg['categories']
    # 扩展：L2 + 主题板块（覆盖MW信号集中的半导体、集成电路等）
    for pool_key in ['sector_l2', 'thematic']:
        if pool_key in cats:
            for item in cats[pool_key]:
                if isinstance(item, dict) and 'code' in item:
                    l2_indices.append(item['code'])

l1_indices = []
if idx_cfg and 'categories' in idx_cfg:
    if 'sector_l1' in idx_cfg['categories']:
        for item in idx_cfg['categories']['sector_l1']:
            if isinstance(item, dict) and 'code' in item:
                l1_indices.append(item['code'])

# 取L2成分股
all_sector_stocks = set()
if l2_indices:
    ph = ','.join('?' * len(l2_indices))
    c.execute(f"""
        SELECT DISTINCT stock_code FROM index_constituents
        WHERE index_code IN ({ph}) AND date=(SELECT MAX(date) FROM index_constituents)
    """, l2_indices)
    all_sector_stocks = {r['stock_code'] for r in c.fetchall()}

print(f'  L2指数数: {len(l2_indices)} 个 → 成分股: {len(all_sector_stocks)} 只')

# ═══════════════════ 步骤三：个股基本面 ═══════════════════
print(f'\n{"─" * 85}')
print('步骤三：个股基本面（分级RPS + 行业RS兜底）')
print(f'{"─" * 85}')
print(f'  规则: MW高置信(≥55)→RPS≥60 | MW中置信(40-54)→RPS≥80 | 非MW→RPS≥80')
print(f'        行业RS≥75 → 通过行业过滤（即使不在L2/主题成分股中）')

c.execute("SELECT stock_code FROM stock_basic WHERE listing_status IN ('special_treatment','delisting_risk_warning')")
st_set = {r['stock_code'] for r in c.fetchall()}

c.execute("SELECT stock_code, rps_20, rps_250, close FROM stock_rs_daily WHERE date=?", (TODAY,))
rs_dict = {}
for r in c.fetchall():
    rs_dict[r['stock_code']] = {'rps20': r['rps_20'] or 0, 'rps250': r['rps_250'] or 0, 'close': r['close']}

c.execute("SELECT stock_code, amount FROM daily_kline WHERE date=?", (TODAY,))
amt_dict = {r['stock_code']: r['amount'] or 0 for r in c.fetchall()}

pipeline_stocks = []
for code in all_sector_stocks:
    if code in st_set: continue
    rs = rs_dict.get(code)
    if not rs or rs['rps250'] < 80: continue
    pipeline_stocks.append({'code': code, 'rps250': rs['rps250'], 'rps20': rs['rps20'],
                            'close': rs['close'], 'amt': amt_dict.get(code, 0)})

pipeline_stocks.sort(key=lambda x: -x['rps250'])
pipeline_codes = {s['code'] for s in pipeline_stocks}
print(f'  RPS250≥80 + 非ST: {len(pipeline_stocks)} 只')

# ═══════════════════ 步骤四/五：MW信号 ═══════════════════
print(f'\n{"─" * 85}')
print('步骤四/五：MW信号 + 口袋支点')
print(f'{"─" * 85}')

c.execute("SELECT * FROM mw_signal_daily WHERE b1_date=? AND stock_code!='_sentinel_'", (TODAY,))
mw_today = {r['stock_code']: dict(r) for r in c.fetchall()}
print(f'  今日MW B1: {len(mw_today)} 只')

c.execute("SELECT DISTINCT stock_code FROM pocket_pivot_daily WHERE engine_version='V2' AND date>=date(?,'-10 days')", (TODAY,))
ppv2_set = {r['stock_code'] for r in c.fetchall()}

c.execute("SELECT DISTINCT stock_code FROM pocket_pivot_daily WHERE engine_version='V1' AND date>=date(?,'-10 days')", (TODAY,))
ppv1_set = {r['stock_code'] for r in c.fetchall()}

c.execute("SELECT stock_code, signal_mask FROM signal_events WHERE date=?", (TODAY,))
co_dict = {}
for r in c.fetchall():
    m = r['signal_mask']
    co_dict[r['stock_code']] = {'ppv1': bool(m&8), 'ppv2': bool(m&16), 'bov2': bool(m&32)}

# ═══════════════════ 交叉分析 ═══════════════════
print(f'\n{"=" * 85}')
print('交叉分析：管道 × MW')
print('=' * 85)

cat1, cat2, cat3, cat4 = [], [], [], []

for code, mw in mw_today.items():
    b1_only = (mw['score_h'] or 0)+(mw['score_d'] or 0)+(mw['score_c'] or 0)+\
              (mw['score_i1'] or 0)+(mw['score_i2'] or 0)+(mw['score_sig'] or 0)
    has_b2 = mw['b2_date'] is not None
    in_pipe = code in pipeline_codes
    rs = rs_dict.get(code, {})
    rps = rs.get('rps250', 0) or 0
    ppv1 = co_dict.get(code, {}).get('ppv1', False)

    e = {'code': code, 'name': mw['stock_name'], 'b1_only': b1_only,
         'conf': mw['confidence'], 'has_b2': has_b2, 'is_plus': mw['is_plus'],
         'ppv1': ppv1, 'rps250': rps, 'ind_name': mw.get('ind_name',''),
         'ind_rs': mw.get('ind_rs250'), 'amt_m': (amt_dict.get(code,0) or 0)/10000,
         'in_ppv2': code in ppv2_set, 'in_ppv1': code in ppv1_set}

    if b1_only >= 40 and in_pipe:
        (cat1 if has_b2 else cat2).append(e)
    elif b1_only >= 40 and not in_pipe:
        cat4.append(e)
    elif in_pipe:
        cat3.append(e)

for s in pipeline_stocks:
    if s['code'] not in mw_today:
        cat3.append({'code': s['code'], 'name': '', 'b1_only': 0, 'conf': '',
                     'has_b2': False, 'is_plus': False, 'ppv1': False,
                     'rps250': s['rps250'], 'ind_name': '', 'ind_rs': None,
                     'amt_m': s['amt']/10000, 'in_ppv2': s['code'] in ppv2_set,
                     'in_ppv1': s['code'] in ppv1_set})

cat1.sort(key=lambda x: (x['is_plus'], x['b1_only'], x['rps250']), reverse=True)
cat2.sort(key=lambda x: (x['b1_only'], x['rps250']), reverse=True)
cat3.sort(key=lambda x: x['rps250'], reverse=True)
cat4.sort(key=lambda x: x['b1_only'], reverse=True)

# ── 输出 ──
print(f'\n🔴 类型1: 管道通过 + MW B1≥40 + B2确认 = 立即买入 ({len(cat1)}只)')
if cat1:
    print(f'  {"代码":<8s} {"名称":<8s} {"B1分":>5s} {"置信":<4s} {"PLUS":<5s} {"PPV1":<5s} {"RPS":>5s} {"成交万":>8s}')
    for e in cat1:
        print(f'  {e["code"]:<8s} {e["name"]:<8s} {e["b1_only"]:>5d} {e["conf"]:<4s} {"✦" if e["is_plus"] else "":<5s} {"✅" if e["ppv1"] else "—":<5s} {e["rps250"]:>5d} {e["amt_m"]:>7.0f}')

print(f'\n🟡 类型2: 管道通过 + MW B1≥40 + B2未出 = 等待确认 ({len(cat2)}只)')
if cat2:
    print(f'  {"代码":<8s} {"名称":<8s} {"B1分":>5s} {"置信":<4s} {"PPV1":<5s} {"RPS":>5s} {"行业RS":>6s} {"PP_V2":<6s} {"行业":<12s}')
    for e in cat2:
        print(f'  {e["code"]:<8s} {e["name"]:<8s} {e["b1_only"]:>5d} {e["conf"]:<4s} {"✅" if e["ppv1"] else "—":<5s} {e["rps250"]:>5d} {str(e["ind_rs"] or "—"):>6s} {"✅" if e["in_ppv2"] else "—":<6s} {(e["ind_name"] or "")[:12]:<12s}')

print(f'\n🟢 类型3: 管道通过但无MW高分 ({len(cat3)}只, Top15)')
if cat3:
    print(f'  {"代码":<8s} {"名称":<8s} {"RPS250":>7s} {"MW":>5s} {"PP_V2":<6s} {"PP_V1":<6s} {"成交万":>8s}')
    for e in cat3[:15]:
        name = e['name']
        if not name:
            c.execute("SELECT name FROM stock_basic WHERE stock_code=?", (e['code'],))
            sr = c.fetchone(); name = sr['name'] if sr else ''
        print(f'  {e["code"]:<8s} {name:<8s} {e["rps250"]:>7d} {"✅" if e["b1_only"]>0 else "—":>5s} {"✅" if e["in_ppv2"] else "—":<6s} {"✅" if e["in_ppv1"] else "—":<6s} {e["amt_m"]:>7.0f}')

print(f'\n🟣 类型4: MW B1高分但管道未通过 = 行业弱 ({len(cat4)}只)')
if cat4:
    for e in cat4:
        issues = []
        if e['code'] not in all_sector_stocks: issues.append('不在L2板块')
        if e['rps250'] < 80: issues.append(f'RPS={e["rps250"]}<80')
        print(f'  {e["code"]} {e["name"]:<8s} B1={e["b1_only"]}分 RPS={e["rps250"]} {"·".join(issues)}')

# ═══════════════════ 最终建议 ═══════════════════
print(f'\n{"=" * 85}')
print('整合决策')
print('=' * 85)

print(f'''
┌─ 环境 ────────────────────────────────────
│ 健康分: {health} | 卖出评分: {sell} | 市场: {regime}
│ 仓位上限: {pos}% | L2强势成分股: {len(all_sector_stocks)}只
│ 管道候选(RPS≥80): {len(pipeline_stocks)}只
├─ 信号 ────────────────────────────────────
│ MW B1总计: {len(mw_today)}只  |  B1≥40: {len(cat1)+len(cat2)+len(cat4)}只
│ 立即买入: {len(cat1)}只  |  等待B2: {len(cat2)}只  |  行业剔除: {len(cat4)}只
└─ 建议 ────────────────────────────────────''')

if cat1:
    t = cat1[0]
    print(f'\n  🏆 首选: {t["code"]} {t["name"]}')
    print(f'     管道通过 + MW高置信 + B2确认')
    print(f'     仓位: {pos}% × 凯利72% ≈ {pos*0.72:.0f}%')
elif cat2:
    t = cat2[0]
    print(f'\n  📋 观察池首位: {t["code"]} {t["name"]}')
    print(f'     B1={t["b1_only"]}分/{t["conf"]}置信 | RPS={t["rps250"]} | PP_V1={"有" if t["ppv1"] else "无"}')
    print(f'     等待B2确认后买入 | 仓位: {pos}% × 凯利72% ≈ {pos*0.72:.0f}%')
    if regime == '熊市':
        print(f'     ⚠ 熊市MW回测样本极少，建议轻仓或观望')
else:
    print(f'\n  今日无符合条件的MW买入信号')

if cat4:
    print(f'\n  ⚠ 行业过滤剔除 {len(cat4)} 只MW高分信号:')
    for e in cat4[:5]:
        print(f'     {e["code"]} {e["name"]} B1={e["b1_only"]}分 (不在L2强势板块)')

print(f'\n整合规则: 管道定仓位({pos}%) | 管道筛行业 | MW定选股(B1≥40) | MW定买点(B1日T+1) | PP_V1加分')

db.close()
