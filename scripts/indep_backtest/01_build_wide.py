# -*- coding: utf-8 -*-
"""独立回测 · 步骤1：构建收益宽表
从 daily_kline 原始数据独立重建每条 B1 信号的多持有期收益 + 因子矩阵。

核心防护：
1. 复权收益用 change_pct 累乘（complex_factor 99.7% NULL 不可用）
2. 入场 = B1 次日 T+1 开盘价（严防前视）
3. T+1 一字涨停剔除
4. B2 隔离标注（事后确认，不作 B1 当天因子）
5. 关注分因子从 K 线独立重算（上轨突破/乖离率/趋势效率），不继承信号表
"""
import sqlite3
import json
import math
import sys
from datetime import datetime

DB = r"D:\hanako\investment-system\data\lixinger.db"
OUT_JSON = r"D:\hanako\investment-system\docs\analysis\mw_indep_wide.json"
COST_ONEWAY = 0.0015  # 单边 0.15% → 双边 0.3%
HOLD_DAYS = [5, 10, 20, 60]


def load_kline(con, code):
    """加载单只股票全部日线，返回按日期升序的 list[dict]。"""
    rows = con.execute(
        "SELECT date, open, high, low, close, change_pct, turnover_rate "
        "FROM daily_kline WHERE stock_code=? ORDER BY date", (code,)
    ).fetchall()
    out = []
    for d, o, h, l, cl, chg, tor in rows:
        # change_pct 是小数制复权日涨跌幅（0.0595 = 涨5.95%）。
        # 脏数据防护：|chg|>0.5（±50%）不合理，归 0 并标记。
        c = chg if chg is not None else 0.0
        bad = abs(c) > 0.5
        if bad:
            c = 0.0
        out.append({"date": d, "open": o, "high": h, "low": l, "close": cl,
                    "chg": c, "tor": tor, "bad": bad})
    # 构造复权净值：nav[0]=1，nav[t]=nav[t-1]*(1+chg)（chg 已是小数）
    nav = 1.0
    for i, bar in enumerate(out):
        if i == 0:
            bar["nav"] = 1.0
        else:
            nav = nav * (1 + bar["chg"])
            bar["nav"] = nav
    return out


def load_index_nav(con, code="000985"):
    # index_daily_kline 的 change 是绝对差不是百分比，直接用 close 序列。
    # 必须过滤 kline_type='normal'（另有 total_return 全收益序列会污染）。
    rows = con.execute(
        "SELECT date, close FROM index_daily_kline "
        "WHERE stock_code=? AND kline_type='normal' ORDER BY date", (code,)
    ).fetchall()
    out = {}
    for d, cl in rows:
        out[d] = cl  # 直接存收盘价，收益 = close_exit/close_entry - 1
    return out


def bollinger_and_factors(bars, idx):
    """在 B1 日(idx)重算关注分外部因子：上轨突破%、乖离率MA20、趋势效率。
    仅用 idx 及之前的数据（防前视）。"""
    if idx < 20:
        return None
    window = bars[idx - 19: idx + 1]  # 含当日共 20 根
    closes = [b["close"] for b in window]
    ma20 = sum(closes) / 20.0
    var = sum((x - ma20) ** 2 for x in closes) / 20.0
    std20 = math.sqrt(var)
    upper = ma20 + 2 * std20
    b1_close = bars[idx]["close"]
    upper_break = (b1_close - upper) / upper * 100 if upper > 0 else None
    bias_ma20 = (b1_close - ma20) / ma20 * 100 if ma20 > 0 else None
    # 趋势效率 = 最近20日净涨跌 / 路径长度
    net = closes[-1] - closes[0]
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    eff = net / path if path > 0 else 0.0
    return {"upper_break": upper_break, "bias_ma20": bias_ma20, "trend_eff": eff}


def compute_returns(bars, date_to_idx, b1_date):
    """T+1 开盘入场，多持有期收益。返回 dict 或 None。"""
    if b1_date not in date_to_idx:
        return {"skip": "b1_no_kline"}
    i_b1 = date_to_idx[b1_date]
    i_entry = i_b1 + 1
    if i_entry >= len(bars):
        return {"skip": "no_next_day"}
    entry_bar = bars[i_entry]
    # 一字涨停剔除：涨幅≪9.9%（chg 是小数）且 开=高=低=收
    if entry_bar["chg"] >= 0.099 and entry_bar["open"] == entry_bar["high"] == entry_bar["low"] == entry_bar["close"]:
        return {"skip": "limit_up_open"}
    if entry_bar["open"] is None or entry_bar["open"] <= 0:
        return {"skip": "bad_open"}
    # 入场复权净值：nav[entry] 是收盘净值，需换成开盘净值
    # 开盘净值 = nav[entry-1] * (open/close[entry-1])
    prev_close = bars[i_entry - 1]["close"]
    if prev_close is None or prev_close <= 0:
        return {"skip": "bad_prev_close"}
    entry_nav = bars[i_entry - 1]["nav"] * (entry_bar["open"] / prev_close)
    if entry_nav <= 0:
        return {"skip": "bad_entry_nav"}
    res = {}
    for h in HOLD_DAYS:
        i_exit = i_entry + h  # 持有 h 交易日后
        truncated = False
        if i_exit >= len(bars):
            i_exit = len(bars) - 1
            truncated = True
        if i_exit <= i_entry:
            res[f"ret_{h}"] = None
            continue
        exit_nav = bars[i_exit]["nav"]  # 出场用收盘净值
        gross = exit_nav / entry_nav - 1.0
        net = gross - 2 * COST_ONEWAY  # 买卖双边
        res[f"ret_{h}"] = round(net * 100, 4)
        res[f"gross_{h}"] = round(gross * 100, 4)
        res[f"exit_date_{h}"] = bars[i_exit]["date"]
        res[f"trunc_{h}"] = truncated
    res["entry_date"] = entry_bar["date"]
    res["i_entry"] = i_entry
    return res


