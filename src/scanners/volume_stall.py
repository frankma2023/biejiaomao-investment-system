"""
放量滞涨检测引擎 v1.0（Volume Stall）

检测双日放量滞涨形态：第1日放量上涨 + 第2日放量下跌，疑似主力出货/顶部反转。

回测依据（web/analysis/volume_reversal_report.html）：
  全A股 2023-08 ~ 2026-07，基准档(+2%x2.0x) 35,893 个事件
  90日中位收益 -1.19%，胜率 48%，最大回撤中位 33.8%

信号等级：
  🔴 strong   — 第1日涨≥4% 或 量比≥3.0x
  🟡 moderate — 第1日涨≥3% 或 量比≥2.5x
  ⚠️ weak    — 达到基准（涨≥2% 且 量比≥2.0x）

用法:
  python -m src.scanners.volume_stall --stock 601012 --date 2026-03-23
  python -m src.scanners.volume_stall --stock 601012 --range 2025-08-01 2026-08-01
"""

import sys, os, argparse, sqlite3, yaml
from datetime import datetime
from typing import Optional, Dict, List

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")

ENGINE_META = {
    "name": "volume_stall",
    "display_name": "放量滞涨检测",
    "category": "sell_signal",
    "version": "1.0",
    "description": "检测双日放量滞涨：放量上涨后次日放量下跌，疑似出货",
}


# ══════════════════════════════════════════════════════════
def load_params() -> Dict:
    cfg_path = os.path.join(PROJECT_DIR, "config", "market", "volume_stall.yaml")
    defaults = {
        'd1_gain_threshold': 0.02,   # 第1日涨幅阈值
        'd1_vol_ratio_min': 2.0,     # 第1日量比阈值
        'd2_vol_ratio_min': 1.8,     # 第2日量比阈值
        'vol_ma_days': 20,           # 均额窗口
        'min_history': 30,           # 最少K线数
    }
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        # 安全解包：兼容顶层裸键和 volume_stall 嵌套键两种写法
        cfg = cfg.get('volume_stall', cfg) if isinstance(cfg, dict) else {}
        for k, v in cfg.items():
            defaults[k] = v
    return defaults


# ══════════════════════════════════════════════════════════
def _ma_prev(amounts: List[float], idx: int, days: int) -> float:
    """前N日均额（不含当日）"""
    start = max(0, idx - days)
    vals = amounts[start:idx]
    return sum(vals) / max(len(vals), 1) if vals else 0


def _signal_level(d1_chg: float, d1_ratio: float) -> str:
    """基于第1日涨幅与量比分级"""
    if d1_chg >= 0.04 or d1_ratio >= 3.0:
        return 'strong'
    if d1_chg >= 0.03 or d1_ratio >= 2.5:
        return 'moderate'
    return 'weak'


def _detect_at(amounts: List[float], daily: List[Dict], idx: int, p: Dict, stock_code: str) -> Optional[Dict]:
    """检测 idx-1, idx 两日是否构成放量滞涨（idx 为第2日）
    传入预计算的 amounts 列表，避免每次重建（detect_range 性能关键）"""
    if idx < 1 or idx >= len(daily):
        return None
    d1 = daily[idx - 1]
    d2 = daily[idx]

    # 第1日条件：涨 + 放量
    d1_chg = d1.get('change_pct')
    d1_amount = d1.get('amount') or 0
    if d1_chg is None or d1_chg < p['d1_gain_threshold'] or d1_amount <= 0:
        return None
    ma_prev1 = _ma_prev(amounts, idx - 1, p['vol_ma_days'])
    if ma_prev1 <= 0:
        return None
    d1_ratio = d1_amount / ma_prev1
    if d1_ratio < p['d1_vol_ratio_min']:
        return None

    # 第2日条件：跌 + 放量
    d2_chg = d2.get('change_pct')
    d2_amount = d2.get('amount') or 0
    if d2_chg is None or d2_chg >= 0 or d2_amount <= 0:
        return None
    ma_prev2 = _ma_prev(amounts, idx, p['vol_ma_days'])
    if ma_prev2 <= 0:
        return None
    d2_ratio = d2_amount / ma_prev2
    if d2_ratio < p['d2_vol_ratio_min']:
        return None

    level = _signal_level(d1_chg, d1_ratio)
    return {
        'type': 'bearish',
        'signal_date': d2['date'],
        'stock_code': stock_code,
        'signal_type': 'volume_stall',
        'signal_level': level,
        'label': ('🔴 ' if level == 'strong' else '🟡 ' if level == 'moderate' else '⚠️ ') + '放量滞涨',
        'details': {
            'd1_date': d1['date'],
            'd1_change_pct': round(d1_chg * 100, 2),
            'd1_vol_ratio': round(d1_ratio, 2),
            'd1_amount': round(d1_amount / 1e8, 2),
            'd2_change_pct': round(d2_chg * 100, 2),
            'd2_vol_ratio': round(d2_ratio, 2),
            'd2_amount': round(d2_amount / 1e8, 2),
        },
    }


