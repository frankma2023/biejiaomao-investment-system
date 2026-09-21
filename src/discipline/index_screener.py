"""
欧奈尔每日精选（指数版）— 四层筛选引擎 v1

第一层：大盘闸门（提醒不过滤）
第二层：RS 强度（RPS250≥75 且 RPS20≥80）
第三层：趋势健康（MA多头 + 量能放大）
第四层：技术信号（base_breakout / pocket_pivot）
"""

import os, sys, sqlite3, json, argparse
import numpy as np
try: import talib
except ImportError: talib = None
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')

# ── 阈值 ──
RPS_250_MIN = 75
RPS_20_MIN = 80
TOP_N = 20
BUY_SIGNALS = {'base_breakout', 'pocket_pivot'}
SIGNAL_BASE_SCORES = {'base_breakout': 70, 'pocket_pivot': 70}
DECAY_WINDOWS = [(5, 1.0), (10, 0.7), (20, 0.4)]
VETO_SIGNALS = {'top_pattern', 'climax_top', 'breakout_failure', 'distribution_day'}

POOL_LABELS = {
    'market': '市场指数', 'sector_l1': '一级行业',
    'sector_l2': '二级行业', 'thematic': '行业主题', 'strategy': '策略指数'
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _compute_indicators(klines):
    if talib is None or len(klines) < 50:
        return {}
    close = np.array([k.get('close') or np.nan for k in klines], dtype=np.float64)
    result = {}
    try:
        for p in [5, 10, 20, 50, 120, 250]:
            sma = talib.SMA(close, p)
            result[f'sma{p}'] = [float(x) if not np.isnan(x) else None for x in sma]
        bb_u, bb_m, bb_l = talib.BBANDS(close, 20, 2, 2, 0)
        result['bb_upper'] = [float(x) if not np.isnan(x) else None for x in bb_u]
        result['bb_middle'] = [float(x) if not np.isnan(x) else None for x in bb_m]
        result['bb_lower'] = [float(x) if not np.isnan(x) else None for x in bb_l]
    except Exception:
        pass
    return result


def load_index_pools():
    """加载 index_style.yaml 中的 5 个池"""
    import yaml
    cfg_path = os.path.join(PROJECT_ROOT, 'config', 'index_style.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    pools = {}
    for cat_name, items in cfg.get('categories', {}).items():
        pools[cat_name] = [item['code'] for item in items]
    return pools


def load_index_names():
    """加载指数代码→名称映射"""
    import yaml
    cfg_path = os.path.join(PROJECT_ROOT, 'config', 'index_style.yaml')
    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    names = {}
    for items in cfg.get('categories', {}).values():
        for item in items:
            names[item['code']] = item['name']
    return names


def check_trend_health(klines):
    """第三层：均线多头 + 量能放大"""
    if len(klines) < 55:
        return False
    close = [k['close'] for k in klines if k.get('close')]
    vol = [k['volume'] for k in klines if k.get('volume')]

    if len(close) < 50:
        return False

    # 简易均线
    ma10 = sum(close[-10:]) / 10
    ma20 = sum(close[-20:]) / 20
    ma50 = sum(close[-50:]) / 50

    if not (ma10 > ma20 > ma50):
        return False

    # 近5日量 > 50日均量 × 1.2
    vol_5 = sum(vol[-5:]) / 5 if len(vol) >= 5 else 0
    vol_50 = sum(vol[-50:]) / 50 if len(vol) >= 50 else 0
    if vol_50 <= 0 or vol_5 < vol_50 * 1.2:
        return False

    return True


def run(target_date=None):
    db = get_db()

    # ── 第一层：大盘闸门 ──
    market = db.execute("""
        SELECT market_phase FROM market_direction_daily ORDER BY date DESC LIMIT 1
    """).fetchone()
    phase = market['market_phase'] if market else ''
    market_warning = not (phase and ('上升' in phase or '确认' in phase or '反弹' in phase))
    if market_warning:
        print(f"[index_screener] market={phase}, non-ideal env, still screening")

    # ── 加载指数池 ──
    pools = load_index_pools()
    idx_names = load_index_names()

    # ── 确定日期 ──
    if target_date is None:
        target_date = datetime.now().strftime('%Y-%m-%d')
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')

    # ── 加载最新 RS ──
    rs_date_row = db.execute("SELECT MAX(date) FROM index_rs_daily").fetchone()
    if not rs_date_row or not rs_date_row[0]:
        print("[index_screener] 无 RS 数据")
        db.close()
        return {'items': [], 'market_phase': phase}
    rs_date = rs_date_row[0]

    rs_map = {}
    rs_rows = db.execute(
        "SELECT stock_code, rs_20, rs_250 FROM index_rs_daily WHERE date=?",
        (rs_date,)
    ).fetchall()
    for r in rs_rows:
        rs_map[r['stock_code']] = {'rs_20': r['rs_20'] or 0, 'rs_250': r['rs_250'] or 0}

    # ── 遍历所有指数 ──
    results = []
    for pool_name, codes in pools.items():
        for code in codes:
            rs = rs_map.get(code, {})
            rps_250 = rs.get('rs_250', 0)
            rps_20 = rs.get('rs_20', 0)

            # Layer 2: RS
            if rps_250 < RPS_250_MIN or rps_20 < RPS_20_MIN:
                continue

            # 取 K 线
            krows = db.execute("""
                SELECT date, open, high, low, close, volume
                FROM index_daily_kline WHERE stock_code=? AND kline_type='normal'
                ORDER BY date DESC LIMIT 400
            """, (code,)).fetchall()
            if len(krows) < 55:
                continue
            klines = [dict(k) for k in reversed(krows)]

            # Layer 3: 趋势健康
            if not check_trend_health(klines):
                continue

            # Layer 4: 技术信号
            from engine_registry import run_all_engines
            ind = _compute_indicators(klines)
            try:
                signals = run_all_engines(klines=klines, indicators=ind,
                    whitelist=['base_breakout', 'pocket_pivot', 'cdl_engine', 'talib_engine'])
            except Exception:
                signals = []

            # 一票否决
            has_veto = False
            cutoff_20d = target_dt - timedelta(days=20)
            for s in signals:
                if s.get('source', '') in VETO_SIGNALS:
                    sd_str = s.get('date', s.get('signal_date', ''))
                    if sd_str:
                        try:
                            if datetime.strptime(sd_str, '%Y-%m-%d') >= cutoff_20d:
                                has_veto = True
                                break
                        except ValueError:
                            pass
            if has_veto:
                continue

            # 信号评分（与股票版一致）
            has_buy_signal = False
            signal_best = 0
            signal_best_src = ''
            signal_best_date = ''
            signal_extra_base = 0
            signal_extra_cdl = 0
            signal_count = 0
            signal_sources_recent = set()
            cutoff_signal = target_dt - timedelta(days=20)
            ideal_buy = None
            buy_source = ''

            for s in signals:
                src = s.get('source', '')
                sig_date = s.get('date', s.get('signal_date', ''))
                if not sig_date:
                    continue

                try:
                    sd = datetime.strptime(sig_date, '%Y-%m-%d')
                except ValueError:
                    continue

                days_ago = (target_dt - sd).days
                decay = 0
                for win_days, factor in DECAY_WINDOWS:
                    if days_ago <= win_days:
                        decay = factor
                        break
                if decay == 0:
                    continue

                # 检查通过条件
                if not has_buy_signal and src in BUY_SIGNALS and sd >= cutoff_signal:
                    has_buy_signal = True

                # 主信号分
                if src in SIGNAL_BASE_SCORES and s.get('type') != 'bearish':
                    cs = SIGNAL_BASE_SCORES[src] * decay
                    if cs > signal_best:
                        signal_best = cs
                        signal_best_src = src
                        signal_best_date = sig_date
                        bp = s.get('close') or s.get('breakout_close') or s.get('breakout_price') or s.get('buy_point')
                        if bp:
                            ideal_buy = round(bp, 2)
                            buy_source = src
                    if src in BUY_SIGNALS:
                        signal_extra_base += 5
                elif 'cdl' in src or src == 'cdl':
                    signal_extra_cdl += 2
                elif 'talib' in src or src == 'talib':
                    signal_extra_cdl += 2

                if sd >= cutoff_signal:
                    signal_sources_recent.add(src)
                    signal_count += 1

            if not has_buy_signal:
                continue

            # 排除最佳信号本身的额外加分
            if signal_best_src and signal_best_src in BUY_SIGNALS and signal_extra_base >= 5:
                signal_extra_base -= 5

            # 信号分
            signal_final = signal_best + min(signal_extra_base, 20) + min(signal_extra_cdl, 10)
            signal_final = min(signal_final, 100)

            # 综合得分
            rps_component = min(rps_250, 100) / 100 * 50
            signal_component = signal_final / 100 * 50
            index_score = round(rps_component + signal_component, 0)
            index_score = min(index_score, 100)

            signal_list = list(signal_sources_recent)
            signal_summary = ' / '.join(signal_list[:4])

            results.append({
                'index_code': code,
                'index_name': idx_names.get(code, code),
                'pool_name': POOL_LABELS.get(pool_name, pool_name),
                'index_score': index_score,
                'rps_250': rps_250,
                'rps_20': rps_20,
                'signal_score': signal_final,
                'signal_count': signal_count,
                'signal_summary': signal_summary,
                'ideal_buy': ideal_buy,
                'buy_signal_date': signal_best_date,
                'buy_source': buy_source,
            })

    # 排序 → TOP 20
    results.sort(key=lambda x: x['index_score'], reverse=True)
    results = results[:TOP_N]

    # ── 自动写入快照 ──
    try:
        db.execute("DELETE FROM discipline_screening_daily_index WHERE date=?", (target_date,))
        for i, r in enumerate(results):
            db.execute("""
                INSERT INTO discipline_screening_daily_index
                (date, rank, index_code, index_name, pool_name, index_score,
                 rps_250, rps_20, signal_score, signal_count, signal_summary,
                 ideal_buy, buy_signal_date, buy_source, market_phase)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                target_date, i+1,
                r['index_code'], r['index_name'], r['pool_name'],
                r['index_score'], r['rps_250'], r['rps_20'],
                r['signal_score'], r['signal_count'], r['signal_summary'],
                r['ideal_buy'], r['buy_signal_date'], r['buy_source'], phase
            ))
        db.commit()
        print(f"[index_screener] 已写入 {len(results)} 条指数精选快照")
    except sqlite3.OperationalError:
        pass

    print(f"[index_screener] 四层筛选完成: {len(results)} 只指数 (TOP {TOP_N})")
    db.close()

    return {
        'market_warning': market_warning,
        'market_phase': phase,
        'items': results,
        'date': target_date,
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='欧奈尔每日精选（指数版）')
    ap.add_argument('--date', type=str, default=None)
    args = ap.parse_args()
    result = run(target_date=args.date)
    if result.get('market_warning'):
        print(f"[WARN] 市场提醒: {result['market_phase']}（非理想买入环境）")
    for i, r in enumerate(result['items']):
        print(f"  #{i+1} {r['index_code']} {r['index_name']} "
              f"得分={r['index_score']:.0f} RPS250={r['rps_250']} RPS20={r['rps_20']} "
              f"信号={r['signal_summary']} 池={r['pool_name']}")
