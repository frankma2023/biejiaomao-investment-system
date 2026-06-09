"""
投资决策驾驶舱 - 仓位计算引擎

核心算法：最大亏损倒推法（主）+ 凯利公式（软参考上限）

用法：
    from src.cockpit.position import calculate_position
    result = calculate_position(entry_price, stop_loss_price, account_size,
                                 max_loss_pct, win_rate=None, avg_win=None, avg_loss=None)
"""
import math


def calculate_position(entry_price, stop_loss_price, account_size=1000000,
                       max_loss_pct=0.02, kelly_fraction=0.25,
                       win_rate=None, avg_win=None, avg_loss=None):
    """
    计算建议仓位。

    参数:
        entry_price: 入场参考价
        stop_loss_price: 止损价
        account_size: 账户总资产
        max_loss_pct: 单笔最大亏损占账户比例 (默认 2%)
        kelly_fraction: 凯利分数 (默认 0.25)
        win_rate: 历史胜率 (可选，用于凯利)
        avg_win: 历史平均盈利 (可选)
        avg_loss: 历史平均亏损 (可选，取绝对值)

    返回:
        dict: {
            'suggested_pct': float,      # 建议仓位%
            'suggested_amount': float,   # 建议仓位金额
            'max_loss_amount': float,    # 单笔最大亏损金额
            'stop_loss_pct': float,      # 止损幅度%
            'kelly_pct': float or None,  # 凯利参考仓位%
            'kelly_enabled': bool,       # 凯利是否启用
            'kelly_warning': str or None,# 凯利警告信息
            'shares': int,               # 建议股数（100股整）
        }
    """
    result = {
        'suggested_pct': 0,
        'suggested_amount': 0,
        'max_loss_amount': account_size * max_loss_pct,
        'stop_loss_pct': 0,
        'kelly_pct': None,
        'kelly_enabled': False,
        'kelly_warning': None,
        'shares': 0,
    }

    if entry_price <= 0 or stop_loss_price <= 0 or stop_loss_price >= entry_price:
        result['kelly_warning'] = '止损价无效（需低于入场价）'
        return result

    # 止损幅度
    stop_loss_pct = (entry_price - stop_loss_price) / entry_price
    result['stop_loss_pct'] = round(stop_loss_pct * 100, 2)

    if stop_loss_pct <= 0:
        result['kelly_warning'] = '止损幅度非正'
        return result

    # 最大亏损倒推法
    max_loss_amount = account_size * max_loss_pct
    suggested_amount = max_loss_amount / stop_loss_pct
    suggested_pct = suggested_amount / account_size

    result['max_loss_amount'] = round(max_loss_amount, 2)
    result['suggested_amount'] = round(suggested_amount, 2)
    result['suggested_pct'] = round(suggested_pct * 100, 2)

    # 计算股数（按100股取整）
    raw_shares = int(suggested_amount / entry_price / 100) * 100
    result['shares'] = max(100, raw_shares)

    # 凯利公式（软参考上限）
    if win_rate is not None and avg_win is not None and avg_loss is not None and avg_loss > 0:
        result['kelly_enabled'] = True
        b_ratio = avg_win / avg_loss  # 盈亏比
        kelly_raw = (win_rate * b_ratio - (1 - win_rate)) / b_ratio
        kelly_pct = max(0, kelly_raw * kelly_fraction)
        result['kelly_pct'] = round(kelly_pct * 100, 2)

        # 凯利软上限对比
        if result['suggested_pct'] > result['kelly_pct']:
            result['kelly_warning'] = (
                f"建议仓位 {result['suggested_pct']}% 超过凯利参考上限 {result['kelly_pct']}%，"
                f"已自动限制"
            )
            result['suggested_pct'] = result['kelly_pct']
            result['suggested_amount'] = account_size * (result['suggested_pct'] / 100)
            raw_shares = int(result['suggested_amount'] / entry_price / 100) * 100
            result['shares'] = max(100, raw_shares)

    return result


def calculate_stop_loss(signal_type, entry_price, h_price=None, l_price=None,
                         b2_low=None, signal_low=None, ma10=None, ma60=None):
    """
    根据信号类型计算动态止损位。

    参数:
        signal_type: 'mw_plus' | 'mw_b2' | 'pocket_pivot_base' |
                     'pocket_pivot_continuation' | 'pocket_pivot_10ma' |
                     'base_breakout'
        entry_price: 入场参考价
        ...
    返回: (stop_loss_price, rule_description)
    """
    rules = {
        'mw_plus': (b2_low, f"MW PLUS B2日最低价 ¥{b2_low:.2f}（突破确认日防守线）"),
        'mw_b2': (b2_low, f"MW B2日最低价 ¥{b2_low:.2f}（突破确认日防守线）"),
        'pocket_pivot_base': (signal_low, f"口袋支点信号日最低价 ¥{signal_low:.2f}（启动日防守线）"),
        'pocket_pivot_continuation': (ma10, f"10日均线 ¥{ma10:.2f}（延续型支撑）"),
        'pocket_pivot_10ma': (ma10, f"10日均线 ¥{ma10:.2f}（均线支撑）"),
        'base_breakout': (l_price, f"基部下沿 ¥{l_price:.2f}（结构支撑）"),
    }

    if signal_type in rules:
        price, desc = rules[signal_type]
        if price and price < entry_price:
            return price, desc

    # 兜底：8% 止损
    fallback = entry_price * 0.92
    return fallback, f"固定8%止损 ¥{fallback:.2f}（信号结构止损不可用，兜底规则）"


def calculate_trailing_stop(entry_price, current_price, cost_basis, pnl_pct, atr=None):
    """
    计算移动止损价位。

    参数:
        entry_price: 入场价
        current_price: 当前价
        cost_basis: 成本价（含手续费）
        pnl_pct: 浮盈%（正数）
        atr: 14日ATR值（可选）

    返回: (trailing_stop_price, rule_description)
    """
    if pnl_pct < 5:
        return None, "浮盈不足5%，维持初始止损"
    elif pnl_pct < 10:
        return cost_basis, f"保本损 ¥{cost_basis:.2f}（浮盈≥5%）"
    elif pnl_pct < 20:
        if atr:
            ts = max(current_price * (1 - 2 * atr / 100), cost_basis)
            return ts, f"2×ATR跟踪止损 ¥{ts:.2f}（浮盈≥10%）"
        else:
            return cost_basis, f"保本损 ¥{cost_basis:.2f}（浮盈≥10%，ATR不可用）"
    else:
        if atr:
            ts = max(current_price * (1 - 3 * atr / 100), entry_price * 1.10)
            return ts, f"3×ATR跟踪止损 ¥{ts:.2f}（浮盈≥20%）"
        else:
            return cost_basis, f"保本损 ¥{cost_basis:.2f}（浮盈≥20%，ATR不可用）"


def get_trailing_stop_rule_text():
    """返回移动止损规则的文字描述"""
    return (
        "浮盈≥5%：止损线上移至成本价（保本损）\n"
        "浮盈≥10%：止损线跟踪 max(当前价×(1-2×ATR%), 成本价)\n"
        "浮盈≥20%：止损线跟踪 max(当前价×(1-3×ATR%), 10日均线)"
    )
