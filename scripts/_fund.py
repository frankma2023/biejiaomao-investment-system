import sqlite3
c = sqlite3.connect("D:/hanako/investment-system/data/lixinger.db")
c.row_factory = sqlite3.Row

# Check fundamental columns
r = c.execute("PRAGMA table_info(fundamental_indicator)").fetchall()
cols = [row['name'] for row in r]
# Find revenue/profit related columns
for col in cols:
    if 'revenue' in col.lower() or 'profit' in col.lower() or 'roe' in col.lower() or 'eps' in col.lower() or 'pe' in col.lower():
        print(col)

print("\n--- Data ---")
# Try common column names
try:
    r = c.execute("SELECT date FROM fundamental_indicator WHERE stock_code='600110' ORDER BY date DESC LIMIT 1").fetchone()
    print(f"Latest date: {r['date'] if r else 'N/A'}")
except: pass

for col in ['revenue_ttm','net_profit_ttm','roe_ttm','eps_ttm','pe_ttm','pb','total_mv']:
    try:
        r = c.execute(f"SELECT date, {col} FROM fundamental_indicator WHERE stock_code='600110' ORDER BY date DESC LIMIT 1").fetchone()
        if r:
            print(f"  {col} ({r['date']}): {r[col]}")
    except: pass

c.close()
