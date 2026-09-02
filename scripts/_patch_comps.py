# -*- coding: utf-8 -*-
"""financial.py 修改：comps 修复 + 新增 quality/cashflow"""
p = r'D:\hanako\investment-system\src\analysis\financial.py'
src = open(p, encoding='utf-8').read()

# ── 修改 B1：申万分支 peer 去重（JOIN equity/kline 产生一对多重复）──
old_sw = """        peers = db.execute('''SELECT DISTINCT ic.stock_code, sb.name,
            (SELECT weighting FROM index_constituent_weightings icw"""
# 上面的其实是 L2 分支。找到申万分支（无 DISTINCT 的 SELECT ... FROM stock_sw_industry sw）
old_sw2 = """            LEFT JOIN stock_equity_change eq ON sw.stock_code=eq.stock_code
            LEFT JOIN daily_kline k ON sw.stock_code=k.stock_code
                AND k.date = (SELECT MAX(date) FROM daily_kline WHERE stock_code=sw.stock_code)
            WHERE sw.industry_name = ? AND sw.stock_code != ?
            ORDER BY eq.capitalization * k.close DESC LIMIT 20''',"""
new_sw2 = """            LEFT JOIN stock_equity_change eq ON sw.stock_code=eq.stock_code
            LEFT JOIN daily_kline k ON sw.stock_code=k.stock_code
                AND k.date = (SELECT MAX(date) FROM daily_kline WHERE stock_code=sw.stock_code)
            WHERE sw.industry_name = ? AND sw.stock_code != ?
            GROUP BY sw.stock_code
            ORDER BY eq.capitalization * k.close DESC LIMIT 20''',"""
if old_sw2 in src:
    src = src.replace(old_sw2, new_sw2)
    print('comps 申万分支去重 ✓')
else:
    # 检查申万分支的实际 SELECT 开头（前面是 L2 的），定位 SELECT 语句
    i = src.find('WHERE sw.industry_name = ? AND sw.stock_code != ?')
    # 向上找 SELECT
    j = src.rfind('SELECT', 0, i)
    print('⚠️ 申万 SELECT 段：', src[j:j+80].replace('\n', ' '))

# L2 分支 DISTINCT 已存在（SELECT DISTINCT ic.stock_code）——但 JOIN 后也可能重复（weight 子查询）——加 GROUP BY 保险
old_l2 = """            WHERE ic.index_code = ? AND ic.date >= date('now', '-3 months')
            AND ic.stock_code != ?
            ORDER BY weight DESC NULLS LAST LIMIT 20''',"""
new_l2 = """            WHERE ic.index_code = ? AND ic.date >= date('now', '-3 months')
            AND ic.stock_code != ?
            GROUP BY ic.stock_code
            ORDER BY weight DESC NULLS LAST LIMIT 20''',"""
if old_l2 in src:
    src = src.replace(old_l2, new_l2)
    print('comps L2 分支去重 ✓')

# ── 修改 B2：PB 目标净资产用真实 total_equity（替代 营收×30% 假设）──
old_pb = """    # 目标公司数据
    target = peers_table[0] if peers_table else {}
    target_revenue = target.get('revenue', 0)
    target_equity = target_revenue * 0.3  # 简化：净资产≈营收×30%"""
new_pb = """    # 目标公司数据（净资产取真实值，替代营收×30% 假设）
    target = peers_table[0] if peers_table else {}
    target_revenue = target.get('revenue', 0)
    ext_t = db2_exec_equity(db, stock_code) if 'db2_exec_equity' in dir() else None
    target_equity = None"""
if old_pb in src:
    src = src.replace(old_pb, new_pb)
    print('PB 目标占位已写入（需再处理 total_equity 查询）')

open(p, 'w', encoding='utf-8').write(src)
import ast
try:
    ast.parse(src)
    print('语法 OK')
except SyntaxError as e:
    print('语法错误（占位未完成）:', e)
