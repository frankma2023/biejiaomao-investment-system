"""
今日 MW 信号买入决策
━━━━━━━━━━━━━━━━━━━━
基于《MW信号全维度回测报告》投资决策规则
"""
import sqlite3
from collections import defaultdict

DB = 'D:/hanako/investment-system/data/lixinger.db'
db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
c = db.cursor()

TODAY = '2026-06-30'

print('=' * 80)
print(f'今日 MW 信号买入决策 · {TODAY}')
print('=' * 80)

# ── 1. 今日市场环境 ──
# 获取中证全指 MA50/MA200
c.execute("""
    SELECT close FROM index_daily_kline 
    WHERE stock_code='000985' AND date <= ? 
    ORDER BY date DESC LIMIT 200
""", (TODAY,))

closes = [r['close'] for r in c.fetchall()]
closes.reverse()

ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else 0
ma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else 0
ma50_20d_ago = sum(closes[-70:-20]) / 50 if len(closes) >= 70 else ma50
slope = (ma50 - ma50_20d_ago) / ma50_20d_ago if ma50_20d_ago > 0 else 0
last_close = closes[-1]

if slope > 0.005 and last_close > ma200:
    regime = '牛市'
elif slope < -0.005 and last_close < ma200:
    regime = '熊市'
else:
    regime = '震荡市'

print(f'\n当前市场环境: {regime}')
print(f'  中证全指: {last_close:.0f} | MA50: {ma50:.0f} | MA200: {ma200:.0f}')
print(f'  MA50斜率(20日): {slope*100:+.2f}%')
print(f'  价格在MA200上方: {"是" if last_close > ma200 else "否"}')

# ── 2. 今日 B1 信号 ──
c.execute("""
    SELECT * FROM mw_signal_daily 
    WHERE b1_date=? AND stock_code!='_sentinel_'
    ORDER BY score DESC
""", (TODAY,))
b1_signals = [dict(r) for r in c.fetchall()]
print(f'\n今日 B1 信号: {len(b1_signals)} 个')

# ── 3. 获取共现信号 ──
co_signals = {}
c.execute("""
    SELECT e.stock_code, e.signal_mask 
    FROM signal_events e
    JOIN mw_signal_daily m ON e.stock_code=m.stock_code AND e.date=m.b1_date
    WHERE m.b1_date=?
""", (TODAY,))
for r in c.fetchall():
    mask = r['signal_mask']
    co_signals[r['stock_code']] = {
        'pp_v1': bool(mask & (1<<3)),
        'pp_v2': bool(mask & (1<<4)),
        'bo_v2': bool(mask & (1<<5)),
    }

# ── 4. 获取个股RS ──
c.execute("SELECT stock_code, rps_20, rps_250 FROM stock_rs_daily WHERE date=?", (TODAY,))
rs_dict = {r['stock_code']: (r['rps_20'], r['rps_250']) for r in c.fetchall()}

# ── 5. 获取行业RS ──
c.execute("SELECT stock_code as code, rs_250 FROM index_rs_daily WHERE date=?", (TODAY,))
irs_dict = {r['code']: r['rs_250'] for r in c.fetchall()}

# ── 6. 获取日成交额 ──
c.execute("SELECT stock_code, amount FROM daily_kline WHERE date=?", (TODAY,))
amt_dict = {r['stock_code']: r['amount'] for r in c.fetchall()}

db.close()

# ═══════════════════ 决策 ═══════════════════
print(f'\n{"=" * 80}')
print('决策分析')
print('=' * 80)

print(f"""
决策规则（来自回测报告）：
  ① B1-only分数 < 40 → 放弃
  ② B1-only分数 ≥ 40 → 关注，次日开盘买入
  ③ 已有B2确认 → 加分（胜率+15-31pp）
  ④ 同日有PP_V1共现 → 加分（胜率+4pp）
  ⑤ 震荡市 > 牛市（当前: {regime}）""" + (f' ← 有利' if regime == '震荡市' else ''))

# ── 分类 ──
buy_now = []       # 有B2确认，可以立即买入
watch = []         # B1高分但无B2，等待确认
skip = []          # B1低分，放弃

