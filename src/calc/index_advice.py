# 纳入「操作建议」口径的指数。三处共用：列表接口、详情接口、日更脚本。
# 000922（中证红利）也纳入，用于同口径横向对照，不对外当作「自由现金流」展示。
FCF_INDICES = [
    ('980092', '国证自由现金流'),
    ('932365', '中证现金流'),
    ('000922', '中证红利'),
]

# 页面上「自由现金流」卡片只展示这两个（000922 仅日更留档）
FCF_INDICES_PUBLIC = [x for x in FCF_INDICES if x[0] != '000922']

# -*- coding: utf-8 -*-
"""指数操作建议的纯计算逻辑：RSI、锚定回撤、分档打分。

**单一实现**——src/server.py 的 API 与 scripts/compute_index_advice_daily.py
的日更任务都从这里 import，避免两份实现分叉。

口径来源：《国证自由现金流指数研究报告 v2.0》第 7 章、§12.4 与问答附录 Q4。
"""

import json
import sqlite3

# ── PRD v2.0：自由现金流操作建议（13.68 年样本，基准=一直持有）──
# 依据《国证自由现金流指数研究报告 v2.0》与 17 问问答附录。
# 本模块不做预测，只把「当前回撤」映射到「历史上该档的表现」。
FCF_BASELINE_ANNUAL = 16.30      # 一直持有（2012-12-31 买入不动）
FCF_MEDIAN_ANNUAL = 16.43        # 全样本 3,277 个入场日的年化中位数
                                 # 分档表是「入场日年化中位数」，应与这个比，
                                 # 而不是与单点买入的 CAGR 16.30% 比
FCF_SAMPLE_DAYS = 3338           # 2012-12-31 ~ 2026-09-29

# (下限, 上限, 年化%, 相对基准pp, 独立段数, 档位)
FCF_DD_BINS = [
    (0, 5, 14.63, -1.80, 212, 'high'),
    (5, 10, 15.34, -1.09, 19, 'high'),
    (10, 15, 17.42, 0.99, 9, 'neutral'),
    (15, 20, 17.48, 1.05, 5, 'neutral'),
    (20, 30, 18.57, 2.14, 4, 'low'),
    (30, 40, 16.50, 0.07, 1, 'low'),
    (40, 50, 17.48, 1.05, 1, 'low'),
    (50, 101, 18.22, 1.79, 1, 'low'),
]
FCF_LABEL = {'low': '偏低区', 'neutral': '中性区', 'high': '偏高区', 'reduce': '减仓提示'}

# RSI60 ≥65 清仓口径的历史结果（用于「减仓提示」的四元信息）
FCF_RSI_SELL = {'annual': 14.40, 'mdd': 29.7, 'base_mdd': 50.2}


