#!/usr/bin/env python3
"""A股每日收盘复盘 — 数据持久化版"""
import sqlite3, json, os, sys, requests, time
from datetime import datetime

DB = 'D:\\hanako\\investment-system\\data\\lixinger.db'
OUT_DIR = 'D:\\hanako\\investment-system\\web\\backtest\\daily'
TOKEN = None
for line in open('D:\\hanako\\.env', encoding='utf-8'):
    if 'LIXINGER_TOKEN' in line:
        TOKEN = line.strip().split('=')[1]
        break

def init_tables(db):
    db.execute('''CREATE TABLE IF NOT EXISTS daily_review_lhb (
        date TEXT, stock_code TEXT, stock_name TEXT, reason TEXT,
        total_buy REAL, total_sell REAL, net_amount REAL,
        inst_buy REAL, inst_sell REAL, inst_net REAL,
        PRIMARY KEY(date, stock_code))''')
    db.execute('''CREATE TABLE IF NOT EXISTS daily_review_block_trade (
        date TEXT, stock_code TEXT, trading_price REAL, 
        trading_volume REAL, trading_amount REAL,
        discount_rate REAL, buy_branch TEXT, sell_branch TEXT,
        PRIMARY KEY(date, stock_code, buy_branch))''')
    db.execute('''CREATE TABLE IF NOT EXISTS daily_review_margin (
        date TEXT, stock_code TEXT, margin_balance REAL,
        margin_fb REAL, margin_sb REAL, net_buy_d1 REAL,
        fb_balance REAL, sb_balance REAL,
        PRIMARY KEY(date, stock_code))''')
    db.execute('''CREATE TABLE IF NOT EXISTS daily_review_summary (
        date TEXT PRIMARY KEY, up_count INT, down_count INT,
        total_amount REAL, sh_idx_close REAL, sh_idx_chg REAL,
        hs300_close REAL, hs300_chg REAL, created_at TEXT)''')
    db.commit()

def get_latest_trade_date(db):
    return db.execute('SELECT MAX(date) FROM daily_kline').fetchone()[0]

def fetch_lhb(db, date):
    rows = db.execute('SELECT * FROM daily_review_lhb WHERE date=?', (date,)).fetchall()
    if rows: return rows
    try:
        r = requests.post('https://open.lixinger.com/api/cn/company/trading-abnormal',
            json={'token': TOKEN, 'date': date}, timeout=30).json()
        if r.get('code') == 1:
            data = r.get('data', [])
            for item in data:
                db.execute('INSERT OR REPLACE INTO daily_review_lhb VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (date, item.get('stockCode',''), item.get('stockName',''),
                     item.get('reasonForDisclosure',''),
                     item.get('totalPurchaseAmount',0), item.get('totalSellAmount',0),
                     item.get('totalNetPurchaseAmount',0),
                     item.get('institutionBuyAmount',0), item.get('institutionSellAmount',0),
                     item.get('institutionNetPurchaseAmount',0)))
            db.commit()
            return db.execute('SELECT * FROM daily_review_lhb WHERE date=?', (date,)).fetchall()
    except: pass
    return []

def fetch_block_trades(db, date):
    rows = db.execute('SELECT * FROM daily_review_block_trade WHERE date=?', (date,)).fetchall()
    if rows: return rows
    try:
        r = requests.post('https://open.lixinger.com/api/cn/company/block-deal',
            json={'token': TOKEN, 'date': date}, timeout=30).json()
        if r.get('code') == 1:
            data = r.get('data', [])
            for item in data:
                db.execute('INSERT OR REPLACE INTO daily_review_block_trade VALUES (?,?,?,?,?,?,?,?)',
                    (date, item.get('stockCode',''), item.get('tradingPrice',0),
                     item.get('tradingVolume',0), item.get('tradingAmount',0),
                     item.get('discountRate',0), item.get('buyBranch',''), item.get('sellBranch','')))
            db.commit()
            return db.execute('SELECT * FROM daily_review_block_trade WHERE date=?', (date,)).fetchall()
    except: pass
    return []

def fetch_margin(db, date):
    rows = db.execute('SELECT * FROM daily_review_margin WHERE date=?', (date,)).fetchall()
    if rows: return rows
    codes = [r[0] for r in db.execute(
        'SELECT stock_code FROM daily_kline WHERE date=? ORDER BY amount DESC LIMIT 500', (date,)).fetchall()]
    data = []
    for i in range(0, len(codes), 100):
        batch = codes[i:i+100]
        try:
            r = requests.post('https://open.lixinger.com/api/cn/company/hot/mtasl',
                json={'token': TOKEN, 'stockCodes': batch}, timeout=30).json()
            if r.get('code') == 1: data.extend(r.get('data', []))
        except: pass
        time.sleep(0.3)
    for item in data:
        db.execute('INSERT OR REPLACE INTO daily_review_margin VALUES (?,?,?,?,?,?,?,?)',
            (date, item.get('stockCode',''), item.get('mtaslb',0),
             item.get('mtaslb_fb',0), item.get('mtaslb_sb',0),
             item.get('npa_o_f_d1',0), item.get('mtaslb_fb',0), item.get('mtaslb_sb',0)))
    db.commit()
    return db.execute('SELECT * FROM daily_review_margin WHERE date=?', (date,)).fetchall()

