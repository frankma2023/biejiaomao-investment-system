# -*- coding: utf-8 -*-
"""
CPA 回测 3：缠论笔顶序列质量体检

分三块：
  A. 全市场结构校验（最新快照）——交替/衔接/单调/长度/价格/新鲜度/截断
  B. 笔顶价与 K 线 high 一致性（抽样 800 只，需读 K 线）
  C. 【口径专项】笔顶收敛用的是未复权价（chanlun.py 读 daily_kline 原始 OHLC）
     → 跨越除权日的相邻笔顶，raw 递降 与 adj 递降 可能符号相反（假收敛）
     统计：除权穿越率 + 结论翻转率

输出：analysis/cpa_bi_audit.md + cpa_bi_audit.json
"""
import sqlite3, os, sys, json, random, statistics
from collections import Counter

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
OUT_MD = os.path.join(PROJECT, 'analysis', 'cpa_bi_audit.md')
OUT_JSON = os.path.join(PROJECT, 'analysis', 'cpa_bi_audit.json')

FOCUS = ['002648', '600309', '000338', '601012', '300750', '600875']
SAMPLE_N = 800
SEED = 42


def load_latest_bi(conn):
    """每只股票最新快照的 bi_json"""
    rows = conn.execute("""
        SELECT b.stock_code, b.scan_date, b.bi_json
        FROM chanlun_bi_json b
        JOIN (SELECT stock_code, MAX(scan_date) md FROM chanlun_bi_json GROUP BY stock_code) m
          ON b.stock_code = m.stock_code AND b.scan_date = m.md
    """).fetchall()
    out = {}
    for code, sd, js in rows:
        try:
            out[code] = (sd, json.loads(js))
        except Exception:
            out[code] = (sd, None)
    return out


def parse_bi(bi):
    """归一成 list[dict]：date/direction/high/low/len"""
    out = []
    for b in bi or []:
        out.append({
            'sdt': (b.get('sdt') or '')[:10],
            'edt': (b.get('edt') or '')[:10],
            'dir': b.get('direction'),
            'high': b.get('high'),
            'low': b.get('low'),
            'len': b.get('length') or 0,
        })
    return out


