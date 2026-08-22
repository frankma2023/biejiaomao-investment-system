# -*- coding: utf-8 -*-
"""
自选池日报 · 规则引擎（纯函数，可审计可回放）

输入：归一化信号列表 + 股票上下文 + 权重表
输出：5 档建议（buy_strong/buy/hold/wait/avoid）+ 净分 + 理由链 + 软提示

设计原则（PRD v1.0 §6）：
- 卖出优先：卖出信号权重 × sell_priority_mult；任何强卖出信号 → 至少 wait
- 位置约束：250日位置 > position_limit → 买入降级 wait（"错过就别追高"）
- 十戒硬规则：MA50 下行禁买 / 持仓浮亏 >stop_loss_pct → avoid
- 理由链：每条建议可追溯到 信号×权重×规则
"""
import os
import yaml

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEIGHTS_PATH = os.path.join(PROJECT_DIR, 'config', 'strategy', 'watchlist_signal_weights.yaml')

# 档位中文与配色（前端共用）
LEVEL_CN = {
    'buy_strong': '买入（强）',
    'buy': '买入',
    'hold': '持有',
    'wait': '等回调',
    'avoid': '回避',
}


def load_weights(path=None):
    with open(path or WEIGHTS_PATH, encoding='utf-8') as f:
        return yaml.safe_load(f)


def normalize_engine_signal(sig, weights=None):
    """归一化 pattern_scan_signals 的单条信号 → 规则引擎输入"""
    src = sig.get('source', '?')
    if src == 'talib' or src == 'cdl':
        return None  # 参考信号不参与评分
    # 未确认信号过滤（引擎明确标 confirmed=False / vol_ok=False）
    det = sig.get('details') or {}
    if det.get('confirmed') is False or det.get('vol_ok') is False:
        return None
    if src == 'mw_signal':
        # mw_signal 引擎实时版：按 signal_type 细分
        st = det.get('signal_type', '')
        if 'b1' in str(st).lower():
            src = 'mw_b1'
        elif 'b2' in str(st).lower():
            src = 'mw_b2'
    # 方向：type 缺失时按权重表引擎分类（买入/卖出引擎）
    t = sig.get('type')
    if t not in ('bullish', 'bearish'):
        w = (weights or load_weights())['signals'].get(src)
        d = w.get('dir') if w else None
        if d is None:
            return None
        direction = d
    else:
        direction = 'long' if t == 'bullish' else 'short'
    return {
        'source': src,
        'date': sig.get('date') or sig.get('signal_date'),
        'dir': direction,
        'score': None,  # 权重表查分
        'note': det.get('description', ''),
        'confidence': sig.get('confidence', 'medium'),
    }


def normalize_mw_rows(rows):
    """mw_signal_daily 行 → 规则引擎信号（B1/B2 两条，含 TS 置信度）"""
    out = []
    for r in rows:
        if r.get('b1_date'):
            out.append({
                'source': 'mw_b1',
                'date': r['b1_date'],
                'dir': 'long',
                'score': None,
                'note': f"MW B1 · TS {r.get('tech_score')} · 回撤 {r.get('decline_pct')}%",
                'ts': r.get('tech_score'),
            })
        if r.get('b2_date'):
            out.append({
                'source': 'mw_b2',
                'date': r['b2_date'],
                'dir': 'long',
                'score': None,
                'note': f"MW B2 · TS {r.get('tech_score')}",
                'ts': r.get('tech_score'),
            })
    return out


def _days_between(a, b):
    """自然日差（a 早于 b 返回正数）"""
    from datetime import datetime
    fmt = '%Y-%m-%d'
    return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).days


def _dedup_by_source(signals):
    """同源去重：每 source 只保留最早一条（首次确认日）
    形态引擎会每日重复报告已确认形态（如 top_pattern 连报 39 天）；
    保留最早=首次确认，让信号在窗口内自然衰减过期，避免重复信号常驻。"""
    best = {}
    for s in signals:
        src = s.get('source')
        if not src:
            continue
        if src not in best or (s.get('date') or '') < (best[src].get('date') or ''):
            best[src] = s
    return list(best.values())


