"""
红利指数超跌触发验证引擎
Ticket 1+2: 指数分类 + 事件检测
"""
import yaml
import sqlite3
import json

# ═══ Ticket 1: 指数分类 ═══
def classify_indices():
    """从 index_style.yaml 读取红利指数，过滤港股/无数据，分4类"""
    with open('D:/hanako/investment-system/config/index_style.yaml', 'r', encoding='utf-8') as f:
        style = yaml.safe_load(f)

    # 港股关键词（排除）
    hk_kw = ['港股', '港通', '香港', 'HKC', 'SH', 'SHS', '沪港深']
    # 类别关键词
    pure_kw = ['红利指数', '380红利', '300红利', '500红利', '800红利', '1000红利',
               '中证红利', '国企红利', '央企红利', '上国红利', 'CS高股息', '股息龙头',
               'A500红利增长', '智选高股息']
    lowvol_kw = ['低波', 'LV']
    quality_kw = ['质量', '成长', '潜力']
    industry_kw = ['消费', '医药', '行业']

    categories = {'pure': [], 'lowvol': [], 'quality': [], 'other': []}
    for cat_key, cat_val in style.get('categories', {}).items():
        for item in cat_val:
            if not isinstance(item, dict):
                continue
            code = item.get('code', '')
            name = item.get('name', '')
            if not any(k in name for k in ['红利', '股息', '高息', '分红']):
                continue
            # 排除港股
            if any(k in name for k in hk_kw):
                continue
            # 排除无数据的 ETF
            if code == '100032':
                continue
            if code == '510880':  # ETF，检查是否有数据
                pass
            # 分类
            if any(k in name for k in industry_kw) and ('红利' in name):
                categories['other'].append((code, name))
            elif any(k in name for k in lowvol_kw):
                categories['lowvol'].append((code, name))
            elif any(k in name for k in quality_kw):
                categories['quality'].append((code, name))
            elif any(k in name for k in pure_kw) or cat_key == 'strategy':
                categories['pure'].append((code, name))
            else:
                categories['other'].append((code, name))
    return categories


# ═══ Ticket 2: 事件检测 ═══
def detect_events(code, conn, thresholds):
    """
    对单只指数检测所有触发事件
    返回: {condition_key: [(trigger_date, ...)]}
    """
    # 读取K线（close + 次日开盘）
    rows = conn.execute("""
        SELECT date, close, open FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND date>='2016-01-01'
        ORDER BY date
    """, (code,)).fetchall()
    if len(rows) < 300:
        return {}

    dates = [r['date'] for r in rows]
    closes = [r['close'] for r in rows]
    opens = [r['open'] for r in rows]

    # 读取估值分位
    val_rows = conn.execute("""
        SELECT date, pe_ttm_pct, pb_pct, dyr_pct FROM index_fundamental_daily
        WHERE stock_code=? ORDER BY date
    """, (code,)).fetchall()
    val_map = {r['date']: r for r in val_rows}

    # 预计算滚动最高
    n = len(closes)
    hist_high = 0
    roll250 = []
    for i in range(n):
        if closes[i] > hist_high:
            hist_high = closes[i]
        w = closes[max(0, i-249):i+1]
        roll250.append(max(w))

    # 去重窗口：记录每个条件最后触发索引
    # 预展开所有条件key
    all_keys = []
    for d in thresholds['drawdown_250']:
        all_keys.append(f'dd250_{int(d*100)}')
    for d in thresholds['drawdown_hist']:
        all_keys.append(f'ddhist_{int(d*100)}')
    for p in thresholds['pe_pct']:
        all_keys.append(f'pe_pct_{int(p*100)}')
    for p in thresholds['pb_pct']:
        all_keys.append(f'pb_pct_{int(p*100)}')
    for p in thresholds['dyr_pct']:
        all_keys.append(f'dyr_pct_{int(p*100)}')
    events = {k: [] for k in all_keys}
    last_trigger = {k: -999 for k in all_keys}

    for i in range(n):
        c = closes[i]
        # 条件A: 250日回撤
        for d in thresholds['drawdown_250']:
            key = f'dd250_{int(d*100)}'
            if roll250[i] > 0 and c <= roll250[i] * (1 - d):
                if i - last_trigger[key] >= 20:
                    events[key].append(dates[i])
                    last_trigger[key] = i
        # 条件B: 历史高点回撤
        for d in thresholds['drawdown_hist']:
            key = f'ddhist_{int(d*100)}'
            if hist_high > 0 and c <= hist_high * (1 - d):
                if i - last_trigger[key] >= 20:
                    events[key].append(dates[i])
                    last_trigger[key] = i
        # 条件C/D/E: 估值分位（需要当日估值数据）
        v = val_map.get(dates[i])
        if v:
            pe_pct = v['pe_ttm_pct']
            pb_pct = v['pb_pct']
            dyr_pct = v['dyr_pct']
            for p in thresholds['pe_pct']:
                key = f'pe_pct_{int(p*100)}'
                if pe_pct is not None and pe_pct < p:
                    if i - last_trigger[key] >= 20:
                        events[key].append(dates[i])
                        last_trigger[key] = i
            for p in thresholds['pb_pct']:
                key = f'pb_pct_{int(p*100)}'
                if pb_pct is not None and pb_pct < p:
                    if i - last_trigger[key] >= 20:
                        events[key].append(dates[i])
                        last_trigger[key] = i
            for p in thresholds['dyr_pct']:
                key = f'dyr_pct_{int(p*100)}'
                if dyr_pct is not None and dyr_pct > p:
                    if i - last_trigger[key] >= 20:
                        events[key].append(dates[i])
                        last_trigger[key] = i

    return events


if __name__ == '__main__':
    cats = classify_indices()
    for k, v in cats.items():
        print(f"{k}: {len(v)} 个")
        for code, name in v:
            print(f"  {code} {name}")

    # 测试单指数事件检测
    conn = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
    conn.row_factory = sqlite3.Row
    thresholds = {
        'drawdown_250': [0.10, 0.15, 0.20],
        'drawdown_hist': [0.15, 0.25],
        'pe_pct': [0.10, 0.20, 0.30],
        'pb_pct': [0.10, 0.20, 0.30],
        'dyr_pct': [0.80, 0.90],
    }
    ev = detect_events('000922', conn, thresholds)
    print("\n000922 中证红利事件数:")
    for k, v in ev.items():
        print(f"  {k}: {len(v)} 次, 最新触发 {v[-1] if v else '-'}")
    conn.close()