def check_structure(bis):
    """返回 dict(counts) + 各类违规样例"""
    r = dict(n=len(bis), alt=0, chain=0, mono=0, len_short=0, price_bad=0,
             zero_px=0, top_edt_bad=0, bot_edt_bad=0)
    bad = []
    for i, b in enumerate(bis):
        if b['high'] is None or b['low'] is None:
            r['zero_px'] += 1
            continue
        if b['high'] <= 0 or b['low'] <= 0:
            r['zero_px'] += 1
        if b['high'] < b['low']:
            r['price_bad'] += 1
        if b['sdt'] > b['edt']:
            r['mono'] += 1
        if b['len'] < 5:
            r['len_short'] += 1
        # 向上笔：高点应在 edt 端（czsc 语义），向下笔：低点应在 edt 端
        if i + 1 < len(bis) and b['edt'] != bis[i + 1]['sdt']:
            r['chain'] += 1
            if len(bad) < 5:
                bad.append(('chain', b['sdt'], b['edt'], bis[i + 1]['sdt']))
        if i + 1 < len(bis) and b['dir'] == bis[i + 1]['dir']:
            r['alt'] += 1
    return r, bad


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    print('[1] 加载最新快照 ...', flush=True)
    latest = load_latest_bi(conn)
    print('    股票数:', len(latest), flush=True)

    # ---------- A. 全市场结构校验 ----------
    print('[2] A 结构校验 ...', flush=True)
    agg = Counter()
    n_stocks = 0
    n_stocks_any_err = 0
    len_counter = Counter()
    nbi_counter = Counter()
    err_stocks = []
    coverage = []
    for code, (sd, bi) in latest.items():
        bis = parse_bi(bi)
        if not bis:
            agg['empty'] += 1
            continue
        n_stocks += 1
        nbi_counter[min(len(bis), 50)] += 1
        r, bad = check_structure(bis)
        for k in ('alt', 'chain', 'mono', 'len_short', 'price_bad', 'zero_px'):
            agg[k] += r[k]
        agg['n_bi'] += r['n']
        for b in bis:
            len_counter[b['len']] += 1
        coverage.append((code, bis[0]['sdt'], bis[-1]['edt']))
        if any(r[k] for k in ('alt', 'chain', 'mono', 'len_short', 'price_bad', 'zero_px')):
            n_stocks_any_err += 1
            if len(err_stocks) < 15:
                err_stocks.append((code, r, bad))

    # 新鲜度：最后一笔 edt 距最新交易日
    last_scan = max(v[0] for v in latest.values())
    stale = sum(1 for c, s, e in coverage if e < last_scan[:10])
    print('    结构校验完成', flush=True)

    # ---------- B. 笔顶价 vs K线 high（抽样） ----------
    print('[3] B 笔顶价一致性（抽样 %d 只）...' % SAMPLE_N, flush=True)
    codes = sorted(latest.keys())
    random.seed(SEED)
    samp = random.sample(codes, min(SAMPLE_N, len(codes)))
    # 指定票强制入样
    for f in FOCUS:
        if f in latest and f not in samp:
            samp.append(f)
    b_stat = Counter()
    b_examples = []
    for code in samp:
        row = conn.execute("SELECT bi_json FROM chanlun_bi_json WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1",
                           (code,)).fetchone()
        bis = parse_bi(json.loads(row[0])) if row and row[0] else []
        if not bis:
            continue
        dts = [b['sdt'] for b in bis]
        # 一次性取 K 线
        krows = conn.execute(
            "SELECT date, high, low, close, adj_close FROM daily_kline WHERE stock_code=? AND date>=? ORDER BY date",
            (code, dts[0])).fetchall()
        kmap = {r['date']: r for r in krows}
        for b in bis:
            kr = kmap.get(b['sdt'])
            if kr is None:
                b_stat['no_kline'] += 1
                continue
            b_stat['checked'] += 1
            if b['dir'] == '向下':
                # 笔顶：K 线 high 应等于笔 high
                if kr['high'] is not None and abs(kr['high'] - b['high']) > 0.011:
                    b_stat['top_mismatch'] += 1
                    if len(b_examples) < 10:
                        b_examples.append((code, b['sdt'], b['high'], kr['high']))
            else:
                if kr['low'] is not None and abs(kr['low'] - b['low']) > 0.011:
                    b_stat['bot_mismatch'] += 1
            # 复权因子
            if kr['close'] and kr['adj_close'] and kr['close'] > 0:
                fac = kr['adj_close'] / kr['close']
                if abs(fac - 1.0) > 1e-6:
                    b_stat['adj_altered'] += 1
    print('    一致性检查完成', flush=True)

    # ---------- C. 除权穿越 / 口径翻转 ----------
    print('[4] C 除权穿越与口径翻转 ...', flush=True)
    c_stat = Counter()
    c_examples = []
    for code in samp:
        row = conn.execute("SELECT bi_json FROM chanlun_bi_json WHERE stock_code=? ORDER BY scan_date DESC LIMIT 1",
                           (code,)).fetchone()
        bis = parse_bi(json.loads(row[0])) if row and row[0] else []
        if len(bis) < 2:
            continue
        dts = [b['sdt'] for b in bis]
        krows = conn.execute(
            "SELECT date, close, adj_close FROM daily_kline WHERE stock_code=? AND date>=? ORDER BY date",
            (code, dts[0])).fetchall()
        if not krows:
            continue
        facmap = {}
        for r in krows:
            if r['close'] and r['adj_close'] and r['close'] > 0:
                facmap[r['date']] = r['adj_close'] / r['close']
        dates = list(facmap.keys())
        # 除权日 = fac 相对前日变化 > 0.5%
        ex_dates = []
        for j in range(1, len(dates)):
            f0, f1 = facmap[dates[j - 1]], facmap[dates[j]]
            if f0 > 0 and abs(f1 / f0 - 1) > 0.005:
                ex_dates.append(dates[j])
        tops = [b for b in bis if b['dir'] == '向下' and b['sdt'] in facmap]
        if len(tops) < 2:
            continue
        c_stat['stocks_with_tops'] += 1
        if ex_dates:
            c_stat['stocks_with_ex'] += 1
        for j in range(len(tops) - 1):
            t1, t2 = tops[j], tops[j + 1]
            if t1['sdt'] > t2['sdt']:
                continue
            c_stat['pairs'] += 1
            crossed = [d for d in ex_dates if t1['sdt'] < d <= t2['sdt']]
            if not crossed:
                continue
            c_stat['pairs_crossing_ex'] += 1
            f1, f2 = facmap.get(t1['sdt']), facmap.get(t2['sdt'])
            if not (f1 and f2):
                continue
            raw_conv = (t2['high'] <= t1['high'] * 1.01)
            a1, a2 = t1['high'] * f1, t2['high'] * f2
            adj_conv = (a2 <= a1 * 1.01)
            if raw_conv != adj_conv:
                c_stat['pairs_flip'] += 1
                if raw_conv and not adj_conv:
                    c_stat['flip_false_conv'] += 1   # raw 假收敛（真收敛被误报）
                if len(c_examples) < 15:
                    c_examples.append((code, t1['sdt'], round(t1['high'], 2), t2['sdt'], round(t2['high'], 2),
                                       round(a1, 2), round(a2, 2), 'raw收敛' if raw_conv else 'raw不收敛',
                                       'adj收敛' if adj_conv else 'adj不收敛'))
    print('    口径专项完成', flush=True)

    # ---------- 输出 ----------
    n_bi = agg['n_bi'] or 1
    lines = []
    lines.append('# CPA 回测 3 · 缠论笔顶序列质量体检\n')
    lines.append('数据：`chanlun_bi_json` 最新快照（%s），%d 只股票\n' % (last_scan, len(latest)))
    lines.append('算法：CZSC 1.0.1（Rust），`max_bi_num=50` 截断\n')

    lines.append('## A. 全市场结构校验\n')
    lines.append('| 项 | 数量 | 占比 |')
    lines.append('|---|---|---|')
    lines.append('| 笔总数 | %d | - |' % agg['n_bi'])
    lines.append('| 方向不交替 | %d | %.4f%% |' % (agg['alt'], agg['alt'] / n_bi * 100))
    lines.append('| 首尾不衔接 | %d | %.4f%% |' % (agg['chain'], agg['chain'] / n_bi * 100))
    lines.append('| sdt>edt | %d | %.4f%% |' % (agg['mono'], agg['mono'] / n_bi * 100))
    lines.append('| length<5 | %d | %.4f%% |' % (agg['len_short'], agg['len_short'] / n_bi * 100))
    lines.append('| high<low | %d | %.4f%% |' % (agg['price_bad'], agg['price_bad'] / n_bi * 100))
    lines.append('| 价格为零/空 | %d | %.4f%% |' % (agg['zero_px'], agg['zero_px'] / n_bi * 100))
    lines.append('| 有 bi_json 但解析为空 | %d 只 | - |' % agg['empty'])
    lines.append('')
    lines.append('有任意结构问题的股票：**%d / %d**\n' % (n_stocks_any_err, n_stocks))
    lines.append('笔数分布（封顶 50）：%s\n' % dict(sorted(nbi_counter.items())))
    lines.append('length 分布（前 15）：%s\n' % dict(sorted(len_counter.items())[:15]))
    lines.append('最后一笔 edt 落后于最新快照日的股票：**%d / %d**\n' % (stale, n_stocks))
    if err_stocks:
        lines.append('样例：\n')
        for code, r, bad in err_stocks:
            lines.append('- `%s` %s %s' % (code, {k: v for k, v in r.items() if k != 'n' and v}, bad))
        lines.append('')

    lines.append('## B. 笔顶价与 K 线 high 一致性（抽样 %d，含指定 6 只）\n' % len(samp))
    lines.append('| 项 | 数量 |')
    lines.append('|---|---|')
    lines.append('| 可核对笔数 | %d |' % b_stat['checked'])
    lines.append('| K 线缺失 | %d |' % b_stat['no_kline'])
    lines.append('| 笔顶价 != 当日 high（>0.01） | %d |' % b_stat['top_mismatch'])
    lines.append('| 笔底价 != 当日 low（>0.01） | %d |' % b_stat['bot_mismatch'])
    lines.append('| 笔端点日 adj_close != close（有复权因子） | %d |' % b_stat['adj_altered'])
    lines.append('')
    if b_examples:
        lines.append('笔顶不匹配样例（code, 日期, 笔high, K线high）：\n')
        for e in b_examples:
            lines.append('- `%s` %s 笔 %.2f vs K线 %.2f' % e)
        lines.append('')

    lines.append('## C. 【口径专项】除权穿越导致笔顶收敛失真\n')
    lines.append('背景：`chanlun.py` 从 `daily_kline` 读**未复权** OHLC 画笔（`SELECT date, open, high, low, close ...`），')
    lines.append('而 CPA 阶段判据用 `adj_close/high_adj/low_adj`（复权）。')
    lines.append('`bi_conv` / ⑥`hl` 拿**未复权**笔顶两两比价（容差 1%），一旦两笔顶之间夹着除权日，')
    lines.append('未复权价会因除权而下跳 → 产生**假"高点不抬高"**。\n')
    lines.append('| 项 | 数量 |')
    lines.append('|---|---|')
    lines.append('| 有笔顶序列的股票 | %d |' % c_stat['stocks_with_tops'])
    lines.append('| 其中存在除权日 | %d |' % c_stat['stocks_with_ex'])
    lines.append('| 相邻笔顶对总数 | %d |' % c_stat['pairs'])
    lines.append('| 跨越除权日的笔顶对 | %d |' % c_stat['pairs_crossing_ex'])
    lines.append('| 其中 raw/adj 结论翻转 | %d |' % c_stat['pairs_flip'])
    lines.append('| **假收敛（raw 收敛、adj 不收敛）** | **%d** |' % c_stat['flip_false_conv'])
    lines.append('')
    if c_stat['pairs_crossing_ex']:
        lines.append('翻转率（跨越除权对中）：**%.2f%%**｜假收敛占跨越除权对：**%.2f%%**\n' % (
            c_stat['pairs_flip'] / c_stat['pairs_crossing_ex'] * 100,
            c_stat['flip_false_conv'] / c_stat['pairs_crossing_ex'] * 100))
    if c_examples:
        lines.append('翻转样例：\n')
        lines.append('| code | 笔顶1 | 价 | 笔顶2 | 价 | adj1 | adj2 | raw | adj |')
        lines.append('|---|---|---|---|---|---|---|---|---|')
        for e in c_examples:
            lines.append('| %s | %s | %.2f | %s | %.2f | %.2f | %.2f | %s | %s |' % e)
        lines.append('')

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    with open(OUT_JSON, 'w', encoding='utf-8') as f:
        json.dump({'A': dict(agg), 'A_stocks': {'total': n_stocks, 'any_err': n_stocks_any_err, 'stale': stale},
                   'B': dict(b_stat), 'C': dict(c_stat),
                   'b_examples': b_examples, 'c_examples': c_examples,
                   'err_stocks': [[c, r] for c, r, _ in err_stocks]},
                  f, ensure_ascii=False, indent=1)
    print('[5] 报告 ->', OUT_MD, flush=True)
    # 控制台摘要
    print('\n===== 摘要 =====')
    print('A 结构：笔 %d｜不交替 %d｜不衔接 %d｜len<5 %d｜有错股票 %d/%d｜滞后 %d' % (
        agg['n_bi'], agg['alt'], agg['chain'], agg['len_short'], n_stocks_any_err, n_stocks, stale))
    print('B 笔顶一致性：核对 %d｜笔顶不符 %d｜笔底不符 %d' % (
        b_stat['checked'], b_stat['top_mismatch'], b_stat['bot_mismatch']))
    print('C 口径：除权股 %d/%d｜跨越除权对 %d/%d｜翻转 %d｜假收敛 %d' % (
        c_stat['stocks_with_ex'], c_stat['stocks_with_tops'],
        c_stat['pairs_crossing_ex'], c_stat['pairs'], c_stat['pairs_flip'], c_stat['flip_false_conv']))
    conn.close()


if __name__ == '__main__':
    main()
