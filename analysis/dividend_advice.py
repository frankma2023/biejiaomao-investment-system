"""T1后端: /api/market-scan/dividend-advice 信号检测+建议合成"""
# 插入到 server.py 的辅助函数（先独立验证逻辑）
import sqlite3
import json

DB = 'D:/hanako/investment-system/data/lixinger.db'

# 默认核心指数
DEFAULT_INDICES = [
    ('000922', '中证红利', 'pure'),
    ('H30269', '红利低波', 'lowvol'),
    ('931468', '红利质量', 'quality'),
    ('000015', '红利指数', 'pure'),
    ('931848', '800红利低波', 'lowvol'),
]

def compute_advice(code, name, cat, target_date):
    """计算单指数在目标日期的信号和建议"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # K线（250日窗口 + 当日）
    rows = conn.execute("""
        SELECT date, close FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date<=?
        ORDER BY date DESC LIMIT 300
    """, (code, target_date)).fetchall()
    rows = list(reversed(rows))  # 升序
    conn.close()

    if len(rows) < 250:
        return None

    closes = [r['close'] for r in rows]
    current = closes[-1]
    high250 = max(closes[-250:])
    dd_250 = (high250 - current) / high250 * 100

    # 估值（目标日期或之前最近）
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    v = conn.execute("""
        SELECT date, pe_ttm, pe_ttm_pct, pb, pb_pct, dyr, dyr_pct
        FROM index_fundamental_daily
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1
    """, (code, target_date)).fetchone()
    conn.close()

    val = None
    if v:
        val = {
            'pe': round(v['pe_ttm'], 1) if v['pe_ttm'] else None,
            'pe_pct': round(v['pe_ttm_pct'] * 100, 0) if v['pe_ttm_pct'] is not None else None,
            'pb': round(v['pb'], 2) if v['pb'] else None,
            'pb_pct': round(v['pb_pct'] * 100, 0) if v['pb_pct'] is not None else None,
            'dyr': round(v['dyr'] * 100, 2) if v['dyr'] else None,
            'dyr_pct': round(v['dyr_pct'] * 100, 0) if v['dyr_pct'] is not None else None,
        }

    # 信号检测
    signals = []
    if dd_250 >= 15:
        signals.append('gold_buy')
    if val and val['dyr_pct'] is not None and val['dyr_pct'] > 90:
        signals.append('high_div')
    if dd_250 >= 15 and val and val['dyr_pct'] is not None and val['dyr_pct'] > 80:
        signals.append('double_confirm')
    if val and val['pe_pct'] is not None and val['pe_pct'] > 80:
        signals.append('pe_warn')
    if val and val['dyr_pct'] is not None and val['dyr_pct'] < 10:
        signals.append('low_div')

    # 建议合成
    advice, level = '持有/观望', 'hold'
    if 'double_confirm' in signals:
        advice, level = '分批买入（回撤+高息双确认）', 'strong_buy'
    elif 'gold_buy' in signals or 'high_div' in signals:
        advice, level = '观察买入（单信号触发）', 'buy'
    elif 'pe_warn' in signals and dd_250 < 10:
        advice, level = '估值偏高（PE分位>80%），建议减仓', 'reduce'
    elif 'low_div' in signals:
        advice, level = '股息保护不足（股息率分位<10%），谨慎', 'caution'

    return {
        'code': code, 'name': name, 'cat': cat,
        'close': round(current, 2), 'date': rows[-1]['date'],
        'dd_250': round(dd_250, 1),
        'high_250': round(high250, 2),
        'valuation': val,
        'signals': signals,
        'advice': advice,
        'advice_level': level,
    }


def get_all_advice(target_date=None):
    if not target_date:
        conn = sqlite3.connect(DB)
        r = conn.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()
        target_date = r[0]
        conn.close()
    results = []
    for code, name, cat in DEFAULT_INDICES:
        r = compute_advice(code, name, cat, target_date)
        if r:
            results.append(r)
    return {'date': target_date, 'indices': results}


if __name__ == '__main__':
    # 验证关键节点
    for dt in ['2026-03-12', '2026-06-30', '2026-07-31']:
        print(f"\n{'='*60}\n日期: {dt}")
        data = get_all_advice(dt)
        for idx in data['indices']:
            sigs = ','.join(idx['signals']) if idx['signals'] else '-'
            print(f"  {idx['name']:<10} 回撤{idx['dd_250']:>5.1f}% PE分位{idx['valuation']['pe_pct'] if idx['valuation'] else '-':>3}% "
                  f"DYR分位{idx['valuation']['dyr_pct'] if idx['valuation'] else '-':>3}% | {idx['advice']} [{sigs}]")