def generate_report(date=None):
    t0 = time.time()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    init_tables(db)
    
    if not date: date = get_latest_trade_date(db)
    print(f'📊 {date} 收盘复盘...')
    
    # 1. 指数
    idx_data = {}
    for code, name in [('000001','上证综指'),('000300','沪深300'),('000688','科创50'),('399006','创业板指')]:
        r = db.execute('SELECT close, change FROM index_daily_kline WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1', (code, date)).fetchone()
        if r: idx_data[name] = {'close': r['close'], 'chg': r['change']}
    
    # 2. 宽度 + 成交
    bd = db.execute('''
        SELECT COUNT(*) as t, SUM(CASE WHEN change_pct>0 THEN 1 ELSE 0 END) as up,
               SUM(CASE WHEN change_pct<0 THEN 1 ELSE 0 END) as dn
        FROM daily_kline WHERE date=?''', (date,)).fetchone()
    vol = db.execute('SELECT SUM(amount) as v FROM daily_kline WHERE date=?', (date,)).fetchone()
    
    # 保存摘要
    sh = idx_data.get('上证综指',{}); hs = idx_data.get('沪深300',{})
    db.execute('INSERT OR REPLACE INTO daily_review_summary VALUES (?,?,?,?,?,?,?,?,?)',
        (date, bd['up'] or 0, bd['dn'] or 0, vol['v']/1e8 if vol and vol['v'] else 0,
         sh.get('close'), sh.get('chg'), hs.get('close'), hs.get('chg'),
         datetime.now().strftime('%Y-%m-%d %H:%M')))
    db.commit()
    
    def lookup_name(code):
        r = db.execute('SELECT name FROM stock_basic WHERE stock_code=?', (code,)).fetchone()
        return r[0] if r else code
    
    # 3. 龙虎榜
    print('  拉取龙虎榜...')
    lhb = fetch_lhb(db, date)
    for r in lhb:
        if not r['stock_name']:
            db.execute('UPDATE daily_review_lhb SET stock_name=? WHERE date=? AND stock_code=?',
                       (lookup_name(r['stock_code']), date, r['stock_code']))
    db.commit()
    lhb = db.execute('SELECT * FROM daily_review_lhb WHERE date=?', (date,)).fetchall()
    
    # 4. 大宗交易
    print('  拉取大宗交易...')
    bt = fetch_block_trades(db, date)
    # 大宗交易也补名称
    bt_names = {}
    for r in bt:
        nm = db.execute('SELECT name FROM stock_basic WHERE stock_code=?', (r['stock_code'],)).fetchone()
        if nm: bt_names[r['stock_code']] = nm[0]
    
    # 5. 两融
    print('  拉取两融(TOP500)...')
    margin = fetch_margin(db, date)
    
    total_margin = sum(r['margin_balance'] or 0 for r in margin)
    total_net_buy = sum(r['net_buy_d1'] or 0 for r in margin)
    
    db.close()
    elapsed = time.time() - t0
    
    # ── HTML ──
    html = f'''<!DOCTYPE html><html lang="zh-CN" class="dark">
<head><meta charset="UTF-8"><title>A股每日复盘 {date}</title>
<style>
body{{font-family:Inter,sans-serif;background:#0f0f12;color:#e4e4e7;margin:0;padding:24px}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-family:'Instrument Serif',serif;font-size:20px;font-weight:400}}
.meta{{color:#8b8b90;font-size:10px;margin:2px 0 16px}}
h2{{font-size:14px;color:#f59e0b;margin:20px 0 8px;border-bottom:1px solid rgba(255,255,255,.06);padding-bottom:4px}}
table{{width:100%;border-collapse:collapse;font-size:11px;margin:6px 0}}
th{{background:rgba(255,255,255,.04);color:#8b8b90;padding:4px 6px;text-align:right;border-bottom:1px solid rgba(255,255,255,.06);font-size:9px;text-transform:uppercase}}
th:first-child{{text-align:left}}
td{{padding:3px 6px;text-align:right;border-bottom:1px solid rgba(255,255,255,.04);font-size:11px}}
td:first-child{{text-align:left;color:#8b8b90}}
.r{{color:#10b981!important}} .g{{color:#ef4444!important}}
.sg{{display:flex;gap:8px;margin:10px 0}}
.sg>div{{background:rgba(26,26,31,.6);border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:10px 12px;flex:1;text-align:center}}
.sg .n{{font-family:'Instrument Serif',serif;font-size:18px;color:#f59e0b}}
.sg .l{{font-size:9px;color:#8b8b90;text-transform:uppercase;margin-top:1px}}
</style></head><body><div class="wrap">
<h1>A股每日收盘复盘</h1>
<div class="meta">{date} | 耗时{elapsed:.0f}s | 数据持久化</div>
<div class="sg">
<div><div class="n">{idx_data.get('上证综指',{}).get('close',0):.0f}</div><div class="l">上证</div></div>
<div><div class="n">{idx_data.get('沪深300',{}).get('close',0):.0f}</div><div class="l">沪深300</div></div>
<div><div class="n">{idx_data.get('科创50',{}).get('close',0):.0f}</div><div class="l">科创50</div></div>
<div><div class="n">{bd["t"]}</div><div class="l">交易股票</div></div>
</div>'''
    
    html += '<h2>1. 指数</h2><table><thead><tr><th>指数</th><th>收盘</th><th>涨跌</th></tr></thead><tbody>'
    for n, d in idx_data.items():
        cls = 'r' if d['chg']<0 else 'g' if d['chg']>0 else ''
        html += f'<tr><td>{n}</td><td>{d["close"]:.2f}</td><td class="{cls}">{d["chg"]:+.2f}%</td></tr>'
    html += '</tbody></table>'
    
    if bd:
        up, dn, t = bd['up'] or 0, bd['dn'] or 0, bd['t'] or 0
        va = vol['v']/1e8 if vol and vol['v'] else 0
        html += f'<h2>2. 市场宽度</h2><table><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>'
        html += f'<tr><td>上涨</td><td class="g">{up} ({up/t*100:.1f}%)</td></tr>'
        html += f'<tr><td>下跌</td><td class="r">{dn} ({dn/t*100:.1f}%)</td></tr>'
        html += f'<tr><td>涨跌比</td><td>{up/dn:.2f}:1</td></tr>'
        html += f'<tr><td>成交额</td><td>{va:.0f}亿</td></tr></tbody></table>'
    
    html += f'<h2>3. 两融(TOP500)</h2>'
    html += f'<table><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>'
    html += f'<tr><td>两融余额</td><td>{total_margin/1e8:.0f}亿</td></tr>'
    html += f'<tr><td>融资净买入</td><td class="{"g" if total_net_buy>0 else "r"}">{total_net_buy/1e8:+.0f}亿</td></tr></tbody></table>'
    
    html += f'<h2>4. 龙虎榜 ({len(lhb)}条)</h2>'
    if lhb:
        buy_lhb = [r for r in lhb if r['net_amount'] > 0]
        sell_lhb = [r for r in lhb if r['net_amount'] <= 0]
        buy_lhb.sort(key=lambda x: x['net_amount'], reverse=True)
        sell_lhb.sort(key=lambda x: x['net_amount'])
        
        html += f'<h3>🟢 净买入 ({len(buy_lhb)}条)</h3>'
        html += '<table><thead><tr><th>股票</th><th>原因</th><th>净买入</th></tr></thead><tbody>'
        for r in buy_lhb:
            nm = f'{r["stock_name"]}({r["stock_code"]})'
            net = r['net_amount']
            html += f'<tr><td>{nm}</td><td style="font-size:10px;max-width:200px;overflow:hidden">{r["reason"][:30]}</td><td class="g">{net/1e4:+.0f}万</td></tr>'
        html += '</tbody></table>'
        
        html += f'<h3>🔴 净卖出 ({len(sell_lhb)}条)</h3>'
        html += '<table><thead><tr><th>股票</th><th>原因</th><th>净卖出</th></tr></thead><tbody>'
        for r in sell_lhb:
            nm = f'{r["stock_name"]}({r["stock_code"]})'
            net = r['net_amount']
            html += f'<tr><td>{nm}</td><td style="font-size:10px;max-width:200px;overflow:hidden">{r["reason"][:30]}</td><td class="r">{abs(net)/1e4:.0f}万</td></tr>'
        html += '</tbody></table>'
    
    html += f'<h2>5. 大宗交易 ({len(bt)}条)</h2>'
    if bt:
        html += '<table><thead><tr><th>股票</th><th>成交额</th><th>折价率</th><th>占比</th></tr></thead><tbody>'
        for r in bt:
            nm2 = bt_names.get(r['stock_code'], r['stock_code']); pct = f'{r["trading_amount"]/r["cmc"]*100:.2f}%' if bt_cmc.get(r['stock_code']) else '—'; html += f'<tr><td>{nm2}({r["stock_code"]})</td><td>{r["trading_amount"]/1e4:.0f}万</td><td>{r["discount_rate"]:.1f}%</td><td>{pct}</td></tr>'
        html += '</tbody></table>'
    
    html += '</div></body></html>'
    
    os.makedirs(OUT_DIR, exist_ok=True)
    p = os.path.join(OUT_DIR, f'review_{date}.html')
    with open(p, 'w', encoding='utf-8') as f: f.write(html)
    print(f'✅ {p} ({elapsed:.0f}s)')
    print(f'  指数+宽度 | 两融{len(margin)}条 | 龙虎榜{len(lhb)}条 | 大宗{len(bt)}条')

if __name__ == '__main__':
    date = sys.argv[1] if len(sys.argv) > 1 else None
    generate_report(date)
