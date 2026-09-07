# -*- coding: utf-8 -*-
"""stock-health-report · 本地数据采集管道
用法: python collect_data.py 600309
输出: 结构化 Markdown 摘要（agent 一次性读入，作为确定性数据底座）
原则: 只读本地库 + 本地 API；外部数据由 agent 用 web_search 补充（行业/公告/业务占比）
"""
import sys, io, os, json, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'src'))
if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, r'D:\hanako\investment-system\src')

from analysis.financial import get_db

CODE = sys.argv[1] if len(sys.argv) > 1 else '600309'
NAME_HINT = ''

db = get_db()

def q(sql, *a):
    return db.execute(sql, a).fetchall()

def one(sql, *a):
    return db.execute(sql, a).fetchone()

def pct(v):
    return f'{v*100:.2f}%' if v is not None else '—'

# ── 基本信息 ──
info = one("SELECT name, market, listing_status FROM stock_basic WHERE stock_code=?", CODE)
name = info['name'] if info and info['name'] else CODE
print(f'# {name} ({CODE}) 本地数据快照')
print(f'> 生成时间：{datetime.datetime.now():%Y-%m-%d %H:%M}')

# ── 1. 股价与位置 ──
try:
    kl = q("SELECT date, close, amount, volume FROM daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 260", CODE)
    kl = list(reversed(kl))
    cur = kl[-1]['close']
    seg = [r['close'] for r in kl[-250:]]
    hi, lo = max(seg), min(seg)
    pos = (cur - lo) / (hi - lo) * 100 if hi > lo else 50
    dd = (cur / hi - 1) * 100
    ma5 = sum(seg[-5:]) / 5
    ma20 = sum(seg[-20:]) / 20
    ma60 = sum(seg[-60:]) / 60
    print(f'\n## 1. 股价结构（{kl[-1]["date"]}）')
    print(f'- 现价 {cur:.2f} ｜ 250日区间 {lo:.2f}~{hi:.2f} ｜ 位置 {pos:.0f}% ｜ 距高点 {dd:.1f}%')
    print(f'- MA5 {ma5:.2f} / MA20 {ma20:.2f} / MA60 {ma60:.2f}（多空：{"多头" if ma5 > ma20 > ma60 else "空头" if ma5 < ma20 < ma60 else "缠绕"}）')
    print(f'- 近10日量额: ' + ' '.join(f'{r["date"][5:]}:{r["amount"]/1e8:.0f}亿' for r in kl[-10:]))
except Exception as e:
    print('股价 err', e)

# ── 2. 否决筛查 ──
print('\n## 2. 否决筛查')
try:
    st = one("SELECT listing_status FROM stock_basic WHERE stock_code=?", CODE)
    print(f'- 上市状态: {st["listing_status"] if st else "未知"}')
    print(f'- 名称: {name}（{"⚠️ 疑似 ST" if "ST" in name else "非 ST"}）')
except Exception:
    print('- ST: 表无字段')
# 质押（akshare 外部接口由 agent 处理，本地无）——标注
# 两融
try:
    mg = q("SELECT date, financing_balance, securities_balance FROM daily_margin_history WHERE stock_code=? ORDER BY date DESC LIMIT 3", CODE)
    if mg:
        cur_m = mg[0]
        chg = (cur_m['financing_balance'] - mg[-1]['financing_balance']) / mg[-1]['financing_balance'] * 100 if mg[-1]['financing_balance'] else 0
        print(f'- 融资余额 {cur_m["financing_balance"]/1e8:.1f}亿（{cur_m["date"]}）｜ 近{len(mg)}日变化 {chg:+.1f}%')
except Exception:
    print('- 两融: 无数据')

# ── 3. 股东人数 ──
print('\n## 3. 股东人数（筹码 proxy）')
try:
    shn = q("SELECT date, total FROM shareholders_num_daily WHERE stock_code=? AND total IS NOT NULL ORDER BY date DESC LIMIT 6", CODE)
    for r in reversed(shn):
        print(f'- {r["date"]}: {r["total"]/1e4:.1f}万户')
    if len(shn) >= 2:
        d = (shn[0]['total'] / shn[-1]['total'] - 1) * 100
        print(f'- 集中度变化（近{len(shn)}期）: {d:+.1f}%（{"集中" if d < 0 else "分散"}）')
except Exception as e:
    print('股东人数 err', str(e)[:60])

# ── 4. 估值（10 年分位自算 3/5/10） ──
print('\n## 4. 估值（fundamental_indicator 历史分位）')
try:
    val_map = {}
    rows = q("SELECT metric_code, date, value FROM fundamental_indicator WHERE stock_code=? AND metric_code IN ('pe_ttm','pb','ps_ttm','dyr') AND value IS NOT NULL ORDER BY date", CODE)
    for r in rows:
        val_map.setdefault(r['metric_code'], []).append(r['value'])
    labels = {'pe_ttm': 'PE_TTM', 'pb': 'PB', 'ps_ttm': 'PS_TTM', 'dyr': '股息率'}
    for mc, lab in labels.items():
        vals = val_map.get(mc)
        if vals and len(vals) > 100:
            cur_v = vals[-1]
            def pctl(n_days):
                w = vals[-n_days:]
                if len(w) < 60:
                    return None
                return sum(1 for v in w if v <= cur_v) / len(w) * 100
            n10 = sum(1 for v in vals if v <= cur_v) / len(vals) * 100
            y = len(vals) // 250
            print(f'- {lab}: 当前 {cur_v:.2f}（{y}年窗分位 {n10:.0f}%）｜ 3年 {pctl(750):.0f}% ｜ 5年 {pctl(1250):.0f}% ｜ 历史 min {min(vals):.2f} / max {max(vals):.2f} / 均值 {sum(vals)/len(vals):.2f}')
        else:
            print(f'- {lab}: 数据不足')