def index_return(index_nav, entry_date, exit_date):
    e = index_nav.get(entry_date)
    x = index_nav.get(exit_date)
    if e and x and e > 0:
        return round((x / e - 1.0) * 100, 4)
    return None


def main():
    con = sqlite3.connect(DB)
    con.execute("PRAGMA cache_size=-200000")
    print("加载基准 000985 净值...")
    index_nav = load_index_nav(con)

    print("拉取全部 B1 信号...")
    sigs = con.execute("""
        SELECT stock_code, b1_date, b2_date, h_date, h_price, l_date, l_price,
               decline_pct, h_pre_rise_pct, h_rs250, ind_rs20, ind_rs250,
               b1_vol_ratio, b1_return_pct, tech_score, score, is_plus
        FROM mw_signal_daily
        WHERE b1_date != '_sentinel_'
        ORDER BY stock_code, b1_date
    """).fetchall()
    print(f"  共 {len(sigs)} 条")

    cols = ['stock_code', 'b1_date', 'b2_date', 'h_date', 'h_price', 'l_date', 'l_price',
            'decline_pct', 'h_pre_rise_pct', 'h_rs250', 'ind_rs20', 'ind_rs250',
            'b1_vol_ratio', 'b1_return_pct', 'tech_score', 'score', 'is_plus']

    # 按股票分组，减少 K 线重复加载
    from collections import defaultdict
    by_code = defaultdict(list)
    for row in sigs:
        d = dict(zip(cols, row))
        by_code[d['stock_code']].append(d)

    records = []
    skip_counter = defaultdict(int)
    n_code = 0
    for code, siglist in by_code.items():
        n_code += 1
        if n_code % 500 == 0:
            print(f"  处理 {n_code}/{len(by_code)} 只股票, 已生成 {len(records)} 条")
        bars = load_kline(con, code)
        if len(bars) < 30:
            skip_counter['too_few_bars'] += len(siglist)
            continue
        date_to_idx = {b["date"]: i for i, b in enumerate(bars)}
        for d in siglist:
            b1_date = d['b1_date']
            rr = compute_returns(bars, date_to_idx, b1_date)
            if rr is None or 'skip' in rr:
                skip_counter[rr['skip'] if rr else 'none'] += 1
                continue
            # 重算因子（B1 日）
            i_b1 = date_to_idx[b1_date]
            fac = bollinger_and_factors(bars, i_b1)
            rec = dict(d)
            rec['entry_date'] = rr['entry_date']
            for h in HOLD_DAYS:
                rec[f'ret_{h}'] = rr.get(f'ret_{h}')
                rec[f'gross_{h}'] = rr.get(f'gross_{h}')
                rec[f'trunc_{h}'] = rr.get(f'trunc_{h}')
                exd = rr.get(f'exit_date_{h}')
                rec[f'idxret_{h}'] = index_return(index_nav, rr['entry_date'], exd) if exd else None
            if fac:
                rec['upper_break'] = round(fac['upper_break'], 4) if fac['upper_break'] is not None else None
                rec['bias_ma20'] = round(fac['bias_ma20'], 4) if fac['bias_ma20'] is not None else None
                rec['trend_eff'] = round(fac['trend_eff'], 4)
            else:
                rec['upper_break'] = rec['bias_ma20'] = rec['trend_eff'] = None
            # 距H天数（自然日近似用交易日索引更好，这里用信号表已有）
            if d['h_date'] and d['h_date'] != '_sentinel_' and d['h_date'] in date_to_idx:
                rec['dist_h'] = i_b1 - date_to_idx[d['h_date']]
            else:
                rec['dist_h'] = None
            records.append(rec)

    print(f"\n完成。生成 {len(records)} 条有效回测记录")
    print("剔除统计:")
    for k, v in sorted(skip_counter.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({"records": records, "skip": dict(skip_counter),
                   "meta": {"cost_oneway": COST_ONEWAY, "hold_days": HOLD_DAYS,
                            "built_at": datetime.now().isoformat(), "n": len(records)}},
                  f, ensure_ascii=False)
    print(f"已写入 {OUT_JSON}")
    con.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    main()
