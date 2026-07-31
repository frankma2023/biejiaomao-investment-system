"""
突破形态结构识别引擎

给定股票代码 + 日期范围，自动提取：
  前高(H) → 调整段(D) → 最低点(L) → 横盘区(C) → 突破日1(B1) → 整理段(P) → 突破日2(B2)

并计算 6 个阶段约 30 项量化特征。
"""

import sqlite3, os, sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


def sma(values, n):
    """简单移动平均"""
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def linear_slope(values):
    """线性回归斜率（最小二乘法）"""
    n = len(values)
    if n < 2:
        return 0
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (values[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den != 0 else 0


def compute_ma(klines, idx, period):
    """计算 idx 位置的 MA"""
    if idx < period - 1:
        return None
    return sum(klines[i]['close'] for i in range(idx - period + 1, idx + 1)) / period


def analyze_structure(code, start_date, end_date):
    """
    主入口：分析一只股票在指定日期范围内的突破形态结构
    
    Returns:
        dict with nodes, phases, features, or {'error': msg}
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ── 1. 获取K线数据 ──
    # 起始日期往前多取 200 天用于均线计算和 RS
    extended_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=250)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume, amount
        FROM daily_kline
        WHERE stock_code=? AND date >= ? AND date <= ?
        ORDER BY date
    """, (code, extended_start, end_date)).fetchall()

    if len(rows) < 150:
        conn.close()
        return {'error': f'K线数据不足（仅{len(rows)}条，需要≥150条）'}

    klines = [dict(r) for r in rows]
    dates = [k['date'] for k in klines]

    # 找到 start_date 和 end_date 在 klines 中的索引
    try:
        start_idx = dates.index(start_date)
    except ValueError:
        # 找最接近的日期
        start_idx = next((i for i, d in enumerate(dates) if d >= start_date), 0)

    try:
        end_idx = dates.index(end_date)
    except ValueError:
        end_idx = next((i for i in range(len(dates)-1, -1, -1) if dates[i] <= end_date), len(dates)-1)

    # ── 2. 预计算均线 ──
    ma5 = []; ma10 = []; ma20 = []; ma30 = []; ma60 = []; ma50 = []
    for i in range(len(klines)):
        ma5.append(compute_ma(klines, i, 5))
        ma10.append(compute_ma(klines, i, 10))
        ma20.append(compute_ma(klines, i, 20))
        ma30.append(compute_ma(klines, i, 30))
        ma60.append(compute_ma(klines, i, 60))
        ma50.append(compute_ma(klines, i, 50))

    # ── 3. 调用 mw_signal.scan_stock 统一检测 H/L/C/B1/B2 ──
    # 与 PRD v5.2 保持完全一致，不再维护独立的检测逻辑。
    try:
        import scanners.mw_signal as mw
    except ImportError:
        conn.close()
        return {'error': '无法导入 mw_signal 模块'}

    # 设置模块级缓存（scan_stock 内部会用到）
    mw._rs_cache = mw._rs_cache or {}
    mw._idx_comp_cache = mw._idx_comp_cache or None
    mw._idx_rs_cache = mw._idx_rs_cache or {}
    mw._reso_cache = mw._reso_cache or {}
    mw._names_cache = mw._names_cache or {}
    mw._sell_existing_cache = mw._sell_existing_cache or {}
    # 注意：不预填充 _chanlun_cache，让 scan_stock 走内部的 ORDER BY DESC LIMIT 1 兜底
    # 这样与 pattern-scan 页面行为一致

    passed, result = mw.scan_stock(klines, end_date, code, conn)

    if not passed or not result:
        conn.close()
        return {'error': f'未检测到MW信号结构（{code}，区间 {start_date}~{end_date}）'}

    # 从 scan_stock 结果中提取日期 → 对应 klines 索引
    try:
        h_idx = dates.index(result['h_date'])
        l_idx = dates.index(result['l_date'])
        h_price = result['h_price']
        l_price = result['l_price']
        consolidation_start = dates.index(result['c_start'])
        consolidation_end = dates.index(result['c_end'])
        b1_idx = dates.index(result['b1_date']) if result.get('b1_date') else None
        b2_idx = dates.index(result['b2_date']) if result.get('b2_date') else None
    except (ValueError, KeyError) as e:
        conn.close()
        return {'error': f'日期映射失败: {e}'}

    # ── 4. 整理段 (P) ──
    p_start = b1_idx + 1 if b1_idx is not None else consolidation_end + 1
    if b2_idx is not None:
        p_end = b2_idx - 1
    elif b1_idx is not None:
        p_end = min(b1_idx + 15, end_idx)
    else:
        p_end = p_start

    # ── 8. 计算 6 阶段特征 ──
    features = compute_features(klines, h_idx, l_idx, h_price, l_price,
                                 consolidation_start, consolidation_end,
                                 b1_idx, b2_idx,
                                 p_start, p_end,
                                 end_idx,
                                 ma5, ma10, ma20, ma30, ma50, ma60,
                                 code, conn)

    conn.close()

    return {
        'code': code,
        'start_date': start_date,
        'end_date': end_date,
        'nodes': {
            'H': {'date': klines[h_idx]['date'], 'idx': h_idx, 'price': round(h_price, 2)},
            'L': {'date': klines[l_idx]['date'], 'idx': l_idx, 'price': round(l_price, 2)},
            'C_start': {'date': klines[consolidation_start]['date'], 'idx': consolidation_start},
            'C_end': {'date': klines[consolidation_end]['date'], 'idx': consolidation_end},
            'B1': {'date': klines[b1_idx]['date'] if b1_idx else None, 'idx': b1_idx,
                   'price': round(klines[b1_idx]['close'], 2) if b1_idx else None},
            'B2': {'date': klines[b2_idx]['date'] if b2_idx else None, 'idx': b2_idx,
                   'price': round(klines[b2_idx]['close'], 2) if b2_idx else None},
        },
        'features': features
    }


def compute_features(klines, h_idx, l_idx, h_price, l_price, c_start, c_end, b1_idx, b2_idx, p_start, p_end, end_idx,
                     ma5, ma10, ma20, ma30, ma50, ma60, code, conn):
    """计算 6 个阶段的约 30 项量化特征"""
    f = {}

    # 辅助函数
    def avg_vol(start, end):
        vs = [klines[i]['volume'] for i in range(max(0, start), min(end + 1, len(klines)))
              if klines[i].get('volume') is not None]
        return sum(vs) / len(vs) if vs else 0

    def max_vol_day(start, end):
        best = None
        max_v = 0
        for i in range(max(0, start), min(end + 1, len(klines))):
            v = klines[i].get('volume') or 0
            if v > max_v:
                max_v = v
                best = i
        return best, max_v

    def price_range(start, end):
        cs = [klines[i]['close'] for i in range(max(0, start), min(end + 1, len(klines)))
              if klines[i].get('close') is not None]
        return (min(cs), max(cs)) if cs else (0, 0)

    def closes(start, end):
        return [klines[i]['close'] for i in range(max(0, start), min(end + 1, len(klines)))
                if klines[i].get('close') is not None]

    # ═══ H 前高段 ═══
    h_date = klines[h_idx]['date']; h_price = klines[h_idx]['close']
    h_start = max(0, h_idx - 20)
    f['H_date'] = h_date
    f['H_price'] = round(h_price, 2)
    f['H_is_n_day_high'] = '是'  # 120日内最高（定义如此）
    f['H_sma50_slope'] = round((ma50[h_idx] - ma50[max(0, h_idx-10)]) / ma50[max(0, h_idx-10)] * 100, 2) if ma50[h_idx] and ma50[max(0, h_idx-10)] else None
    f['H_close_vs_sma50'] = round((h_price / ma50[h_idx] - 1) * 100, 2) if ma50[h_idx] else None

    # RS 数据
    row = conn.execute(
        "SELECT rps_20, rps_120, rps_250 FROM stock_rs_daily WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1",
        (code, h_date)
    ).fetchone()
    if row:
        f['H_rs20'] = row['rps_20']
        f['H_rs120'] = row['rps_120']
        f['H_rs250'] = row['rps_250']
    else:
        f['H_rs20'] = f['H_rs120'] = f['H_rs250'] = None

    # 行业RS
    row2 = conn.execute(
        "SELECT industry_name FROM discipline_observation_pool WHERE stock_code=? ORDER BY date DESC LIMIT 1",
        (code,)
    ).fetchone()
    industry = row2['industry_name'] if row2 else None
    f['H_industry'] = industry

    # ═══ D 调整段 ═══
    d_prices = closes(h_idx, l_idx)
    f['D_decline_pct'] = round((l_price - h_price) / h_price * 100, 2)  # 负值 = 下跌
    f['D_days'] = l_idx - h_idx
    f['D_max_daily_drop'] = None
    max_drop = 0
    for i in range(h_idx + 1, l_idx + 1):
        drop = (klines[i]['close'] / klines[i-1]['close'] - 1) if klines[i-1]['close'] > 0 else 0
        if drop < max_drop:
            max_drop = drop
    f['D_max_daily_drop'] = round(max_drop * 100, 2)
    f['D_vol_avg'] = round(avg_vol(h_idx, l_idx), 0)

    # ═══ C 横盘段 ═══
    c_days = c_end - c_start + 1
    c_range = price_range(c_start, c_end)
    c_amp = (c_range[1] - c_range[0]) / c_range[0] * 100 if c_range[0] > 0 else 0
    c_lows = closes(c_start, c_end)
    f['C_days'] = c_days
    f['C_amplitude_pct'] = round(c_amp, 2)
    f['C_low_slope'] = round(linear_slope(c_lows) / (sum(c_lows)/len(c_lows)) * 100, 3) if c_lows else None  # 低点趋势
    f['C_vol_avg'] = round(avg_vol(c_start, c_end), 0)
    f['C_vol_vs_D'] = round(f['C_vol_avg'] / f['D_vol_avg'], 2) if f['D_vol_avg'] and f['D_vol_avg'] > 0 else None  # 横盘量萎缩程度

    # 横盘期 AD 线趋势（简化版：用收盘位置近似）
    c_close_positions = []
    for i in range(c_start, c_end + 1):
        k = klines[i]
        if k['high'] != k['low']:
            pos = (k['close'] - k['low']) / (k['high'] - k['low'])
        else:
            pos = 0.5
        c_close_positions.append(pos)
    f['C_ad_slope'] = round(linear_slope(c_close_positions), 4) if len(c_close_positions) >= 3 else None

    # ═══ B1 突破日1 ═══
    if b1_idx:
        b1k = klines[b1_idx]
        b1_ret = (b1k['close'] / klines[b1_idx-1]['close'] - 1) if b1_idx > 0 else 0
        recent_vol = avg_vol(max(0, b1_idx-20), b1_idx-1)
        vol_ratio_20 = b1k['volume'] / recent_vol if recent_vol > 0 else 0

        if b1k['high'] != b1k['low']:
            b1_pos = (b1k['close'] - b1k['low']) / (b1k['high'] - b1k['low'])
        else:
            b1_pos = 1.0

        # 突破均线条数
        b1_mas = 0
        for ma_val, ma_name in [(ma5[b1_idx], 'MA5'), (ma10[b1_idx], 'MA10'),
                                 (ma20[b1_idx], 'MA20'), (ma30[b1_idx], 'MA30'),
                                 (ma60[b1_idx], 'MA60')]:
            if ma_val and b1k['close'] > ma_val:
                b1_mas += 1

        # 是否创横盘新高
        c_high = max(klines[i]['close'] for i in range(c_start, c_end + 1))
        b1_new_high = b1k['close'] > c_high

        # 量 vs 横盘最大量
        _, c_max_vol = max_vol_day(c_start, c_end)
        vol_vs_c_max = b1k['volume'] / c_max_vol if c_max_vol > 0 else 0

        f['B1_date'] = klines[b1_idx]['date']
        f['B1_return_pct'] = round(b1_ret * 100, 2)
        f['B1_close_pos'] = round(b1_pos * 100, 1)
        f['B1_vol'] = b1k['volume']
        f['B1_vol_ratio_vs_20d'] = round(vol_ratio_20, 2)
        f['B1_vol_ratio_vs_50d'] = round(b1k['volume'] / avg_vol(max(0, b1_idx-50), b1_idx-1), 2) if avg_vol(max(0, b1_idx-50), b1_idx-1) > 0 else None
        f['B1_vol_vs_C_max'] = round(vol_vs_c_max, 2)
        f['B1_ma_break_count'] = b1_mas
        f['B1_new_high_vs_C'] = '是' if b1_new_high else '否'
        f['B1_amount'] = b1k.get('amount', 0)
    else:
        for k in ['B1_date','B1_return_pct','B1_close_pos','B1_vol','B1_vol_ratio_vs_20d',
                   'B1_vol_ratio_vs_50d','B1_vol_vs_C_max','B1_ma_break_count','B1_new_high_vs_C','B1_amount']:
            f[k] = None

    # ═══ P 整理段 ═══
    if b1_idx and b2_idx:
        p_days = b2_idx - b1_idx - 1
        p_range = price_range(b1_idx + 1, b2_idx - 1)
        p_max_dd = 0
        b1_close = klines[b1_idx]['close']
        for i in range(b1_idx + 1, b2_idx):
            dd = (klines[i]['close'] / b1_close - 1)
            if dd < p_max_dd:
                p_max_dd = dd
        # 是否守住 B1 低点
        b1_low = klines[b1_idx]['low']
        held_low = all(klines[i]['low'] >= b1_low * 0.98 for i in range(b1_idx + 1, b2_idx))

        f['P_days'] = p_days
        f['P_max_drawdown_pct'] = round(p_max_dd * 100, 2)
        f['P_held_b1_low'] = '是' if held_low else '否'
        f['P_vol_avg'] = round(avg_vol(b1_idx + 1, b2_idx - 1), 0)
        f['P_vol_vs_B1'] = round(f['P_vol_avg'] / klines[b1_idx]['volume'], 2) if klines[b1_idx]['volume'] > 0 else None
    else:
        for k in ['P_days','P_max_drawdown_pct','P_held_b1_low','P_vol_avg','P_vol_vs_B1']:
            f[k] = None

    # ═══ B2 突破日2 ═══
    if b2_idx:
        b2k = klines[b2_idx]
        b2_ret = (b2k['close'] / klines[b2_idx-1]['close'] - 1) if b2_idx > 0 else 0
        recent_vol_b2 = avg_vol(max(0, b2_idx-20), b2_idx-1)
        vol_ratio_20_b2 = b2k['volume'] / recent_vol_b2 if recent_vol_b2 > 0 else 0

        if b2k['high'] != b2k['low']:
            b2_pos = (b2k['close'] - b2k['low']) / (b2k['high'] - b2k['low'])
        else:
            b2_pos = 1.0

        # 是否跳空
        is_gap = b2k['open'] > klines[b2_idx-1]['high'] if b2_idx > 0 else False

        # 突破均线条数
        b2_mas = 0
        for ma_val, ma_name in [(ma5[b2_idx], 'MA5'), (ma10[b2_idx], 'MA10'),
                                 (ma20[b2_idx], 'MA20'), (ma30[b2_idx], 'MA30'),
                                 (ma60[b2_idx], 'MA60')]:
            if ma_val and b2k['close'] > ma_val:
                b2_mas += 1

        # 是否创 50 日新高
        h50 = max(klines[i]['close'] for i in range(max(0, b2_idx-50), b2_idx))
        is_50d_high = b2k['close'] >= h50 * 0.99

        f['B2_date'] = klines[b2_idx]['date']
        f['B2_return_pct'] = round(b2_ret * 100, 2)
        f['B2_is_gap'] = '是' if is_gap else '否'
        f['B2_close_pos'] = round(b2_pos * 100, 1)
        f['B2_vol'] = b2k['volume']
        f['B2_vol_ratio_vs_20d'] = round(vol_ratio_20_b2, 2)
        f['B2_vol_ratio_vs_50d'] = round(b2k['volume'] / avg_vol(max(0, b2_idx-50), b2_idx-1), 2) if avg_vol(max(0, b2_idx-50), b2_idx-1) > 0 else None
        f['B2_vol_vs_B1'] = round(b2k['volume'] / klines[b1_idx]['volume'], 2) if b1_idx and klines[b1_idx]['volume'] > 0 else None
        f['B2_ma_break_count'] = b2_mas
        f['B2_is_50d_high'] = '是' if is_50d_high else '否'
        f['B2_amount'] = b2k.get('amount', 0)
    else:
        for k in ['B2_date','B2_return_pct','B2_is_gap','B2_close_pos','B2_vol','B2_vol_ratio_vs_20d',
                   'B2_vol_ratio_vs_50d','B2_vol_vs_B1','B2_ma_break_count','B2_is_50d_high','B2_amount']:
            f[k] = None

    # ── B1 → end 的总体表现 ──
    if b1_idx:
        b1_close = klines[b1_idx]['close']
        end_close = klines[end_idx]['close'] if end_idx < len(klines) else klines[-1]['close']
        f['total_return_B1_to_end'] = round((end_close / b1_close - 1) * 100, 2)

    return f


if __name__ == '__main__':
    # 测试
    import json
    result = analyze_structure('601138', '2025-10-01', '2026-04-10')
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
