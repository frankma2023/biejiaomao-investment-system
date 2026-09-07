# -*- coding: utf-8 -*-
"""stock-health-report · 本地数据采集管道
用法: python collect_data.py 600309
输出: 结构化 Markdown 摘要（agent 一次性读入，作为确定性数据底座）
原则: 只读本地库 + 本地 API；外部数据由 agent 用 web_search 补充（行业/公告/业务占比）
"""
import sys, io, os, json, datetime, requests
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


# ═══════════════════════════════════════════
# --detail 扩展（v1.1：财务评估六卡 + 技术指标全量）
# ═══════════════════════════════════════════
def _detail(code):
    db2 = get_db()
    print('\n══════ DETAIL 扩展 ══════')

    # A. 技术指标（本地 K 线计算）
    rows2 = db2.execute("SELECT date, open, high, low, close, volume, amount FROM daily_kline WHERE stock_code=? ORDER BY date", (code,)).fetchall()
    if len(rows2) > 260:
        rows2 = rows2[-260:]
    closes = [r['close'] for r in rows2]
    highs = [r['high'] for r in rows2]
    lows = [r['low'] for r in rows2]
    vols = [r['volume'] or 0 for r in rows2]
    dates = [r['date'] for r in rows2]
    cur = closes[-1]

    def ma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None

    print('\n## A. 均线系统')
    for n in (5, 10, 20, 30, 60, 120, 250):
        v = ma(n)
        if v:
            rel = (cur / v - 1) * 100
            print(f'- MA{n}: {v:.2f}（现价{"上" if cur >= v else "下"}方 {rel:+.1f}%）')
    mas = [ma(n) for n in (5, 10, 20, 30, 60, 120, 250)]
    order = '多头排列' if all(mas[i] > mas[i+1] for i in range(len(mas)-1) if mas[i] and mas[i+1]) else ('空头排列' if all(mas[i] < mas[i+1] for i in range(len(mas)-1) if mas[i] and mas[i+1]) else '缠绕/中性')
    print(f'- 排列状态: {order}')

    # MACD(12,26,9)
    def ema_series(vals, n):
        k = 2 / (n + 1)
        out = [vals[0]]
        for v in vals[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out
    if len(closes) > 40:
        e12 = ema_series(closes, 12); e26 = ema_series(closes, 26)
        dif = [a - b for a, b in zip(e12, e26)]
        dea = ema_series(dif, 9)
        hist = [(dif[i] - dea[i]) * 2 for i in range(len(dif))]
        d0, d1 = dif[-1], dif[-2]
        cross = '金叉' if d1 <= dea[-2] and d0 > dea[-1] else ('死叉' if d1 >= dea[-2] and d0 < dea[-1] else '无新交叉')
        print(f'\n## B. MACD(12,26,9)')
        print(f'- DIF {d0:.3f} / DEA {dea[-1]:.3f} / 柱 {hist[-1]:.3f}（{"红柱" if hist[-1]>0 else "绿柱"}）｜ {cross} ｜ 零轴{"上" if d0>0 else "下"}')

    # KDJ(9,3,3)
    if len(closes) > 20:
        k_ = d_ = 50.0
        k_arr, d_arr = [], []
        for i in range(len(closes)):
            lo9 = min(lows[max(0,i-8):i+1]); hi9 = max(highs[max(0,i-8):i+1])
            rsv = (closes[i] - lo9) / (hi9 - lo9) * 100 if hi9 > lo9 else 50
            k_ = 2/3 * k_ + 1/3 * rsv
            d_ = 2/3 * d_ + 1/3 * k_
            k_arr.append(k_); d_arr.append(d_)
        j_ = 3 * k_arr[-1] - 2 * d_arr[-1]
        j_prev = 3 * k_arr[-2] - 2 * d_arr[-2]
        print(f'\n## C. KDJ(9,3,3)')
        print(f'- K {k_arr[-1]:.1f} / D {d_arr[-1]:.1f} / J {j_:.1f}（前日 J {j_prev:.1f}）｜ {"超买(J>90)" if j_>90 else "偏热(J>80)" if j_>80 else "中性" if j_>20 else "超卖"}')
        print(f'- 金叉状态: {"KDJ金叉" if k_arr[-1]>d_arr[-1] and k_arr[-2]<=d_arr[-2] else ("KDJ死叉" if k_arr[-1]<d_arr[-1] and k_arr[-2]>=d_arr[-2] else "无新交叉")}')

    # RSI(6/12/24)
    def rsi(vals, n):
        g = l = 0.0
        for i in range(len(vals)-n, len(vals)):
            chg = vals[i] - vals[i-1]
            if chg > 0: g += chg
            else: l -= chg
        if l == 0: return 100.0
        rs = (g/n) / (l/n)
        return 100 - 100/(1+rs)
    print(f'\n## D. RSI')
    print(f'- RSI6 {rsi(closes,6):.1f} / RSI12 {rsi(closes,12):.1f} / RSI24 {rsi(closes,24):.1f}')

    # BOLL(20,2)
    if len(closes) > 20:
        import statistics
        w = closes[-20:]
        mid = sum(w)/20
        sd = statistics.pstdev(w)
        upb, lob = mid + 2*sd, mid - 2*sd
        pos_b = '上轨外' if cur > upb else ('下轨外' if cur < lob else ('上轨区' if cur > mid + sd else ('下轨区' if cur < mid - sd else '中轨区')))
        print(f'\n## E. BOLL(20,2)')
        print(f'- 上轨 {upb:.2f} / 中轨 {mid:.2f} / 下轨 {lob:.2f} ｜ 现价 {cur:.2f} 在{pos_b}（带宽 {2*sd:.2f}）')

    # 量能
    v20 = sum(vols[-20:]) / 20
    v5 = sum(vols[-5:]) / 5
    print(f'\n## F. 量能')
    print(f'- 5日均量 {v5/1e4:.0f}万手 / 20日均量 {v20/1e4:.0f}万手 / 量比(5vs20) {v5/v20:.2f}')
    up_days = sum(1 for i in range(-10, 0) if closes[i] > closes[i-1])
    print(f'- 近10日涨跌: {up_days}涨{10-up_days}跌 ｜ 近10日量额: ' + ' '.join(f'{r["date"][5:]} {r["amount"]/1e8 if r["amount"] else 0:.0f}亿' for r in rows2[-10:]))

    # 支撑压力
    hi250, lo250 = max(closes), min(closes)
    hi60, lo60 = max(closes[-60:]), min(closes[-60:])
    print(f'\n## G. 支撑压力')
    print(f'- 250日: 高 {hi250:.2f} / 低 {lo250:.2f} ｜ 60日: 高 {hi60:.2f} / 低 {lo60:.2f}')
    import math
    fib = [hi250 - (hi250-lo250)*r for r in (0.236, 0.382, 0.5, 0.618)]
    print(f'- 斐波那契回撤位: ' + ' / '.join(f'{v:.2f}' for v in fib))

    # 近 15 日 K 线明细
    print(f'\n## H. 近15日K线')
    print('日期 开 高 低 收 涨跌% 成交(亿)')
    for r in rows2[-15:]:
        chg = (r['close']/rows2[rows2.index(r)-1]['close']-1)*100 if rows2.index(r) > 0 else 0
        print(f'{r["date"]} {r["open"]:.2f} {r["high"]:.2f} {r["low"]:.2f} {r["close"]:.2f} {chg:+.2f} {r["amount"]/1e8 if r["amount"] else 0:.1f}')

    db2.close()

    # 财务评估六卡（API）
    try:
        r = requests.get(f'http://localhost:8788/api/stock-analysis?code={code}', timeout=60).json()
        dcf = r.get('dcf') or {}
        comps = r.get('comps') or {}
        earnings = r.get('earnings') or {}
        model = r.get('model') or {}
        quality = r.get('quality') or {}
        cashflow = r.get('cashflow') or {}
        print('\n## I. DCF')
        if dcf.get('error'):
            print(f'- DCF: {dcf["error"]}')
        else:
            print(f'- 目标价 {dcf.get("target_price")}（现价对比 {dcf.get("upside_pct")}%）｜ 基准 {dcf.get("base_basis")} | WACC {dcf.get("wacc")} 税率 {dcf.get("tax_rate")} 终值法 {dcf.get("tv_method")}')
            print(f'- EV {dcf.get("enterprise_value")} 亿 → 股权 {dcf.get("equity_value")} 亿 ｜ 敏感性: ' + ' '.join(f'{s["label"]}={s["target_price"]}' for s in (dcf.get("sensitivity") or [])[:6]))
        print('\n## J. Comps（TTM 口径）')
        if comps.get('error'):
            print(f'- {comps["error"]}')
        else:
            print(f'- 行业 {comps.get("industry")}（{comps.get("peer_count")}家可比）｜ 中位 PE {comps.get("median_multiples",{}).get("pe")} / PB {comps.get("median_multiples",{}).get("pb")} / PS {comps.get("median_multiples",{}).get("ps")}')
            print(f'- 估值: {comps.get("implied_valuations")} ｜ 均值 {comps.get("average_valuation")} 亿')
            peers = comps.get("peers") or []
            if peers:
                print('- 可比明细: ' + ' | '.join(f'{pp["name"]} PE{pp.get("pe")} PB{pp.get("pb")} 增长{pp.get("revenue_growth")}%' for pp in peers[:6]))
        print('\n## K. 盈利趋势（近8季）')
        qts = earnings.get('quarters') or []
        for qq in qts[-8:]:
            print(f'- {qq.get("report_date")}: 营收 {qq.get("revenue")/1e8:.1f}亿(yoy {qq.get("revenue_yoy")}%) 净利 {qq.get("net_profit")/1e8:.1f}亿(yoy {qq.get("net_profit_yoy")}%) 毛利率 {qq.get("gross_margin")}')
        print('\n## L. 三表预测（model）')
        projs = model.get('projections') or []
        for pp in projs:
            i = pp.get('income_statement') or {}
            print(f'- {pp.get("year")}: 营收 {i.get("revenue",0)/1e8:.0f}亿 净利 {i.get("net_income",0)/1e8:.1f}亿（基准 {model.get("base_basis")}）')
        print('\n## M. 盈利质量 + 现金流')
        lt = quality.get('latest') or {}
        if lt:
            print(f'- 扣非占比 {lt.get("adj_ratio")}%（{lt.get("report_date")}）现金含量 {lt.get("fcf_np_ratio_annual")}x ｜ 警示: {quality.get("alerts") or "无"}')
        cf = cashflow.get('fcf_latest_cum')
        if cf is not None:
            print(f'- FCF 累计 {cf} 亿 | 年度: ' + ' | '.join(f'{a.get("year")}年 OCF {a.get("ocf")} FCF {a.get("fcf")}' for a in (cashflow.get("annual") or [])[-4:]))
    except Exception as e:
        print('财务 API err', str(e)[:120])

if '--detail' in sys.argv:
    _detail(CODE)
