"""
指数资金活跃度评分引擎 v1.0

5 个量价指标 × 3 个时间窗口（10/65/250日），独立计算指数级别的资金活跃度。
纯量价驱动，不依赖机构持股数据，每日可更新。

输出：
  - index_capital_flow_daily 表（每日每指数一条）
  - 三个窗口评分（0-100）+ 资金流向标签
"""

import sys, os, json, argparse, sqlite3, math
from datetime import datetime, date as dt_date, timedelta

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

from scripts.common import log as logger

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")
INDEX_YAML = os.path.join(PROJECT_DIR, "config", "index_style.yaml")

# 三个时间窗口
WINDOWS = [10, 65, 250]
WINDOW_LABELS = {10: '10d', 65: '65d', 250: '250d'}

# 各窗口权重（5指标: MFV, 量比, 上涨量占比, 金额增速, 价量相关性）
WEIGHTS = {
    10:  [0.15, 0.35, 0.25, 0.15, 0.10],
    65:  [0.25, 0.20, 0.20, 0.20, 0.15],
    250: [0.30, 0.10, 0.15, 0.20, 0.25],
}


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS index_capital_flow_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            pool TEXT,
            mfv_10d REAL, mfv_65d REAL, mfv_250d REAL,
            vol_ratio_10d REAL, vol_ratio_65d REAL, vol_ratio_250d REAL,
            upvol_ratio_10d REAL, upvol_ratio_65d REAL, upvol_ratio_250d REAL,
            amt_growth_10d REAL, amt_growth_65d REAL, amt_growth_250d REAL,
            pv_corr_10d REAL, pv_corr_65d REAL, pv_corr_250d REAL,
            score_10d INTEGER, score_65d INTEGER, score_250d INTEGER,
            flow_label TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(date, stock_code)
        )
    """)
    conn.commit()
    conn.close()


def load_index_pool():
    """从 YAML 加载 L2 + 主题指数列表"""
    import yaml
    with open(INDEX_YAML, encoding='utf-8') as f:
        style = yaml.safe_load(f)
    l2 = {item['code']: item['name'] for item in style['categories'].get('sector_l2', [])}
    theme = {item['code']: item['name'] for item in style['categories'].get('thematic', [])}
    return l2, theme


def fetch_klines(conn, codes, target_date):
    """批量获取所有需要的 K 线数据"""
    if not codes:
        return {}
    placeholders = ','.join('?' * len(codes))
    rows = conn.execute(f"""
        SELECT stock_code, date, open, high, low, close, volume, amount, change
        FROM index_daily_kline
        WHERE stock_code IN ({placeholders})
          AND date >= date(?, '-300 days')
          AND date <= ?
        ORDER BY stock_code, date
    """, (*codes, target_date, target_date)).fetchall()
    
    result = {}
    for r in rows:
        sc = r['stock_code']
        if sc not in result:
            result[sc] = []
        result[sc].append({
            'date': r['date'], 'open': r['open'], 'high': r['high'],
            'low': r['low'], 'close': r['close'],
            'volume': r['volume'], 'amount': r['amount'], 'change': r['change'],
        })
    return result


def compute_mfv(open_p, high, low, close, volume, amount):
    """计算单日 MFV（资金流向强度）"""
    if not all(v is not None for v in [high, low, volume, close]):
        return 0.0
    if high == low or not volume:
        return 0.0
    pos = (2 * close - high - low) / (high - low)
    return pos * math.log1p(volume) / 20


def compute_vol_ratio(klines, window):
    """量比：当前量 / MA50(量)"""
    if len(klines) < 51:
        return 0.0
    today_vol = klines[-1].get('volume') or 0
    vols = [k['volume'] for k in klines[-51:-1] if k.get('volume')]
    if not vols:
        return 0.0
    avg_vol = sum(vols) / len(vols)
    return today_vol / avg_vol if avg_vol > 0 else 0.0


def compute_upvol_ratio(klines, window):
    """上涨量占比：窗口内涨日的成交量 / 总成交量"""
    recent = klines[-window:] if len(klines) >= window else klines
    total_vol = 0
    up_vol = 0
    for k in recent:
        v = k['volume'] or 0
        if v <= 0:
            continue
        total_vol += v
        close = k.get('close')
        open_p = k.get('open')
        if close is not None and open_p is not None and close > open_p:
            up_vol += v
    return up_vol / total_vol if total_vol > 0 else 0.0


def compute_amt_growth(klines, window):
    """金额增速：今日金额 / MA50(金额)"""
    if len(klines) < 51:
        return 0.0
    today_amt = klines[-1].get('amount') or 0
    amts = [k['amount'] for k in klines[-51:-1] if k.get('amount')]
    if not amts:
        return 0.0
    avg_amt = sum(amts) / len(amts)
    return today_amt / avg_amt if avg_amt > 0 else 0.0


def compute_pv_corr(klines, window):
    """价量相关性：近 N 日 close 与 volume 的 Pearson 相关系数"""
    recent = klines[-window:] if len(klines) >= window else klines
    n = len(recent)
    if n < 5:
        return 0.0
    prices = [k['close'] for k in recent if k.get('close') is not None]
    vols = [k['volume'] for k in recent if k.get('volume') is not None]
    if len(prices) < 5 or len(vols) < 5:
        return 0.0
    # 取两者最小长度对齐
    min_n = min(len(prices), len(vols))
    prices = prices[:min_n]
    vols = vols[:min_n]
    
    mean_p = sum(prices) / min_n
    mean_v = sum(vols) / min_n
    
    num = sum((p - mean_p) * (v - mean_v) for p, v in zip(prices, vols))
    den_p = math.sqrt(sum((p - mean_p) ** 2 for p in prices))
    den_v = math.sqrt(sum((v - mean_v) ** 2 for v in vols))
    
    if den_p == 0 or den_v == 0:
        return 0.0
    return num / (den_p * den_v)


def percentile_rank(values, target):
    """计算 target 在 values 中的百分位排名（0-100）"""
    if not values:
        return 50.0
    n = len(values)
    if n <= 1:
        return 50.0
    below = sum(1 for v in values if v < target)
    equal = sum(1 for v in values if v == target)
    return (below + equal * 0.5) / n * 100


def score_from_percentile(pct):
    """百分位映射为 0-100 分数"""
    if pct >= 90: return 100
    if pct >= 80: return 80
    if pct >= 60: return 60
    if pct >= 40: return 40
    if pct >= 20: return 20
    return 0


def compute_score(raw_values, key, window):
    """对一组原始值计算评分（百分位映射）"""
    scores = {}
    for code, vals in raw_values.items():
        v = vals.get(WINDOW_LABELS[window], {}).get(key, 0)
        if v is None:
            v = 0
        scores[code] = v
    # 百分位排名
    ranked = {}
    for code, v in scores.items():
        pct = percentile_rank(list(scores.values()), v)
        ranked[code] = score_from_percentile(pct)
    return ranked


def make_flow_label(mfv_10d, mfv_65d, mfv_250d):
    """生成资金流向标签"""
    def sign(v):
        if v > 0.01: return '+'
        if v < -0.01: return '-'
        return '0'
    return sign(mfv_10d) + sign(mfv_65d) + sign(mfv_250d)


def compute_all(target_date):
    """主流程：计算所有指数 5 指标 × 3 窗口"""
    conn = get_db()
    ensure_tables()
    
    # 自动回退到有 K 线数据的日期
    has_data = conn.execute("SELECT COUNT(*) FROM index_daily_kline WHERE date=?", (target_date,)).fetchone()[0]
    if not has_data:
        fallback = conn.execute("SELECT MAX(date) FROM index_daily_kline").fetchone()[0]
        if fallback:
            logger.warning(f"  ⚠️ {target_date} 无K线数据，回退到 {fallback}")
            target_date = fallback
    
    l2_indices, theme_indices = load_index_pool()
    all_indices = {**l2_indices, **theme_indices}
    codes = list(all_indices.keys())
    
    logger.info(f"📊 指数资金活跃度 — {target_date}")
    logger.info(f"   指数总数: {len(codes)} (L2:{len(l2_indices)} 主题:{len(theme_indices)})")
    
    # 获取 K 线数据
    kline_data = fetch_klines(conn, codes, target_date)
    logger.info(f"   有K线数据的指数: {len(kline_data)}")
    
    # 逐指数计算 5 指标 × 3 窗口
    raw = {}  # {code: {window_label: {key: val}}}
    for code in codes:
        klines = kline_data.get(code, [])
        if len(klines) < 30:
            continue
        raw[code] = {}
        for w in WINDOWS:
            wl = WINDOW_LABELS[w]
            # MFV：窗口内日均值，不是只取最后一天
            recent_mfv = klines[-min(w, len(klines)):]
            mfv_sum = 0
            mfv_n = 0
            for k in recent_mfv:
                m = compute_mfv(k.get('open'), k.get('high'), k.get('low'),
                                k.get('close'), k.get('volume'), k.get('amount'))
                if m != 0:
                    mfv_sum += m
                    mfv_n += 1
            mfv_avg = mfv_sum / mfv_n if mfv_n > 0 else 0
            
            raw[code][wl] = {
                'mfv': mfv_avg,
                'vol_ratio': compute_vol_ratio(klines, w),
                'upvol_ratio': compute_upvol_ratio(klines, w),
                'amt_growth': compute_amt_growth(klines, w),
                'pv_corr': compute_pv_corr(klines, w),
            }
    
    logger.info(f"   有效计算: {len(raw)} 个指数")
    
    # 分窗口评分
    scored = {}  # {code: {window_label: {final_score, raw_values}}}
    for code in raw:
        scored[code] = {wl: {'score': 0, 'raw': dict(raw[code][wl])} for wl in ['10d','65d','250d']}
    
    # 对每个窗口、每个指标做百分位评分，然后加权合成
    for w in WINDOWS:
        wl = WINDOW_LABELS[w]
        weights = WEIGHTS[w]
        keys = ['mfv', 'vol_ratio', 'upvol_ratio', 'amt_growth', 'pv_corr']
        
        # 获取各指标百分位分数
        key_scores = {}
        for ki, key in enumerate(keys):
            key_scores[key] = compute_score(raw, key, w)
        
        # 加权合成
        for code in scored:
            total = 0
            for ki, key in enumerate(keys):
                score = key_scores[key].get(code, 0)
                total += score * weights[ki]
            scored[code][wl]['score'] = round(total)
    
    # 生成资金流向标签
    flow_labels = {}
    for code in scored:
        flow_labels[code] = make_flow_label(
            raw[code]['10d']['mfv'],
            raw[code]['65d']['mfv'],
            raw[code]['250d']['mfv'],
        )
    
    # 写入数据库
    pool_map = {}
    for c in l2_indices: pool_map[c] = 'sector_l2'
    for c in theme_indices: pool_map[c] = 'thematic'
    
    rows = []
    for code in raw:
        pool = pool_map.get(code, '')
        rows.append((
            target_date, code, pool,
            raw[code]['10d']['mfv'], raw[code]['65d']['mfv'], raw[code]['250d']['mfv'],
            raw[code]['10d']['vol_ratio'], raw[code]['65d']['vol_ratio'], raw[code]['250d']['vol_ratio'],
            raw[code]['10d']['upvol_ratio'], raw[code]['65d']['upvol_ratio'], raw[code]['250d']['upvol_ratio'],
            raw[code]['10d']['amt_growth'], raw[code]['65d']['amt_growth'], raw[code]['250d']['amt_growth'],
            raw[code]['10d']['pv_corr'], raw[code]['65d']['pv_corr'], raw[code]['250d']['pv_corr'],
            scored[code]['10d']['score'], scored[code]['65d']['score'], scored[code]['250d']['score'],
            flow_labels.get(code, '000'),
        ))
    
    conn.execute("DELETE FROM index_capital_flow_daily WHERE date=?", (target_date,))
    cols = ['date','stock_code','pool','mfv_10d','mfv_65d','mfv_250d','vol_ratio_10d','vol_ratio_65d','vol_ratio_250d',
            'upvol_ratio_10d','upvol_ratio_65d','upvol_ratio_250d','amt_growth_10d','amt_growth_65d','amt_growth_250d',
            'pv_corr_10d','pv_corr_65d','pv_corr_250d','score_10d','score_65d','score_250d','flow_label']
    placeholders = ','.join(['?' for _ in cols])
    sql = f"INSERT INTO index_capital_flow_daily ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, rows)
    conn.commit()
    
    # TOP 10 输出
    for w in WINDOWS:
        wl = WINDOW_LABELS[w]
        top = sorted(scored.items(), key=lambda x: x[1][wl]['score'], reverse=True)[:10]
        names = [f"{all_indices.get(c,c)}({s[wl]['score']})" for c, s in top]
        logger.info(f"   TOP 10 ({wl}): {' > '.join(names)}")
    
    conn.close()
    logger.info(f"📊 指数资金活跃度完成: {len(rows)} 条")
    return len(rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="指数资金活跃度评分")
    parser.add_argument('--date', type=str, default=None)
    args = parser.parse_args()
    target = args.date or dt_date.today().strftime('%Y-%m-%d')
    ensure_tables()
    compute_all(target)
