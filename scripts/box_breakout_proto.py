#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
放量箱体突破检测 — 单股原型 v0.4（前复权价 + 上下沿双边界）
=============================================================
v0.4 更新：
  - 使用前复权价格（change_pct 逆向推算，与 pattern-scan 页面一致）
  - 箱体 = 上沿（阻力带）+ 下沿（支撑带）双边界
  - 上沿：收盘局部高点聚类；下沿：收盘局部低点聚类
  - 触碰下沿不破 = 支撑确认；破位 = 箱体失效
"""
import sys, os, sqlite3, argparse

PROJ = r'D:\hanako\investment-system'
DB = os.path.join(PROJ, 'data', 'lixinger.db')

# ── 参数 ──
LOOKBACK_DAYS = 300      # 箱体识别窗口
BAND_TOL = 0.03          # 带容差 ±3%
MIN_TOUCHES = 3          # 触碰最少天数
BOX_MIN_DAYS = 40        # 箱体最小跨度（交易日）
MAX_DEPTH = 0.35         # 箱体最大深度
VOL_MA_N = 20
VOL_RATIO = 1.5
HOLD_DAYS = 2
LOCAL_W = 10             # 局部极值窗口


def load_klines_adj(code):
    """加载前复权 K 线（change_pct 逆向推算，与 server._ensure_adj_prices 一致）"""
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    rows = db.execute("""SELECT date, open, high, low, close, volume, change_pct FROM daily_kline
        WHERE stock_code=? ORDER BY date""", (code,)).fetchall()
    db.close()
    kl = [dict(r) for r in rows]
    n = len(kl)
    if n:
        kl[n-1]['adj_close'] = kl[n-1]['close']
        for i in range(n-2, -1, -1):
            chg = kl[i+1].get('change_pct')
            kl[i]['adj_close'] = kl[i+1]['adj_close'] / (1 + chg) if chg is not None else kl[i+1]['adj_close']
        for k in kl:
            ratio = k['adj_close'] / k['close'] if k['close'] else 1
            k['open'] = k['open'] * ratio if k['open'] else k['open']
            k['high'] = k['high'] * ratio if k['high'] else k['high']
            k['low'] = k['low'] * ratio if k['low'] else k['low']
            k['close'] = k['adj_close']
    return kl


def find_bands(values, end_idx, is_high=True):
    """找收盘价带（上沿=高点聚类 / 下沿=低点聚类）。
    values: 收盘价序列; 返回 [{level, touches}]"""
    start = max(0, end_idx - LOOKBACK_DAYS)
    seg = values[start:end_idx+1]
    if len(seg) < 60:
        return []

    # 局部极值
    extrema = []
    for i in range(LOCAL_W, len(seg) - LOCAL_W):
        c = seg[i]
        if c is None:
            continue
        win_l = [x for x in seg[i-LOCAL_W:i] if x is not None] or [0]
        win_r = [x for x in seg[i+1:i+LOCAL_W+1] if x is not None] or [0]
        if is_high:
            if c >= max(win_l) and c >= max(win_r):
                extrema.append(c)
        else:
            if c <= min(win_l) and c <= min(win_r):
                extrema.append(c)
    if not extrema:
        return []

    # 聚类成带
    xs = sorted(set(round(x, 2) for x in extrema), reverse=is_high)
    bands = []
    for x in xs:
        placed = False
        for b in bands:
            if abs(x - b['level']) / b['level'] <= BAND_TOL:
                b['level'] = (max if is_high else min)(b['level'], x)
                placed = True
                break
        if not placed:
            bands.append({'level': x})

    # 触碰天数
    for b in bands:
        if is_high:
            lo, hi = b['level'] * (1 - BAND_TOL), b['level']
            b['touches'] = sum(1 for c in seg if c is not None and lo <= c <= hi)
        else:
            lo, hi = b['level'], b['level'] * (1 + BAND_TOL)
            b['touches'] = sum(1 for c in seg if c is not None and lo <= c <= hi)

    return [b for b in bands if b['touches'] >= MIN_TOUCHES]


def detect(code, verbose=True):
    kl = load_klines_adj(code)
    n = len(kl)
    closes = [k['close'] for k in kl]
    vols = [k['volume'] or 0 for k in kl]
    dates = [k['date'] for k in kl]

    vol_ma = [None] * n
    for i in range(n):
        if i >= VOL_MA_N - 1:
            vol_ma[i] = sum(vols[i-VOL_MA_N+1:i+1]) / VOL_MA_N

    # ── 阶段1：定位箱体（2025-08 ~ 2026-06 窗口内找上下沿）──
    # 简化：在指定区间找最显著的箱体，输出上下沿供用户核对
    events = []
    band_state = {}

    for t in range(LOOKBACK_DAYS + 30, n):
        if closes[t] is None:
            continue
        upper_bands = find_bands(closes, t, is_high=True)
        lower_bands = find_bands(closes, t, is_high=False)
        if not upper_bands:
            continue

        for ub in upper_bands:
            top = ub['level']
            key = round(top, 2)
            st = band_state.get(key)

            # 下沿：与上沿配对的支撑带（取上沿下方最近、且深度 ≤ MAX_DEPTH 的）
            support = None
            for lb in sorted(lower_bands, key=lambda x: -x['level']):
                if lb['level'] < top and (top - lb['level']) / top <= MAX_DEPTH:
                    support = lb
                    break

            if closes[t] >= top:
                if st is not None and st.get('broken_at') is not None and not st.get('back_in_box'):
                    continue
                pre = [c for c in closes[max(0, t-BOX_MIN_DAYS):t] if c is not None]
                if len(pre) < BOX_MIN_DAYS:
                    continue
                if st is None:
                    break_pre = [c for c in pre if c > top * 1.00]
                    if break_pre:
                        continue
                    pre_low = min(pre)
                    if pre_low < top * (1 - MAX_DEPTH):
                        continue

                vma = vol_ma[t]
                vr = vols[t] / vma if vma else 0
                events.append({
                    'date': dates[t], 'close': closes[t],
                    'band_top': top,
                    'support': round(support['level'], 2) if support else None,
                    'sup_touches': support['touches'] if support else 0,
                    'touches': ub['touches'],
                    'box_days': len(pre) if st is None else '二次',
                    'vol_ratio': round(vr, 2),
                    'volume_ok': vr >= VOL_RATIO,
                    'result': 'pending',
                })
                band_state[key] = {'broken_at': t, 'back_in_box': False}
            else:
                if st is not None and st.get('broken_at') is not None and closes[t] < top * 0.97:
                    st['back_in_box'] = True

    # 确认
    for ev in events:
        t = next(i for i, k in enumerate(kl) if k['date'] == ev['date'])
        future = [closes[j] for j in range(t+1, min(t+1+HOLD_DAYS, n))]
        if len(future) < HOLD_DAYS:
            ev['result'] = '待确认'
        elif all(f >= ev['band_top'] for f in future):
            ev['result'] = '✅ 成功站稳'
        elif any(f >= ev['band_top'] for f in future):
            ev['result'] = '🟡 回踩确认'
        else:
            ev['result'] = '❌ 假突破'

    if verbose:
        print(f'═══ {code} 放量箱体突破 v0.4（前复权）═══')
        print(f'K线: {n} 根 | 参数: 容差±{BAND_TOL*100:.0f}% 触碰≥{MIN_TOUCHES}天 箱体≥{BOX_MIN_DAYS}天 深度≤{MAX_DEPTH*100:.0f}% 放量≥{VOL_RATIO}x 站稳{HOLD_DAYS}日')
        print()
        # 核对用户指定的关键日期
        print('用户核对点（前复权）：')
        for d in ['2025-09-02', '2025-10-09', '2025-12-03', '2026-01-14', '2026-02-12', '2026-03-23', '2026-04-15', '2026-04-29', '2026-06-09', '2026-06-24']:
            if d in dates:
                i = dates.index(d)
                print(f'  {d}: C={closes[i]:.2f} H={kl[i]["high"]:.2f} L={kl[i]["low"]:.2f} V={vols[i]/1e6:.0f}M')
        print()
        print(f'箱体突破事件 {len(events)} 次：')
        print(f'{"日期":<12}{"收盘":>8}{"上沿":>8}{"下沿":>8}{"上触":>5}{"下触":>5}{"量比":>7}{"放量":>5}  结果')
        print('-' * 80)
        for ev in events:
            flag = '✓' if ev['volume_ok'] else '✗'
            box_days = ev['box_days'] if isinstance(ev['box_days'], str) else str(ev['box_days'])
            sup = f'{ev["support"]:.2f}' if ev['support'] else '—'
            print(f'{ev["date"]:<12}{ev["close"]:>8.2f}{ev["band_top"]:>8.2f}{sup:>8}{ev["touches"]:>5}{ev["sup_touches"]:>5}{ev["vol_ratio"]:>7.2f}{flag:>5}  {ev["result"]} ({box_days})')
    return events


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', default='603259')
    args = parser.parse_args()
    detect(args.code)
