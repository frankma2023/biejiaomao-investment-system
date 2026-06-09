import subprocess,sys
r=subprocess.run([sys.executable,'src/scanners/pocket_pivot_v2.py','--date','2026-06-04','--save','--backfill'],
    capture_output=True,text=True,timeout=60,cwd="D:/hanako/investment-system")
print('=== STDOUT (last 800 chars) ===')
print(r.stdout[-800:])
print('\n=== STDERR (last 800 chars) ===')
print(r.stderr[-800:])
print('\n=== RC:',r.returncode)
