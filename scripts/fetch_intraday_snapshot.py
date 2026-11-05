#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中实时快照（akshare 东财源），供 market-logic 技能第 1 层使用。

用法：python scripts/fetch_intraday_snapshot.py
产出：.scratch/intraday_<YYYYMMDD_HHMM>.json + 控制台摘要

口径：涨跌幅单位为 %（东财原始口径）；成交额单位为元；家数含北交所。
"""
import json
import os
import warnings
from datetime import datetime

import akshare as ak

warnings.filterwarnings('ignore')

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT, '.scratch')

BROAD = ['000001', '399001', '399006', '000688', '000985', '000300',
         '000905', '000852', '000922', '000510', '399975', '399967', '000941']
KEY = {'600309': '万华化学', '002648': '卫星化学', '000703': '恒逸石化',
       '600346': '恒力石化', '601857': '中国石油', '600028': '中国石化',
       '601012': '隆基绿能', '300274': '阳光电源', '600438': '通威股份',
       '002129': 'TCL中环'}


def main():
    now = datetime.now()
    stamp = now.strftime('%Y%m%d_%H%M')

    spot = ak.stock_zh_a_spot_em()
    chg = [float(x) for x in spot['涨跌幅'] if x == x]
    up = sum(1 for x in chg if x > 0)
    dn = sum(1 for x in chg if x < 0)
    flat = len(chg) - up - dn
    amt = float(spot['成交额'].fillna(0).sum()) / 1e8
    avg = sum(chg) / len(chg) if chg else 0

    print(f'=== 盘中快照 {now:%Y-%m-%d %H:%M}（涨跌幅单位 %）===')
    print(f'全市场 {len(chg)} 只：涨 {up} / 跌 {dn} / 平 {flat}，'
          f'平均 {avg:+.2f}%，成交额 {amt:,.0f} 亿')

    # 指数：合并四个系列
    idx = {}
    for sym in ('沪深重要指数', '上证系列指数', '深证系列指数', '中证系列指数'):
        try:
            df = ak.stock_zh_index_spot_em(symbol=sym)
            for _, r in df.iterrows():
                code = str(r['代码'])
                if code not in idx:
                    idx[code] = {'name': str(r['名称']),
                                 'chg': float(r['涨跌幅']),
                                 'close': float(r['最新价']),
                                 'amt': float(r.get('成交额') or 0)}
        except Exception as e:
            print(f'  [WARN] {sym} 失败: {str(e)[:60]}')

    print()
    print('--- 宽基 ---')
    for c in BROAD:
        d = idx.get(c)
        if d:
            print(f'  {c} {d["name"][:12]:<14}{d["chg"]:>7.2f}%')

    ranked = [(c, d) for c, d in idx.items() if d['amt'] > 0]
    ranked.sort(key=lambda x: -x[1]['chg'])
    print()
    print('--- 指数涨幅榜前 15（按成交额>0 过滤）---')
    for c, d in ranked[:15]:
        print(f'  {c} {d["name"][:20]:<22}{d["chg"]:>7.2f}%  额 {d["amt"] / 1e8:.0f}亿')
    print()
    print('--- 指数跌幅榜前 8 ---')
    for c, d in ranked[-8:]:
        print(f'  {c} {d["name"][:20]:<22}{d["chg"]:>7.2f}%  额 {d["amt"] / 1e8:.0f}亿')

    print()
    print('--- 关键板块 ---')
    for kw in ('化工', '光伏', '黄金', '半导体', '芯片', '算力', '军工', '煤炭',
               '银行', '白酒', '消费', '医药', '证券'):
        hits = [(c, d) for c, d in ranked if kw in d['name']]
        hits.sort(key=lambda x: -x[1]['amt'])
        if hits:
            c, d = hits[0]
            print(f'  {kw:<6}{d["name"][:18]:<20}{c:<9}{d["chg"]:>7.2f}%')

    print()
    print('--- 关键个股 ---')
    sp = {str(r['代码']): r for _, r in spot.iterrows()}
    for c, nm in KEY.items():
        r = sp.get(c)
        if r is not None and r['涨跌幅'] == r['涨跌幅']:
            print(f'  {c} {nm:<10}{float(r["涨跌幅"]):>7.2f}%  额 {float(r["成交额"]) / 1e8:.1f}亿')

    print()
    print('--- 个股涨幅榜前 10 / 跌幅榜前 5（挡板外的普通股）---')
    s2 = spot.dropna(subset=['涨跌幅']).copy()
    s2['涨跌幅'] = s2['涨跌幅'].astype(float)
    s2 = s2.sort_values('涨跌幅', ascending=False)
    for _, r in s2.head(10).iterrows():
        print(f'  {r["代码"]} {str(r["名称"])[:10]:<12}{r["涨跌幅"]:>7.2f}%')
    for _, r in s2.tail(5).iterrows():
        print(f'  {r["代码"]} {str(r["名称"])[:10]:<12}{r["涨跌幅"]:>7.2f}%')

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f'intraday_{stamp}.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'asof': now.strftime('%Y-%m-%d %H:%M'),
                   'breadth': {'n': len(chg), 'up': up, 'down': dn,
                               'flat': flat, 'avg': avg, 'amount_yi': amt},
                   'indices': idx}, f, ensure_ascii=False)
    print(f'\n已写出 {out}')


if __name__ == '__main__':
    main()
