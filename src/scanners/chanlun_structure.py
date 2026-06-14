"""
缠论结构共享层 — 为所有引擎提供统一的峰谷识别接口

基于 chanlun_scan_daily 表的 bi_json 字段（每日更新），对外暴露：
  - get_bi_list()        获取完整笔列表
  - get_bi_peaks()       提取笔顶（峰）
  - get_bi_troughs()     提取笔底（谷）
  - get_recent_bi()      最近 N 笔
  - get_bi_force()       笔力度/斜率/角度

数据格式（bi_json 中每条笔）：
  {sdt, edt, direction, high, low, power, slope, angle, length}

峰谷约定：
  笔顶（峰）= direction="向上" → edt 为峰日期，high 为峰价格
  笔底（谷）= direction="向下" → edt 为谷日期，low 为谷价格
"""
import json
import sqlite3
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_bi_list(stock_code, db=None, max_age_days=5):
    """
    从 chanlun_scan_daily 获取股票的最新笔列表。

    Args:
        stock_code: 股票代码
        db: 数据库连接（可选）
        max_age_days: 数据最大允许天数（超过则返回空，提示数据过期）

    Returns:
        list[dict]: 笔列表，按时间排序；无数据时返回 []
    """
    own_db = db is None
    if own_db:
        db = _get_db()

    try:
        row = db.execute("""
            SELECT bj.bi_json, bj.scan_date FROM chanlun_bi_json bj
            WHERE bj.stock_code = ?
            ORDER BY bj.scan_date DESC LIMIT 1
        """, (stock_code,)).fetchone()

        if not row:
            return []

        # 检查数据是否过期
        scan_date = row['scan_date'][:10] if row['scan_date'] else ''
        if scan_date:
            age = (datetime.now() - datetime.strptime(scan_date, '%Y-%m-%d')).days
            if age > max_age_days:
                print(f"[chanlun_structure] {stock_code} bi数据过期 ({age}天前)")
                # 仍然返回（可能比没有好）

        bi_list = json.loads(row['bi_json']) if row['bi_json'] else []
        return bi_list

    except sqlite3.OperationalError:
        return []
    finally:
        if own_db:
            db.close()


def get_bi_peaks(bi_list, count=None):
    """
    提取笔顶（峰）。

    笔顶 = direction="向上" 的笔
    峰日期 = edt（向上笔的终点）
    峰价格 = high（向上笔的最高价）

    Args:
        bi_list: 笔列表
        count: 限制返回最近 N 个峰（None 返回全部）

    Returns:
        list[dict]: [{date, price, bi_index, ...}]
    """
    peaks = []
    for i, bi in enumerate(bi_list):
        if bi.get('direction') == '向上':
            peaks.append({
                'date': _clean_date(bi.get('edt', '')),
                'price': bi.get('high', 0),
                'low': bi.get('low', 0),
                'bi_index': i,
                'power': bi.get('power', 0),
                'slope': bi.get('slope', 0),
                'angle': bi.get('angle', 0),
                'length': bi.get('length', 0),
                'raw': bi,
            })
    if count and len(peaks) > count:
        return peaks[-count:]
    return peaks


def get_bi_troughs(bi_list, count=None):
    """
    提取笔底（谷）。

    笔底 = direction="向下" 的笔
    谷日期 = edt（向下笔的终点）
    谷价格 = low（向下笔的最低价）

    Args:
        bi_list: 笔列表
        count: 限制返回最近 N 个谷

    Returns:
        list[dict]: [{date, price, bi_index, ...}]
    """
    troughs = []
    for i, bi in enumerate(bi_list):
        if bi.get('direction') == '向下':
            troughs.append({
                'date': _clean_date(bi.get('edt', '')),
                'price': bi.get('low', 0),
                'high': bi.get('high', 0),
                'bi_index': i,
                'power': bi.get('power', 0),
                'slope': bi.get('slope', 0),
                'angle': bi.get('angle', 0),
                'length': bi.get('length', 0),
                'raw': bi,
            })
    if count and len(troughs) > count:
        return troughs[-count:]
    return troughs