# ══════════════════════════════════════════════════════════
def detect(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> List[Dict]:
    """检测整个 K 线范围内的全部放量滞涨信号（pattern-scan 约定：返回全历史）。
    与 pocket_pivot 等引擎行为一致——前端按请求日期范围过滤展示。"""
    if params is None:
        params = load_params()
    if len(daily) < params['min_history']:
        return []
    amounts = [r.get('amount') or 0 for r in daily]
    all_signals = []
    last_of_level = {}
    for i in range(1, len(daily)):
        sig = _detect_at(amounts, daily, i, params, stock_code)
        if sig:
            lv = sig['signal_level']
            prev = last_of_level.get(lv)
            if prev:
                d1 = datetime.strptime(prev, '%Y-%m-%d')
                d2 = datetime.strptime(sig['signal_date'], '%Y-%m-%d')
                if (d2 - d1).days < 5:
                    continue
            last_of_level[lv] = sig['signal_date']
            all_signals.append(sig)
    return sorted(all_signals, key=lambda x: x['signal_date'])


def detect_range(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> List[Dict]:
    """逐日扫描整个序列，返回所有信号（同等级5日去重）。与 detect 等价，保留兼容。"""
    return detect(daily, params, stock_code)


def detect_all(
    daily: List[Dict],
    params: Optional[Dict] = None,
    stock_code: str = '',
) -> Dict:
    """综合检测，返回 daily + 全部信号。"""
    if params is None:
        params = load_params()
    signals = detect(daily, params, stock_code)
    return {
        'daily': daily,
        'signals': signals,
        'stock_code': stock_code,
    }


# ══════════════════════════════════════════════════════════
def _load_kline(stock_code: str, start: str, end: str) -> List[Dict]:
    """从数据库加载个股日K线（CLI 调试用，含 amount/change_pct 字段）。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT date, open, close, high, low, volume, amount, change_pct
        FROM daily_kline WHERE stock_code=? AND date>=? AND date<=?
        ORDER BY date
    """, (stock_code, start, end)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='放量滞涨检测')
    parser.add_argument('--stock', type=str, default='601012')
    parser.add_argument('--date', type=str, default='', help='单日检测（该日为第2日）')
    parser.add_argument('--range', type=str, nargs=2, metavar=('START', 'END'), help='范围扫描')
    args = parser.parse_args()

    if args.range:
        daily = _load_kline(args.stock, args.range[0], args.range[1])
        sigs = detect_range(daily, None, args.stock)
        print(f"🔍 {args.stock} {args.range[0]}~{args.range[1]} 放量滞涨信号: {len(sigs)}")
        for s in sigs:
            d = s['details']
            print(f"  {s['signal_date']} [{s['signal_level']}] 第1日{d['d1_date']} "
                  f"+{d['d1_change_pct']}% 量比{d['d1_vol_ratio']} | 第2日 {d['d2_change_pct']}% 量比{d['d2_vol_ratio']}")
    else:
        end = args.date or datetime.now().strftime('%Y-%m-%d')
        start = (datetime.strptime(end, '%Y-%m-%d').replace(year=datetime.strptime(end, '%Y-%m-%d').year - 1)).strftime('%Y-%m-%d')
        daily = _load_kline(args.stock, start, end)
        sigs = detect(daily, None, args.stock)
        # 单日模式：只看指定日期当天的信号
        sigs = [s for s in sigs if s['signal_date'] == end]
        print(f"🔍 {args.stock} @ {end} 放量滞涨信号: {len(sigs)}")
        for s in sigs:
            d = s['details']
            print(f"  {s['signal_date']} [{s['signal_level']}] 第1日{d['d1_date']} "
                  f"+{d['d1_change_pct']}% 量比{d['d1_vol_ratio']} | 第2日 {d['d2_change_pct']}% 量比{d['d2_vol_ratio']}")
