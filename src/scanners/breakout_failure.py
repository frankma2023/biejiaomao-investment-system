"""
基部突破失败检测引擎 v1.0

核心问题: 某个已确认的基部突破日，在后续 N 个交易日内是否出现了失败信号？

本引擎是 Layer 1 base_breakout 的下游消费者：
  1. 调用 base_breakout.detect() 获取全部突破信号
  2. 对每个突破信号，在监控窗口内逐日检查 7 条触发规则
  3. 每次满足都产出独立信号，三级严重级别区分

用法:
  python breakout_failure.py --stock 600519 --date 2026-05-17
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime, timedelta
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC_DIR)
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "breakout_failure",
    "display_name": "基部突破失败",
    "category": "layer1",
    "version": "1.0",
    "description": "检测基部突破后的失败信号：价格跌破关键价位 + 成交量验证，三级严重级别"
}


def load_params():
    """加载失败检测配置"""
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "breakout_failure.yaml")
    defaults = {
        'monitor_days': 10,
        'rule_b_enabled': True, 'rule_c_enabled': True, 'rule_d_enabled': True,
        'rule_e_enabled': True, 'rule_f_enabled': True, 'rule_g_enabled': True,
        'rule_h_enabled': True,
        'rule_d_trough_mult': 0.98, 'rule_e_chg_pct': 3.0, 'rule_e_vol_ratio': 1.5,
        'rule_f_days': 3, 'rule_f_vol_ratio': 1.0,
        'rule_g_dd_threshold': -0.2, 'rule_g_window': 3, 'rule_g_count': 2,
        'rule_h_total_vol_ratio': 1.2,
        'vol_ma_days': 50,
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        defaults.update(cfg.get('breakout_failure', {}))
    return defaults


def _sma(arr, n):
    """简单移动均线"""
    if len(arr) < n:
        return sum(arr) / max(len(arr), 1)
    return sum(arr[-n:]) / n


def _is_distribution_day(today_vol, prev_vol, chg_pct, threshold=-0.2):
    """判断是否为抛盘日：放量下跌"""
    if today_vol <= 0 or prev_vol <= 0:
        return False
    return today_vol > prev_vol and chg_pct < threshold


def detect(klines, indicators=None, bp_params=None):
    """
    检测基部突破失败信号。

    Args:
        klines: OHLCV dict 列表，按日期排序。需覆盖到 monitor_days 之后
        indicators: 未使用（保留接口兼容性）
        bp_params: 传递给 base_breakout.detect() 的参数，不传则用 YAML 默认值

    Returns:
        List[Dict]: 失败信号列表，每条符合 PRD 定义的输出格式
    """
    from scanners.base_breakout_v2 import detect as detect_breakout
    from scanners.base_breakout_v2 import load_params as load_bp_params

    params = load_params()
    monitor_days = params['monitor_days']
    vol_ma_days = params['vol_ma_days']

    # 规则开关
    rb = {
        'B': params.get('rule_b_enabled', True),
        'C': params.get('rule_c_enabled', True),
        'D': params.get('rule_d_enabled', True),
        'E': params.get('rule_e_enabled', True),
        'F': params.get('rule_f_enabled', True),
        'G': params.get('rule_g_enabled', True),
        'H': params.get('rule_h_enabled', True),
    }

    # 1. 获取基部突破信号
    if bp_params is None:
        bp_params = load_bp_params()
    breakout_signals = detect_breakout(klines, bp_params)

    if not breakout_signals:
        return []

    # 建立日期 → K线索引的映射
    date_to_idx = {}
    for i, k in enumerate(klines):
        date_to_idx[k['date']] = i

    n = len(klines)
    volumes = [k.get('volume', 0) for k in klines]
    closes = [k.get('close', 0) for k in klines]
    opens = [k.get('open', 0) for k in klines]

    failures = []

    # 2. 对每个突破信号逐个监控
    for bs in breakout_signals:
        b_date = bs['signal_date']
        b_idx = date_to_idx.get(b_date)
        if b_idx is None:
            continue

        # 从K线数据提取突破日最低价
        breakout_low = klines[b_idx].get('low', klines[b_idx].get('close', 0))
        buy_point = bs['buy_point']
        trough_price = bs['trough_price']

        # 监控窗口
        monitor_start = b_idx + 1
        monitor_end = min(b_idx + monitor_days, n - 1)
        if monitor_start > monitor_end:
            continue

        # 预计算50日均量基线（不含突破日当日，用于量比计算）
        # 使用突破日之前的数据
        vol_baseline = 0
        vol_start = max(0, b_idx - vol_ma_days)
        if b_idx > vol_start:
            vol_baseline = sum(volumes[vol_start:b_idx]) / (b_idx - vol_start)

        # 规则F的连续计数
        rule_f_streak = 0

        # 规则G的抛盘日统计
        rule_g_dd_dates = []  # 记录窗口内每天的抛盘日状态
        rule_g_triggered = False  # G规则是否已触发过（每天计算但只触发一次）

        # 逐日监控
        for day_idx in range(monitor_start, monitor_end + 1):
            day_k = klines[day_idx]
            day_date = day_k['date']
            day_close = closes[day_idx]
            day_volume = volumes[day_idx]
            day_open = opens[day_idx]

            # 当日量比
            day_vol_ratio = day_volume / vol_baseline if vol_baseline > 0 else 0

            # 日间涨跌幅（方案A：当日收盘 vs 前一日收盘）
            prev_close = closes[day_idx - 1] if day_idx > 0 else closes[day_idx]
            day_chg_pct = ((day_close - prev_close) / prev_close * 100) if prev_close > 0 else 0

            # 抛盘日判定
            prev_vol = volumes[day_idx - 1] if day_idx > 0 else 0
            is_dd = _is_distribution_day(day_volume, prev_vol, day_chg_pct, params['rule_g_dd_threshold'])

            failure_day = day_idx - b_idx  # 第几个交易日

            # ── 规则 C：跌回枢轴点（mild）──
            if rb['C'] and day_close < buy_point:
                failures.append({
                    'date': day_date, 'type': 'bearish',
                    'breakout_date': b_date, 'buy_point': buy_point,
                    'breakout_low': round(breakout_low, 2), 'failure_day': failure_day,
                    'failed_rule': 'rule_c', 'severity': 'mild',
                    'details': {
                        'rule_label': f'跌回枢轴点({b_date}突破 D+{failure_day})', 'current_close': round(day_close, 2),
                        'volume_ratio': round(day_vol_ratio, 2),
                        'day_chg_pct': round(day_chg_pct, 2),
                        'trough_price': round(trough_price, 2),
                        'breakout_vol_ratio': bs.get('breakout_vol_ratio', 0),
                        'secondary_rules': [],
                    }
                })

            # ── 规则 B：跌破突破日最低价（severe）──
            if rb['B'] and day_close < breakout_low:
                failures.append({
                    'date': day_date, 'type': 'bearish',
                    'breakout_date': b_date, 'buy_point': buy_point,
                    'breakout_low': round(breakout_low, 2), 'failure_day': failure_day,
                    'failed_rule': 'rule_b', 'severity': 'severe',
                    'details': {
                        'rule_label': f'跌破突破日最低价({b_date}突破 D+{failure_day})', 'current_close': round(day_close, 2),
                        'volume_ratio': round(day_vol_ratio, 2),
                        'day_chg_pct': round(day_chg_pct, 2),
                        'trough_price': round(trough_price, 2),
                        'breakout_vol_ratio': bs.get('breakout_vol_ratio', 0),
                        'secondary_rules': [],
                    }
                })

            # ── 规则 D：跌破谷底支撑（severe）──
            d_threshold = trough_price * params['rule_d_trough_mult']
            if rb['D'] and day_close < d_threshold:
                failures.append({
                    'date': day_date, 'type': 'bearish',
                    'breakout_date': b_date, 'buy_point': buy_point,
                    'breakout_low': round(breakout_low, 2), 'failure_day': failure_day,
                    'failed_rule': 'rule_d', 'severity': 'severe',
                    'details': {
                        'rule_label': f'跌破谷底支撑({b_date}突破 D+{failure_day})', 'current_close': round(day_close, 2),
                        'volume_ratio': round(day_vol_ratio, 2),
                        'day_chg_pct': round(day_chg_pct, 2),
                        'trough_price': round(trough_price, 2),
                        'breakout_vol_ratio': bs.get('breakout_vol_ratio', 0),
                        'secondary_rules': [],
                    }
                })

            # ── 规则 E：放量长阴砸穿（severe）──
            if rb['E']:
                e_chg_ok = day_chg_pct <= -params['rule_e_chg_pct']
                e_vol_ok = day_vol_ratio >= params['rule_e_vol_ratio']
                e_price_ok = day_close < buy_point
                if e_chg_ok and e_vol_ok and e_price_ok:
                    failures.append({
                        'date': day_date, 'type': 'bearish',
                        'breakout_date': b_date, 'buy_point': buy_point,
                        'breakout_low': round(breakout_low, 2), 'failure_day': failure_day,
                        'failed_rule': 'rule_e', 'severity': 'severe',
                        'details': {
                            'rule_label': f'放量长阴砸穿({b_date}突破 D+{failure_day})', 'current_close': round(day_close, 2),
                            'volume_ratio': round(day_vol_ratio, 2),
                            'day_chg_pct': round(day_chg_pct, 2),
                            'trough_price': round(trough_price, 2),
                            'breakout_vol_ratio': bs.get('breakout_vol_ratio', 0),
                            'secondary_rules': [],
                        }
                    })

            # ── 规则 F：连续放量溃败（severe）──
            if rb['F'] and day_close < buy_point and day_vol_ratio > params['rule_f_vol_ratio']:
                rule_f_streak += 1
                if rule_f_streak >= params['rule_f_days']:
                    failures.append({
                        'date': day_date, 'type': 'bearish',
                        'breakout_date': b_date, 'buy_point': buy_point,
                        'breakout_low': round(breakout_low, 2), 'failure_day': failure_day,
                        'failed_rule': 'rule_f', 'severity': 'severe',
                        'details': {
                            'rule_label': f'连续放量溃败({b_date}突破 D+{failure_day})', 'current_close': round(day_close, 2),
                            'volume_ratio': round(day_vol_ratio, 2),
                            'day_chg_pct': round(day_chg_pct, 2),
                            'trough_price': round(trough_price, 2),
                            'breakout_vol_ratio': bs.get('breakout_vol_ratio', 0),
                            'secondary_rules': [],
                        }
                    })
            else:
                rule_f_streak = 0

            # ── 规则 G：抛盘日叠加（confirmed）──
            if rb['G'] and not rule_g_triggered:
                rule_g_dd_dates.append(is_dd)
                # 只在规则G窗口内统计
                g_window = params['rule_g_window']
                if len(rule_g_dd_dates) >= g_window:
                    recent_dd = sum(rule_g_dd_dates[-g_window:])
                else:
                    recent_dd = sum(rule_g_dd_dates)

                dd_count_ok = recent_dd >= params['rule_g_count']
                price_ok = False
                # 检查窗口内任一天是否收盘 < buy_point
                g_check_start = max(monitor_start, day_idx - g_window + 1)
                for gi in range(g_check_start, day_idx + 1):
                    if closes[gi] < buy_point:
                        price_ok = True
                        break

                if dd_count_ok and price_ok:
                    rule_g_triggered = True
                    failures.append({
                        'date': day_date, 'type': 'bearish',
                        'breakout_date': b_date, 'buy_point': buy_point,
                        'breakout_low': round(breakout_low, 2), 'failure_day': failure_day,
                        'failed_rule': 'rule_g', 'severity': 'confirmed',
                        'details': {
                            'rule_label': f'抛盘日叠加({b_date}突破 D+{failure_day})', 'current_close': round(day_close, 2),
                            'volume_ratio': round(day_vol_ratio, 2),
                            'day_chg_pct': round(day_chg_pct, 2),
                            'trough_price': round(trough_price, 2),
                            'breakout_vol_ratio': bs.get('breakout_vol_ratio', 0),
                            'secondary_rules': [],
                        }
                    })

            # ── 规则 H：窗口结束机构出货（confirmed）──
            # 只在最后一天（monitor_end）且当前循环正好走到最后一天时触发
            if rb['H'] and day_idx == monitor_end and day_close < buy_point:
                # 窗口总成交量 / 同期均量
                window_volumes = volumes[monitor_start:monitor_end + 1]
                window_total = sum(window_volumes)
                window_avg = window_total / len(window_volumes) if window_volumes else 0
                daily_avg = vol_baseline
                if daily_avg > 0 and window_avg > daily_avg * params['rule_h_total_vol_ratio']:
                    failures.append({
                        'date': day_date, 'type': 'bearish',
                        'breakout_date': b_date, 'buy_point': buy_point,
                        'breakout_low': round(breakout_low, 2), 'failure_day': failure_day,
                        'failed_rule': 'rule_h', 'severity': 'confirmed',
                        'details': {
                            'rule_label': f'窗口结束机构出货({b_date}突破 D+{failure_day})', 'current_close': round(day_close, 2),
                            'volume_ratio': round(day_vol_ratio, 2),
                            'day_chg_pct': round(day_chg_pct, 2),
                            'trough_price': round(trough_price, 2),
                            'breakout_vol_ratio': bs.get('breakout_vol_ratio', 0),
                            'secondary_rules': [],
                        }
                    })

    # 同日期去重：保留最严重的信号（severe > mild），合并触发的规则列表
    deduped = {}
    for f in failures:
        key = f['date']
        if key not in deduped:
            deduped[key] = f
            deduped[key]['triggered_rules'] = [f['failed_rule']]
        else:
            existing = deduped[key]
            existing['triggered_rules'].append(f['failed_rule'])
            # 保留更严重的
            severity_order = {'severe': 3, 'confirmed': 2, 'mild': 1}
            if severity_order.get(f['severity'], 0) > severity_order.get(existing['severity'], 0):
                # 用更严重的信号替换，但保留合并的规则列表
                f['triggered_rules'] = existing['triggered_rules']
                deduped[key] = f

    # 更新 details 中的规则标签为合并后的
    for f in deduped.values():
        rules = f.get('triggered_rules', [f['failed_rule']])
        f['details']['triggered_rules'] = rules
        if len(rules) > 1:
            # 合并标签时去掉重复的日期后缀
            short_rules = [r.replace(f'({f["breakout_date"]}突破 D+{f["failure_day"]})','') for r in rules]
            f['details']['rule_label'] = ' + '.join(short_rules) + f' ({f["breakout_date"]}突破 D+{f["failure_day"]})'

    return list(deduped.values())


# ═══════════════════════════════════════════════════
# 诊断函数：对指定 (breakout_date, check_date) 做逐规则排查
# ═══════════════════════════════════════════════════

def diagnose(klines, breakout_date, check_date, bp_params=None):
    """
    对特定突破日在特定日期的失败信号进行逐规则排查。

    Returns:
        Dict: need/actual 格式的诊断结果
    """
    from scanners.base_breakout_v2 import detect as detect_breakout
    from scanners.base_breakout_v2 import load_params as load_bp_params

    params = load_params()
    vol_ma_days = params['vol_ma_days']

    if bp_params is None:
        bp_params = load_bp_params()

    # 建立日期 → K线索引
    date_to_idx = {}
    for i, k in enumerate(klines):
        date_to_idx[k['date']] = i

    n = len(klines)
    volumes = [k.get('volume', 0) for k in klines]
    closes = [k.get('close', 0) for k in klines]

    # 找到突破日对应的 K 线
    b_idx = date_to_idx.get(breakout_date)
    if b_idx is None:
        return {'error': f'未找到突破日 {breakout_date} 的K线数据'}

    check_idx = date_to_idx.get(check_date)
    if check_idx is None:
        return {'error': f'未找到检查日 {check_date} 的K线数据'}

    # 获取该突破日的信号
    breakout_signals = detect_breakout(klines, bp_params)
    bs = None
    for s in breakout_signals:
        if s['signal_date'] == breakout_date:
            bs = s
            break
    if bs is None:
        return {'error': f'{breakout_date} 不是基部突破日'}

    breakout_low = klines[b_idx].get('low', klines[b_idx].get('close', 0))
    buy_point = bs['buy_point']
    trough_price = bs['trough_price']

    day_k = klines[check_idx]
    day_close = closes[check_idx]
    day_volume = volumes[check_idx]

    # 量比计算
    vol_start = max(0, b_idx - vol_ma_days)
    vol_baseline = sum(volumes[vol_start:b_idx]) / (b_idx - vol_start) if b_idx > vol_start else 0
    day_vol_ratio = day_volume / vol_baseline if vol_baseline > 0 else 0

    # 日间涨跌幅
    prev_close = closes[check_idx - 1] if check_idx > 0 else closes[check_idx]
    day_chg_pct = ((day_close - prev_close) / prev_close * 100) if prev_close > 0 else 0

    # 抛盘日
    prev_vol = volumes[check_idx - 1] if check_idx > 0 else 0
    is_dd = _is_distribution_day(day_volume, prev_vol, day_chg_pct, params['rule_g_dd_threshold'])

    monitor_day = check_idx - b_idx

    # 连续放量计数
    rule_f_count = 0
    for fi in range(max(b_idx + 1, check_idx - params['rule_f_days'] + 1), check_idx + 1):
        if closes[fi] < buy_point and (volumes[fi] / vol_baseline if vol_baseline > 0 else 0) > params['rule_f_vol_ratio']:
            rule_f_count += 1
        else:
            rule_f_count = 0

    # 抛盘日窗口统计
    g_window = params['rule_g_window']
    g_check_start = max(b_idx + 1, check_idx - g_window + 1)
    dd_count = sum(1 for gi in range(g_check_start, check_idx + 1)
                   if _is_distribution_day(volumes[gi], volumes[gi-1] if gi > 0 else 0,
                                           ((closes[gi] - closes[gi-1]) / closes[gi-1] * 100) if closes[gi-1] > 0 else 0,
                                           params['rule_g_dd_threshold']))
    g_price_ok = any(closes[gi] < buy_point for gi in range(g_check_start, check_idx + 1))

    # 规则 H 相关
    is_last_day = (check_idx - b_idx) == params['monitor_days']
    h_window_avg = 0
    if is_last_day:
        window_vols = volumes[b_idx + 1:check_idx + 1]
        h_window_avg = sum(window_vols) / len(window_vols) if window_vols else 0

    def mk_rule(rule_id, label, triggered, need_str, actual_str):
        return {'triggered': triggered, 'label': label, 'need': need_str, 'actual': actual_str}

    rules = {}
    rules['rule_b'] = mk_rule('rule_b', '跌破突破日最低价',
        day_close < breakout_low,
        f'close < {breakout_low:.2f}', f'{day_close:.2f}')

    rules['rule_c'] = mk_rule('rule_c', '跌回枢轴点',
        day_close < buy_point,
        f'close < {buy_point:.2f}', f'{day_close:.2f}')

    d_threshold = trough_price * params['rule_d_trough_mult']
    rules['rule_d'] = mk_rule('rule_d', '跌破谷底×0.98',
        day_close < d_threshold,
        f'close < {d_threshold:.2f}', f'{day_close:.2f}')

    e_triggered = (day_chg_pct <= -params['rule_e_chg_pct'] and
                   day_vol_ratio >= params['rule_e_vol_ratio'] and
                   day_close < buy_point)
    rules['rule_e'] = mk_rule('rule_e', '放量长阴砸穿',
        e_triggered,
        f'chg≤-{params["rule_e_chg_pct"]}% & vol≥{params["rule_e_vol_ratio"]} & close<{buy_point:.2f}',
        f'chg={day_chg_pct:.2f}% vol={day_vol_ratio:.2f} close={day_close:.2f}')

    rules['rule_f'] = mk_rule('rule_f', '连续放量溃败',
        rule_f_count >= params['rule_f_days'],
        f'count≥{params["rule_f_days"]}',
        f'count={rule_f_count}')

    rules['rule_g'] = mk_rule('rule_g', '抛盘日叠加',
        dd_count >= params['rule_g_count'] and g_price_ok,
        f'≥{params["rule_g_count"]}个抛盘日 & close<{buy_point:.2f}',
        f'{dd_count}个抛盘日 price_ok={g_price_ok}')

    rules['rule_h'] = mk_rule('rule_h', '窗口结束出货',
        is_last_day and day_close < buy_point and h_window_avg > vol_baseline * params['rule_h_total_vol_ratio'] if is_last_day else False,
        f'day={params["monitor_days"]} & close<{buy_point:.2f} & avg_vol>{vol_baseline*params["rule_h_total_vol_ratio"]:.0f}',
        f'day={monitor_day} {"(last)" if is_last_day else ""} close={day_close:.2f}')

    return {
        'code': klines[check_idx].get('stock_code', ''),
        'breakout_date': breakout_date,
        'check_date': check_date,
        'buy_point': buy_point,
        'close': round(day_close, 2),
        'vol_ratio': round(day_vol_ratio, 2),
        'day_chg_pct': round(day_chg_pct, 2),
        'monitor_day': monitor_day,
        'rules': rules,
    }


if __name__ == '__main__':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description='Breakout Failure Detection')
    parser.add_argument('--stock', type=str, default='600519')
    parser.add_argument('--date', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--mode', type=str, default='stock')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    table = 'index_daily_kline' if args.mode == 'index' else 'daily_kline'
    kf = "AND kline_type='normal'" if args.mode == 'index' else ''

    rows = conn.execute(f"""
        SELECT date, open, high, low, close, volume FROM {table}
        WHERE stock_code=? {kf} AND date<=date(?, '+20 days') AND date>=date(?, '-750 days')
        ORDER BY date
    """, (args.stock, args.date, args.date)).fetchall()
    conn.close()

    if len(rows) < 170:
        print(f"K线不足: {len(rows)} 条 (需要 >= 170)")
        sys.exit(1)

    daily = [dict(r) for r in rows]
    params = load_params()

    # 只监控 end_date 之前的突破
    end_idx = None
    for i, k in enumerate(daily):
        if k['date'] > args.date:
            end_idx = i - 1
            break
    if end_idx is None:
        end_idx = len(daily) - 1

    monitor_klines = daily[:end_idx + params['monitor_days'] + 2]
    failures = detect(monitor_klines)

    # 过滤到 args.date 之前
    failures = [f for f in failures if f['date'] <= args.date]

    print(f"[{args.stock}] Breakout Failure Detection")
    print(f"   Total failure signals: {len(failures)}")
    by_sev = {}
    for f in failures:
        sev = f['severity']
        by_sev[sev] = by_sev.get(sev, 0) + 1
    for sev, count in sorted(by_sev.items()):
        print(f"   {sev}: {count}")
    for f in failures[-10:]:
        print(f"   {f['date']} [{f['severity']}] {f['details']['rule_label']}")
        print(f"      orig_breakout={f['breakout_date']} buy_point={f['buy_point']} D+{f['failure_day']} close={f['details']['current_close']}")
