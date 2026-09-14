"""
欧奈尔每日精选 — 六层筛选引擎 v2

第一层：大盘闸门（一票否决）
第二层：RS 精英（双强+龙头）
第三层：CANSLIM 质量（总分≥32, ROE≥5%, 营收增速≥5%, 负债≤70%）
第四层：技术形态确认（必须有基部突破类信号，否决顶部/高潮/失败）
第五层：行业共振（命中最强指数加权）
第六层：量价确认 + 综合排序 → TOP 20
"""

import os
import sys
import sqlite3
import json
import argparse
import numpy as np
try:
    import talib
except ImportError:
    talib = None
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


def _compute_indicators(klines):
    """计算技术指标 dict"""
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


# ── 阈值配置 ──
CANSLIM_MIN = 32       # CANSLIM 总分底线（全A前25%≈634只）
ROE_MIN = 5.0          # ROE 底线
REVENUE_YOY_MIN = 5.0  # 营收增速底线
DEBT_MAX = 70.0        # 资产负债率上限
TOP_N = 20             # 最终输出数量
# RS 精英门槛
RPS_250_STRONG = 90    # 长牛底线（回调用，放宽 RPS_20 要求）
RPS_20_BURST = 95      # 短爆发底线（放宽 RPS_250 要求）
RPS_250_MIN = 80       # RS 最低门槛
RPS_20_MIN = 85        # RS 最低门槛

# 买入信号（必须有至少一个）
BUY_SIGNALS = {'base_breakout', 'pocket_pivot'}

# 信号基础分（基部突破 = 口袋支点 = 70，其他形态引擎不计入）
SIGNAL_BASE_SCORES = {
    'base_breakout': 70,
    'pocket_pivot': 70,
}
# 时间衰减窗口（天, 衰减因子）
DECAY_WINDOWS = [(5, 1.0), (10, 0.7), (20, 0.4)]
# 一票否决信号
VETO_SIGNALS = {'top_pattern', 'climax_top', 'breakout_failure', 'distribution_day'}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_strongest_indices(db):
    """加载当日最强指数 TOP 5×3 池"""
    # 读取 index_rs_daily 最新日期
    latest = db.execute("SELECT MAX(date) FROM index_rs_daily").fetchone()
    if not latest or not latest[0]:
        return {}
    ldate = latest[0]

    # 加载指数池配置
    try:
        from server import load_index_pools, load_index_names
    except ImportError:
        return {}

    pools_cfg = load_index_pools()
    idx_names = load_index_names()

    strongest = {}
    # 每个池取 TOP 5
    for pn in ['sector_l2', 'thematic', 'strategy']:
        if pn not in pools_cfg:
            continue
        codes = pools_cfg[pn]
        ph = ','.join(['?' for _ in codes])
        rows = db.execute(
            f"SELECT stock_code, rs_20, rs_60, rs_250 FROM index_rs_daily "
            f"WHERE date=? AND stock_code IN ({ph}) "
            f"AND rs_250>=80 AND rs_60>=85 AND rs_20>=90 "
            f"ORDER BY rs_20 DESC, rs_60 DESC, rs_250 DESC LIMIT 5",
            [ldate] + codes
        ).fetchall()
        strongest[pn] = []
        for r in rows:
            strongest[pn].append({
                'code': r['stock_code'],
                'name': idx_names.get(r['stock_code'], r['stock_code']),
                'rs_20': r['rs_20'], 'rs_60': r['rs_60'], 'rs_250': r['rs_250']
            })
    return strongest


POOL_LABELS = {'sector_l2': '二级行业', 'thematic': '行业主题', 'strategy': '策略指数'}


def check_industry_resonance(db, stock_code, strongest_indices):
    """检查股票是否属于最强指数，返回 (权重, 指数名, 所属池名)"""
    if not strongest_indices:
        return 1.0, '', ''

    # 构建 code→(name, pool) 映射
    all_strong = []
    for pn, indices in strongest_indices.items():
        for s in indices:
            all_strong.append((s['code'], s['name'], POOL_LABELS.get(pn, pn)))

    codes = [s[0] for s in all_strong]
    ph = ','.join(['?' for _ in codes])
    if not codes:
        return 1.0, '', ''

    # 精确匹配：index_constituents
    row = db.execute(
        f"SELECT DISTINCT index_code FROM index_constituents "
        f"WHERE stock_code=? AND index_code IN ({ph})",
        [stock_code] + codes
    ).fetchone()
    if row:
        match = next((s for s in all_strong if s[0] == row['index_code']), None)
        if match:
            return 1.25, match[1], match[2]

    # 模糊匹配：stock_index
    row = db.execute(
        f"SELECT DISTINCT index_code FROM stock_index "
        f"WHERE stock_code=? AND index_code IN ({ph})",
        [stock_code] + codes
    ).fetchone()
    if row:
        match = next((s for s in all_strong if s[0] == row['index_code']), None)
        if match:
            return 1.15, match[1], match[2]

    return 1.0, '', ''


