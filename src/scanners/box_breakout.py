"""
放量箱体突破检测引擎 v1.3
=========================
检测水平箱体（上沿阻力带 + 下沿支撑带）的放量突破。

核心价值（区别于 base_breakout / pocket_pivot）：
  - 输出箱体结构信息：上下沿价格、触碰次数、箱体时长
  - 记录失败尝试累计：第 N 次突破尝试，前 N-1 次失败 → 突破可信度更高
  - 下沿触碰不破 = 支撑确认

信号分级（v1.3，突破日必给信号）：
  strong_confirmed — 突破 + 放量 + 次日站稳（主信号）
  confirmed        — 突破 + 次日站稳（未放量）
  weak             — 突破日信号（次日未确认/跌破保留 weak，标注"突破未确认"）
  failed           — 内部状态：次日跌破上沿，保留 weak 信号 + 计入失败尝试

数据口径：前复权日 K 线（change_pct 逆向推算），防未来（逐日滚动只用当日及之前数据）。
上沿 = 盘中高点聚类，下沿 = 盘中低点聚类（贴合用户画线直觉）。

用法:
  python -m src.scanners.box_breakout --stock 603259 --date 2026-08-07
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "box_breakout",
    "display_name": "放量箱体突破",
    "category": "layer1",
    "type": "bullish",
    "pattern": "box_breakout",
    "version": "1.3",
    "description": "水平箱体识别 + 放量突破：盘中高低点上下沿/触碰次数/失败尝试累计/次日确认",
}


# ══════════════════════════════════════════════
# 参数加载
# ══════════════════════════════════════════════

def load_params() -> Dict:
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "box_breakout.yaml")
    defaults = {
        'lookback_days': 300,     # 箱体识别窗口（交易日）
        'band_tol': 0.03,         # 带容差 ±3%（聚类 + 触碰区间）
        'touch_tol': 0.03,        # 触碰容差：收盘 ∈ [上沿×(1-touch_tol), 上沿] 算触碰
        'break_tol': 0.00,        # 突破容差：收盘 ≥ 上沿×(1+break_tol) 算突破尝试（0=严格站上）
        'min_touches': 3,         # 触碰最少天数
        'box_min_days': 40,       # 箱体最小跨度（交易日）
        'max_box_days': 250,      # 箱体最大跨度（超过降级 weak）
        'max_depth': 0.35,        # 箱体最大深度（下沿距上沿比例）
        'min_depth': 0.03,        # 箱体最小深度（上下沿差 ≥3%，排除窄平台）
        'vol_ma_n': 20,           # 量均线窗口
        'vol_ratio': 1.5,         # 放量倍数
        'hold_days': 1,           # 确认天数（v1.2：次日收盘站稳即成功）
        'local_window': 10,       # 局部极值窗口
        'min_lookback_days': 60,  # 最少数据天数
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        blk = cfg.get('box_breakout', cfg)
        defaults.update(blk)
    return defaults


# ══════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════

def _adj_prices(daily: List[Dict]) -> List[Dict]:
    """前复权：change_pct 逆向推算 adj_close，OHLC 等比缩放（与 server._ensure_adj_prices 一致）"""
    if not daily:
        return daily
    n = len(daily)
    daily[n-1]['adj_close'] = daily[n-1]['close']
    for i in range(n-2, -1, -1):
        chg = daily[i+1].get('change_pct')
        daily[i]['adj_close'] = daily[i+1]['adj_close'] / (1 + chg) if chg is not None else daily[i+1]['adj_close']
    for k in daily:
        ratio = k['adj_close'] / k['close'] if k['close'] else 1
        for f in ('open', 'high', 'low'):
            if k.get(f):
                k[f] = k[f] * ratio
        k['close'] = k['adj_close']
    return daily


def _find_bands(values: List, end_idx: int, is_high: bool, params: Dict, start_idx: int = None) -> List[Dict]:
    """找价格极值聚类带（传入 high 则找阻力带上沿，传入 low 则找支撑带下沿）。返回 [{level, touches}]
    start_idx: 窗口起始索引（None 时用 end_idx - lookback_days）
    """
    start = max(0, end_idx - params['lookback_days']) if start_idx is None else max(0, start_idx)
    seg = values[start:end_idx+1]
    if len(seg) < 60:
        return []

    lw = params['local_window']
    tol = params['band_tol']
    min_touch = params['min_touches']

    extrema = []
    for i in range(lw, len(seg) - lw):
        c = seg[i]
        if c is None:
            continue
        win_l = [x for x in seg[i-lw:i] if x is not None] or [0]
        win_r = [x for x in seg[i+1:i+lw+1] if x is not None] or [0]
        if is_high:
            if c >= max(win_l) and c >= max(win_r):
                extrema.append(c)
        else:
            if c <= min(win_l) and c <= min(win_r):
                extrema.append(c)
    if not extrema:
        return []

    xs = sorted(set(round(x, 2) for x in extrema), reverse=is_high)
    bands = []
    for x in xs:
        placed = False
        for b in bands:
            if abs(x - b['level']) / b['level'] <= tol:
                b['level'] = (max if is_high else min)(b['level'], x)
                placed = True
                break
        if not placed:
            bands.append({'level': x})

    for b in bands:
        if is_high:
            lo, hi = b['level'] * (1 - tol), b['level']
        else:
            lo, hi = b['level'], b['level'] * (1 + tol)
        touch_idx = [i for i, c in enumerate(seg) if c is not None and lo <= c <= hi]
        b['touches'] = len(touch_idx)
        b['first_idx'] = (start + touch_idx[0]) if touch_idx else start  # 带内最早触碰日（绝对索引）
        b['last_idx'] = (start + touch_idx[-1]) if touch_idx else start  # 带内最后触碰日（调试/溯源预留）
        # 触碰中位日：箱体核心时间段（避免分散触碰把 first/last 拉宽）
        b['mid_idx'] = (start + touch_idx[len(touch_idx)//2]) if touch_idx else start

    return [b for b in bands if b['touches'] >= min_touch]


def _pick_support(cands, ub, band_start, params):
    """同期性约束后取最低支撑带（模块级，box_breakdown 复用）。
    双侧约束：下沿核心日与上沿核心日差距 ≤ sync_window（box_min_days × 2）。
    允许下行-横盘模式（上沿先形成、下沿后确认），同时拦截跨期配对
    （如 688665：上沿触碰 2025-08~10，下沿 2026-03 相差 >120 天）。"""
    if not cands:
        return None
    top_mid = ub.get('mid_idx', band_start)
    sync_window = params['box_min_days'] * 2
    synced = [lb for lb in cands
              if abs(lb.get('first_idx', band_start) - top_mid) <= sync_window]
    if not synced:
        return None
    return min(synced, key=lambda x: x['level'])


# ══════════════════════════════════════════════
# 主检测
# ══════════════════════════════════════════════

def detect(daily: List[Dict], params: Optional[Dict] = None) -> List[Dict]:
    """
    检测放量箱体突破，返回全部历史事件（防未来：逐日滚动只用当日及之前数据）。

    Args:
        daily: 日线列表（date/open/high/low/close/volume/change_pct；close 可为前复权）
        params: 参数字典，None 时自动加载

    Returns:
        信号列表，每条含 signal_date / type='bullish' / pattern='box_breakout'
        / signal_level / details
    """
    if params is None:
        params = load_params()

    # 前复权（若调用方已复权则 change_pct 反推结果 ≈ 原值，幂等）
    daily = _adj_prices([dict(k) for k in daily])

    n = len(daily)
    if n < params['min_lookback_days']:
        return []

    closes = [k['close'] for k in daily]
    highs = [k['high'] for k in daily]
    lows = [k['low'] for k in daily]
    vols = [k['volume'] or 0 for k in daily]
    dates = [k['date'] for k in daily]

    vol_ma = [None] * n
    for i in range(n):
        if i >= params['vol_ma_n'] - 1:
            vol_ma[i] = sum(vols[i-params['vol_ma_n']+1:i+1]) / params['vol_ma_n']

    events = []          # 所有突破尝试（含 failed）
    band_state = {}      # band_top -> {'broken_at': idx, 'back_in_box': bool, 'first_break_idx': idx}
    frozen = {}          # band_top -> 冻结的带信息（{top, touches, first_idx}）
    frozen_keys = []     # 已冻结的 key 列表（容差内归一化用）
    expired = set()      # 已有效突破的箱体 key（形态失效，不再参与检测）
    pending_confirm = [] # 待确认事件：{key, trigger_idx, top, volume_ok}

    def _check_pending(t):
        """检查待确认事件：突破后 hold_days 日，站稳则箱体失效（形态完成）"""
        done = []
        hold = params['hold_days']
        for pc in pending_confirm:
            if t - pc['trigger_idx'] >= hold:
                # 确认窗口已过：检查 hold_days 日收盘是否都在上沿上方
                idxs = range(pc['trigger_idx'] + 1, pc['trigger_idx'] + 1 + hold)
                holds = [closes[j] for j in idxs if j < n]
                # 长度守卫：hold 窗口数据不足时不判定站稳（避免 all([]) 假阳性）
                if len(holds) == hold and all(c >= pc['top'] for c in holds):
                    # ✅ 有效突破：站稳，箱体失效（形态完成）
                    expired.add(pc['key'])
                done.append(pc)
        for pc in done:
            pending_confirm.remove(pc)

    for t in range(params['lookback_days'] + params['local_window'] + 1, n):
        if closes[t] is None:
            continue
        # 实时确认待定突破（站稳 hold_days → 箱体失效）
        _check_pending(t)
        upper_bands = _find_bands(highs, t, True, params)  # v1.2：盘中高点聚类上沿
        if not upper_bands:
            continue

        # v1.3：上沿选择——已站上（≤收盘）的带中，从高到低选触碰 ≥ min_touches×3 的带
        # 原因：① 最高带可能是孤立尖峰（688665 的 65.90 触碰7 vs 真实上沿 62.84 触碰18）
        #       ② 触碰最多的带可能是箱体内部平台（603259 的 100.59 触碰39 vs 真实上沿 113.65 触碰9）
        #       ③ 必须已站上（≤收盘），避免选中历史高位旧箱体（600309 的 91.66 vs 当前箱体 76.84）
        touch_threshold = params['min_touches'] * 3
        reached = [b for b in upper_bands
                   if b['level'] <= closes[t] and b['touches'] >= touch_threshold]
        if reached:
            ub = max(reached, key=lambda x: x['level'])
        else:
            cands = [b for b in upper_bands if b['touches'] >= touch_threshold]
            if cands:
                ub = max(cands, key=lambda x: x['level'])
            else:
                ub = max(upper_bands, key=lambda x: x['level'])  # 兜底：最高带

        # 箱体上沿选择：优先已冻结带（容差内）；否则取当前价已站上的最高带；
        # 当前价低于所有带时取最低带（等待突破）
        # 已冻结带优先：冻结值不被新高污染；已失效箱体（有效突破过）跳过
        frozen_candidate = None
        for k in frozen_keys:
            if k in expired:
                continue  # 箱体已有效突破，形态失效
            if abs(ub['level'] - k) / k <= params['band_tol']:
                frozen_candidate = k
                break
        if frozen_candidate is not None:
            key = frozen_candidate
            top = frozen[key]['top']
            # 冻结带的原始 ub（用于 first_idx / touches）
            ub_frozen = [b for b in upper_bands if abs(b['level'] - top) / top <= params['band_tol']]
            if ub_frozen:
                ub = max(ub_frozen, key=lambda x: x['level'])
        else:
            # 无冻结带匹配：直接使用初次选带结果（触碰最多的带，v1.3 核心），不再重选
            # 再匹配一次冻结带（选出的带可能与冻结带容差内；跳过已失效箱体）
            key = round(ub['level'], 2)
            for k in frozen_keys:
                if k in expired:
                    continue
                if abs(ub['level'] - k) / k <= params['band_tol']:
                    key = k
                    break
            if key in frozen:
                top = frozen[key]['top']
            else:
                top = ub['level']
        st = band_state.get(key)

        # 内部平台信息：除最高带外的其他合格带（仅记录，不报信号）
        inner_platforms = [round(b['level'], 2) for b in upper_bands
                           if abs(b['level'] - top) / top > params['band_tol']]
        inner_platforms.sort(reverse=True)

        # 下沿带：限定在上沿带形成日之后（箱体内部），避免突破前的历史平台冒充下沿
        # 上沿带形成日 ≈ 窗口内最早触及带上沿区间的日期
        band_start = ub.get('first_idx')
        lower_bands = _find_bands(lows, t, False, params, start_idx=band_start) if band_start is not None else []  # v1.2：盘中低点聚类下沿

        support = None
        candidates = [lb for lb in lower_bands
                      if lb['level'] < top
                      and params['min_depth'] <= (top - lb['level']) / top <= params['max_depth']]
        support = _pick_support(candidates, ub, band_start, params)

        if closes[t] >= top * (1 + params['break_tol']):
            # ── 突破日 ──
            if key in expired:
                continue  # 箱体已有效突破（站稳 hold_days）→ 形态失效，涨回上沿只是日常波动
            if st is not None and st.get('broken_at') is not None and not st.get('back_in_box'):
                continue  # 突破后未跌回箱体，不重复报

            # 首次突破：需要箱体前置（前 box_min_days 天无真突破 + 深度检查）
            if st is None:
                pre = [c for c in closes[max(0, t-params['box_min_days']):t] if c is not None]
                if len(pre) < params['box_min_days']:
                    continue
                break_pre = [c for c in pre if c > top * (1 + params['break_tol'])]
                if break_pre:
                    continue
                pre_low = min(pre)
                if pre_low < top * (1 - params['max_depth']):
                    continue
                # 冻结带上沿（事件时点值，不被后续新高污染）
                frozen[key] = {
                    'top': round(top, 2),
                    'touches': ub['touches'],
                    'support': round(support['level'], 2) if support else None,
                    'sup_touches': support['touches'] if support else 0,
                    'first_idx': t,
                }
                if key not in frozen_keys:
                    frozen_keys.append(key)

            info = frozen.get(key)
            if info is None:
                continue

            # 下沿 = 箱体最低支撑带（已在上方 _pick_support 计算，同期性约束已应用；无下沿 = 箱体未成熟）
            support_now = support
            if support_now:
                support_level = round(support_now['level'], 2)
                sup_touches = support_now['touches']
            else:
                support_level = None
                sup_touches = 0
            # 无下沿 → 箱体未成熟，降级为 weak（不可能是 strong）
            box_immature = support_level is None

            # 箱体时长 = 冻结时点到突破日
            box_days = t - info['first_idx'] + params['box_min_days']
            # 箱体起点日期（画虚线左端点用）：冻结时点往前推前置窗口
            box_start_idx = max(0, info['first_idx'] - params['box_min_days'])
            box_start_date = dates[box_start_idx]

            # 失败尝试计数：该带此前 failed 事件数（确认分级后第二遍重算）
            prior_failures = sum(1 for e in events if e.get('band_key') == key and e.get('result') == 'failed')
            attempt_no = prior_failures + 1

            vma = vol_ma[t]
            vr = vols[t] / vma if vma else 0
            events.append({
                'signal_date': dates[t],
                'type': 'bullish',
                'pattern': 'box_breakout',
                'signal_level': None,   # 确认后填充
                'band_key': key,
                'band_top': info['top'],          # 冻结值（不被新高污染）
                'band_bottom': support_level,
                'box_start_date': box_start_date,
                'top_touches': info['touches'],
                'bottom_touches': sup_touches,
                'box_days': box_days,
                'box_immature': box_immature,
                'inner_platforms': inner_platforms,
                'attempt_no': attempt_no,
                'prior_failures': prior_failures,
                'vol_ratio': round(vr, 2),
                'volume_ok': vr >= params['vol_ratio'],
                'close': closes[t],
                'result': 'pending',
                'details': None,
            })
            band_state[key] = {'broken_at': t, 'back_in_box': False}
            # 加入待确认：hold_days 后站稳 → 箱体失效
            pending_confirm.append({'key': key, 'trigger_idx': t, 'top': info['top']})
        else:
            # 非突破日：跌回箱体（收盘 < 上沿×(1-touch_tol)）→ 允许下次再尝试
            if st is not None and st.get('broken_at') is not None and closes[t] < top * (1 - params['touch_tol']):
                st['back_in_box'] = True

    # ── 确认分级 ──
    # （箱体失效已由循环内 _check_pending 实时处理：站稳 hold_days → expired）
    for ev in events:
        t = next(i for i, k in enumerate(daily) if k['date'] == ev['signal_date'])
        top = ev['band_top']
        # v1.2：突破日即给出信号（weak），次日站稳 → 增强为 confirmed/strong_confirmed；
        # 次日跌破 → 信号保留为 weak（突破尝试已发生），result='failed' 仅供失败计数
        next_close = closes[t+1] if t+1 < n else None
        if next_close is None:
            status = 'pending'
            level = 'weak'  # 数据最后一日：突破日信号按 weak 输出（待后续确认升级）
        elif next_close >= top:
            status = 'confirmed'
            level = 'strong_confirmed' if ev['volume_ok'] else 'confirmed'
        else:
            status = 'failed'
            level = 'weak'  # 突破日信号保留（次日跌破未确认）；failed 状态进 prior_failures 计数

        ev['result'] = status
        ev['signal_level'] = level

        # 超长箱体降级 + 箱体未成熟降级
        if ev['box_days'] > params['max_box_days'] and level in ('strong_confirmed', 'confirmed'):
            ev['signal_level'] = 'weak'
        if ev.get('box_immature') and level in ('strong_confirmed', 'confirmed'):
            ev['signal_level'] = 'weak'

    # ── 第二遍：确认完成后重算失败计数（第一遍时所有 result 还是 pending）──
    # 失败尝试 = result == 'failed'：突破后次日收盘跌破上沿（weak 是 signal_level 而非 result）
    for ev in events:
        prior = sum(1 for e in events
                    if e.get('band_key') == ev.get('band_key')
                    and e['signal_date'] < ev['signal_date']
                    and e['result'] == 'failed')
        ev['prior_failures'] = prior
        ev['attempt_no'] = prior + 1

    # ── 组装输出 ──
    results = []
    for ev in events:
        level = ev['signal_level']
        ev['details'] = {
            'signal_type': 'box_breakout',
            'band_top': ev['band_top'],
            'band_bottom': ev['band_bottom'],
            'box_start_date': ev.get('box_start_date'),
            'top_touches': ev['top_touches'],
            'bottom_touches': ev['bottom_touches'],
            'box_days': ev['box_days'],
            'inner_platforms': ev.get('inner_platforms', []),
            'attempt_no': ev['attempt_no'],
            'prior_failures': ev['prior_failures'],
            'vol_ratio': ev['vol_ratio'],
            'volume_ok': ev['volume_ok'],
            'description': f"箱体{ev['band_top']}/{ev['band_bottom'] if ev['band_bottom'] is not None else '下沿未识别'} | 第{ev['attempt_no']}次尝试 | "
                           f"{'放量' if ev['volume_ok'] else '未放量'}{ev['vol_ratio']}x | 次日站稳确认"
                           + (f" | 内部平台:{ev.get('inner_platforms', [])}" if ev.get('inner_platforms') else '')
                           if level and ev['result'] != 'failed'
                           else f"突破未确认（次日跌破上沿，第{ev['attempt_no']}次尝试，前{ev['prior_failures']}次失败）",
        }
        # 突破日信号一律输出（weak/confirmed/strong_confirmed）；failed 的 weak 信号也保留（突破尝试可见）
        results.append(ev)

    return results


# ══════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════

if __name__ == '__main__':
    os.chdir(PROJECT_DIR)  # CLI 调试时方便（模块导入不改变 cwd，review B1）
    parser = argparse.ArgumentParser(description='放量箱体突破检测')
    parser.add_argument('--stock', type=str, default='603259')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""SELECT date, open, high, low, close, volume, change_pct FROM daily_kline
            WHERE stock_code=? AND date<=? ORDER BY date""", (args.stock, args.date)).fetchall()
    finally:
        conn.close()

    daily = [dict(r) for r in rows]
    sigs = detect(daily)

    print(f"🔍 {args.stock} @ {args.date} — 箱体突破信号: {len(sigs)} 个")
    for s in sigs:
        print(f"   {s['signal_date']} [{s['signal_level']}] 箱体 {s['band_top']}/{s['band_bottom']} "
              f"尝试#{s['attempt_no']}(失败{s['prior_failures']}) 量比{s['vol_ratio']}x 时长{s['box_days']}天")
        print(f"      {s['details']['description']}")