def get_recent_bi(bi_list, count=10):
    """获取最近 N 笔（按列表顺序，最后的是最新的）"""
    if len(bi_list) <= count:
        return bi_list
    return bi_list[-count:]


def get_bi_by_date_range(bi_list, start_date, end_date):
    """按日期范围过滤笔"""
    result = []
    for bi in bi_list:
        edt = _clean_date(bi.get('edt', ''))
        if start_date <= edt <= end_date:
            result.append(bi)
    return result


def get_bi_at_date(bi_list, target_date):
    """获取覆盖指定日期的笔"""
    for bi in bi_list:
        sdt = _clean_date(bi.get('sdt', ''))
        edt = _clean_date(bi.get('edt', ''))
        if sdt <= target_date <= edt:
            return bi
    return None


def get_last_up_bi_force(bi_list, count=3):
    """
    获取最近 N 根向上笔的力度统计。

    用于高潮见顶：判断最后一段上涨是否加速。

    Returns:
        list[dict]: [{power, slope, angle, pct_chg, ...}]
    """
    up_bis = [bi for bi in bi_list if bi.get('direction') == '向上']
    recent = up_bis[-count:] if len(up_bis) >= count else up_bis

    result = []
    for bi in recent:
        low = bi.get('low', 0)
        high = bi.get('high', 0)
        pct = (high - low) / low * 100 if low > 0 else 0
        result.append({
            'date': _clean_date(bi.get('edt', '')),
            'power': bi.get('power', 0),
            'slope': bi.get('slope', 0),
            'angle': bi.get('angle', 0),
            'length': bi.get('length', 0),
            'pct_chg': round(pct, 2),
            'high': high,
            'low': low,
        })
    return result


def _clean_date(dt_str):
    """清理日期字符串 '2024-04-17 00:00:00' → '2024-04-17'"""
    return dt_str[:10] if dt_str else ''


# ═══════════════════════════════════════════════
# 便捷：一次性提取完整结构
# ═══════════════════════════════════════════════

def get_structure(stock_code, db=None):
    """
    一次性获取股票的完整缠论结构。

    Returns:
        {
            'bi_list': [...],
            'peaks': [...],
            'troughs': [...],
            'recent_bi': [...],
            'last_up_force': [...],
            'bi_count': int,
        }
    """
    bi_list = get_bi_list(stock_code, db)
    if not bi_list:
        return None

    return {
        'bi_list': bi_list,
        'peaks': get_bi_peaks(bi_list),
        'troughs': get_bi_troughs(bi_list),
        'recent_bi': get_recent_bi(bi_list, 10),
        'last_up_force': get_last_up_bi_force(bi_list, 5),
        'bi_count': len(bi_list),
    }


# ═══════════════════════════════════════════════
# CLI 测试
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else '600519'
    s = get_structure(code)
    if s:
        print(f"股票: {code}")
        print(f"笔总数: {s['bi_count']}")
        print(f"峰数量: {len(s['peaks'])}")
        print(f"谷数量: {len(s['troughs'])}")
        print(f"\n最近 5 个峰:")
        for p in s['peaks'][-5:]:
            print(f"  {p['date']} ¥{p['price']:.2f} 力度={p['power']:.2f}")
        print(f"\n最近 5 个谷:")
        for t in s['troughs'][-5:]:
            print(f"  {t['date']} ¥{t['price']:.2f} 力度={t['power']:.2f}")
        print(f"\n最近向上笔力度:")
        for f in s['last_up_force'][-3:]:
            print(f"  {f['date']} 涨幅={f['pct_chg']:.2f}% 力度={f['power']:.2f} 斜率={f['slope']:.4f}")
    else:
        print(f"{code}: 无缠论数据")
