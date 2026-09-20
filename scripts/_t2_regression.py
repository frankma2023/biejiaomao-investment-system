# -*- coding: utf-8 -*-
"""T2 回归验证：日线引擎参数化前后行为必须零变化。
用法:
  python scripts/_t2_regression.py snapshot   # 拍基线（改前跑一次）
  python scripts/_t2_regression.py compare    # 改后复跑对比
对比口径: 600309 + 002648 全历史 run_state_machine 输出的 daily_rows + trans_rows 逐行比对。
"""
import sys, os, json, hashlib, sqlite3

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, 'src'))
DB = os.path.join(PROJECT, 'data', 'lixinger.db')
OUT = os.path.join(PROJECT, 'analysis', '_t2_regression_baseline.json')
CODES = ['600309', '002648']

def run_and_dump():
    import scanners.cpa_stage as daily
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    out = {}
    for code in CODES:
        kl = daily.load_klines(conn, code, '2014-01-01')
        if len(kl) < 320:
            out[code] = {'error': 'too short %d' % len(kl)}
            continue
        ind = daily.compute_indicators(kl)
        tops = daily.load_bi_tops_by_date(conn, code, [k['date'] for k in kl])
        d, t = daily.run_state_machine(conn, code, kl, ind, tops)
        # 只留可复现字段：date/stage/stage_start/days_in/support/invalid/action + metrics
        out[code] = {
            'n_klines': len(kl),
            'daily': [[r[1], r[2], r[4], r[5], r[6], r[7], r[8], r[10]] for r in d],
            'trans': [[r[1], r[2], r[3]] for r in t],
        }
    conn.close()
    return out

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'snapshot'
    if mode == 'snapshot':
        data = run_and_dump()
        with open(OUT, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        n = sum(len(v.get('daily', [])) for v in data.values())
        print('baseline saved: %d daily rows, %s' % (n, OUT))
    else:
        with open(OUT, encoding='utf-8') as f:
            base = json.load(f)
        cur = run_and_dump()
        ok = True
        for code in CODES:
            b, c = base.get(code), cur.get(code)
            if b != c:
                ok = False
                print('MISMATCH %s' % code)
                if b and c:
                    bd, cd = b.get('daily', []), c.get('daily', [])
                    print('  daily rows: baseline=%d current=%d' % (len(bd), len(cd)))
                    for i, (rb, rc) in enumerate(zip(bd, cd)):
                        if rb != rc:
                            print('  first diff @%d:' % i)
                            print('    base: %s' % rb[:5])
                            print('    cur : %s' % rc[:5])
                            break
                    bt, ct = b.get('trans', []), c.get('trans', [])
                    print('  trans: baseline=%d current=%d' % (len(bt), len(ct)))
            else:
                print('OK %s: %d daily rows, %d trans, byte-identical' % (
                    code, len(b.get('daily', [])), len(b.get('trans', []))))
        print('RESULT:', 'PASS - 行为零变化' if ok else 'FAIL - 有行为差异，禁止继续')
        sys.exit(0 if ok else 1)

if __name__ == '__main__':
    main()
