#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指数 CPA 八阶段状态机（Oliver Kell 的 Cycle of Price Action，指数适配版）。

与个股版 `src/scanners/cpa_stage.py` 的差别：
  1. 不依赖 stock_rs_daily（无基准）、不依赖缠论笔（用分形摆动高低点自算）
  2. 所有数值阈值集中在 CFG，便于在指数样本上重新标定
  3. 纯状态机：每日归入八阶段之一，阶段切换写记录

阶段与动作（见 docs/product/指数CPA阶段判定_PRD_v0.1.md）：
  ① Reversal Extension  反转尝试      → 观察
  ② Wedge Pop            突破          → 入场
  ③ EMA Crossback        回踩          → 标准档入场 / 深档观察
  ④ Base 'n Break        基部突破      → 观察
  ⑤ Exhaustion Extension 衰竭延伸      → 保护利润
  ⑥ Wedge Drop           破位下坠      → ⑥a 清仓 / ⑥w 减仓
  ⑦ EMA Crossback Down   下行回踩      → 观察
  ⑧ Base 'n Break Down   下行平台破位  → 观察

用法：
    python src/scanners/cpa_index.py --code 980092            # 单指数
    python src/scanners/cpa_index.py --all                    # 全部指数
    python src/scanners/cpa_index.py --all --save             # 写入 index_cpa_stage
