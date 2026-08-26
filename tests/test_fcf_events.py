# -*- coding: utf-8 -*-
"""fcf 信号事件去重逻辑单测（v1.1 修复固化，PRD §5.1b）

覆盖：首触发弱信号 → 窗口内强信号替换 → 同分更极端替换 → 20 交易日后新窗口
      → 替换重置冷却（W1 决策 B：同一波低估合并为一个代表点）
"""
import sys
import os

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_events(dates, val_map):
    """复刻 server.py api_market_fcf_detail 的 events 生成逻辑（纯逻辑抽取）"""
    def _score(d):
        return (d.get('pe_pct') if d.get('pe_pct') is not None else 100) + \
               (d.get('pb_pct') if d.get('pb_pct') is not None else 100) + \
               (100 - (d.get('dyr_pct') if d.get('dyr_pct') is not None else 0))
    events = []
    last_trig = -999
    for i, d in enumerate(dates):
        if d not in val_map:
            continue
        pe_p, pb_p, dy_p = val_map[d]
        n_buy = sum([pe_p is not None and pe_p < 0.33,
                     pb_p is not None and pb_p < 0.33,
                     dy_p is not None and dy_p > 0.66])
        if n_buy < 1:
            continue
        if i - last_trig >= 20:
            events.append({'date': d, 'pe_pct': round(pe_p * 100) if pe_p is not None else None,
                           'pb_pct': round(pb_p * 100) if pb_p is not None else None,
                           'dyr_pct': round(dy_p * 100) if dy_p is not None else None, 'n_buy': n_buy})
            last_trig = i
        else:
            cur = events[-1]
            cur_n = cur.get('n_buy', 0)
            if n_buy > cur_n:
                events[-1] = {'date': d, 'pe_pct': round(pe_p * 100) if pe_p is not None else None,
                              'pb_pct': round(pb_p * 100) if pb_p is not None else None,
                              'dyr_pct': round(dy_p * 100) if dy_p is not None else None, 'n_buy': n_buy}
                last_trig = i
            elif n_buy == cur_n:
                nw = {'date': d, 'pe_pct': round(pe_p * 100) if pe_p is not None else None,
                      'pb_pct': round(pb_p * 100) if pb_p is not None else None,
                      'dyr_pct': round(dy_p * 100) if dy_p is not None else None, 'n_buy': n_buy}
                if _score(nw) < _score(cur):
                    events[-1] = nw
                    last_trig = i
    return events


def _dates(n):
    return [f'2026-01-{1 + i:02d}' for i in range(n)]


def test_weak_first_then_strong_replaces():
    """首触发弱信号(n_buy=1) → 窗口内强信号(n_buy=3)替换"""
    dates = _dates(30)
    vm = {dates[0]: (0.50, 0.20, 0.30),   # PE不满足, PB<33%, DYR不满足 → n_buy=1
          dates[5]: (0.18, 0.00, 0.95)}   # 全达标 → n_buy=3
    ev = build_events(dates, vm)
    assert len(ev) == 1, ev
    assert ev[0]['date'] == dates[5], ev[0]  # 被更强信号替换
    assert ev[0]['n_buy'] == 3
    print('test_weak_first_then_strong_replaces ✅')


def test_same_nbuy_more_extreme_wins():
    """同 n_buy=3：更极端者替换（PB=0 不被 or-100 误判）"""
    dates = _dates(30)
    vm = {dates[0]: (0.20, 0.02, 0.92),   # PE20/PB2/DYR92 → extreme 20+2+8=30
          dates[3]: (0.18, 0.00, 0.95)}   # PE18/PB0/DYR95 → extreme 18+0+5=23 更极端
    ev = build_events(dates, vm)
    assert len(ev) == 1 and ev[0]['date'] == dates[3], ev
    assert ev[0]['pb_pct'] == 0  # PB 0 未被 or-100 误判
    print('test_same_nbuy_more_extreme_wins ✅')


def test_new_window_after_20_days():
    """20 交易日后新窗口独立标记"""
    dates = _dates(30)
    vm = {dates[0]: (0.50, 0.20, 0.30),    # n_buy=1 触发
          dates[22]: (0.40, 0.10, 0.80)}   # 距 22 ≥20 → 新窗口 n_buy=2
    ev = build_events(dates, vm)
    assert len(ev) == 2, ev
    assert ev[0]['date'] == dates[0] and ev[1]['date'] == dates[22]
    print('test_new_window_after_20_days ✅')


def test_replace_resets_cooldown():
    """W1 决策 B：替换重置冷却——6-30 替换后 9 交易日内的 7-13 被合并"""
    dates = _dates(40)
    # dates[10] = 6-12 位置（n_buy=1），dates[18] = 6-30 位置（n_buy=3），dates[27] = 7-13 位置（n_buy=3 但距 6-30 仅 9 交易日）
    vm = {dates[10]: (0.68, 0.01, 0.22),
          dates[18]: (0.18, 0.00, 0.95),
          dates[27]: (0.35, 0.02, 0.83)}
    ev = build_events(dates, vm)
    assert len(ev) == 1, ev
    assert ev[0]['date'] == dates[18]  # 6-30 是代表点，7-13 被合并
    print('test_replace_resets_cooldown ✅')


def test_round_boundary_nbuy():
    """round 边界：PE 原始 0.325（round=33 不达标显示）但原始 <0.33 达标 → n_buy 存原始计算值"""
    dates = _dates(20)
    vm = {dates[0]: (0.325, 0.50, 0.90)}  # PE 0.325<0.33 ✅, PB 不满足, DYR 0.90>0.66 ✅ → n_buy=2
    ev = build_events(dates, vm)
    assert ev[0]['n_buy'] == 2, ev  # 不用 round 后重算（33 不达标会低估为 1）
    print('test_round_boundary_nbuy ✅')


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    ok = 0
    for fn in fns:
        try:
            fn()
            ok += 1
        except AssertionError as e:
            print(f'  ❌ {fn.__name__}: {e}')
    print(f'\n{ok}/{len(fns)} 通过')
    sys.exit(0 if ok == len(fns) else 1)