for s in b1_signals:
    code = s['stock_code']
    name = s['stock_name']
    b1_score = s['score']  # 完整score（含B2部分如果存在）
    
    # 计算B1-only分
    b1_only = (s['score_h'] or 0) + (s['score_d'] or 0) + (s['score_c'] or 0) + \
              (s['score_i1'] or 0) + (s['score_i2'] or 0) + (s['score_sig'] or 0)
    
    has_b2 = s['b2_date'] is not None
    b1_conf = s['confidence']  # 已更新的v3.4置信度
    is_plus = s['is_plus'] == 1
    ppv1 = co_signals.get(code, {}).get('pp_v1', False)
    ppv2 = co_signals.get(code, {}).get('pp_v2', False)
    bov2 = co_signals.get(code, {}).get('bo_v2', False)
    
    rs = rs_dict.get(code, (None, None))
    rps250 = rs[1]
    
    irs = irs_dict.get(s.get('ind_code'), None) if s.get('ind_code') else None
    
    amt = amt_dict.get(code, 0) or 0
    
    entry = {
        'code': code, 'name': name,
        'b1_only': b1_only, 'full_score': b1_score,
        'b1_conf': b1_conf, 'has_b2': has_b2, 'is_plus': is_plus,
        'ppv1': ppv1, 'ppv2': ppv2, 'bov2': bov2,
        'rps250': rps250, 'irs250': irs,
        'amount_million': amt / 10000,  # 万元
        'decline_pct': s['decline_pct'],
        'b1_return': s['b1_return_pct'],
        'ind_name': s.get('ind_name', ''),
    }
    
    if b1_only < 40:
        skip.append(entry)
    elif has_b2:
        buy_now.append(entry)
    else:
        watch.append(entry)

# ── 排序 ──
buy_now.sort(key=lambda x: (x['is_plus'], x['b1_only'], x.get('rps250') or 0), reverse=True)
watch.sort(key=lambda x: (x['b1_only'], x.get('rps250') or 0), reverse=True)

# ── 输出 ──
print(f'\n{"─" * 80}')
print(f'🔴 立即买入（B1≥40 + B2已确认）: {len(buy_now)} 只')
print(f'{"─" * 80}')
if buy_now:
    print(f'  {"代码":<8s} {"名称":<10s} {"B1-only分":>9s} {"置信度":<6s} {"PLUS":<6s} {"PPV1":<6s} {"RPS250":>7s} {"行业RS":>7s} {"成交(万)":>10s}')
    print(f'  {"─" * 75}')
    for e in buy_now:
        plus = '✦' if e['is_plus'] else ''
        ppv1_str = '✅' if e['ppv1'] else '—'
        rs_str = f'{e["rps250"]}' if e["rps250"] else '—'
        irs_str = f'{e["irs250"]}' if e["irs250"] else '—'
        print(f'  {e["code"]:<8s} {e["name"]:<10s} {e["b1_only"]:>8d}  {e["b1_conf"]:<6s} {plus:<6s} {ppv1_str:<6s} {rs_str:>7s} {irs_str:>7s} {e["amount_million"]:>9.0f}')

print(f'\n{"─" * 80}')
print(f'🟡 关注等待B2（B1≥40，B2未出）: {len(watch)} 只')
print(f'{"─" * 80}')
if watch:
    print(f'  {"代码":<8s} {"名称":<10s} {"B1-only分":>9s} {"置信度":<6s} {"PPV1":<6s} {"RPS250":>7s} {"行业RS":>7s} {"成交(万)":>10s} {"行业":<15s}')
    print(f'  {"─" * 90}')
    for e in watch:
        ppv1_str = '✅' if e['ppv1'] else '—'
        rs_str = f'{e["rps250"]}' if e["rps250"] else '—'
        irs_str = f'{e["irs250"]}' if e["irs250"] else '—'
        print(f'  {e["code"]:<8s} {e["name"]:<10s} {e["b1_only"]:>8d}  {e["b1_conf"]:<6s} {ppv1_str:<6s} {rs_str:>7s} {irs_str:>7s} {e["amount_million"]:>9.0f}  {(e.get("ind_name") or "")[:15]:<15s}')

print(f'\n{"─" * 80}')
print(f'⚫ 放弃（B1<40，胜率太低）: {len(skip)} 只（省略）')
print(f'{"─" * 80}')

# ── 最终建议 ──
print(f'\n{"=" * 80}')
print(f'最终建议')
print(f'=' * 80)

if buy_now:
    top = buy_now[0]
    print(f'\n🏆 首选: {top["code"]} {top["name"]}')
    print(f'   B1-only分数: {top["b1_only"]}/75 ({top["b1_conf"]}置信)')
    if top['is_plus']:
        print(f'   ✦ PLUS信号！胜率预期91%，H20收益预期+28%')
    print(f'   PP_V1共现: {"有" if top["ppv1"] else "无"}')
    print(f'   个股RPS250: {top["rps250"]}')
    print(f'   行业RS250: {top["irs250"]}')
    print(f'   建议操作: 明日(T+1)开盘买入，止损-7%，目标H20~H60')

if watch:
    print(f'\n📋 观察池（等B2确认后买入）:')
    for e in watch[:5]:
        rec = ' ⭐优先' if e['b1_only'] >= 55 else (' 👍推荐' if e['b1_only'] >= 50 else '')
        ppv1_note = ' +PP_V1加持' if e['ppv1'] else ''
        print(f'   {e["code"]} {e["name"]:<8s} B1={e["b1_only"]}分 RPS={e["rps250"]}{rec}{ppv1_note}')

print(f'\n当前环境: {regime}', '(震荡市是MW信号最优环境 ✅)' if regime == '震荡市' else '')
print(f'决策规则: B1≥40买入, B2确认持有, 不在B2日追买')
