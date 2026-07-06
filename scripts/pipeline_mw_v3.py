# -*- coding: utf-8 -*-
"""Pipeline x MW Cross Analysis v3"""
import sqlite3, yaml, os

DB = 'D:/hanako/investment-system/data/lixinger.db'
PROJECT = 'D:/hanako/investment-system'
TODAY = '2026-06-30'

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

print('=' * 80)
print('Pipeline x MW Cross v3 -', TODAY)
print('Improvements: L2+Thematic | Tiered RPS | IndRS>=75 fallback')
print('=' * 80)

# Step 1: Market
r = c.execute("SELECT total_score, rating FROM market_health_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
health, health_r = (r['total_score'], r['rating']) if r else (50, '-')
r = c.execute("SELECT total_score, position_advice FROM market_sell_score_daily WHERE date<=? ORDER BY date DESC LIMIT 1", (TODAY,)).fetchone()
sell, sell_adv = (r['total_score'], r['position_advice']) if r else (0, '-')

c.execute("SELECT close FROM index_daily_kline WHERE stock_code='000985' AND date<=? ORDER BY date DESC LIMIT 200", (TODAY,))
cl = [r['close'] for r in c.fetchall()]; cl.reverse()
lc = cl[-1] if cl else 0
ma50 = sum(cl[-50:])/50 if len(cl)>=50 else 0
ma200 = sum(cl[-200:])/200 if len(cl)>=200 else 0
ma50_20d = sum(cl[-70:-20])/50 if len(cl)>=70 else ma50
slope = (ma50-ma50_20d)/ma50_20d if ma50_20d>0 else 0
if slope>0.005 and lc>ma200: regime='Bull'
elif slope<-0.005 and lc<ma200: regime='Bear'
else: regime='Ranging'

pos = 100
if health<50 or sell>=100: pos=30
elif health<65 or sell>=60: pos=50
if regime=='Bear': pos=min(pos,50)
elif regime=='Ranging': pos=min(pos,80)

print('Step1: Health=%s(%s) Sell=%s(%s) %s slope=%.1f%% -> Position=%d%%' % (health, health_r, sell, sell_adv, regime, slope*100, pos))

# Step 2: Sectors
with open(os.path.join(PROJECT, 'config/index_style.yaml'), 'r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f)

indices = []
for key in ['sector_l2', 'thematic']:
    if 'categories' in cfg and key in cfg['categories']:
        for item in cfg['categories'][key]:
            if isinstance(item, dict) and 'code' in item:
                indices.append(item['code'])

all_sector_stocks = set()
if indices:
    ph = ','.join('?'*len(indices))
    c.execute("SELECT DISTINCT stock_code FROM index_constituents WHERE index_code IN (%s) AND date=(SELECT MAX(date) FROM index_constituents)" % ph, indices)
    all_sector_stocks = {r['stock_code'] for r in c.fetchall()}

print('Step2: %d indices -> %d stocks' % (len(indices), len(all_sector_stocks)))

# Step 3: Fundamentals + MW
c.execute("SELECT stock_code FROM stock_basic WHERE listing_status IN ('special_treatment','delisting_risk_warning')")
st_set = {r['stock_code'] for r in c.fetchall()}

c.execute("SELECT stock_code, rps_20, rps_250, close FROM stock_rs_daily WHERE date=?", (TODAY,))
rs_dict = {r['stock_code']: {'rps20': r['rps_20'] or 0, 'rps250': r['rps_250'] or 0} for r in c.fetchall()}

c.execute("SELECT stock_code, amount FROM daily_kline WHERE date=?", (TODAY,))
amt_dict = {r['stock_code']: r['amount'] or 0 for r in c.fetchall()}

c.execute("SELECT * FROM mw_signal_daily WHERE b1_date=? AND stock_code!='_sentinel_'", (TODAY,))
mw = {r['stock_code']: dict(r) for r in c.fetchall()}

c.execute("SELECT stock_code, signal_mask FROM signal_events WHERE date=?", (TODAY,))
co = {}
for r in c.fetchall():
    m = r['signal_mask']
    co[r['stock_code']] = {'ppv1': bool(m&8), 'ppv2': bool(m&16), 'bov2': bool(m&32)}

c.execute("SELECT DISTINCT stock_code FROM pocket_pivot_daily WHERE engine_version='V2' AND date>=date(?,'-10 days')", (TODAY,))
ppv2 = {r['stock_code'] for r in c.fetchall()}

def b1score(mw_entry):
    if not mw_entry: return 0
    return (mw_entry['score_h'] or 0)+(mw_entry['score_d'] or 0)+(mw_entry['score_c'] or 0)+(mw_entry['score_i1'] or 0)+(mw_entry['score_i2'] or 0)+(mw_entry['score_sig'] or 0)

def pipeline_pass(code):
    rs = rs_dict.get(code)
    if not rs: return False, 'NoRPS'
    if code in st_set: return False, 'ST'
    rps = rs['rps250']
    mwe = mw.get(code)
    bs = b1score(mwe)
    ind_rs = mwe.get('ind_rs250') if mwe else None
    sector_ok = (code in all_sector_stocks) or (ind_rs is not None and ind_rs >= 75)
    if mwe and bs >= 55:
        if rps >= 60 and sector_ok: return True, 'MW-High+RPS%d' % rps
        else: return False, 'Fail: RPS=%d or NoSector(IRS=%s)' % (rps, ind_rs)
    elif mwe and bs >= 40:
        if rps >= 80 and sector_ok: return True, 'MW-Mid+RPS%d' % rps
        else: return False, 'Fail: RPS=%d or NoSector' % rps
    else:
        if rps >= 80 and sector_ok: return True, 'RPS%d' % rps
        else: return False, 'Fail'

pipeline = []
for code in set(list(all_sector_stocks) + list(mw.keys())):
    ok, reason = pipeline_pass(code)
    if ok:
        rs = rs_dict.get(code, {})
        pipeline.append({'code': code, 'rps250': rs.get('rps250',0), 'amt': amt_dict.get(code,0), 'reason': reason, 'has_mw': code in mw, 'b1_only': b1score(mw.get(code))})

pipeline.sort(key=lambda x: -x['rps250'])
pipe_codes = {s['code'] for s in pipeline}

mw_in_pipe = sum(1 for s in pipeline if s['has_mw'])
print('Step3: %d passed (%d with MW signals)' % (len(pipeline), mw_in_pipe))

# Cross classify
cat1, cat2, cat3, cat4 = [], [], [], []

for code, mwe in mw.items():
    bs = b1score(mwe)
    has_b2 = mwe['b2_date'] is not None
    in_pipe = code in pipe_codes
    rs = rs_dict.get(code, {})
    ppv1_flag = co.get(code, {}).get('ppv1', False)
    e = {'code': code, 'name': mwe['stock_name'], 'b1_only': bs, 'conf': mwe['confidence'],
         'has_b2': has_b2, 'is_plus': mwe['is_plus'], 'ppv1': ppv1_flag,
         'rps250': rs.get('rps250',0), 'ind_name': mwe.get('ind_name',''),
         'ind_rs': mwe.get('ind_rs250'), 'amt_m': (amt_dict.get(code,0) or 0)/10000,
         'in_ppv2': code in ppv2}
    if bs >= 40 and in_pipe:
        (cat1 if has_b2 else cat2).append(e)
    elif bs >= 40 and not in_pipe:
        cat4.append(e)
    elif in_pipe:
        cat3.append(e)

for s in pipeline:
    if s['code'] not in mw:
        cat3.append({'code': s['code'], 'name': '', 'b1_only': 0, 'conf': '', 'has_b2': False,
                     'is_plus': False, 'ppv1': False, 'rps250': s['rps250'],
                     'ind_name': '', 'ind_rs': None, 'amt_m': s['amt']/10000, 'in_ppv2': s['code'] in ppv2})

cat1.sort(key=lambda x: (x['is_plus'], x['b1_only'], x['rps250']), reverse=True)
cat2.sort(key=lambda x: (x['b1_only'], x['rps250']), reverse=True)
cat3.sort(key=lambda x: x['rps250'], reverse=True)
cat4.sort(key=lambda x: x['b1_only'], reverse=True)

# Output
print('\n' + '-' * 80)
print('RED Cat1: Buy Now (Pipeline+MW+High+B2): %d' % len(cat1))
for e in cat1:
    plus = ' *PLUS*' if e['is_plus'] else ''
    print('  %s %-8s B1=%d %s RPS=%d%s' % (e['code'], e['name'], e['b1_only'], e['conf'], e['rps250'], plus))

print('\nYELLOW Cat2: Wait B2 (Pipeline+MW+High, no B2): %d' % len(cat2))
if cat2:
    hdr = '%-8s %-8s %4s %-4s %5s %4s %6s %-6s %-14s %-20s' % ('Code','Name','B1','Conf','PPV1','RPS','IndRS','PP_V2','Industry','Reason')
    print(hdr)
    for e in cat2:
        reason = next((s['reason'] for s in pipeline if s['code']==e['code']), '')
        ppv1_str = 'Y' if e['ppv1'] else '-'
        ppv2_str = 'Y' if e['in_ppv2'] else '-'
        irs = str(e['ind_rs'] or '-')
        ind = (e['ind_name'] or '')[:14]
        print('%-8s %-8s %4d %-4s %5s %4d %6s %-6s %-14s %-20s' % (e['code'], e['name'], e['b1_only'], e['conf'], ppv1_str, e['rps250'], irs, ppv2_str, ind, reason))

print('\nGREEN Cat3: Pipeline only (no MW high): %d (Top10)' % len(cat3))
for e in cat3[:10]:
    name = e['name']
    if not name:
        c.execute("SELECT name FROM stock_basic WHERE stock_code=?", (e['code'],))
        sr = c.fetchone(); name = sr['name'] if sr else ''
    reason = next((s['reason'] for s in pipeline if s['code']==e['code']), '')
    mw_tag = '[MW]' if e['b1_only'] > 0 else ''
    print('  %s %-8s RPS=%d %s %s' % (e['code'], name, e['rps250'], mw_tag, reason))

print('\nPURPLE Cat4: MW High but pipeline rejected: %d' % len(cat4))
for e in cat4:
    rs = rs_dict.get(e['code'], {})
    issues = []
    irs = e['ind_rs']
    if e['code'] not in all_sector_stocks and (irs is None or irs < 75):
        issues.append('NoSector(IRS=%s)' % irs)
    if rs.get('rps250', 0) < 60:
        issues.append('RPS=%d<60' % rs.get('rps250', 0))
    print('  %s %-8s B1=%d %s %s' % (e['code'], e['name'], e['b1_only'], e['conf'], ' | '.join(issues)))

# Final
print('\n' + '=' * 80)
print('DECISION')
print('=' * 80)
print('Health=%d(%s) Sell=%d %s Pos=%d%% | Pipe=%d stocks | MW=%d signals' % (health, health_r, sell, regime, pos, len(pipeline), len(mw)))
print('BuyNow=%d WaitB2=%d PipeOnly=%d Rejected=%d' % (len(cat1), len(cat2), len(cat3), len(cat4)))

if cat1:
    t = cat1[0]
    print('\nBEST: %s %s B1=%d/%s Pos=%d%%' % (t['code'], t['name'], t['b1_only'], t['conf'], int(pos*0.72)))
elif cat2:
    t = cat2[0]
    print('\nWATCH: %s %s B1=%d/%s RPS=%d -> Wait B2, then Pos=%d%%' % (t['code'], t['name'], t['b1_only'], t['conf'], t['rps250'], int(pos*0.72)))
    if regime == 'Bear':
        print('WARNING: Bear market, MW backtest samples minimal, suggest light position or wait')

print('\nImprovement: v2(0 MW in pipe) -> v3(%d MW in pipe: %d BuyNow + %d WaitB2)' % (len(cat1)+len(cat2), len(cat1), len(cat2)))
print('Rules: Pipe=Position | Pipe=Sector | MW=Selection(B1>=40) | MW=Timing(B1+T1) | PP_V1=Bonus')

db.close()
