# -*- coding: utf-8 -*-
import requests
d = requests.get('http://localhost:8788/api/canslim-score?code=688531&date=2026-08-27', timeout=60).json()
print(json.dumps(d, ensure_ascii=False, indent=1)[:3000]) if (json := __import__('json')) else None