def run(target_date=None):
    db = get_db()

    # ── 第一层：大盘闸门 ──
    market = db.execute("""
        SELECT market_phase, risk_level, suggested_position_size
        FROM market_direction_daily ORDER BY date DESC LIMIT 1
    """).fetchone()
    market_data = dict(market) if market else {}
    phase = market_data.get('market_phase', '')
    market_warning = False
    if not (phase and ('上升' in phase or '确认' in phase or '反弹' in phase)):
        market_warning = True
        print(f"[screener] market={phase}, non-ideal env, still screening")

    # ── 确定日期 ──
    if target_date is None:
        row = db.execute("SELECT MAX(date) FROM discipline_observation_pool").fetchone()
        if not row or not row[0]:
            db.close()
            return {'market_gate': 'ok', 'market_warning': market_warning, 'market_phase': phase, 'items': [], 'strongest_indices': {}, 'date': None}
        target_date = row[0]

    # ── 标准化日期 ──
    target_dt = datetime.strptime(target_date, '%Y-%m-%d')

    # ── 最强指数 ──
    strongest_indices = load_strongest_indices(db)

    # ── 读取观察池 ──
    obs_rows = db.execute("""
        SELECT * FROM discipline_observation_pool WHERE date = ?
    """, (target_date,)).fetchall()

    # ── 注入最新 CANSLIM 评分（绕过观察池快照延迟）──
    cs_map = {}
    cs_rows = db.execute("""
        SELECT stock_code, score as canslim_total,
               score_c as canslim_c, score_a as canslim_a,
               score_n as canslim_n, score_s as canslim_s,
               score_l as canslim_l, score_i as canslim_i
        FROM cansim_scores
        WHERE (stock_code, date) IN (
            SELECT stock_code, MAX(date) FROM cansim_scores GROUP BY stock_code
        )
    """).fetchall()
    for cs in cs_rows:
        cs_map[cs['stock_code']] = dict(cs)

    results = []
    for r in obs_rows:
        code = r['stock_code']

        # ── 第二层：RS 精英 ──
        rps_250 = r['rps_250'] or 0
        rps_20 = r['rps_20'] or 0
        rs_cat = r['rs_category'] or ''

        # 三档 RS 通行：
        # A. 标准精英：RPS_250≥80 且 RPS_20≥85（稳健龙头/加速爆发/双强）
        standard_elite = (rps_250 >= RPS_250_MIN and rps_20 >= RPS_20_MIN)
        # B. 长牛回调：RPS_250≥90（长期强势），即使 RPS_20<85 — 依赖信号豁免
        strong_long = (rps_250 >= RPS_250_STRONG)
        # C. 短爆发：RPS_20≥95，即使 RPS_250<80
        short_burst = (rps_20 >= RPS_20_BURST)

        if standard_elite:
            pass  # 通关
        elif strong_long:
            pass  # 待第四层信号拯救（见下方 buy_signal_check）
        elif short_burst:
            pass  # 通关
        else:
            continue

        # ── 第三层：CANSLIM 质量 ──
        cs = cs_map.get(code, {})
        canslim_total = cs.get('canslim_total') or r['canslim_total'] or 0
        canslim_c = cs.get('canslim_c') or r['canslim_c'] or 0
        canslim_a = cs.get('canslim_a') or r['canslim_a'] or 0
        canslim_n = cs.get('canslim_n') or r['canslim_n'] or 0
        canslim_s = cs.get('canslim_s') or r['canslim_s'] or 0
        canslim_l = cs.get('canslim_l') or r['canslim_l'] or 0
        canslim_i = cs.get('canslim_i') or r['canslim_i'] or 0
        roe = r['roe'] or 0
        rev_yoy = r['revenue_yoy'] or 0
        debt = r['debt_ratio'] or 0

        if canslim_total < CANSLIM_MIN:
            continue
        if roe < ROE_MIN:
            continue
        if rev_yoy < REVENUE_YOY_MIN:
            continue
        if debt > DEBT_MAX:
            continue

        # ── 第四层：技术形态 ──
        # 加载 K 线 + 实时扫描引擎获取完整信号
        kline_rows = db.execute("""
            SELECT date, open, high, low, close, volume
            FROM daily_kline_adj WHERE stock_code=? ORDER BY date DESC LIMIT 400
        """, (code,)).fetchall()
        if not kline_rows:
            kline_rows = db.execute("""
                SELECT date, open, high, low, close, volume
                FROM index_daily_kline WHERE stock_code=? ORDER BY date DESC LIMIT 400
            """, (code,)).fetchall()
        klines = [dict(r) for r in reversed(kline_rows)]

        signals = []
        if len(klines) >= 50:
            try:
                from engine_registry import run_all_engines
                ind = _compute_indicators(klines)
                signals = run_all_engines(klines=klines, indicators=ind)
            except Exception:
                # 引擎不可用时回退观察池快照
                if r['signals_json']:
                    try: signals = json.loads(r['signals_json'])
                    except: pass

        # 检查一票否决信号（20日内）
        has_veto = False
        cutoff_20d = target_dt - timedelta(days=20)
        for s in signals:
            src = s.get('source', '')
            if src in VETO_SIGNALS:
                sig_date = s.get('date', s.get('signal_date', ''))
                if sig_date:
                    try:
                        sd = datetime.strptime(sig_date, '%Y-%m-%d')
                        if sd >= cutoff_20d:
                            has_veto = True
                            break
                    except ValueError:
                        pass
        if has_veto:
            continue

        # 信号窗口：长牛回调股收紧至 10 日，其余 20 日
        correction_stock = strong_long and not standard_elite and not short_burst
        signal_window = 10 if correction_stock else 20
        cutoff_signal = target_dt - timedelta(days=signal_window)

        # 必须有至少一个基部突破类信号
        has_buy_signal = False
        signal_best = 0           # 最佳主信号分
        signal_best_src = ''      # 最佳主信号类型
        signal_best_date = ''     # 最佳主信号日期
        signal_extra_base = 0     # 额外基部/口袋加分
        signal_extra_cdl = 0      # 额外 cdl/talib 加分
        signal_count = 0
        signal_count_total = 0
        signal_sources = set()
        signal_sources_recent = set()
        ideal_buy = None
        buy_source = ''

        for s in signals:
            src = s.get('source', '')
            sig_date = s.get('date', s.get('signal_date', ''))
            signal_sources.add(src)

            # 检查信号窗口内的买入信号
            if not has_buy_signal and src in BUY_SIGNALS:
                if sig_date:
                    try:
                        sd = datetime.strptime(sig_date, '%Y-%m-%d')
                        if sd >= cutoff_signal:
                            has_buy_signal = True
                    except ValueError:
                        pass

            # 信号加分：最佳信号为主分 + 额外信号小额加分
            if s.get('type') != 'bearish' and sig_date:
                try:
                    sd = datetime.strptime(sig_date, '%Y-%m-%d')
                    days_ago = (target_dt - sd).days
                except ValueError:
                    continue

                # 时间衰减
                decay = 0
                for win_days, factor in DECAY_WINDOWS:
                    if days_ago <= win_days:
                        decay = factor
                        break
                if decay == 0:
                    continue

                # 主信号分
                if src in SIGNAL_BASE_SCORES:
                    candidate_score = SIGNAL_BASE_SCORES[src] * decay
                    if candidate_score > signal_best:
                        signal_best = candidate_score
                        signal_best_src = src
                        signal_best_date = sig_date
                        # 同步取买点价格（与日期保持同一信号）
                        bp = s.get('close') or s.get('breakout_close') or s.get('breakout_price') or s.get('buy_point')
                        if bp:
                            ideal_buy = round(bp, 2)
                            buy_source = src
                    # 额外基部/口袋信号加分（PRD §3.2）
                    if src in BUY_SIGNALS:
                        signal_extra_base += 5
                # cdl/talib 额外加分
                elif 'cdl' in src or src == 'cdl':
                    signal_extra_cdl += 2
                elif 'talib' in src or src == 'talib':
                    signal_extra_cdl += 2

            # 区分信号窗口内和全量
            signal_count_total += 1
            if sig_date:
                try:
                    sd = datetime.strptime(sig_date, '%Y-%m-%d')
                    if sd >= cutoff_signal:
                        signal_count += 1
                        signal_sources_recent.add(src)
                except ValueError:
                    pass

        # 修正：排除最佳信号本身的额外加分（PRD §3.2 "除最佳信号外"）
        if signal_best_src and signal_best_src in BUY_SIGNALS and signal_extra_base >= 5:
            signal_extra_base -= 5

        if not has_buy_signal:
            continue

        # ── 第五层：行业共振 ──
        resonance_weight, resonance_name, resonance_pool = check_industry_resonance(db, code, strongest_indices)

        # ── 第六层：O'Neil 综合得分 ──
        # CANSLIM × 0.30 + RPS250 × 0.30 + 形态信号 × 0.25 + 行业共振 × 0.15
        canslim_component = min(canslim_total, 100) / 100 * 30
        rs_component = min(rps_250, 100) / 100 * 30
        # 信号分 = 最佳主信号分 + min(额外基部加分, 20) + min(额外 cdl/talib 加分, 10)，封顶 100
        signal_final = signal_best + min(signal_extra_base, 20) + min(signal_extra_cdl, 10)
        signal_final = min(signal_final, 100)
        signal_component = signal_final / 100 * 25
        resonance_component = resonance_weight * 15

        oneil_score = round(canslim_component + rs_component + signal_component + resonance_component, 0)
        oneil_score = min(oneil_score, 100)

        # 止损价
        stop_loss = round(ideal_buy * 0.92, 2) if ideal_buy else None

        # 信号摘要（仅近20日）
        signal_list = list(signal_sources_recent)
        signal_summary = ' / '.join(signal_list[:4])

        results.append({
            'stock_code': code,
            'stock_name': r['stock_name'],
            'industry_name': r['industry_name'],
            'rs_category': rs_cat,
            'rps_20': rps_20, 'rps_60': r['rps_60'], 'rps_120': r['rps_120'], 'rps_250': rps_250,
            'canslim_total': canslim_total, 'canslim_c': canslim_c, 'canslim_a': canslim_a,
            'canslim_n': canslim_n, 'canslim_s': canslim_s, 'canslim_l': canslim_l, 'canslim_i': canslim_i,
            'roe': roe, 'revenue_yoy': rev_yoy, 'gross_margin': r['gross_margin'],
            'debt_ratio': debt,
            'oneil_score': oneil_score,
            'signal_count': signal_count,
            'signal_summary': signal_summary,
            'ideal_buy': ideal_buy,
            'buy_source': buy_source,
            'buy_signal_date': signal_best_date,
            'stop_loss': stop_loss,
            'resonance_name': resonance_name,
            'resonance_pool': resonance_pool,
            'resonance_weight': resonance_weight,
            'correction_stock': correction_stock,
            'market_phase': phase,
            'risk_level': market_data.get('risk_level', ''),
            'suggested_position_size': market_data.get('suggested_position_size'),
        })

    # 按得分降序
    results.sort(key=lambda x: x['oneil_score'], reverse=True)
    results = results[:TOP_N]

    # ── 自动写入精选快照表 ──
    try:
        db.execute("DELETE FROM discipline_screening_daily WHERE date = ?", (target_date,))
        for i, r in enumerate(results):
            db.execute("""
                INSERT INTO discipline_screening_daily
                (date, rank, stock_code, stock_name, oneil_score, canslim_total, rps_250,
                 signal_score, signal_count, signal_summary,
                 ideal_buy, buy_signal_date, buy_source, stop_loss,
                 resonance_name, correction_stock, market_phase)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                target_date, i+1,
                r['stock_code'], r['stock_name'],
                r['oneil_score'], r['canslim_total'], r['rps_250'],
                r['signal_count'], r['signal_count'], r['signal_summary'],
                r['ideal_buy'], r['buy_signal_date'], r['buy_source'], r['stop_loss'],
                r['resonance_name'], 1 if r.get('correction_stock') else 0,
                phase
            ))
        db.commit()
        print(f"[screener] 已写入 {len(results)} 条精选快照")
    except sqlite3.OperationalError:
        pass

    print(f"[screener] 六层筛选完成: {len(results)} 只 (TOP {TOP_N})")
    db.close()

    return {
        'market_gate': 'ok',
        'market_warning': market_warning,
        'market_phase': phase,
        'items': results,
        'strongest_indices': strongest_indices,
        'date': target_date,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='欧奈尔每日精选')
    parser.add_argument('--date', type=str, default=None)
    args = parser.parse_args()
    result = run(target_date=args.date)
    if result.get('market_warning'):
        print(f"⚠ 市场提醒: {result['market_phase']}（非理想买入环境，以下精选仅供参考）")
    for i, r in enumerate(result['items']):
        print(f"  #{i+1} {r['stock_code']} {r['stock_name']} "
                  f"得分={r['oneil_score']:.0f} 信号={r['signal_summary']} "
                  f"买点={r['ideal_buy'] or '—'} 共振={r['resonance_name'] or '—'}")