except Exception as e:
    print('估值 err', str(e)[:80])

# ── 5. 财务季/年双轨 ──
print('\n## 5. 财务（季度单季 + TTM）')
try:
    qq = q("SELECT report_date, revenue_single, net_profit_single, gross_margin_single FROM stock_financials_quarterly WHERE stock_code=? ORDER BY report_date DESC LIMIT 12", CODE)
    qq = list(reversed(qq))
    for r in qq[-8:]:
        print(f'- {r["report_date"]}: 营收 {r["revenue_single"]/1e8:.1f}亿 净利 {r["net_profit_single"]/1e8:.1f}亿 毛利率 {r["gross_margin_single"] if r["gross_margin_single"] else "—"}')
    if len(qq) >= 8:
        r4 = sum(x['revenue_single'] for x in qq[-4:]) / 1e8
        r8 = sum(x['revenue_single'] for x in qq[-8:-4]) / 1e8
        n4 = sum(x['net_profit_single'] for x in qq[-4:]) / 1e8
        n8 = sum(x['net_profit_single'] for x in qq[-8:-4]) / 1e8
        print(f'- TTM 营收 {r4:.1f}亿（滚动同比 {(r4/r8-1)*100:+.1f}%）｜ TTM 净利 {n4:.1f}亿（{(n4/n8-1)*100:+.1f}%）')
        # 二阶导：最近季增速 vs 前季增速
        def g8(vals):
            return [ (vals[i]-vals[i-1])/vals[i-1]*100 for i in range(1, len(vals)) ]
        revs = [x['revenue_single']/1e8 for x in qq[-6:]]
        yoy_g = []
        for i in range(4, len(revs)):
            yoy_g.append((revs[i] - revs[i-4]) / revs[i-4] * 100)
        if len(yoy_g) >= 2:
            acc = yoy_g[-1] - yoy_g[-2]
            print(f'- 营收滚动同比序列: {[round(g,1) for g in yoy_g]} → 二阶导(加速度) {acc:+.1f}pp（{"加速" if acc > 0 else "减速"}）')
except Exception as e:
    print('财务 err', str(e)[:80])

print('\n## 6. 年度财务')
try:
    ann = q("SELECT report_date, revenue, revenue_yoy, net_profit, net_profit_yoy, gross_margin, roe FROM stock_financials_annual WHERE stock_code=? ORDER BY report_date DESC LIMIT 6", CODE)
    for r in reversed(ann):
        print(f'- {r["report_date"][:4]}: 营收 {r["revenue"]/1e8:.0f}亿(yoy {r["revenue_yoy"]}%) 净利 {r["net_profit"]/1e8:.1f}亿(yoy {r["net_profit_yoy"]}%) 毛利率 {r["gross_margin"]} ROE {r["roe"]}')
except Exception as e:
    print('年度 err', str(e)[:80])

# ── 7. RS 强度 ──
print('\n## 7. RS 强度')
try:
    rs = one("SELECT date, rps_20, rps_60, rps_120, rps_250 FROM stock_rs_daily WHERE stock_code=? ORDER BY date DESC LIMIT 1", CODE)
    if rs:
        print(f'- RPS20 {rs["rps_20"]} / RPS60 {rs["rps_60"]} / RPS120 {rs["rps_120"]} / RPS250 {rs["rps_250"]}（{rs["date"]}）')
except Exception:
    pass
try:
    ind = q('SELECT ir.stock_code, ir.date, ir.rs_20, ir.rs_60, ir.rs_120 FROM index_rs_daily ir WHERE ir.stock_code IN (SELECT ind_code FROM mw_signal_daily WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1) ORDER BY ir.date DESC LIMIT 1', CODE)
    if ind:
        r = ind[0]
        print(f'- 所属行业RS({r["stock_code"]}): RS20 {r["rs_20"]} / RS60 {r["rs_60"]} / RS120 {r["rs_120"]}')
except Exception:
    pass

# ── 8. 缠论/信号 ──
print('\n## 8. 信号（pattern-scan 引擎表）')
try:
    mw = q("SELECT scan_date, b1_date, tech_score_v4, h_date, l_date FROM mw_signal_daily WHERE stock_code=? ORDER BY scan_date DESC LIMIT 2", CODE)
    for r in mw:
        print(f'- MW: scan {r["scan_date"]} B1 {r["b1_date"]} TS {r["tech_score_v4"]} H {r["h_date"]} L {r["l_date"]}')
except Exception:
    pass
# chanlun 扫描
try:
    cl = q("SELECT scan_date, latest_bi_dir, divergence_count, latest_div_type, trade_signal_count, latest_trade_side, resonance_strength FROM chanlun_scan_daily WHERE stock_code=? ORDER BY scan_date DESC LIMIT 2", CODE)
    for r in cl:
        print(f'- 缠论扫描 {r["scan_date"]}: 笔向 {r["latest_bi_dir"]} 背驰 {r["divergence_count"]}({r["latest_div_type"]}) 信号 {r["trade_signal_count"]}({r["latest_trade_side"]}) 共振 {r["resonance_strength"]}')
except Exception:
    pass

db.close()
print('\n> 注：确定性数据以上；行业逻辑/业务占比/公告/前瞻需 agent web_search 补充；CYQ 筹码/主力资金本地缺（proxy 见 §3）')