def evaluate(signals, ctx, weights=None, scan_date=None):
    """
    signals: 归一化信号列表 [{source, date, dir, ts, note}]
    ctx: {close, pos_250, ma50, ma50_slope, gain_from_low,
          holding_cost (None=非持仓), low_250, fib_levels, grid_levels}
    返回: {level, net, buy_score, sell_score, reasons, tips, callback, signals_used}
    """
    w = weights or load_weights()
    sig_weights = w['signals']
    rules = w['rules']
    import datetime as _dt
    scan_date = scan_date or _dt.date.today().strftime('%Y-%m-%d')

    reasons = []
    tips = []
    used = []
    expired_sell = []
    buy_score = 0.0
    sell_score = 0.0
    buy_list = []
    sell_list = []
    decay_after = rules.get('decay_after', 30)
    decay_factor = rules.get('decay_factor', 0.6)
    window = rules.get('new_signal_window', 60)

    for s in signals:
        d = s.get('date')
        if not d or d > scan_date:
            continue
        age = _days_between(d, scan_date)
        if age > window:
            continue
        sw = sig_weights.get(s['source'])
        if not sw or sw.get('dir') is None:
            continue  # 参考信号（talib/cdl）跳过
        base = sw.get('score', 0)
        # 时间衰减：近 decay_after 日 1.0，之后 ×decay_factor
        mult = 1.0 if age <= decay_after else decay_factor
        score = base * mult
        if s.get('ts'):
            score = score * 0.5 + min(100, s['ts']) * 0.5  # 有置信度则混合
        if s['dir'] == 'long':
            buy_score += score
            buy_list.append((s['source'], s['date'], round(score, 1), age))
        else:
            # 卖出信号：新鲜窗口内计入净分；过期卖出只提示不计分（避免旧信号长期压死净分）
            if age <= rules.get('sell_fresh_days', 20):
                sell_score += score * rules.get('sell_priority_mult', 1.5)
                sell_list.append((s['source'], s['date'], round(score * rules.get('sell_priority_mult', 1.5), 1), age))
            else:
                expired_sell.append(f"{s['source']}@{s['date'][5:]}(已过新鲜期 {age}日，仅提示)")
        used.append({'source': s['source'], 'date': d, 'dir': s['dir'], 'score': round(score, 1),
                     'note': s.get('note', ''), 'wsrc': sw.get('source', 'expert')})

    net = round(buy_score - sell_score, 1)
    reasons.append(f"净分 {net} = 买入 {round(buy_score,1)} − 卖出 {round(sell_score,1)}（卖出×{rules.get('sell_priority_mult',1.5)}，仅新鲜窗口 {rules.get('sell_fresh_days',20)}日内的卖出信号计分）")

    # ── 档位判定 ──
    level = 'hold'
    pos = ctx.get('pos_250', 50)
    close = ctx.get('close', 0)

    # ① 十戒：持仓止损
    cost = ctx.get('holding_cost')
    if cost:
        drawdown = (close / cost - 1) * 100
        if drawdown <= -rules.get('stop_loss_pct', 8):
            level = 'avoid'
            reasons.append(f"🔴 十戒止损：持仓浮亏 {round(drawdown,1)}% ≤ -{rules['stop_loss_pct']}%（成本 {cost}）")

    # ② 卖出优先：任何卖出信号（强卖需在新鲜窗口内）
    if level != 'avoid':
        if sell_list:
            fresh = rules.get('sell_fresh_days', 20)
            strong_sell = any(age <= fresh and sl[2] >= 30 for sl in sell_list)
            if strong_sell and net < 30:
                if pos > 50:
                    level = 'avoid'
                    reasons.append(f"🔴 卖出优先：{len(sell_list)} 个强卖出信号（如 {sell_list[0][0]} @{sell_list[0][1]}，{sell_list[0][3]}日前）+ 高位 {pos}%——回避")
                else:
                    level = 'wait'
                    reasons.append(f"⏳ 卖出警示：{len(sell_list)} 个强卖出信号但位置 {pos}% 偏低——超跌区信号易假，等确认")
            elif strong_sell and pos > 60:
                level = 'wait'
                reasons.append(f"⏳ 卖出警示：{len(sell_list)} 个卖出信号但净分 {net} 仍偏多——高位不追，等回调（强势股回调例外需人工判断）")
            elif strong_sell:
                level = 'wait'
                reasons.append(f"⏳ 卖出警示：{len(sell_list)} 个卖出信号，净分 {net} 偏多且位置 {pos}% 低——观察/等回调")
            elif net < rules.get('avoid_net', -30):
                level = 'avoid'
                reasons.append(f"🔴 净分 {net} < {rules['avoid_net']}（无新鲜卖出信号但净空显著）")

    # ③ 买入判定（无卖出阻挡时）
    if level == 'hold':
        resonance = len(buy_list)
        if buy_score >= rules.get('buy_strong_net', 80) and resonance >= rules.get('buy_strong_resonance', 3) and pos < rules.get('buy_position_ok', 60):
            level = 'buy_strong'
            reasons.append(f"🟢 买入（强）：净分 {net} ≥{rules['buy_strong_net']}，共振 {resonance} ≥{rules['buy_strong_resonance']}，位置 {pos}% <{rules['buy_position_ok']}")
        elif buy_score >= rules.get('buy_min_net', 60) and resonance >= rules.get('buy_min_resonance', 2):
            # 有买入资格但位置约束
            if pos > rules.get('position_limit', 85):
                level = 'wait'
                reasons.append(f"⏳ 位置约束：净分 {net} 够买入，但 250日位置 {pos}% >{rules['position_limit']}——不追高，等回调")
            elif pos >= rules.get('buy_position_ok', 60):
                level = 'wait'
                reasons.append(f"⏳ 位置 {pos}% ≥{rules['buy_position_ok']}，买入窗口不佳——等回调")
            else:
                level = 'buy'
                reasons.append(f"🟢 买入：净分 {net} ≥{rules['buy_min_net']}，共振 {resonance} ≥{rules['buy_min_resonance']}，位置 {pos}% <{rules['buy_position_ok']}")

    # ④ 十戒：MA50 下行禁买
    if level in ('buy', 'buy_strong') and rules.get('ma50_down_ban') and ctx.get('ma50'):
        if close < ctx['ma50'] and ctx.get('ma50_slope', 0) < 0:
            level = 'wait'
            reasons.append(f"⏳ 十戒禁买：现价 {close} < MA50 {round(ctx['ma50'],2)} 且 MA50 下行——下降通道不接飞刀")

    # ⑤ 十戒：追高（自低点涨幅）→ 提示 + 降级
    gain = ctx.get('gain_from_low', 0)
    if gain > 100:
        if level in ('buy', 'buy_strong'):
            level = 'wait'
            reasons.append(f"⏳ 追高约束：自 250日低点已涨 {round(gain,0)}% >100%——降级等回调")
        tips.append(f"📢 自250日低点已涨 {round(gain,0)}%，注意欧奈尔纪律：涨幅 20-25% 考虑部分获利了结")
    elif gain > rules.get('profit_take_pct', 25) and cost:
        tips.append(f"📢 自低点 +{round(gain,0)}%：欧奈尔纪律，考虑部分获利了结 20-25%")
    if cost and close < cost:
        tips.append("📢 纪律：不要在跌势中摊平亏损")

    # ⑥ 等回调触发：现价距回调位 ≤5%
    callback = None
    if ctx.get('fib_levels') or ctx.get('grid_levels'):
        targets = []
        for lv in (ctx.get('fib_levels') or []):
            if lv < close:
                targets.append({'type': '斐波那契回调位', 'price': round(lv, 2),
                                'pct': round((close - lv) / close * 100, 1)})
        for g in (ctx.get('grid_levels') or []):
            if g < close:
                targets.append({'type': '网格档', 'price': round(g, 3),
                                'pct': round((close - g) / close * 100, 1)})
        targets.sort(key=lambda x: x['pct'])
        if targets and level in ('hold', 'wait'):
            nearest = targets[0]
            callback = nearest
            if nearest['pct'] <= rules.get('callback_trigger_pct', 5):
                level = 'wait'
                reasons.append(f"⏳ 回调触发：现价 {close} 距 {nearest['type']} {nearest['price']} 仅 {nearest['pct']}%——挂单窗口")

    # ⑦ 兜底：无信号 → 观望（仅当未被其他规则改判时）
    if level == 'hold' and not buy_list and not sell_list:
        level = 'hold'
        reasons.append("⚪ 近 60 日无强信号，趋势中性——持有/观望")

    # 理由链压缩
    if expired_sell:
        reasons.append("👁 过期卖出提示（不计分）: " + '，'.join(expired_sell[:3]) + ('…' if len(expired_sell) > 3 else ''))

    def _fmt(l):
        return '，'.join(f"{a}@{b[5:]}({c}分)" for a, b, c, _ in l[:4]) + ('…' if len(l) > 4 else '')
    if buy_list:
        reasons.insert(1, f"🟢 支撑信号: {_fmt(buy_list)}")
    if sell_list:
        reasons.insert(1, f"🔴 反对信号: {_fmt(sell_list)}")

    return {
        'level': level,
        'level_cn': LEVEL_CN[level],
        'net': net,
        'buy_score': round(buy_score, 1),
        'sell_score': round(sell_score, 1),
        'reasons': reasons,
        'tips': tips,
        'callback': callback,
        'signals_used': used,
        'resonance': len(buy_list),
    }


def detect_missed(signals, last_view, scan_date, ctx, weights=None, gain_pct=None):
    """
    错过检测：last_view 之后出现的买入信号 + 信号日价 → 现价涨幅
    signals: 归一化信号（含 date）
    返回: [{source, date, close_at, gain_pct, note, chaseable}]
    """
    w = weights or load_weights()
    miss = []
    th = w['rules'].get('miss_gain_pct', 15)
    for s in signals:
        d = s.get('date')
        if not d or not last_view or d <= last_view:
            continue
        if s['dir'] != 'long':
            continue
        sw = w['signals'].get(s['source'])
        if not sw or sw.get('dir') != 'long':
            continue
        c_at = s.get('close_at') or ctx.get('close')
        g = gain_pct if gain_pct is not None else (ctx.get('close', 0) / c_at - 1) * 100 if c_at else 0
        miss.append({
            'source': s['source'],
            'date': d,
            'close_at': c_at,
            'gain_pct': round(g, 1),
            'note': s.get('note', ''),
            'missed': g >= th,
            'chaseable': ctx.get('pos_250', 100) < 70 and ctx.get('pos_250', 100) >= 0,
        })
    return miss
