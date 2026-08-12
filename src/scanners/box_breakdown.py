"""
跌破箱体检测引擎 v1.5
=====================
检测"跌破箱体下沿"的卖出信号。

镜像 box_breakout（放量箱体突破买入信号），共享箱体识别函数：
  - 箱体完整形成（上沿触碰≥min_touches×3 且下沿触碰≥min_touches×3 且时长≥box_min_days）
  - 跌破触发：收盘 < 下沿×(1-break_tol)（默认 1% 容差，避免贴线假信号）
  - 次日确认：次日收盘未收回（≤ 下沿）→ 跌破成功 strong_sell，箱体失效
  - 次日收回（> 下沿）→ 跌破失败 failed，箱体保留可再次尝试

与买入侧的关键差异：
  - 不检测成交量（阴跌比放量下跌更常见、更阴险）
  - 箱体失效窗口 = max_box_days（250天）：确认后同价位不再报；超期视为新箱体

信号等级（v1.5，次日确认语义，与 box_breakout 完全镜像）：
  strong_sell — 跌破日 + 次日未收回（确认成功，箱体失效）
  warning     — 跌破日信号（待次日确认）
  failed      — 次日收回（假跌破，箱体保留，计入失败）

用法:
  python -m src.scanners.box_breakdown --stock 603259 --date 2026-08-07
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "box_breakdown",
    "display_name": "跌破箱体",
    "category": "sell_signal",
    "type": "bearish",
    "pattern": "box_breakdown",
    "version": "1.5",
    "description": "跌破箱体下沿检测：跌破1%触发→次日未收回确认→箱体失效（与box_breakout镜像）",
}

# ══════════════════════════════════════════════
# 共享函数（硬性约定：直接复用 box_breakout，不复制代码）
# ══════════════════════════════════════════════
try:
    from scanners.box_breakout import _find_bands, _adj_prices, _pick_support
except ImportError:
    from .box_breakout import _find_bands, _adj_prices, _pick_support


# ══════════════════════════════════════════════
# 参数加载
# ══════════════════════════════════════════════

def load_params() -> Dict:
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "box_breakdown.yaml")
    defaults = {
        'lookback_days': 300,
        'band_tol': 0.03,
        'touch_tol': 0.03,
        'min_touches': 3,
        'box_min_days': 40,
        'max_box_days': 250,
        'max_depth': 0.35,
        'min_depth': 0.03,
        'local_window': 10,
        'min_lookback_days': 60,
        'break_tol': 0.01,     # 跌破容差：收盘 < 下沿×(1-1%) 才算跌破（贴线假信号过滤）
        'confirm_days': 1,     # 确认天数：1 = 次日收盘未收回即确认（v1.5 用户语义）
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('box_breakdown', cfg))
    return defaults


# ══════════════════════════════════════════════
# 主检测
# ══════════════════════════════════════════════

def detect(daily: List[Dict], params: Optional[Dict] = None) -> List[Dict]:
    if params is None:
        params = load_params()

    daily = _adj_prices([dict(k) for k in daily])
    n = len(daily)
    if n < params['min_lookback_days']:
        return []

    closes = [k['close'] for k in daily]
    highs = [k['high'] for k in daily]
    lows = [k['low'] for k in daily]
    dates = [k['date'] for k in daily]

    # 活跃事件：key(下沿价位) -> event
    active = {}
    # 历史事件（已结束）
    events = []
    # 已确认跌破的箱体 key -> 失效日索引（strong_sell 后箱体失效：新走势下跌，不再视为箱体破位）
    # 失效窗口 = max_box_days：超过后视为重新形成的箱体（价格回到同价位但已是新结构）
    expired = {}

    for t in range(params['lookback_days'] + params['local_window'] + 1, n):
        if closes[t] is None:
            continue

        # ── v1.5：统一次日确认（不依赖 support 匹配，带演化也能确认）──
        # 所有达到确认窗口（跌破日次日及之后）的 pending 事件：
        #   次日收盘 ≤ 下沿 → 确认成功 strong_sell + 箱体失效
        #   次日收盘 > 下沿 → 确认失败 failed（收回），箱体保留
        for k in list(active.keys()):
            ev = active[k]
            if t - ev.get('trigger_idx', t) < 1:
                continue  # 未到确认日
            bottom = ev['band_bottom']
            if closes[t] <= bottom:
                ev['signal_level'] = 'strong_sell'
                ev['max_level'] = 'strong_sell'
                ev['close'] = closes[t]
                dd = (bottom - closes[t]) / bottom * 100
                ev['drop_pct'] = round(dd, 2)
                ev['max_drop_pct'] = round(dd, 2)
                ev['result'] = 'active'
                expired[k] = t  # 跌破确认 → 箱体失效
            else:
                ev['close'] = closes[t]
                ev['result'] = 'failed'
                ev['end_reason'] = '次日收回'
            active.pop(k)
            events.append(ev)

        # v1.2 对齐 box_breakout：上沿 = 盘中高点聚类，下沿 = 盘中低点聚类
        upper_bands = _find_bands(highs, t, True, params)
        if not upper_bands:
            continue

        # v1.3 对齐 box_breakout：已站上（≤收盘）的带中选触碰 ≥ min_touches×3 的最高带
        # （排除孤立尖峰 688665/内部平台 603259/历史旧箱体 600309）
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
        top = ub['level']
        band_start = ub.get('first_idx')
        lower_bands = _find_bands(lows, t, False, params, start_idx=band_start) if band_start is not None else []
        support = None
        candidates = [lb for lb in lower_bands
                      if lb['level'] < top
                      and params['min_depth'] <= (top - lb['level']) / top <= params['max_depth']]
        # 同期性约束（复用 box_breakout._pick_support，双侧 ±box_min_days×2）
        support = _pick_support(candidates, ub, band_start, params)

        # 匹配已冻结的活跃箱体：用当前 support 带与活跃事件的下沿比较
        matched_key = None
        if support is not None:
            for k in active.keys():
                if abs(support['level'] - active[k]['band_bottom']) / active[k]['band_bottom'] <= params['band_tol']:
                    matched_key = k
                    break

        # 若未匹配且无活跃事件，尝试冻结新箱体（完整性校验）
        if matched_key is None:
            if support is not None and (
                    len([c for c in closes[max(0, t - params['box_min_days']):t] if c is not None])
                    >= params['box_min_days']
                    and ub['touches'] >= params['min_touches'] * 3  # v1.4：下沿对齐买侧 ×3（弱支撑一碰就破，假信号多）
                    and support['touches'] >= params['min_touches'] * 3):
                # 检查是否与已结束事件的下沿重复（同一箱体再次跌破）
                bottom_val = round(support['level'], 2)
                key = bottom_val
                # 箱体已确认跌破过（strong_sell）且在失效窗口内 → 不报（新走势下跌）
                # 容差匹配（band_tol×2）：同箱体下沿带会随数据微调（如 50.27→52.86），精确 key 会漏
                blocked = False
                for exp_key, exp_idx in expired.items():
                    if abs(support['level'] - exp_key) / exp_key <= params['band_tol'] * 2 \
                            and t - exp_idx <= params['max_box_days']:
                        blocked = True
                        break
                if blocked:
                    continue
                box_start_idx = max(0, t - params['box_min_days'])
                # 冻结箱体信息暂存到临时变量
                new_box = {
                    'top': round(top, 2),
                    'bottom': bottom_val,
                    'start_date': dates[box_start_idx],
                    'top_touches': ub['touches'],
                    'bottom_touches': support['touches'],
                    'first_idx': box_start_idx,
                }

                # 检查当前价是否跌破新箱体下沿，触发新事件（v1.5：跌破容差 break_tol，贴线不触发）
                if closes[t] < bottom_val * (1 - params.get('break_tol', 0.01)):
                    ev = {
                        'signal_date': dates[t],
                        'type': 'bearish',
                        'pattern': 'box_breakdown',
                        'signal_level': 'warning',
                        'band_key': key,
                        'band_top': new_box['top'],
                        'band_bottom': new_box['bottom'],
                        'box_start_date': new_box['start_date'],
                        'top_touches': new_box['top_touches'],
                        'bottom_touches': new_box['bottom_touches'],
                        'box_days': t - new_box['first_idx'],
                        'trigger_idx': t,          # v1.5：跌破日索引（次日确认用）
                        'below_count': 1,
                        'close': closes[t],
                        'drop_pct': round((new_box['bottom'] - closes[t]) / new_box['bottom'] * 100, 2),
                        'max_drop_pct': round((new_box['bottom'] - closes[t]) / new_box['bottom'] * 100, 2),
                        'result': 'pending',       # v1.5：待次日确认
                        'max_level': 'warning',
                        'details': None,
                    }
                    active[key] = ev
                # 未跌破：箱体已冻结但无事件，继续观察（不创建事件）
            continue

        # 已匹配活跃箱体（防御分支：顶部统一次日确认已处理到期事件，此处理论不可达）
        # 事件创建当天 continue，次日被顶部统一确认移除；到达这里说明有未处理事件，直接交给下轮确认
        if matched_key is not None and matched_key not in active:
            matched_key = None
        if matched_key is None:
            continue
        # 若事件仍在（未到确认日），保持 pending，下轮处理
        continue

    # 收尾：仍活跃的事件（数据末尾，尚未到确认日）
    for key in list(active.keys()):
        ev = active.pop(key)
        if ev.get('result') == 'pending':
            ev['result'] = 'pending'  # 待确认（数据截止，未到次日）
        else:
            ev['result'] = 'active'
        ev['end_reason'] = ''
        events.append(ev)

    # 组装输出（按事件创建日期排序，消除 active 遍历序差异）
    results = []
    for ev in sorted(events, key=lambda e: e['signal_date']):
        ev['details'] = {
            'signal_type': 'box_breakdown',
            'band_top': ev['band_top'],
            'band_bottom': ev['band_bottom'],
            'box_start_date': ev['box_start_date'],
            'top_touches': ev['top_touches'],
            'bottom_touches': ev['bottom_touches'],
            'box_days': ev['box_days'],
            'drop_pct': ev.get('drop_pct'),
            'max_drop_pct': ev.get('max_drop_pct'),
            'max_level': ev.get('max_level', ev.get('signal_level')),
            'status': ev['result'],
            'description': f"跌破箱体下沿 {ev['band_bottom']} | 收盘 {ev['close']:.2f} | "
                           f"跌破幅度 {ev.get('drop_pct', 0)}% | 最大跌幅 {ev.get('max_drop_pct', 0)}%",
        }
        if ev['result'] == 'failed':
            ev['signal_level'] = None
        results.append(ev)

    return results


# ══════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════

if __name__ == '__main__':
    os.chdir(PROJECT_DIR)  # CLI 调试时方便（模块导入不改变 cwd，review B1）
    parser = argparse.ArgumentParser(description='跌破箱体检测')
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

    print(f"🔍 {args.stock} @ {args.date} — 跌破箱体事件: {len(sigs)} 个")
    for s in sigs:
        if s['signal_level']:
            print(f"   {s['signal_date']} [{s['signal_level']}] 下沿 {s['band_bottom']} "
                  f"收盘 {s['close']:.2f} 跌破 {s.get('drop_pct')}% 箱体 {s['box_days']}天")
        else:
            ml = s['details'].get('max_level', '—')
            print(f"   {s['signal_date']} [清除·曾{ml}] 下沿 {s['band_bottom']}（{s.get('end_reason', '')}）")
