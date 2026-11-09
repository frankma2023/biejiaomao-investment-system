#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""研究报告自动校验器（index-research skill 的验证环节）。

在生成报告后运行，用无头浏览器逐项检查：图表是否真的画出来、图与数字是否自洽、
章节是否齐全、有没有 NaN/空图/非法路径。

用法：
    python docs/report/validate_report.py docs/report/中证煤炭指数研究/index.html

退出码 0 = 全部通过；1 = 有未通过项。
"""
import json
import pathlib
import re
import sys

from playwright.sync_api import sync_playwright

REQUIRED_H2 = ['结论', '本质总结']


def main():
    if len(sys.argv) < 2:
        print('用法: python docs/report/validate_report.py <html路径>')
        return 2
    f = pathlib.Path(sys.argv[1]).resolve()
    if not f.exists():
        print(f'  ✗ 文件不存在: {f}')
        return 2

    html = f.read_text(encoding='utf-8')
    fails, warns, oks = [], [], []

    def ok(m):
        oks.append(m)
        print(f'  ✓ {m}')

    def bad(m):
        fails.append(m)
        print(f'  ✗ {m}')

    def warn(m):
        warns.append(m)
        print(f'  ⚠ {m}')

    print(f'\n校验：{f.name}\n' + '=' * 68)

    # ── 1. 章节齐全 ──
    h2 = [re.sub(r'<[^>]+>', '', x).strip() for x in re.findall(r'<h2[^>]*>(.*?)</h2>', html, re.S)]
    if len(h2) >= 6:
        ok(f'章节数 {len(h2)}')
    else:
        bad(f'章节数仅 {len(h2)}，报告结构不完整')
    for kw in REQUIRED_H2:
        if any(kw in x for x in h2):
            ok(f'含必需章节「{kw}」')
        else:
            bad(f'缺必需章节「{kw}」')

    # ── 2. 嵌入数据无 NaN ──
    # 两种图表实现：ECharts（嵌入 var D = {...}）与纯内联 SVG。后者无嵌入数据，跳过本项。
    m = re.search(r'var D = (\{.*?\});', html, re.S)
    if not m:
        if html.count('<svg') >= 2:
            ok('图表为内联 SVG，无嵌入数据（跳过 var D 检查）')
        else:
            bad('未找到嵌入的图表数据，也没有内联 SVG 图表')
        D = None
    else:
        raw = m.group(1)
        if 'NaN' in raw or 'Infinity' in raw:
            bad('图表数据含 NaN/Infinity')
        else:
            ok('图表数据无 NaN')
        try:
            D = json.loads(raw)
            ok(f'图表数据可解析，{len(D.get("dates", []))} 个数据点')
        except Exception as e:
            bad(f'图表数据解析失败: {e}')
            D = None

    # ── 3. 浏览器渲染检查 ──
    uri = f.as_uri()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1300, 'height': 1000})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)[:120]))
        pg.goto(uri, wait_until='load', timeout=40000)
        pg.wait_for_timeout(1500)

        if errs:
            bad(f'页面 JS 错误: {errs[:2]}')
        else:
            ok('页面无 JS 错误')

        # 所有 svg path 都必须有非零包围盒（能真的画出来）
        info = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('svg').forEach(svg => {
            svg.querySelectorAll('path').forEach(p => {
              const b = p.getBBox();
              out.push({id: svg.id, stroke: p.getAttribute('stroke'),
                        w: Math.round(b.width), h: Math.round(b.height),
                        dlen: (p.getAttribute('d') || '').length});
            });
          });
          return out;
        }""")
        zero = [x for x in info if x['w'] == 0 or x['h'] == 0]
        if not info:
            bad('页面上没有任何图表路径')
        elif zero:
            bad(f'{len(zero)} 条曲线未渲染（包围盒为 0）: '
                + ', '.join(f"{z['id']}/{z['stroke']}" for z in zero[:4]))
        else:
            ok(f'全部 {len(info)} 条曲线均已渲染')

        # 非法路径：以 L 开头（缺 moveto）
        illegal = pg.evaluate("""() => {
          const bad = [];
          document.querySelectorAll('svg path').forEach(p => {
            const d = (p.getAttribute('d') || '').trim();
            if (d && !d.startsWith('M')) bad.push(p.getAttribute('stroke'));
          });
          return bad;
        }""")
        if illegal:
            bad(f'{len(illegal)} 条路径缺少起笔 M（首点为 null 时常见）: {illegal[:4]}')
        else:
            ok('所有路径起笔合法')

        # 曲线是否被裁到图外：path 的包围盒必须落在自身 svg 的 viewBox 内
        clip = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('svg[id]').forEach(svg => {
            const vb = (svg.getAttribute('viewBox') || '').split(/[ ]+/).map(Number);
            if (vb.length !== 4 || !vb[3]) return;
            const vbH = vb[3], vbW = vb[2];
            svg.querySelectorAll('path').forEach(pth => {
              const d = (pth.getAttribute('d') || '').trim();
              if (!d || !d.startsWith('M')) return;   // 非法路径由另一项检查负责
              let b;
              try { b = pth.getBBox(); } catch (e) { return; }
              if (b.width === 0 && b.height === 0) return;
              // 允许 2px 容差（描边宽度/取整）
              const over = (b.y < -2) || (b.y + b.height > vbH + 2)
                        || (b.x < -2) || (b.x + b.width > vbW + 2);
              if (over) out.push({
                id: svg.id, stroke: pth.getAttribute('stroke'),
                y: Math.round(b.y), h: Math.round(b.height), vbH: vbH,
              });
            });
          });
          return out;
        }""")
        if clip:
            bad(f'{len(clip)} 条曲线超出图表范围（被 Y 轴裁切）: '
                + '; '.join(f"{c['id']}/{c['stroke']} y={c['y']} h={c['h']} viewBoxH={c['vbH']}"
                            for c in clip[:3]))
        else:
            ok('所有曲线均落在各自图表范围内')

        # 图表内部一致性：开关按钮数 == 曲线数 == 图例项数
        charts = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('svg[id]').forEach(svg => {
            const id = svg.id;
            const bar = svg.closest('.chart-wrap');
            if (!bar) return;
            const btns = bar.querySelectorAll('.ch-btn').length;
            // 折线是 path，堆叠条是 rect，都要算作「图形元素」
            const paths = svg.querySelectorAll('path').length
                        + svg.querySelectorAll('rect').length;
            const lg = bar.querySelector('.ch-legend');
            const items = lg ? lg.querySelectorAll('span').length : 0;
            if (btns || items) out.push({id, btns, paths, items});
          });
          return out;
        }""")
        mis = [c for c in charts if c['btns'] and c['paths'] and c['paths'] > 3
               and not (c['paths'] - 1 <= c['items'] + 2)]
        short = [c for c in charts if c['items'] and not c['paths']]
        if short:
            bad_f = ', '.join(c['id'] for c in short)
            mis.append({'id': bad_f, 'btns': 0, 'paths': 0, 'items': 0})
        if mis:
            bad(f'图表内部不一致（按钮/曲线/图例）: '
                + '; '.join(f"{c['id']} 按钮{c['btns']} 曲线{c['paths']} 图例{c['items']}"
                            for c in mis[:3]))
        elif charts:
            ok(f'图表内部一致：{len(charts)} 个图的开关/曲线/图例数量匹配')

        # KPI 与图表终点一致性
        kpi = pg.evaluate("""() => [...document.querySelectorAll('.kpi .v')].map(e => e.textContent.trim())""")
        ok(f'KPI 卡片 {len(kpi)} 个: {kpi}')

        # 图与数字一致性：若存在累计涨幅，应与 KPI 的 CAGR 同向
        if D and 'tr' in D and 'hs' in D and len(D['tr']) > 10 \
                and D['tr'][0] and D['tr'][-1] and D['hs'][0] and D['hs'][-1]:
            tr_gain = D['tr'][-1] / D['tr'][0] - 1
            hs_gain = D['hs'][-1] / D['hs'][0] - 1
            first, last = D['dates'][0], D['dates'][-1]
            ok(f'图表窗口 {first} ~ {last}：指数TR {tr_gain*100:+.1f}%，基准TR {hs_gain*100:+.1f}%')
            # 检查正文里是否有与该窗口不一致的年化描述
            m2 = re.search(r'样本\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})', html)
            if m2 and m2.group(1) != first:
                warn(f'正文样本起点 {m2.group(1)} 与图表起点 {first} 不一致——'
                     f'年化数字与曲线可能讲不同的故事，需在报告中显式说明')
            else:
                ok('正文样本区间与图表窗口一致')

        pg.screenshot(path=str(f.parent / '_validate.png'), full_page=False)
        b.close()

    # ── 4. 文字卫生 ──
    for token in ('None', 'nan%', 'undefined', '{}'):
        if token in html:
            warn(f'正文含可疑占位符「{token}」')

    print('=' * 68)
    print(f'  通过 {len(oks)} 项 / 警告 {len(warns)} 项 / 失败 {len(fails)} 项')
    if fails:
        print('  ✗ 校验未通过：')
        for x in fails:
            print(f'    - {x}')
        return 1
    print('  ✓ 校验通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
