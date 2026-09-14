#!/usr/bin/env python3
"""
【v1.4 六项变更 · 单股 A/B 验证】
新算法（内存计算）对比库中旧结果（cpa_stage_daily，尚未重算）。
验证点：
  #7  ③ 深档动作应变「观察」
  #16 ⑤ 命中数应变多（ATR60 分母更小）
  #20 ⑥a 占比应显著上升
  #13 ④ 事件应大幅减少（深度 ≤5%）
  #14 ④ 事件构成变化（长涨幅被排除）
  #11 ④ 动作全为「观察」
"""
import sys, os, sqlite3, json, time
from collections import Counter
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts')); sys.path.insert(0, os.path.join(ROOT, 'src'))
from common import DB_PATH
import scanners.cpa_stage as cpa

CASES = ['600309', '002648', '300308', '000885', '002001']

conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row
conn.execute('PRAGMA busy_timeout=60000')

print('参数确认：')
for k in ('b_depth_max', 'b_prior_gain_max', 'b_vcp_score', 'atr_win_slow'):
    print(f'  {k} = {cpa.CFG[k]}')
print(f'  ACTIONS["④"] = {cpa.ACTIONS["④"]}')
print()

tot_old = Counter(); tot_new = Counter()
for code in CASES:
    t = time.time()
    kl = cpa.load_klines(conn, code, '2014-01-01')
    if len(kl) < 320:
        print(f'{code}: K线不足'); continue
    ind = cpa.compute_indicators(kl)
    tops = cpa.load_bi_tops(conn, code)
    try:
        daily, trans = cpa.run_state_machine(conn, code, kl, ind, tops)
    except Exception as e:
        print(f'{code}: 跑失败 {type(e).__name__}: {e}')
        continue
    el = time.time() - t

    new_st = Counter(r[2] for r in daily)
    new_act = Counter(r[8] for r in daily)
    new_tr = Counter(f'{r[3]}' for r in trans if r[3] == '④')
    # 新算法下 ③ 深档的分布
    deep_act = Counter()
    for r in daily:
        if r[2] in ('③', '③T'):
            try:
                m = json.loads(r[10] or '{}')
            except Exception:
                m = {}
            ph = (m.get('detail') or {}).get('phase')
            deep_act[f'{r[2]}/{ph or "?"}→{r[8]}'] += 1

    old_st = Counter()
    for r in conn.execute("SELECT stage FROM cpa_stage_daily WHERE stock_code=?", (code,)):
        old_st[r[0]] += 1

    tot_old.update(old_st); tot_new.update(new_st)
    print(f'══ {code}  ({len(kl)} 根K线 / {el:.2f}s)')
    print(f'   阶段  旧: ④={old_st.get("④",0):<5} ⑤={old_st.get("⑤",0):<5} '
          f'⑥a={old_st.get("⑥a",0):<5} ⑥b={old_st.get("⑥b",0):<5}')
    print(f'   阶段  新: ④={new_st.get("④",0):<5} ⑤={new_st.get("⑤",0):<5} '
          f'⑥a={new_st.get("⑥a",0):<5} ⑥b={new_st.get("⑥b",0):<5}')
    print(f'   新进入④次数={new_tr.get("④",0)}   ④ 的动作分布={dict((k,v) for k,v in new_act.items() if k in ("加仓","观察"))}')
    print(f'   ③ 档位×动作: {dict(deep_act)}')
    print()

print('=== 五只合计阶段分布 旧 vs 新 ===')
keys = sorted(set(tot_old) | set(tot_new))
print(f'{"阶段":<8}{"旧":>8}{"新":>8}{"变化":>9}')
for k in keys:
    o, n_ = tot_old.get(k, 0), tot_new.get(k, 0)
    print(f'{k:<8}{o:>8,}{n_:>8,}{n_-o:>+9,}')
conn.close()
