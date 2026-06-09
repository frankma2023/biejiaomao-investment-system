import sys;sys.path.insert(0,"D:/hanako/investment-system/src")
from engine_registry import discover_engines
e=discover_engines(force_reload=True)
for n in sorted(e):
    m=e[n]['meta']
    if 'break' in n or 'base' in n:
        print(f"  {n}: {m['display_name']} v{m['version']}")
