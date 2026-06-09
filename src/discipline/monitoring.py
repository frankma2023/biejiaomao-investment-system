"""
知行系统 V2 — 持仓监控引擎

扫描所有持仓标的，检查止损触发、大盘环境、个股走弱、见顶信号，
生成分级告警并写入 discipline_alerts 表。

使用方式：
    python src/discipline/monitoring.py                  # 扫描所有持仓
    python src/discipline/monitoring.py --stock 300750    # 扫描指定标的
"""
import os
import sys
import sqlite3
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')

# 告警级别
LEVEL_RED = 'red'      # 止损触发 / 大盘危险
LEVEL_YELLOW = 'yellow'  # 个股走弱 / 见顶信号
LEVEL_GREEN = 'green'   # 正常

# 告警类型优先级（数字越小越严重）
ALERT_PRIORITY = {
    'stop_loss': 0,
    'market_risk': 1,
    'stock_weakness': 2,
    'top_signal': 3,
    'volume_alert': 4,
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_latest_close(db, code, asset_type='stock'):
    """获取最新收盘价：按类型查对应K线表"""
    if asset_type == 'index':
        row = db.execute("SELECT close FROM index_daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        if row: return row['close']
        row = db.execute("SELECT close FROM daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    else:
        row = db.execute("SELECT close FROM daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
        if row: return row['close']
        row = db.execute("SELECT close FROM index_daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    return row['close'] if row else None


def run_scan(db, target_stock=None):
    """执行持仓监控扫描，返回告警列表"""
    alerts = []

    # 1. 获取所有持仓
    if target_stock:
        trades = db.execute("""
            SELECT * FROM discipline_trades
            WHERE sell_date IS NULL AND stock_code = ?
        """, (target_stock,)).fetchall()
    else:
        trades = db.execute("""
            SELECT * FROM discipline_trades
            WHERE sell_date IS NULL
        """).fetchall()

    if not trades:
        return alerts

    # 2. 获取大盘环境
    market = db.execute("""
        SELECT * FROM market_direction_daily
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    market_data = dict(market) if market else {}

    # 3. 获取当天日期
    latest_sig_date = db.execute(
        "SELECT MAX(date) FROM pattern_scan_signals"
    ).fetchone()
    scan_date = latest_sig_date[0] if latest_sig_date else datetime.now().strftime('%Y-%m-%d')

    # 4. 逐只扫描
    for trade in trades:
        stock_code = trade['stock_code']
        stock_name = trade['stock_name'] or stock_code
        trade_id = trade['id']
        buy_price = trade['buy_price']
        stop_loss_price = trade['stop_loss_price']
        asset_type = trade['asset_type'] if 'asset_type' in trade.keys() else 'stock'

        # 4a. 获取最新收盘价
        close = _get_latest_close(db, stock_code, asset_type)
        if close is None:
            continue

        current_price = close
        pnl_pct = round((current_price - buy_price) / buy_price * 100, 2)

        # 4b. 止损检查（最优先）
        if current_price <= stop_loss_price:
            alerts.append({
                'trade_id': trade_id,
                'stock_code': stock_code,
                'alert_date': scan_date,
                'alert_level': LEVEL_RED,
                'alert_type': 'stop_loss',
                'alert_message': f'{stock_name}({stock_code}) 触发止损！当前价 ¥{current_price:.2f} ≤ 止损价 ¥{stop_loss_price:.2f}，浮盈 {pnl_pct:+.1f}%',
            })

        # 4c. 大盘风险检查
        risk_level = market_data.get('risk_level', '')
        if risk_level and risk_level in ('high', '危险'):
            alerts.append({
                'trade_id': trade_id,
                'stock_code': stock_code,
                'alert_date': scan_date,
                'alert_level': LEVEL_RED,
                'alert_type': 'market_risk',
                'alert_message': f'大盘风险级别={risk_level}，建议仓位={market_data.get("suggested_position_size", "?")}%。{stock_name} 当前浮盈 {pnl_pct:+.1f}%',
            })

        # 4d. 个股走弱检查（近5日连续下跌或跌破50日线）
        recent_klines = db.execute("""
            SELECT close, date FROM daily_kline
            WHERE stock_code = ? ORDER BY date DESC LIMIT 50
        """, (stock_code,)).fetchall()

        if len(recent_klines) >= 50:
            closes = [r['close'] for r in recent_klines]
            # 50日均线
            ma50 = sum(closes[:50]) / 50 if len(closes) >= 50 else None
            # 是否连续5日下跌
            last5 = closes[:5]
            down_streak = all(last5[i] < last5[i+1] for i in range(len(last5)-1)) if len(last5) >= 2 else False

            if ma50 and current_price < ma50:
                alerts.append({
                    'trade_id': trade_id,
                    'stock_code': stock_code,
                    'alert_date': scan_date,
                    'alert_level': LEVEL_YELLOW,
                    'alert_type': 'stock_weakness',
                    'alert_message': f'{stock_name} 跌破50日均线 (MA50=¥{ma50:.2f})，当前价 ¥{current_price:.2f}',
                })

            if down_streak:
                alerts.append({
                    'trade_id': trade_id,
                    'stock_code': stock_code,
                    'alert_date': scan_date,
                    'alert_level': LEVEL_YELLOW,
                    'alert_type': 'stock_weakness',
                    'alert_message': f'{stock_name} 连续5日下跌，当前价 ¥{current_price:.2f}',
                })

        # 4e. 见顶信号检查（从 pattern_scan_signals 查顶部形态）
        sig_row = db.execute("""
            SELECT signals_json FROM pattern_scan_signals
            WHERE stock_code = ? AND date = ?
        """, (stock_code, scan_date)).fetchone()

        if sig_row and sig_row['signals_json']:
            try:
                sigs = json.loads(sig_row['signals_json'])
                top_patterns = [s for s in sigs if s.get('type') in ('bearish', 'distribution', 'top')
                                or s.get('pattern') in ('double_top', 'head_shoulders_top')]
                if top_patterns:
                    names = ', '.join(s.get('type') or s.get('pattern') or '顶部信号' for s in top_patterns[:3])
                    alerts.append({
                        'trade_id': trade_id,
                        'stock_code': stock_code,
                        'alert_date': scan_date,
                        'alert_level': LEVEL_YELLOW,
                        'alert_type': 'top_signal',
                        'alert_message': f'{stock_name} 检测到顶部信号: {names}',
                    })
            except (json.JSONDecodeError, TypeError):
                pass

        # 4f. 量价异常检查
        if len(recent_klines) >= 5:
            last_vols = [db.execute("""
                SELECT volume FROM daily_kline WHERE stock_code = ? ORDER BY date DESC LIMIT 25
            """, (stock_code,)).fetchall()]
            # 简化：如果最近放量下跌
            if len(recent_klines) >= 3:
                cur_close = recent_klines[0]['close']
                prev_close = recent_klines[2]['close']
                if cur_close < prev_close:
                    # 简单量比检查 - 跳过避免过多查询
                    pass

        # 5. 写入告警
        for alert in alerts:
            # 避免重复：检查当日同类型告警是否已存在
            existing = db.execute("""
                SELECT id FROM discipline_alerts
                WHERE trade_id = ? AND alert_date = ? AND alert_type = ? AND acknowledged = 0
            """, (alert['trade_id'], alert['alert_date'], alert['alert_type'])).fetchone()
            if not existing:
                db.execute("""
                    INSERT INTO discipline_alerts (trade_id, stock_code, alert_date, alert_level, alert_type, alert_message)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (alert['trade_id'], alert['stock_code'], alert['alert_date'],
                      alert['alert_level'], alert['alert_type'], alert['alert_message']))

    db.commit()
    return alerts


def get_holdings_status(db):
    """获取所有持仓状态（用于monitor页面展示）"""
    trades = db.execute("""
        SELECT * FROM discipline_trades WHERE sell_date IS NULL ORDER BY buy_date DESC
    """).fetchall()

    holdings = []
    for t in trades:
        stock_code = t['stock_code']
        asset_type = t['asset_type'] if 'asset_type' in t.keys() else 'stock'

        # 最新收盘价：优先手工录入的快照（取最新日期），其次K线表，最后买入价
        snap = db.execute("""
            SELECT current_price FROM discipline_daily_snapshots
            WHERE trade_id = ? ORDER BY date DESC LIMIT 1
        """, (t['id'],)).fetchone()
        if snap and snap['current_price'] is not None:
            current_price = snap['current_price']
        else:
            close = _get_latest_close(db, stock_code, asset_type)
            current_price = close if close is not None else t['buy_price']
        pnl_pct = round((current_price - t['buy_price']) / t['buy_price'] * 100, 2)

        # 最新告警
        alerts = db.execute("""
            SELECT * FROM discipline_alerts
            WHERE trade_id = ? AND acknowledged = 0
            ORDER BY CASE alert_level WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END, created_at DESC
        """, (t['id'],)).fetchall()

        alert_list = [dict(a) for a in alerts]

        # 告警灯颜色：有红色→红，有黄色→黄，否则绿
        alert_light = LEVEL_GREEN
        for a in alert_list:
            if a['alert_level'] == LEVEL_RED:
                alert_light = LEVEL_RED
                break
            elif a['alert_level'] == LEVEL_YELLOW:
                alert_light = LEVEL_YELLOW

        market_value = round(current_price * t['buy_qty'], 2)

        holdings.append({
            'trade_id': t['id'],
            'stock_code': stock_code,
            'stock_name': t['stock_name'],
            'buy_date': t['buy_date'],
            'buy_price': t['buy_price'],
            'buy_qty': t['buy_qty'],
            'entry_signal': t['entry_signal'] if 'entry_signal' in t.keys() else '',
            'current_price': current_price,
            'market_value': market_value,
            'pnl_pct': pnl_pct,
            'stop_loss_price': t['stop_loss_price'],
            'hold_days': (datetime.now() - datetime.strptime(t['buy_date'], '%Y-%m-%d')).days,
            'alert_light': alert_light,
            'alerts': alert_list,
        })

    # 汇总总市值，计算每条仓位占比
    total_mv = sum(h['market_value'] for h in holdings)
    for h in holdings:
        h['position_pct'] = round(h['market_value'] / total_mv * 100, 1) if total_mv > 0 else 0

    # 按告警灯排序：红→黄→绿
    light_order = {LEVEL_RED: 0, LEVEL_YELLOW: 1, LEVEL_GREEN: 2}
    holdings.sort(key=lambda h: light_order.get(h['alert_light'], 99))

    return holdings, total_mv


# ════════════════════════════════════════
# CLI
# ════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='持仓监控扫描')
    parser.add_argument('--stock', type=str, default=None, help='单只股票代码')
    args = parser.parse_args()

    db = get_db()
    alerts = run_scan(db, target_stock=args.stock)
    print(f"生成了 {len(alerts)} 条告警")
    for a in alerts:
        print(f"  [{a['alert_level']}] {a['alert_type']}: {a['alert_message']}")
    db.close()
