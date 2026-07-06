import sqlite3
db = sqlite3.connect('D:/hanako/investment-system/data/lixinger.db')
cur = db.cursor()

# PP V2: 全量 vs RS≥80 过滤 vs RS≥85 过滤 vs 双过滤(RS≥80 + vol_ratio≥1.3)
print('=== PP V2 质量过滤对比 (H10 + T+1_O + full环境) ===\n')

filters = [
    ('全量（无过滤）', '1=1'),
    ('RS_250 ≥ 80', 'e.pp_v2_rps_250 >= 80'),
    ('RS_250 ≥ 85', 'e.pp_v2_rps_250 >= 85'),
    ('RS_250 ≥ 90', 'e.pp_v2_rps_250 >= 90'),
    ('vol_ratio ≥ 1.3', 'e.pp_v2_vol_ratio >= 1.3'),
    ('RS≥80 AND vol≥1.3', 'e.pp_v2_rps_250 >= 80 AND e.pp_v2_vol_ratio >= 1.3'),
    ('RS≥85 AND vol≥1.3', 'e.pp_v2_rps_250 >= 85 AND e.pp_v2_vol_ratio >= 1.3'),
    ('RS≥85 AND vol≥1.5', 'e.pp_v2_rps_250 >= 85 AND e.pp_v2_vol_ratio >= 1.5'),
]

for label, condition in filters:
    cur.execute(f"""
        SELECT COUNT(*), ROUND(AVG(net_ret_pct),2), ROUND(AVG(is_win)*100,1),
               ROUND(AVG(ret_pct),2)
        FROM backtest_results br
        JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
        WHERE br.combo_label='PP_V2' AND br.hold_days=10 AND br.entry_method='T+1_O'
          AND br.pool_mode='full' AND {condition}
    """)
    cnt, avg_net, win, avg_gross = cur.fetchone()
    kelly = max(0, win/100 - (1-win/100) / (avg_net/max(0.01, abs(avg_net))) if avg_net else 0)
    print(f'  {label:25s}  n={cnt:>6,d}  net={avg_net:>6.2f}%  win={win:>5.1f}%  gross={avg_gross:>6.2f}%')

# 对比 PP V1
print('\n=== PP V1 对比 ===')
for label, condition in [
    ('全量（无过滤）', '1=1'),
    ('RS_250 ≥ 80', 'e.pp_v1_rps_250 >= 80'),
    ('RS_250 ≥ 85', 'e.pp_v1_rps_250 >= 85'),
    ('RS≥80 AND vol≥1.3', 'e.pp_v1_rps_250 >= 80 AND e.pp_v1_vol_ratio >= 1.3'),
]:
    cur.execute(f"""
        SELECT COUNT(*), ROUND(AVG(net_ret_pct),2), ROUND(AVG(is_win)*100,1),
               ROUND(AVG(ret_pct),2)
        FROM backtest_results br
        JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
        WHERE br.combo_label='PP_V1' AND br.hold_days=10 AND br.entry_method='T+1_O'
          AND br.pool_mode='full' AND {condition}
    """)
    cnt, avg_net, win, avg_gross = cur.fetchone()
    print(f'  {label:25s}  n={cnt:>6,d}  net={avg_net:>6.2f}%  win={win:>5.1f}%  gross={avg_gross:>6.2f}%')

# BO V2 也看看
print('\n=== BO V2 对比 ===')
for label, condition in [
    ('全量（无过滤）', '1=1'),
    ('ind_rs250 ≥ 80', 'e.bo_v2_ind_rs250 >= 80'),
    ('RS_250 ≥ 80', 'e.bo_v2_ind_rs250 >= 80'),  # 同上
    ('vol≥1.3 AND indRS≥80', 'e.bo_v2_vol_ratio >= 1.3 AND e.bo_v2_ind_rs250 >= 80'),
]:
    cur.execute(f"""
        SELECT COUNT(*), ROUND(AVG(net_ret_pct),2), ROUND(AVG(is_win)*100,1),
               ROUND(AVG(ret_pct),2)
        FROM backtest_results br
        JOIN signal_events e ON br.stock_code=e.stock_code AND br.signal_date=e.date
        WHERE br.combo_label='BO_V2' AND br.hold_days=10 AND br.entry_method='T+1_O'
          AND br.pool_mode='full' AND {condition}
    """)
    cnt, avg_net, win, avg_gross = cur.fetchone()
    print(f'  {label:25s}  n={cnt:>6,d}  net={avg_net:>6.2f}%  win={win:>5.1f}%  gross={avg_gross:>6.2f}%')

db.close()