def fcf_advice_from_db(db, code, target_date):
    """从 index_advice_daily 读最近一条打分结果；读不到返回 None。

    只取「打分」部分——等级/买入分/风险分/因子明细。
    四元信息（样本数、该档中位、独立段）由调用方按当前回撤档补，
    因为它们只跟回撤档位有关，不随日更表变化。
    """
    try:
        r = db.execute(
            "SELECT date, dd_tr, rsi20, buy_score, risk_score, advice_level, "
            "advice_label, score_detail FROM index_advice_daily "
            "WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (code, target_date)).fetchone()
    except sqlite3.OperationalError:
        return None          # 表还没建（日更尚未跑过）
    if not r:
        return None
    try:
        detail = json.loads(r['score_detail']) if r['score_detail'] else []
    except (ValueError, TypeError):
        detail = []
    return {
        'level': r['advice_level'],
        'label': r['advice_label'],
        'buy_score': r['buy_score'],
        'risk_score': r['risk_score'],
        'score_detail': detail,
        'source': 'index_advice_daily',
        'advice_date': r['date'],
        'stored_dd_tr': r['dd_tr'],
        'stored_rsi20': r['rsi20'],
    }


def fcf_advice_v2(dd, rsi60, rsi20=None):
    """持仓建议打分模型（PRD v2.2）。

    买入分 = 锚定回撤 + RSI20 超卖 + 估值分位（求和）
    风险分 = max(RSI20 超买, 估值双高, 回撤过浅)

    风险分取 max 而非求和：三个因子指向同一件事（价格不便宜），
    求和会重复计分并把「略贵」放大成「极贵」。

    分值来源（研究报告 §7 / §12.4，13.68 年样本）：
      回撤分档年化：0~5 14.63 / 5~10 15.34 / 10~15 17.42 / 15~20 17.48 /
                   20~30 18.57 / 30~40 16.50 / 40~50 17.48 / 50+ 18.22
      RSI20 <30：60 日 +9.71%（胜率100%）、120 日 +17.27%；≥78：60 日 −4.23%
    """
    detail = []
    buy = 0
    risk = 0

    # ── 买入分 · 锚定回撤 ──
    if dd >= 30:
        v, note = 40, '20~30 档实测最优（+2.14pp），≥30 仅 1 个独立轮次，故略低于该档'
    elif dd >= 20:
        v, note = 45, '该档年化 18.57%，跑赢全样本中位 2.14pp（4 个独立轮次）'
    elif dd >= 15:
        v, note = 30, '该档年化 17.48%，+1.05pp（5 段）'
    elif dd >= 10:
        v, note = 25, '该档年化 17.42%，+0.99pp（9 段）'
    elif dd >= 5:
        v, note = 10, '该档年化 15.34%，跑输 −1.09pp（19 段）'
    else:
        v, note = 0, '该档年化 14.63%，跑输 −1.80pp（212 段，样本最厚）'
    buy += v
    detail.append({'factor': '锚定回撤', 'desc': f'{dd:.1f}%', 'value': v, 'note': note})

    # ── 买入分 · RSI20 超卖 ──
    if rsi20 is None:
        v, note = 0, '数据不可用'
    elif rsi20 < 20:
        v, note = 30, 'RSI20 <20：历史上 60/120 日前瞻均为正（样本少，谨慎）'
    elif rsi20 < 30:
        v, note = 22, 'RSI20 <30：60 日 +5.29pp、120 日 +7.91pp（n=36）'
    elif rsi20 < 40:
        v, note = 8, 'RSI20 30~40：弱买点区，无显著超额'
    else:
        v, note = 0, 'RSI20 ≥40：无超卖信号'
    buy += v
    detail.append({'factor': 'RSI20 超卖', 'desc': f'{rsi20:.1f}' if rsi20 is not None else '—',
                   'value': v, 'note': note})

    # ── 买入分 · 估值（样本仅 501 天，权重压低） ──
    detail.append({'factor': '估值分位', 'desc': '仅 2 年数据',
                   'value': 0,
                   'note': '估值序列仅 501 个交易日，不足以支撑打分，固定 0 分'})

    # ── 风险分 · RSI20 超买 ──
    if rsi20 is None:
        r_v, r_note = 0, '数据不可用'
    elif rsi20 >= 85:
        r_v, r_note = 75, 'RSI20 ≥85：历史极值区，60 日胜率仅 30%'
    elif rsi20 >= 78:
        r_v, r_note = 70, 'RSI20 ≥78：60 日 −8.65pp、120 日 −17.57pp（n=30）'
    elif rsi20 >= 70:
        r_v, r_note = 35, 'RSI20 70~78：偏热但未到实测卖点阈值'
    else:
        r_v, r_note = 0, 'RSI20 <70：无超买信号'
    risk = max(risk, r_v)
    detail.append({'factor': 'RSI20 超买', 'desc': f'{rsi20:.1f}' if rsi20 is not None else '—',
                   'value': r_v, 'risk': True, 'note': r_note})

    # ── 风险分 · 回撤过浅 ──
    if dd < 3:
        s_v, s_note = 30, '回撤 <3%：接近历史高点，该档历史年化最差（−1.80pp）'
    elif dd < 5:
        s_v, s_note = 15, '回撤 <5%：浅回撤区，历史跑输基准'
    else:
        s_v, s_note = 0, '回撤已超过 5%，不构成「位置过高」风险'
    risk = max(risk, s_v)
    detail.append({'factor': '位置过高', 'desc': f'回撤 {dd:.1f}%', 'value': s_v,
                   'risk': True, 'note': s_note})

    # ── 合成 ──
    if risk >= 70 and buy < 20:
        level = 'reduce'
        reasons = [f'风险分 {risk}（RSI20 超买），买入分仅 {buy}']
    elif buy >= 60 and risk < 70:
        level = 'low'
        reasons = [f'买入分 {buy}（回撤与 RSI 共振），风险分 {risk}']
    elif buy >= 30 and risk < 70:
        level = 'neutral'
        reasons = [f'买入分 {buy}，风险分 {risk}']
    else:
        level = 'high'
        reasons = [f'买入分仅 {buy}，风险分 {risk}；当前位置不在历史优势区间']

    # 附上回撤档位说明（保留原有信息）
    for lo, hi, ann, exc, seg, _lv in FCF_DD_BINS:
        if lo <= dd < hi:
            reasons.append(f'锚定回撤 {dd:.1f}% 落在 {lo}~{hi}% 档，历史年化 {ann:.2f}%'
                           f'（{exc:+.2f}pp，{seg} 个独立段）')
            if seg <= 4:
                reasons.append(f'该档历史仅 {seg} 个独立下跌轮次，样本很薄')
            break

    return {
        'level': level, 'label': FCF_LABEL[level],
        'buy_score': buy, 'risk_score': risk,
        'score_detail': detail,
        'reasons': reasons,
        'meta': {
            'sample_days': FCF_SAMPLE_DAYS,
            'independent_segments': next((seg for lo, hi, _a, _e, seg, _l in FCF_DD_BINS
                                          if lo <= dd < hi), None),
            'median_annual': next((ann for lo, hi, ann, _e, _s, _l in FCF_DD_BINS
                                   if lo <= dd < hi), None),
            'baseline_annual': FCF_BASELINE_ANNUAL,
            'median_annual_all': FCF_MEDIAN_ANNUAL,
            'excess_pp': next((exc for lo, hi, _a, exc, _s, _l in FCF_DD_BINS
                               if lo <= dd < hi), None),
        },
    }


def fcf_pct(sorted_vals, q):
    """从已排序列表中取分位数（线性插值）。"""
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


# 各周期卖点阈值（RSI 高于等于该值 → 记一个卖点）。
# 数值来自实测段数扫描，目标是每个周期一年出 1~3 个卖点。
FCF_SELL_THR = {6: 80.0, 14: 80.0, 20: 78.0}


def fcf_rsi_multi(dates, closes, periods=(6, 14, 20)):
    """多周期 RSI + 研究口径的绝对买卖阈值 + 段内极值标记。

    阈值取研究实测的绝对水平（不是分位数）：
      买：RSI < 30 为超卖（档 2），RSI < 20 为极值（档 1）
      卖：RSI >= 65 为超买（档 2），RSI >= 70 为极值（档 1）
    与问答附录 Q4 中「RSI60 从未跌破 30」的结论同一口径，
    因此 RSI60 不纳入——它标不出任何买点。
    """
    out = []
    for per in periods:
        vals = fcf_rsi(closes, per)
        valid = [v for v in vals if v is not None]
        if len(valid) < 200:
            continue
        # 买：用绝对值 30——这是教科书与本研究共用的语义线，
        # 且各周期 <30 的天数（344/81/36）本身就是研究结论。
        BUY, BUY_STRONG = 30.0, 20.0
        # 卖：按周期配置。实测各阈值下的段数（2012-12-31~2026-10-16）：
        #   RSI6  >=80 → 96 段
        #   RSI14 >=80 → 23 段（与买 <30 的 24 段天然对称）
        #   RSI20 >=78 → 11 段
        SELL = FCF_SELL_THR.get(per, 80.0)
        SELL_STRONG = SELL + 5.0

        # 买点：跌破 30 算一段，取段内最低点
        buys, i, n = [], 0, len(vals)
        while i < n:
            if vals[i] is not None and vals[i] < BUY:
                j = i
                while j < n and vals[j] is not None and vals[j] < BUY:
                    j += 1
                seg = [(k, vals[k]) for k in range(i, j) if vals[k] is not None]
                if seg:
                    k, v = min(seg, key=lambda x: x[1])
                    deep = v < BUY_STRONG
                    buys.append({'date': dates[k], 'rsi': round(v, 1),
                                 'tier': 1 if deep else 2,
                                 'tier_label': f'{v:.0f}' + ('★' if deep else '')})
                i = j
            else:
                i += 1

        # 卖点：升破 65 算一段，取段内最高点
        sells, i = [], 0
        while i < n:
            if vals[i] is not None and vals[i] >= SELL:
                j = i
                while j < n and vals[j] is not None and vals[j] >= SELL:
                    j += 1
                seg = [(k, vals[k]) for k in range(i, j) if vals[k] is not None]
                if seg:
                    k, v = max(seg, key=lambda x: x[1])
                    deep = v >= SELL_STRONG
                    sells.append({'date': dates[k], 'rsi': round(v, 1),
                                  'tier': 1 if deep else 2,
                                  'tier_label': f'{v:.0f}' + ('★' if deep else '')})
                i = j
            else:
                i += 1

        out.append({
            'period': per,
            'series': [round(v, 1) if v is not None else None for v in vals],
            'buy_threshold': BUY, 'buy_strong': BUY_STRONG,
            'sell_threshold': SELL, 'sell_strong': SELL_STRONG,
            'min': round(min(valid), 1), 'max': round(max(valid), 1),
            'buy_marks': buys, 'sell_marks': sells,
            'buy_days': sum(1 for v in valid if v < BUY),
            'sell_days': sum(1 for v in valid if v >= SELL),
        })
    return out


def fcf_anchored_dd(closes):
    """锚定回撤序列：运行最高点只在被突破时更新。"""
    out, hi = [], -1.0
    for c in closes:
        if c > hi:
            hi = c
        out.append((hi - c) / hi * 100 if hi > 0 else 0.0)
    return out


def fcf_rsi(closes, period=60):
    """Wilder RSI，返回与 closes 等长的列表（前 period 个为 None）。"""
    n = len(closes)
    out = [None] * n
    if n <= period:
        return out
    g = l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / period, l / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


FCF_DATA_GAPS = [
    '估值分位仅 2024-09 起 499 个交易日，不参与信号生成，仅供展示',
    '回撤 30% 以上三档合计仅 1 个独立下跌轮次（2015-06 ~ 2017-05）',
    '2012~2024 点位为编制方按现行规则回溯计算，非真实交易记录',
]




# ── 兼容别名：server.py 中的历史调用点仍用下划线名 ──
_fcf_advice_from_db = fcf_advice_from_db
_fcf_advice_v2 = fcf_advice_v2
_fcf_pct = fcf_pct
_fcf_rsi_multi = fcf_rsi_multi
_fcf_anchored_dd = fcf_anchored_dd
_fcf_rsi = fcf_rsi
