import sqlite3, sys
sys.path.insert(0, "D:/hanako/investment-system/src")
from scanners.pocket_pivot_v2 import *

cfg = load_config()
db = sqlite3.connect(DB_PATH); db.row_factory = sqlite3.Row
codes = [r['stock_code'] for r in db.execute("SELECT DISTINCT stock_code FROM daily_kline WHERE date='2026-06-05'").fetchall()[:200]]
kc, rc = load_data_batch(db, codes, '2026-06-05')

# Sample a few stocks that pass trend
for code in list(kc.keys())[:10]:
    kl = kc[code]
    if len(kl) < 65: continue
    idx = len(kl) - 1
    closes = [k['close'] for k in kl]
    s10 = sma(closes, 10); s50 = sma(closes, 50)
    c = kl[-1]['close']
    
    if s10 and s50 and c > s50 and c > s10:
        # This stock passes trend. Check base.
        ok, info = check_base_consolidation(kl, idx-1, cfg)  # check period before today
        print(f"{code}: c={c:.2f} sma10={s10:.2f} sma50={s50:.2f} → base={'OK' if ok else info}")
        if ok: print(f"  GAIN CHECK: prev_close={kl[-2]['close']:.2f} today_c={c:.2f} gain={(c-kl[-2]['close'])/kl[-2]['close']*100:.1f}%")

db.close()
