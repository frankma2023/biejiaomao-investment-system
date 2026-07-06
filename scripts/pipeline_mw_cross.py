"""
管道 × MW 信号 交叉决策
━━━━━━━━━━━━━━━━━━━━━━
模拟选股漏斗五步，与今日MW信号交叉分析
"""
import sqlite3, json, math
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

# 健康分
r = c.execute("SELECT * FROM market_health_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
if r:
    health_score = r['health_score'] or 50
    print(f'  市场健康分: {health_score}')
    if health_score >= 65: pos_health = '可重仓 (80-100%)'
    elif health_score >= 50: pos_health = '可操作 (50-80%)'
    else: pos_health = '防守 (<50%)'
else:
    health_score = 50
    pos_health = '无数据'

# 卖出评分
r = c.execute("SELECT * FROM market_sell_score_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
if r:
    sell_score = r['sell_score'] or 0
    print(f'  市场卖出评分: {sell_score}')
    if sell_score < 60: pos_sell = '正常 (100%)'
    elif sell_score < 100: pos_sell = '减仓 (50-80%)'
    else: pos_sell = '轻仓 (<50%)'
else:
    sell_score = 0
    pos_sell = '无数据'

# 中证全指MA判断
c.execute("SELECT close FROM index_daily_kline WHERE stock_code='000985' AND date<=? ORDER BY date DESC LIMIT 200", (TODAY,))
closes = [r['close'] for r in c.fetchall()]
closes.reverse()
last_close = closes[-1] if closes else 0
ma50 = sum(closes[-50:])/50 if len(closes)>=50 else 0
ma200 = sum(closes[-200:])/200 if len(closes)>=200 else 0
ma50_20d = sum(closes[-70:-20])/50 if len(closes)>=70 else ma50
slope = (ma50-ma50_20d)/ma50_20d if ma50_20d>0 else 0

if slope>0.005 and last_close>ma200: regime='牛市'
elif slope<-0.005 and last_close<ma200: regime='熊市'
else: regime='震荡市'

print(f'  中证全指: {last_close:.0f} | MA50: {ma50:.0f} | MA200: {ma200:.0f} | 斜率: {slope*100:+.1f}%')
print(f'  市场状态: {regime}')

# 仓位上限
pos_pct = 100
if health_score < 50 or sell_score >= 100: pos_pct = 30
elif health_score < 65 or sell_score >= 60: pos_pct = 50
elif regime == '熊市': pos_pct = 50
elif regime == '震荡市': pos_pct = 80
print(f'  建议仓位上限: {pos_pct}%')
print(f'  MW信号回测覆盖: {regime}环境 {"样本极少(6%)，结论可靠度低 ⚠" if regime=="熊市" else ("最优环境 ✅" if regime=="震荡市" else "一般")}')

# ═══════════════════ 步骤二：行业选择 ═══════════════════
print(f'\n{"─" * 85}')
print('步骤二：行业选择（RS强势 + 资金流入）')
print(f'{"─" * 85}')

# 获取行业分组健康分
c.execute("""
    SELECT group_name, index_codes, health_rating, rs_health
    FROM market_health_sector_daily WHERE date<=? ORDER BY date DESC LIMIT 50
""", (TODAY,))
sector_rows = c.fetchall()

strong_sectors = []
weak_sectors = []
for r in sector_rows:
    if r['health_rating'] in ('A', 'B') and r['rs_health']:
        strong_sectors.append({'name': r['group_name'], 'codes': r['index_codes'], 'rating': r['health_rating']})
    else:
        weak_sectors.append({'name': r['group_name'], 'codes': r['index_codes'], 'rating': r['health_rating']})

# 资金活跃度
c.execute("""
    SELECT index_code, score_10d, score_65d, score_250d
    FROM index_capital_flow_daily WHERE date<=? ORDER BY date DESC
""", (TODAY,))
cap_flow = {}
for r in c.fetchall():
    if r['index_code'] not in cap_flow:
        cap_flow[r['index_code']] = {'s10': r['score_10d'] or 0, 's65': r['score_65d'] or 0, 's250': r['score_250d'] or 0}

# 交叉验证
confirmed_sectors = []
for s in strong_sectors:
    if not s['codes']: continue
    codes = s['codes'].split(',')
    avg_s10 = avg_s65 = avg_s250 = n = 0
    for code in codes:
        cf = cap_flow.get(code.strip())
        if cf:
            avg_s10 += cf['s10']; avg_s65 += cf['s65']; avg_s250 += cf['s250']; n += 1
    if n > 0:
        avg_s10 /= n; avg_s65 /= n; avg_s250 /= n
        flow_ok = avg_s10 >= 60 and avg_s65 >= 60 and avg_s250 >= 60
        if flow_ok:
            confirmed_sectors.append({'name': s['name'], 'codes': s['codes'], 'rating': s['rating'],
                                       'flow10': avg_s10, 'flow65': avg_s65, 'flow250': avg_s250})

print(f'  RS强势组: {len(strong_sectors)} 个')
print(f'  资金三线确认: {len(confirmed_sectors)} 个')
for s in confirmed_sectors:
    print(f'    ⭐ {s["name"]:<15s} 评级{s["rating"]} | 资金10d={s["flow10"]:.0f} 65d={s["flow65"]:.0f} 250d={s["flow250"]:.0f}')

# 收集强势板块的所有成分股
strong_indices = set()
for s in confirmed_sectors:
    if s['codes']:
        for code in s['codes'].split(','):
            strong_indices.add(code.strip())

if not strong_indices:
    # fallback: use all A-rated sectors
    for s in strong_sectors:
        if s['codes']:
            for code in s['codes'].split(','):
                strong_indices.add(code.strip())
    print(f'  ⚠ 无资金确认板块，回退到所有RS强势组 ({len(strong_indices)} 个指数)')

# 获取成分股
all_stocks_in_sectors = set()
if strong_indices:
    ph = ','.join('?' * len(strong_indices))
    c.execute(f"""
        SELECT DISTINCT stock_code FROM index_constituents
        WHERE index_code IN ({ph}) AND date=(SELECT MAX(date) FROM index_constituents)
    """, list(strong_indices))
    all_stocks_in_sectors = {r['stock_code'] for r in c.fetchall()}

print(f'  强势板块成分股: {len(all_stocks_in_sectors)} 只')

# ═══════════════════ 步骤三：个股基本面 ═══════════════════
print(f'\n{"─" * 85}')
print('步骤三：个股基本面（RPS250≥80 + 非ST + 正常上市）')
print(f'{"─" * 85}')

# ST stocks
c.execute("SELECT stock_code FROM stock_basic WHERE listing_status IN ('special_treatment','delisting_risk_warning')")
st_set = {r['stock_code'] for r in c.fetchall()}

# RPS
c.execute("SELECT stock_code, rps_250 FROM stock_rs_daily WHERE date=?", (TODAY,))
rps_dict = {r['stock_code']: r['rps_250'] for r in c.fetchall()}

# Amount
c.execute("SELECT stock_code, amount FROM daily_kline WHERE date=?", (TODAY,))
amt_dict = {r['stock_code']: r['amount'] for r in c.fetchall()}

# Filter
pipeline_stocks = []
for code in all_stocks_in_sectors:
    if code in st_set: continue
    rps = rps_dict.get(code, 0) or 0
    if rps < 80: continue
    amt = amt_dict.get(code, 0) or 0
    pipeline_stocks.append({'code': code, 'rps250': rps, 'amount': amt})

pipeline_stocks.sort(key=lambda x: -x['rps250'])
print(f'  RPS250≥80 + 非ST: {len(pipeline_stocks)} 只')
pipeline_codes = {s['code'] for s in pipeline_stocks}

# ═══════════════════ 步骤四/五：形态信号 ═══════════════════
print(f'\n{"─" * 85}')
print('步骤四/五：形态信号（MW B1/B2 + 口袋支点V2）')
print(f'{"─" * 85}')

# MW signals for today
c.execute("""
    SELECT stock_code, stock_name, b1_date, b2_date, score, confidence, is_plus,
           score_h, score_d, score_c, score_i1, score_i2, score_sig,
           h_rs250, ind_rs250, ind_code, ind_name, decline_pct, b1_return_pct
    FROM mw_signal_daily WHERE b1_date=? AND stock_code!='_sentinel_'
""", (TODAY,))
mw_today = {r['stock_code']: dict(r) for r in c.fetchall()}

# PP V2 signals
c.execute("""
    SELECT DISTINCT stock_code FROM pocket_pivot_daily
    WHERE engine_version='V2' AND date >= date(?, '-10 days')
""", (TODAY,))
pp_set = {r['stock_code'] for r in c.fetchall()}

# PP V1 signals
c.execute("""
    SELECT DISTINCT stock_code FROM pocket_pivot_daily
    WHERE engine_version='V1' AND date >= date(?, '-10 days')
""", (TODAY,))
ppv1_set = {r['stock_code'] for r in c.fetchall()}

# Co-occurrence
c.execute("SELECT stock_code, signal_mask FROM signal_events WHERE date=?", (TODAY,))
co_dict = {}
for r in c.fetchall():
    m = r['signal_mask']
    co_dict[r['stock_code']] = {'ppv1': bool(m&8), 'ppv2': bool(m&16), 'bov2': bool(m&32)}

# ═══════════════════ 交叉分析 ═══════════════════
print(f'\n{"=" * 85}')
print('交叉分析：管道候选人 × MW B1信号')
print('=' * 85)

# Category 1: 管道通过 + MW B1≥40 + B2已出 = 最强
# Category 2: 管道通过 + MW B1≥40 + B2未出 = 等待
# Category 3: 管道通过 + MW B1<40 或无MW = 仅管道
# Category 4: MW B1≥40 + 管道未通过 = 注意（行业弱但个股强）

cat1 = []  # 立即买入
cat2 = []  # 等待B2
cat3 = []  # 仅管道
cat4 = []  # MW高分但管道不过

for code, mw in mw_today.items():
    b1_only = (mw['score_h'] or 0) + (mw['score_d'] or 0) + (mw['score_c'] or 0) + \
              (mw['score_i1'] or 0) + (mw['score_i2'] or 0) + (mw['score_sig'] or 0)
    has_b2 = mw['b2_date'] is not None
    in_pipeline = code in pipeline_codes
    rps = rps_dict.get(code, 0) or 0
    ppv1 = co_dict.get(code, {}).get('ppv1', False)
    
    entry = {
        'code': code, 'name': mw['stock_name'],
        'b1_only': b1_only, 'full_score': mw['score'],
        'conf': mw['confidence'], 'has_b2': has_b2, 'is_plus': mw['is_plus'],
        'ppv1': ppv1, 'rps250': rps,
        'ind_name': mw.get('ind_name', ''),
        'ind_rs': mw.get('ind_rs250'),
        'amt_m': (amt_dict.get(code, 0) or 0) / 10000,
    }
    
    if b1_only >= 40 and in_pipeline:
        if has_b2:
            cat1.append(entry)
        else:
            cat2.append(entry)
    elif b1_only >= 40 and not in_pipeline:
        cat4.append(entry)
    elif in_pipeline and b1_only < 40:
        cat3.append(entry)

# Also add pipeline stocks without MW signals
for s in pipeline_stocks:
    if s['code'] not in mw_today:
        cat3.append({
            'code': s['code'], 'name': '',
            'b1_only': 0, 'full_score': 0,
            'conf': '', 'has_b2': False, 'is_plus': False,
            'ppv1': False, 'rps250': s['rps250'],
            'ind_name': '', 'ind_rs': None,
            'amt_m': s['amount'] / 10000,
        })

cat1.sort(key=lambda x: (x['is_plus'], x['b1_only'], x['rps250']), reverse=True)
cat2.sort(key=lambda x: (x['b1_only'], x['rps250']), reverse=True)
cat3.sort(key=lambda x: x['rps250'], reverse=True)
cat4.sort(key=lambda x: x['b1_only'], reverse=True)

# ── 输出 ──
print(f'\n🔴 类型1: 管道通过 + MW B1≥40 + B2确认 = 立即买入 ({len(cat1)}只)')
if cat1:
    print(f'  {"代码":<8s} {"名称":<8s} {"B1分":>5s} {"置信":<4s} {"PLUS":<5s} {"PPV1":<5s} {"RPS":>5s} {"行业RS":>6s} {"成交(万)":>9s}')
    for e in cat1:
        print(f'  {e["code"]:<8s} {e["name"]:<8s} {e["b1_only"]:>5d} {e["conf"]:<4s} {"✦" if e["is_plus"] else "":<5s} {"✅" if e["ppv1"] else "—":<5s} {e["rps250"]:>5d} {e["ind_rs"] or "—":>6s} {e["amt_m"]:>8.0f}')

print(f'\n🟡 类型2: 管道通过 + MW B1≥40 + B2未出 = 等待确认 ({len(cat2)}只)')
if cat2:
    print(f'  {"代码":<8s} {"名称":<8s} {"B1分":>5s} {"置信":<4s} {"PPV1":<5s} {"RPS":>5s} {"行业":<12s}')
    for e in cat2[:10]:
        print(f'  {e["code"]:<8s} {e["name"]:<8s} {e["b1_only"]:>5d} {e["conf"]:<4s} {"✅" if e["ppv1"] else "—":<5s} {e["rps250"]:>5d} {(e["ind_name"] or "")[:12]:<12s}')

print(f'\n🟢 类型3: 管道通过但无MW高分信号 = 仅管道候选 ({len(cat3)}只, 展示Top10)')
if cat3:
    print(f'  {"代码":<8s} {"名称":<8s} {"RPS250":>7s} {"成交(万)":>9s} {"MW":>5s} {"PP":>5s}')
    for e in cat3[:10]:
        has_mw = '✅' if e['b1_only'] > 0 else '—'
        has_pp = '✅' if e['code'] in pp_set else '—'
        name = e['name'] or ''
        c.execute("SELECT name FROM stock_basic WHERE stock_code=?", (e['code'],))
        sr = c.fetchone()
        if sr: name = sr['name']
        print(f'  {e["code"]:<8s} {name:<8s} {e["rps250"]:>7d} {e["amt_m"]:>8.0f} {has_mw:>5s} {has_pp:>5s}')

print(f'\n🟣 类型4: MW B1高分但管道未通过 = 行业弱 ({len(cat4)}只)')
if cat4:
    print(f'  {"代码":<8s} {"名称":<8s} {"B1分":>5s} {"RPS":>5s} {"问题":<20s}')
    for e in cat4:
        issues = []
        if e['code'] not in all_stocks_in_sectors: issues.append('不在强势板块')
        if e['rps250'] < 80: issues.append(f'RPS={e["rps250"]}<80')
        print(f'  {e["code"]:<8s} {e["name"]:<8s} {e["b1_only"]:>5d} {e["rps250"]:>5d} {", ".join(issues):<20s}')

# ═══════════════════ 最终建议 ═══════════════════
print(f'\n{"=" * 85}')
print('整合决策')
print('=' * 85)

print(f'''
┌─ 环境 ──────────────────────────────────────
│ 市场状态: {regime}  |  健康分: {health_score}  |  卖出评分: {sell_score}
│ 仓位上限: {pos_pct}%  |  MW回测覆盖: {"样本极少⚠" if regime=="熊市" else "充分" if regime=="震荡市" else "一般"}
│ 强势板块: {len(confirmed_sectors)}个（RS+资金双确认）
│ 管道候选: {len(pipeline_stocks)}只（RPS≥80+非ST+强势板块）
├─ 信号 ──────────────────────────────────────
│ MW B1信号: {len(mw_today)}只  |  B1≥40: {len(cat1)+len(cat2)+len(cat4)}只
│ 立即买入(cat1): {len(cat1)}只  |  等待B2(cat2): {len(cat2)}只
│ 行业过滤剔除(cat4): {len(cat4)}只
└─ 建议 ──────────────────────────────────────''')

if cat1:
    print(f'  🏆 首选: {cat1[0]["code"]} {cat1[0]["name"]}')
    print(f'     管道通过 + MW高置信 + B2确认')
    print(f'     仓位: {pos_pct}% × 凯利72% = {pos_pct*0.72:.0f}%')
elif cat2:
    top = cat2[0]
    print(f'  📋 观察池首位: {top["code"]} {top["name"]}')
    print(f'     B1={top["b1_only"]}分/{top["conf"]}置信, RPS={top["rps250"]}')
    print(f'     等待B2确认后买入, 仓位: {pos_pct}% × 凯利72% = {pos_pct*0.72:.0f}%')
    if regime == '熊市':
        print(f'     ⚠ 熊市环境，MW回测样本极少，建议轻仓或等待市场转震荡/牛市')
else:
    print(f'  今日无符合条件的MW买入信号')
    print(f'  建议: 等待市场环境改善 + B2确认出现')

if cat4:
    print(f'\n  ⚠ 行业过滤剔除 {len(cat4)} 只MW高分信号（不在强势板块），避免了潜在假信号')

print(f'\n管道+MW整合规则:')
print(f'  ① 管道定仓位（{pos_pct}%） + MW定选股（B1≥40+等B2）')
print(f'  ② 管道筛行业（{len(confirmed_sectors)}个强势板块） + MW定买点（B1日T+1）')
print(f'  ③ PP_V1共现加分，PP_V2和BO_V2不加分')
print(f'  ④ {"熊市轻仓/不操作" if regime=="熊市" else "震荡市重仓/牛市慎入"}')

db.close()
