# -*- coding: utf-8 -*-
"""规则引擎单测（PRD §13：净分/卖出优先/位置约束/十戒/档位）"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from src.scanners.report_rules import (evaluate, normalize_engine_signal,
                                       _dedup_by_source, load_weights, detect_missed)

W = load_weights()
D = '2026-08-18'
B = {'close': 10, 'pos_250': 40, 'ma50': 9.5, 'ma50_slope': 0.1, 'gain_from_low': 15}


def ev(signals, ctx, **kw):
    return evaluate(signals, ctx, W, scan_date=kw.get('scan_date', '2026-08-21'))


def test_no_signal_hold():
    assert ev([], B)['level'] == 'hold'


def test_resonance_buy():
    sigs = [{'source': 'mw_b1', 'date': D, 'dir': 'long', 'ts': 70},
            {'source': 'pocket_pivot', 'date': D, 'dir': 'long', 'ts': None}]
    r = ev(sigs, B)
    assert r['level'] == 'buy', (r['level'], r['net'])


def test_resonance_buy_strong():
    sigs = [{'source': 'mw_b1', 'date': D, 'dir': 'long', 'ts': 70},
            {'source': 'pocket_pivot', 'date': D, 'dir': 'long', 'ts': None},
            {'source': 'box_breakout', 'date': D, 'dir': 'long'}]
    assert ev(sigs, B)['level'] == 'buy_strong'


def test_single_signal_not_buy():
    assert ev([{'source': 'pocket_pivot', 'date': D, 'dir': 'long', 'ts': None}], B)['level'] == 'hold'


def test_position_degrades_buy():
    sigs = [{'source': 'mw_b1', 'date': D, 'dir': 'long', 'ts': 70},
            {'source': 'pocket_pivot', 'date': D, 'dir': 'long', 'ts': None}]
    assert ev(sigs, {**B, 'pos_250': 90})['level'] == 'wait'


def test_sell_priority_high_pos():
    sell = [{'source': 'top_pattern', 'date': D, 'dir': 'short'}]
    assert ev(sell, {**B, 'pos_250': 80})['level'] == 'avoid'


def test_sell_priority_low_pos():
    sell = [{'source': 'top_pattern', 'date': D, 'dir': 'short'}]
    assert ev(sell, {**B, 'pos_250': 30})['level'] == 'wait'


def test_ma50_down_ban():
    sigs = [{'source': 'mw_b1', 'date': D, 'dir': 'long', 'ts': 70},
            {'source': 'pocket_pivot', 'date': D, 'dir': 'long', 'ts': None}]
    assert ev(sigs, {**B, 'close': 9.0, 'ma50': 9.5, 'ma50_slope': -0.05})['level'] == 'wait'


def test_stop_loss():
    """十戒止损：持仓浮亏>8% → avoid（B1 回归）"""
    r = ev([], {**B, 'holding_cost': 10.0, 'close': 9.0})
    assert r['level'] == 'avoid', r['level']
    # 止损优先于买入信号
    sigs = [{'source': 'mw_b1', 'date': D, 'dir': 'long', 'ts': 70},
            {'source': 'pocket_pivot', 'date': D, 'dir': 'long', 'ts': None}]
    r = ev(sigs, {**B, 'holding_cost': 10.0, 'close': 9.0})
    assert r['level'] == 'avoid', r['level']


def test_callback_trigger():
    ctx = {**B, 'fib_levels': [9.4, 9.0, 8.5], 'grid_levels': [9.8]}
    r = ev([], ctx)
    assert r['level'] == 'wait' and r['callback'] and r['callback']['price'] == 9.8


def test_expired_signal_filtered():
    r = ev([{'source': 'mw_b1', 'date': '2026-05-01', 'dir': 'long', 'ts': 80}], B)
    assert r['net'] == 0


def test_normalize_direction_fallback():
    """type 缺失的引擎信号按权重表方向 fallback（base_breakout=long）"""
    ns = normalize_engine_signal({'source': 'base_breakout', 'date': D, 'details': {}})
    assert ns['dir'] == 'long', ns


def test_normalize_confirmed_filter():
    ns = normalize_engine_signal({'source': 'railroad_tracks', 'type': 'bearish', 'date': D,
                                  'details': {'confirmed': False}})
    assert ns is None, ns


def test_dedup_earliest():
    sigs = [{'source': 'top_pattern', 'date': '2026-08-01', 'dir': 'short'},
            {'source': 'top_pattern', 'date': '2026-08-10', 'dir': 'short'}]
    d = _dedup_by_source(sigs)
    assert len(d) == 1 and d[0]['date'] == '2026-08-01'


def test_dedup_keep_ts():
    sigs = [{'source': 'mw_b1', 'date': '2026-08-01', 'dir': 'long', 'ts': None},
            {'source': 'mw_b1', 'date': '2026-08-01', 'dir': 'long', 'ts': 70}]
    d = _dedup_by_source(sigs)
    assert len(d) == 1 and d[0]['ts'] == 70


def test_detect_missed():
    sigs = [{'source': 'mw_b1', 'date': '2026-08-10', 'dir': 'long', 'close_at': 10.0,
             'note': ''}]
    miss = detect_missed(sigs, '2026-08-01', '2026-08-21', {**B, 'close': 12.5}, weights=W)
    assert len(miss) == 1 and miss[0]['missed'] is True and round(miss[0]['gain_pct'], 1) == 25.0


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn()
            ok += 1
            print(f'  ✅ {fn.__name__}')
        except AssertionError as e:
            print(f'  ❌ {fn.__name__}: {e}')
    print(f'\n{ok}/{len(fns)} 通过')
    sys.exit(0 if ok == len(fns) else 1)
