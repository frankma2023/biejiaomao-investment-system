#!/usr/bin/env python3
"""按 market-daily-review skill 模板生成 A股每日复盘 Markdown 报告。
数据源：本地 lixinger.db（理杏仁回填），替代 Pandadata API。
用法：python scripts/gen_daily_review_skill.py [date]
"""
import sqlite3, os, sys, time
from datetime import datetime

_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(_PROJ, 'data', 'lixinger.db')
OUT_DIR = os.path.join(_PROJ, 'docs', 'daily-reviews')

IDX_NAMES = {
    '000001': '上证综指', '399001': '深证成指', '000300': '沪深300',
    '000905': '中证500', '000688': '科创50', '399006': '创业板指',
}

def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    if not date:
        date = db.execute('SELECT MAX(date) FROM daily_kline').fetchone()[0]
    t0 = time.time()
    print(f'📊 生成 {date} 复盘...')

    # ── 1. 指数概览与估值 ──
    idx_rows = []
    for code, name in IDX_NAMES.items():
        k = db.execute('SELECT close, change FROM index_daily_kline WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1', (code, date)).fetchone()
        if not k: continue
        # change 仅 1% 粒度，用 close/前收盘反推
        prev = db.execute('SELECT close FROM index_daily_kline WHERE stock_code=? AND date<? ORDER BY date DESC LIMIT 1', (code, date)).fetchone()
        chg = round((k['close'] / prev['close'] - 1) * 100, 2) if prev and prev['close'] else None
        f = db.execute('SELECT pe_ttm, pe_ttm_pct, pb, pb_pct FROM index_fundamental_daily WHERE stock_code=? AND date=?', (code, date)).fetchone()
        idx_rows.append({
            'name': name, 'close': k['close'], 'chg': chg,
            'pe': round(f['pe_ttm'], 1) if f and f['pe_ttm'] else None,
            'pe_pct': round(f['pe_ttm_pct'] * 100, 1) if f and f['pe_ttm_pct'] is not None else None,
            'pb': round(f['pb'], 2) if f and f['pb'] else None,
            'pb_pct': round(f['pb_pct'] * 100, 1) if f and f['pb_pct'] is not None else None,
        })

    # ── 2. 市场宽度与情绪 ──
    bd = db.execute('''SELECT COUNT(*) t,
        SUM(CASE WHEN change_pct>0 THEN 1 ELSE 0 END) up,
        SUM(CASE WHEN change_pct<0 THEN 1 ELSE 0 END) dn,
        SUM(amount) amt FROM daily_kline WHERE date=?''', (date,)).fetchone()
    lt = db.execute('''SELECT
        SUM(CASE WHEN change_pct>=0.099 AND (stock_code LIKE '60%' OR stock_code LIKE '00%') THEN 1 ELSE 0 END) up10,
        SUM(CASE WHEN change_pct>=0.199 AND (stock_code LIKE '30%' OR stock_code LIKE '68%') THEN 1 ELSE 0 END) up20,
        SUM(CASE WHEN change_pct<=-0.099 AND (stock_code LIKE '60%' OR stock_code LIKE '00%') THEN 1 ELSE 0 END) dn10,
        SUM(CASE WHEN change_pct<=-0.199 AND (stock_code LIKE '30%' OR stock_code LIKE '68%') THEN 1 ELSE 0 END) dn20
        FROM daily_kline WHERE date=?''', (date,)).fetchone()

    # ── 3. 行业热点（stock_industry + daily_kline 聚合） ──
    ind_rows = db.execute('''
        SELECT i.industry_name, COUNT(*) cnt,
               AVG(k.change_pct) avg_chg,
               SUM(CASE WHEN k.change_pct>0 THEN 1 ELSE 0 END) up_cnt
        FROM (SELECT DISTINCT stock_code, industry_name FROM stock_industry
              WHERE industry_name IS NOT NULL AND industry_name != '') i
        JOIN daily_kline k ON i.stock_code = k.stock_code AND k.date=?
        GROUP BY i.industry_name HAVING cnt >= 10
        ORDER BY avg_chg DESC''', (date,)).fetchall()

    # ── 4. 龙虎榜 ──
    lhb = db.execute('SELECT * FROM daily_review_lhb WHERE date=?', (date,)).fetchall()
    lhb_buy = sorted([r for r in lhb if r['net_amount'] > 0], key=lambda x: x['net_amount'], reverse=True)
    lhb_sell = sorted([r for r in lhb if r['net_amount'] <= 0], key=lambda x: x['net_amount'])[:10]

    # ── 5. 大宗交易（T+1，取最新可用日） ──
    bt_date = db.execute('SELECT MAX(date) FROM daily_review_block_trade WHERE date<=?', (date,)).fetchone()[0]
    bt = db.execute('''SELECT * FROM daily_review_block_trade WHERE date=?
        ORDER BY trading_amount DESC LIMIT 10''', (bt_date,)).fetchall()

    # ── 6. 两融（T+1，取最新可用日） ──
    mg_date = db.execute('SELECT MAX(date) FROM daily_margin_history WHERE date<=?', (date,)).fetchone()[0]
    mg = db.execute('SELECT SUM(financing_balance+securities_balance) bal, SUM(net_purchase) net FROM daily_margin_history WHERE date=?', (mg_date,)).fetchone()
    mg_prev = db.execute('SELECT SUM(financing_balance+securities_balance) bal FROM daily_margin_history WHERE date=?', (
        db.execute('SELECT MAX(date) FROM daily_margin_history WHERE date<?', (mg_date,)).fetchone()[0],)).fetchone()

    ge5_cnt = db.execute('SELECT SUM(CASE WHEN change_pct>=0.05 THEN 1 ELSE 0 END) c FROM daily_kline WHERE date=?', (date,)).fetchone()[0] or 0
    le5_cnt = db.execute('SELECT SUM(CASE WHEN change_pct<=-0.05 THEN 1 ELSE 0 END) c FROM daily_kline WHERE date=?', (date,)).fetchone()[0] or 0

    db.close()

    # ── 渲染 Markdown ──
    def pct(v):
        # 指数 chg 为百分数格式（1.47 = 1.47%）
        if v is None: return '—'
        return f'{v:+.2f}%' if abs(v) < 10 else f'{v:+.1f}%'

    def pct_x(v):
        # 行业 change_pct 为小数格式（0.0378 = 3.78%），×100 转百分数
        if v is None: return '—'
        v = v * 100
        return f'{v:+.2f}%' if abs(v) < 10 else f'{v:+.1f}%'

    up_total = (lt['up10'] or 0) + (lt['up20'] or 0)
    dn_total = (lt['dn10'] or 0) + (lt['dn20'] or 0)
    mg_chg = ''
    if mg and mg['bal'] and mg_prev and mg_prev['bal']:
        mg_chg = f"（{(mg['bal']-mg_prev['bal'])/1e8:+.0f}亿，{(mg['bal']/mg_prev['bal']-1)*100:+.2f}%）"

    idx_summary = '；'.join(f"{r['name']} {pct(r['chg'])}" for r in idx_rows[:3])
    lines = []
    lines.append(f'# A股每日收盘复盘（{date}）')
    lines.append('')
    lines.append(f'> 数据来源：本地 SQLite（理杏仁数据回填，替代 Pandadata API）。报告生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}。除特别说明外，行情数据日为 {date}。')
    lines.append('')
    lines.append('## 摘要')
    lines.append('')
    lines.append(f'- 指数与成交：{idx_summary}；全市场成交额 {(bd["amt"] or 0)/1e8:.0f} 亿')
    lines.append(f'- 市场宽度：上涨 {bd["up"]} 家 / 下跌 {bd["dn"]} 家，涨跌比 {bd["up"]/(bd["dn"] or 1):.2f}；涨停 {up_total} 家（10%口径 {lt["up10"] or 0}，20%口径 {lt["up20"] or 0}），跌停 {dn_total} 家')
    top_ind = ind_rows[0] if ind_rows else None
    lines.append(f'- 热点结构：行业最强 {top_ind["industry_name"] if top_ind else "—"}（均值 {pct_x(top_ind["avg_chg"]) if top_ind else "—"}），行业普涨家数 {len([r for r in ind_rows if r["avg_chg"]>0])}/{len(ind_rows)}')
    lines.append(f'- 资金与异动：龙虎榜 {len(lhb)} 条；大宗交易 {len(bt)} 条（数据日 {bt_date}）；两融余额 {mg["bal"]/1e8:.0f} 亿（数据日 {mg_date}）{mg_chg}')
    lines.append('')
    lines.append('## 1. 指数概览与估值')
    lines.append('')
    lines.append('| 指数 | 收盘点位 | 涨跌幅 | PE(TTM) | PE分位 | PB | PB分位 |')
    lines.append('| --- | ---: | ---: | ---: | ---: | ---: | ---: |')
    for r in idx_rows:
        lines.append(f'| {r["name"]} | {r["close"]:.2f} | {pct(r["chg"])} | {r["pe"] if r["pe"] is not None else "—"} | {r["pe_pct"] if r["pe_pct"] is not None else "—"}% | {r["pb"] if r["pb"] is not None else "—"} | {r["pb_pct"] if r["pb_pct"] is not None else "—"}% |')
    up_cnt = len([r for r in idx_rows if (r['chg'] or 0) > 0])
    lines.append('')
    lines.append(f'要点：{up_cnt}/{len(idx_rows)} 个主要指数收涨。')
    lines.append('')
    lines.append('## 2. 市场宽度与情绪')
    lines.append('')
    lines.append('| 指标 | 数值 | 口径 |')
    lines.append('| --- | ---: | --- |')
    lines.append(f'| 上涨家数 | {bd["up"]} | 全A（含北交所，{bd["t"]} 只有行情） |')
    lines.append(f'| 下跌家数 | {bd["dn"]} | 全A |')
    lines.append(f'| 涨停家数 | {up_total} | 不含ST/新股一字板；10%口径{lt["up10"] or 0} + 20%口径{lt["up20"] or 0} |')
    lines.append(f'| 跌停家数 | {dn_total} | 同上 |')
    lines.append(f'| 全市场成交额 | {(bd["amt"] or 0)/1e8:.0f} 亿 | 全A |')
    lines.append('')
    lines.append(f'情绪观察：涨跌比 {bd["up"]/(bd["dn"] or 1):.2f}，涨停/跌停比 {up_total/max(dn_total,1):.1f}，市场呈{"普涨" if bd["up"]>bd["dn"]*2 else "分化" if bd["up"]>bd["dn"] else "普跌"}格局。')
    lines.append('')
    lines.append('## 3. 行业与概念热点')
    lines.append('')
    lines.append('### 行业表现（按个股均值聚合，≥10只有效）')
    lines.append('')
    lines.append('| 排名 | 行业 | 涨跌幅均值 | 上涨占比 | 有效样本 |')
    lines.append('| ---: | --- | ---: | ---: | ---: |')
    for i, r in enumerate(ind_rows[:10], 1):
        lines.append(f'| {i} | {r["industry_name"]} | {pct_x(r["avg_chg"])} | {r["up_cnt"]/r["cnt"]*100:.0f}% | {r["cnt"]} |')
    if len(ind_rows) > 10:
        lines.append(f'| … | 共 {len(ind_rows)} 个行业 | | | |')
    lines.append('')
    lines.append('结构观察：' + ('行业普涨，赚钱效应较强。' if len([r for r in ind_rows if r['avg_chg']>0])/len(ind_rows) > 0.6 else '行业分化明显。'))
    lines.append('')
    lines.append('## 4. 龙虎榜与大宗交易')
    lines.append('')
    lines.append('### 龙虎榜')
    lines.append('')
    if lhb:
        lines.append('| 股票 | 上榜原因 | 净额 | 数据日 |')
        lines.append('| --- | --- | ---: | --- |')
        for r in lhb_buy[:5]:
            nm = f'{r["stock_name"]}({r["stock_code"]})' if r['stock_name'] else r['stock_code']
            lines.append(f'| {nm} | {(r["reason"] or "")[:20]} | {r["net_amount"]/1e4:+.0f}万 | {date} |')
        if lhb_sell:
            lines.append('| … 净卖出前5：' + '、'.join(f'{(r["stock_name"] or r["stock_code"])}({r["net_amount"]/1e4:+.0f}万)' for r in lhb_sell[:5]) + ' |')
    else:
        lines.append('当日无龙虎榜数据。')
    lines.append('')
    lines.append('### 大宗交易')
    lines.append('')
    if bt:
        lines.append('| 股票 | 成交额 | 折溢价率 | 数据日 |')
        lines.append('| --- | ---: | ---: | --- |')
        for r in bt:
            lines.append(f'| {r["stock_code"]} | {r["trading_amount"]/1e4:.0f}万 | {r["discount_rate"]:+.1f}% | {bt_date} |')
    else:
        lines.append(f'无大宗交易数据（数据日 {bt_date}）。')
    lines.append('')
    lines.append('## 5. 两融与北向持股')
    lines.append('')
    lines.append('> 两融数据 T+1 披露；北向持股自 2024-08 起交易所停止披露日频数据，本地无替代源，本节缺失。')
    lines.append('')
    lines.append('| 指标 | 数值 | 变化 | 数据日 |')
    lines.append('| --- | ---: | --- | --- |')
    lines.append(f'| 融资融券余额 | {mg["bal"]/1e8:.0f} 亿 | {mg_chg.strip("（）") if mg_chg else "—"} | {mg_date} |')
    lines.append(f'| 融资净买入 | {mg["net"]/1e8:+.0f} 亿 | — | {mg_date} |')
    lines.append('')
    lines.append('资金观察：' + ('两融余额' + ('回升' if mg and mg['net'] and mg['net']>0 else '回落') + '，杠杆资金' + ('净流入' if mg and mg['net'] and mg['net']>0 else '净流出') + '。' if mg else '两融数据缺失。'))
    lines.append('')
    lines.append('## 6. 异动与风险提示')
    lines.append('')
    lines.append(f'- 涨幅 ≥5%：{ge5_cnt} 家（涨）/ {le5_cnt} 家（跌）；跌停 {dn_total} 家')
    lines.append('- 风险提示：本报告仅作市场事实归纳与结构梳理，不构成投资建议。')
    lines.append('')
    lines.append('## 7. 数据说明')
    lines.append('')
    lines.append(f'- 使用接口：本地 SQLite lixinger.db（理杏仁回填：index_daily_kline / index_fundamental_daily / daily_kline / daily_review_lhb / daily_review_block_trade / daily_margin_history / stock_industry）')
    lines.append(f'- 数据截止时间：{date} 收盘（两融 {mg_date}、大宗 {bt_date}，T+1 披露）')
    lines.append('- 缺失或降级数据：北向持股（交易所停止披露，无替代源）；概念板块涨幅（本地无概念成分表，以申万行业聚合替代）')
    lines.append('- 统计口径：涨跌停按 10%/20% 涨跌幅阈值近似（未排除 ST 5% 及新股无涨跌幅限制），行业涨幅为个股简单均值')
    lines.append('')

    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, f'{date}.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'✅ {p} ({time.time()-t0:.0f}s)')

if __name__ == '__main__':
    main()