"""
import argparse
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime

DB = 'data/lixinger.db'

# ── 可标定参数（指数版初始值，来自个股结构、待指数样本校准）──
CFG = dict(
    ema_fast=10, ema_mid=20, ema_slow=50,
    atr_n=20, atr_n_slow=60,
    vr_n=20,              # 量比窗口
    fractal_k=3,          # 分形摆动点：前后各 k 日
    # ② Wedge Pop：突破需放量
    pop_vr_min=1.5,
    pop_buf=0.005,        # 突破缓冲 0.5%
    # ③ 回踩：缩量 + 守住均线
    cb_vr_max=0.9,
    cb_tol_atr=0.3,
    cb_hold_days=2,
    cb_slope_lag=3,
    cb_window_min=5,      # ③ 必须在 ② 之后 5~15 日内（PRD §3.3 前置）
    cb_window_max=15,
    # ④ 箱体
    box_min=10, box_max=40,
    box_depth_max=0.05,
    # ⑤ 衰竭：自低点涨幅
    exh_gain_min=0.30,
    # ⑥ 破位
    drop_buf=0.005,
    # ① 反转尝试：延伸极值 + 恐慌量
    r_nd20_min=3.0,       # N_D20 ≥ 3（ATR 归一化跌幅）
    r_d20_max_pct=-0.12,  # 或 20 日跌幅 ≤ −12%
    r_panic_vr=1.8,       # 近 20 日出现 VR≥1.8 的下跌日
    r_lower_shadow_ratio=2.0,
    r_lower_shadow_atr=0.5,
    # ② 停顿区（阶段②/④ 共用）
    w_win_min=3, w_win_max=40,
    w_amp_tol=0.12,       # 长窗口振幅 ≤12%
    w_amp_shrink=0.6,     # 短窗振幅 ≤ 长窗 ×0.6
    w_vol_dry=0.7,        # 后段均量/前段均量 ≤0.7
    pause_amp_max=0.30,
    w_first_lookback=20,  # 「首次站上」回看
    # ⑦ 下行回踩的容差
    dcb_tol_atr=0.5,
    # 回溯窗口
    lookback=120,
    warmup=120,
)

# ── 状态迁移约束（CPA 生命周期）──
# 上行：① → ② → ③ → ⑤ ；下行：⑤/③ → ⑥ → ⑦/⑧ → ①
# 每个集合「包含自身」，即允许停留。
ALLOWED = {
    0: {0, 1, 2, 3, 4, 5, 6, 7, 8},   # 待确认态可退回到任何阶段
    1: {0, 1, 2, 3, 4, 6, 7, 8},      # 反转尝试：可突破、可回踩、可失败回到下行
    2: {0, 2, 3, 4, 5, 6},            # 突破：进入回踩/延伸，或突破失败破位
    3: {0, 3, 2, 4, 5, 6},            # 回踩：可再次突破、可延伸、可失败破位
    4: {0, 4, 3, 2, 5, 6},            # 基部突破：同 ③
    5: {0, 5, 3, 4, 6},               # 衰竭延伸：回落做回踩，或见顶破位
    6: {0, 6, 7, 8, 1, 2},         # 破位下坠：进入下行回踩/平台、反转尝试，或直接放量突破
    7: {0, 7, 6, 8, 1, 2},         # 下行回踩：继续跌、做平台、反转，或直接突破
    8: {0, 8, 6, 7, 1, 2},         # 下行平台破位：同上
}
# 允许从任意阶段切入的「紧急」阶段（破位是不可忽视的）
URGENT = {6}
MIN_DWELL = 10    # 默认最小驻留
# 瞬态阶段允许更短驻留：③ 回踩约 2~5 日、⑦ 下行回踩约 3~8 日
MIN_DWELL_OVR = {3: 3, 7: 5}

# 动作绑定
# 动作按指数样本校准（见 docs/analysis/指数CPA/06 与 PRD v1.0）
#   ③ 原「回踩入场」→ 观察（胜率 44.4%，低于抛硬币）
#   ⑤ 原「保护利润」→ 持有（实测反向：涨幅越大之后越强）
#   ② 保留买入信号，但样本外已退化（57.8%→48.9%），页面必须同屏警告
ACTION = {
    0: '待确认（次根 K 线收盘定夺）',
    1: '观察', 2: '买入信号', 3: '观察', 4: '观察',
    5: '持有，不减仓',
    6: '离场信号', 7: '观察', 8: '观察',
}

# 全样本 / 样本外(2020+) 区间胜率，供页面直接展示
# 各阶段区间统计（进入→离开，不跨阶段），由新引擎在 394 个指数上实算
# 各阶段区间统计（进入→离开，不跨阶段），由新引擎在 394 个指数上实算
SAMPLE_STATS = {
    0: {'name': 'Pending', 'win': 0.0, 'oos_win': 0.0, 'med': 0.0, 'dur': 1, 'n': 7985},
    1: {'name': 'Reversal', 'win': 40.4, 'oos_win': 37.6, 'med': 0.0, 'dur': 3, 'n': 21371},
    2: {'name': 'Wedge', 'win': 23.5, 'oos_win': 22.4, 'med': 0.0, 'dur': 2.0, 'n': 6050},
    3: {'name': 'EMA', 'win': 38.1, 'oos_win': 36.6, 'med': 0.0, 'dur': 5, 'n': 28997},
    4: {'name': 'Base', 'win': 42.3, 'oos_win': 42.3, 'med': 0.0, 'dur': 4.0, 'n': 11300},
    5: {'name': 'Exhaustion', 'win': 52.0, 'oos_win': 48.3, 'med': 0.113, 'dur': 5.0, 'n': 20724},
    6: {'name': 'Wedge', 'win': 27.0, 'oos_win': 23.7, 'med': -0.556, 'dur': 6, 'n': 21481},
    7: {'name': 'EMA', 'win': 21.2, 'oos_win': 21.1, 'med': 0.0, 'dur': 2, 'n': 26041},
    8: {'name': 'Base', 'win': 35.7, 'oos_win': 32.9, 'med': 0.0, 'dur': 4, 'n': 26897},
}
STAGE_NAME = {
    0: 'Pending Breakout 待确认突破',
    1: 'Reversal Extension 反转尝试',
    2: 'Wedge Pop 突破',
    3: 'EMA Crossback 回踩',
    4: "Base 'n Break 基部突破",
    5: 'Exhaustion Extension 衰竭延伸',
    6: 'Wedge Drop 破位下坠',
    7: 'EMA Crossback Downside 下行回踩',
    8: "Base 'n Break Downside 下行平台破位",
}


# ══════════════ 指标 ══════════════
def ema(vals, n):
    k = 2 / (n + 1)
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    seed = sum(vals[:n]) / n
    out[n - 1] = seed
    for i in range(n, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def atr(h, l, c, n):
    out = [None] * len(c)
    tr = [None] * len(c)
    for i in range(1, len(c)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    for i in range(n, len(c)):
        if out[i - 1] is None:
            out[i] = sum(tr[i - n + 1:i + 1]) / n
        else:
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def vol_ratio(vol, n):
    out = [None] * len(vol)
    for i in range(n, len(vol)):
        base = sum(vol[i - n:i]) / n
        out[i] = vol[i] / base if base > 0 else None
    return out


def fractal_highs(high, k):
    """前后各 k 日都低 → 该日为摆动高点。返回 bool 列表。"""
    n = len(high)
    out = [False] * n
    for i in range(k, n - k):
        if all(high[i] >= high[i - j] for j in range(1, k + 1)) and \
           all(high[i] >= high[i + j] for j in range(1, k + 1)):
            out[i] = True
    return out


def fractal_lows(low, k):
    n = len(low)
    out = [False] * n
    for i in range(k, n - k):
        if all(low[i] <= low[i - j] for j in range(1, k + 1)) and \
           all(low[i] <= low[i + j] for j in range(1, k + 1)):
            out[i] = True
    return out


# ══════════════ 状态机 ══════════════
def load_bi(con, code):
    """读 index_chanlun_bi，返回 [(end_date, direction, hi, lo)] 按 end_date 升序。
    若表不存在或该指数无笔，返回空列表（状态机回退到无笔模式）。"""
    try:
        rows = con.execute(
            "SELECT end_date, direction, high, low FROM index_chanlun_bi "
            "WHERE stock_code=? ORDER BY end_date", (code,)).fetchall()
    except Exception:
        return []
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def bi_features(bis, dates):
    """把笔结构展开成与 dates 等长的逐日特征。

    返回 dict: dir[i], hi_rising[i], lo_rising[i], last_hi[i], last_lo[i], in_box[i]
    —— 只用「截至当日已完成」的笔，避免未来函数。
    """
    n = len(dates)
    out = {k: [None] * n for k in ('dir', 'hi_rising', 'lo_rising',
                                   'last_hi', 'last_lo', 'in_box')}
    if not bis:
        return out
    bi_i = 0
    tops, bots = [], []          # 已完成笔的顶/底序列
    dirs = []
    for i, d in enumerate(dates):
        while bi_i < len(bis) and bis[bi_i][0] <= d:
            _, dr, hi, lo = bis[bi_i]
            dirs.append(dr)
            if dr == 'up':
                tops.append(hi)
                bots.append(lo)
            else:
                bots.append(lo)
                tops.append(hi)
            bi_i += 1
        if not dirs:
            continue
        out['dir'][i] = dirs[-1]
        out['last_hi'][i] = tops[-1] if tops else None
        out['last_lo'][i] = bots[-1] if bots else None
        if len(dirs) >= 3:
            # 最近两个同类笔的极值是否抬高
            up_tops = [tops[k] for k in range(len(dirs)) if dirs[k] == 'up'][-2:]
            dn_bots = [bots[k] for k in range(len(dirs)) if dirs[k] == 'down'][-2:]
            if len(up_tops) == 2:
                out['hi_rising'][i] = up_tops[-1] > up_tops[0]
            if len(dn_bots) == 2:
                out['lo_rising'][i] = dn_bots[-1] > dn_bots[0]
            # 箱体：最近两笔的高低区间相对前一窗收敛
            if len(tops) >= 4:
                r_now = tops[-1] - bots[-1]
                r_prev = max(tops[-3:]) - min(bots[-3:])
                out['in_box'][i] = r_prev > 0 and r_now / r_prev < 0.6
    return out


# ══════════ 环境层参数（规范 §2）══════════
SPREAD_BOX = 0.015      # 均线收敛度 ≤1.5% → RANGE
SLOPE_TH = 0.005        # EMA50 20 日斜率阈值 ±0.5%
DEV_EXT = 2.0           # 距 EMA20 超过 2 个 ATR → ⑤
BREAK_LOOKBACK = 20     # 突破回看窗口
BREAK_BUF = 0.005       # 突破缓冲 0.5%
BOX_AMP = 0.05          # DOWN 环境内判定窄幅箱体的振幅上限（8% 太松，会让 ⑧ 变成兜底）


def run_state_machine(o, h, l, c, v, bis=None, dates=None):
    """指数 CPA 状态机 —— 两层结构，第二层有界记忆。

    第一层：环境（RANGE / UP / DOWN），每日重算，严格分区。
    第二层：环境内最近事件，环境一变立即重置 —— 因此不存在吸收态。
    """
    n = len(c)
    e10 = ema(c, CFG['ema_fast'])
    e20 = ema(c, CFG['ema_mid'])
    e50 = ema(c, CFG['ema_slow'])
    a20 = atr(h, l, c, CFG['atr_n'])
    a60 = atr(h, l, c, CFG['atr_n_slow'])
    vr = vol_ratio(v, CFG['vr_n'])

    stage = [None] * n
    regime_arr = [None] * n
    trans = []

    cur = 4                 # 数据起点无历史，按规范 §5 归 ④
    regime = None
    last_trend = 'UP'       # RANGE 继承用
    pending = None          # (突破位, 退回阶段, 目标阶段)
    thesis = None           # ②/④ 的突破价位；收盘跌破即论点失效

    for i in range(CFG['warmup'], n):
        if None in (e10[i], e20[i], e50[i], a20[i]):
            continue
        C = c[i]
        vv = vr[i] if vr[i] is not None else 1.0

        # ══ 待确认突破（规范 §6）：只存在一天 ══
        if pending is not None:
            bound, back, target = pending
            pending = None
            if C > bound:
                new = target
                thesis = bound if target in (2, 4) else None
            else:
                new = back
                thesis = None
            if new != cur:
                trans.append((i, cur, new))
            cur = new
            # 确认日也要记录环境，否则 regime_arr 出现 None 空洞
            _mas = (e10[i], e20[i], e50[i])
            _sp = (max(_mas) - min(_mas)) / min(_mas) if min(_mas) > 0 else 9.0
            _sl = (e50[i] / e50[i - 20] - 1) if i >= 20 and e50[i - 20] else 0.0
            regime_arr[i] = ('RANGE' if _sp <= SPREAD_BOX else
                             'UP' if _sl > SLOPE_TH else
                             'DOWN' if _sl < -SLOPE_TH else 'RANGE')
            stage[i] = cur
            continue

        # ══ 第一层：环境（规范 §2）══
        mas = (e10[i], e20[i], e50[i])
        spread = (max(mas) - min(mas)) / min(mas) if min(mas) > 0 else 9.0
        slope50 = (e50[i] / e50[i - 20] - 1) if i >= 20 and e50[i - 20] else 0.0

        if spread <= SPREAD_BOX:
            new_regime = 'RANGE'
        elif slope50 > SLOPE_TH:
            new_regime = 'UP'
        elif slope50 < -SLOPE_TH:
            new_regime = 'DOWN'
        else:
            new_regime = 'RANGE'

        if new_regime != regime:
            # 环境切换 → 记忆重置（这是 I2 无吸收态的保证）
            if regime in ('UP', 'DOWN'):
                last_trend = regime
            regime = new_regime
            if regime == 'UP':
                cur = 3
            elif regime == 'DOWN':
                cur = 7
            else:
                cur = 4 if last_trend == 'UP' else 8
        regime_arr[i] = regime

        # 突破候选（不含当日的历史极值）
        win = c[max(0, i - BREAK_LOOKBACK):i]
        box_top = max(win) if win else C
        box_bot = min(win) if win else C
        dev = (C - e20[i]) / a20[i] if a20[i] else 0.0
        e10_up = e10[i] > e10[max(0, i - CFG['cb_slope_lag'])]

        # ══ 第二层：环境内事件（规范 §3~§5）══
        new = cur
        if regime == 'UP':
            # ②/④ 的论点持有：只要收盘仍在突破位之上，就不因回踩而离开。
            # 只有两个例外：偏离过远转 ⑤，或跌破突破位（论点失效）。
            # 论点持有必须同时满足：未跌破突破位 且 未跌破 EMA20 且 EMA10 未转下行。
            # 只检查突破位是不够的——突破位可能远低于均线，价格跌穿全部均线仍赖在 ②。
            if (cur in (2, 4) and thesis is not None and C > thesis
                    and C > e20[i] and e10_up):
                if dev > DEV_EXT:
                    new = 5
                    thesis = None
                else:
                    stage[i] = cur
                    continue
            if dev > DEV_EXT:
                new = 5
            elif C > box_top * (1 + BREAK_BUF) and vv >= 1.5:
                # 放量破 20 日高点 → 先进待确认；目标由「是否刚进入 UP」决定
                target = 4 if (i > 0 and regime_arr[i - 1] == 'UP') else 2
                pending = (box_top * (1 + BREAK_BUF), cur, target)
                new = 0
            elif l[i] <= e10[i] + 0.3 * a20[i] and C > e10[i]:
                new = 3
        elif regime == 'DOWN':
            if C > e20[i]:
                new = 1
            elif e10[i] < C < e20[i]:
                new = 7
            elif i >= 10 and C < min(c[max(0, i - 10):i]):
                # 创新低：必须与「今天之前的 10 日最低」比较，
                # 若把当日算进去，C < min(含自己) 永假，⑥ 永远触发不了。
                new = 6
            elif box_top > 0 and (box_top - box_bot) / box_top <= BOX_AMP and not e10_up:
                new = 8
        else:                       # RANGE
            if C > box_top * (1 + BREAK_BUF) and vv >= 1.5:
                pending = (box_top * (1 + BREAK_BUF), cur, 2)
                new = 0
            elif C < box_bot * (1 - BREAK_BUF):
                new = 8
            elif C < e20[i] and C < e50[i]:
                # 跌穿 EMA20 与 EMA50：箱体已向下破坏，不能继续挂在上行阶段
                new = 8
            elif C > e20[i]:
                # 站上 EMA20 且不在突破中：箱体上缘的基部
                new = 4
            else:
                new = 8

        if new != cur:
            if new not in (2, 4):
                thesis = None
            trans.append((i, cur, new))
            cur = new
        stage[i] = cur

    return stage, trans, dict(e10=e10, e20=e20, e50=e50, a20=a20, a60=a60,
                              vr=vr, regime=regime_arr)


def load(con, code):
    rows = con.execute("""
        SELECT date, open, high, low, close, volume FROM index_daily_kline
        WHERE stock_code=? AND kline_type='normal' AND close>0
          AND open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
        ORDER BY date
    """, (code,)).fetchall()
    if len(rows) < 400:
        return None
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--code', help='单个指数代码')
    ap.add_argument('--all', action='store_true', help='全部指数')
    ap.add_argument('--save', action='store_true', help='写入 index_cpa_stage')
    ap.add_argument('--show', type=int, default=0, help='打印最近 N 日阶段')
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    if a.code:
        codes = [a.code]
    elif a.all:
        codes = [r[0] for r in con.execute(
            "SELECT DISTINCT stock_code FROM index_daily_kline "
            "WHERE kline_type='normal' GROUP BY stock_code HAVING COUNT(*)>=400")]
    else:
        print('需指定 --code 或 --all')
        return 1

    if a.save:
        con.execute("""
            CREATE TABLE IF NOT EXISTS index_cpa_stage (
                stock_code TEXT NOT NULL, date TEXT NOT NULL,
                stage INTEGER, stage_name TEXT, action TEXT,
                close REAL, ema10 REAL, ema20 REAL, ema50 REAL,
                atr20 REAL, vr REAL, updated_at TEXT,
                PRIMARY KEY (stock_code, date))
        """)
        con.commit()

    now = datetime.now().isoformat(timespec='seconds')
    counts_all = defaultdict(int)
    done = 0
    for code in codes:
        rows = load(con, code)
        if not rows:
            continue
        dates = [r[0] for r in rows]
        o = [r[1] for r in rows]
        h = [r[2] for r in rows]
        l = [r[3] for r in rows]
        c = [r[4] for r in rows]
        v = [r[5] if r[5] else 0.0 for r in rows]
        stage, trans, ind = run_state_machine(o, h, l, c, v)
        valid = [s for s in stage if s is not None]
        if not valid:
            continue
        for s in valid:
            counts_all[s] += 1
        done += 1

        if a.code and not a.save:
            last = valid[-1]
            print(f'  {code}  {dates[-1]}  当前阶段 {last} · {STAGE_NAME[last]}'
                  f'  → {ACTION[last]}')
            print(f'  有效判定 {len(valid)} 日，切换 {len(trans)} 次')
            if a.show:
                for date, s in list(zip(dates, stage))[-a.show:]:
                    if s:
                        print(f'    {date}  阶段{s} {STAGE_NAME[s][:22]}')

        if a.save:
            payload = []
            for i, s in enumerate(stage):
                if s is None:
                    continue
                payload.append((code, dates[i], s, STAGE_NAME[s], ACTION[s],
                                c[i], ind['e10'][i], ind['e20'][i], ind['e50'][i],
                                ind['a20'][i], ind['vr'][i], now))
            con.executemany(
                "INSERT OR REPLACE INTO index_cpa_stage VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?)", payload)

    if a.save:
        con.commit()

    if a.all:
        tot = sum(counts_all.values())
        print(f'  处理 {done} 个指数，{tot:,} 个有效判定日')
        print('  阶段分布：')
        for s in range(1, 9):
            pct = counts_all[s] / tot * 100 if tot else 0
            print(f'    {s} {STAGE_NAME[s]:<32} {counts_all[s]:>9,}  {pct:>5.1f}%')
    con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
