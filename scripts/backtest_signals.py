#!/usr/bin/env python
"""
全信号回测引擎 v1.0 · M0 阶段
────────────────────────────────
固定持有期回测 + 信号事件模型 + 凯利矩阵 + 质量分层 + 前视偏差审计

硬约束（不可违反）：
  0.1 未来函数禁令：信号日T使用的全部数据必须≤T日收盘
  0.2 幸存者偏差控制：全市场回测 + 过滤池双模输出
  0.3 交易成本：每笔强制扣除买入0.125%+卖出0.175%=0.3%
  0.4 涨跌停与停牌：涨停无法买入/跌停无法卖出/停牌跳过
  0.5 过度拟合防御：输出参数稳定域，不追唯一最优
  0.6 性能：K线预加载O(1)查价 + Polars聚合 + 批量写入

用法：
  python scripts/backtest_signals.py --step 1          # 只跑步骤1
  python scripts/backtest_signals.py                    # 跑全部
  python scripts/backtest_signals.py --start 2024-01-01 --end 2024-12-31
"""

import sys, os, time, argparse, sqlite3, json, yaml, math
from datetime import datetime, timedelta
from collections import defaultdict
from itertools import combinations

import polars as pl
import numpy as np
from scipy import stats as scipy_stats

# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def iter_groups(df, group_cols):
    """迭代Polars group_by的结果，yield (name_tuple, sub_df)"""
    agg = df.group_by(group_cols).agg(pl.len())
    for row in agg.iter_rows(named=True):
        name = tuple(row[c] for c in group_cols)
        # 构建过滤条件
        mask = pl.lit(True)
        for c in group_cols:
            mask = mask & (pl.col(c) == row[c])
        yield name, df.filter(mask)

def max_consecutive(arr, target):
    """最长连续出现target的次数"""
    max_streak = cur = 0
    for v in arr:
        if v == target:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 0
    return max_streak

def james_stein_shrink(group_mean, n, all_values):
    """简单贝叶斯收缩：小样本向总均值收缩
    all_values 应该是同类型的所有组均值（win_rate列表），不是原始收益值
    """
    grand_mean = np.mean(all_values) if len(all_values) > 0 else group_mean
    if n >= 100:
        return group_mean
    weight = n / 100.0
    return weight * group_mean + (1 - weight) * grand_mean

def compute_kelly(win_rate, avg_win, avg_loss):
    """凯利公式: f = p - (1-p) / (W/L)"""
    if avg_loss == 0 or avg_loss is None:
        return 0.0
    b = avg_win / avg_loss if avg_loss > 0 else avg_win
    k = win_rate - (1 - win_rate) / b if b > 0 else 0
    return max(0, min(0.5, k))  # 凯利上限50%

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)
sys.path.insert(0, os.path.join(PROJECT, 'src'))

DB_PATH = os.path.join(PROJECT, 'data', 'lixinger.db')
CONFIG_DIR = os.path.join(PROJECT, 'config', 'strategy')
os.makedirs(CONFIG_DIR, exist_ok=True)

# ═══════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════

BENCHMARK_INDEX = '000985'  # 中证全指

# 信号位掩码
SIGNAL_BITS = {
    'MW_B1':   0,  # bit0
    'MW_B2':   1,  # bit1
    'MW_PLUS': 2,  # bit2
    'PP_V1':   3,  # bit3
    'PP_V2':   4,  # bit4
    'BO_V2':   5,  # bit5
}
SIGNAL_NAMES = {v: k for k, v in SIGNAL_BITS.items()}

# 交易成本（买入/卖出分别计算）
COST_BUY  = 0.00125   # 佣金0.025% + 滑点0.1%
COST_SELL = 0.00175   # 佣金0.025% + 印花税0.05% + 滑点0.1%
COST_TOTAL = COST_BUY + COST_SELL  # 0.3%

# 入场方法
ENTRY_METHODS = ['T+0_C', 'T+1_O', 'T+2_O']

# 持有期
HOLD_PERIODS = [5, 10, 20, 60]

# 市场环境
MARKET_REGIMES = ['all', 'bull', 'bear', 'ranging']

# 涨停/跌停阈值
LIMIT_UP_PCT = 9.5   # 开盘涨幅≥9.5%视为涨停无法买入
LIMIT_DOWN_PCT = -9.5
MAX_LIMIT_DELAY = 3  # 顺延最多3天


# ═══════════════════════════════════════════
# 数据库工具
# ═══════════════════════════════════════════

def get_db():
    """每次调用创建新连接（线程安全），超时60s"""
    db = sqlite3.connect(DB_PATH, timeout=60)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=60000")
    return db


# ═══════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════

class BacktestEngine:
    def __init__(self, start_date='2023-01-01', end_date='2026-06-22',
                 pool_mode='full', skip_cost=False):
        self.start_date = start_date
        self.end_date = end_date
        self.pool_mode = pool_mode   # 'full' | 'filtered'
        self.skip_cost = skip_cost   # 调试模式跳过交易成本
        self.cost_total = 0.0 if skip_cost else COST_TOTAL
        
        # 缓存
        self.kline_idx = {}          # {(code, date): {open,high,low,close,adj_close,amount}}
        self.trading_dates = []      # 全量交易日列表
        self.regime_cache = {}       # {date: 'bull'|'bear'|'ranging'}
        self.st_stocks = set()       # ST/*ST股票集合
        self.benchmark_klines = []   # 基准指数K线 [(date, close), ...]
        self.date_to_idx = {}        # {date: index_in_trading_dates}
        
    # ── 步骤 1: 构建信号事件表 ──
    
    def step1_build_events(self):
        """聚合6张信号表→signal_events表，每行=(stock_code, date, signal_mask, factors)"""
        t0 = time.time()
        print('[1/7] 构建信号事件表...')
        
        db = get_db()
        
        # 建表
        db.executescript("""
            DROP TABLE IF EXISTS signal_events;
            CREATE TABLE signal_events (
                stock_code TEXT NOT NULL,
                date TEXT NOT NULL,
                stock_name TEXT,
                signal_mask INTEGER NOT NULL DEFAULT 0,
                combo_label TEXT NOT NULL DEFAULT '',
                signal_count INTEGER NOT NULL DEFAULT 0,
                -- MW因子
                mw_b1_decline_pct REAL,  mw_b1_h_rs250 INTEGER,  mw_b1_vol_ratio REAL,
                mw_b2_score INTEGER,     mw_b2_is_gap INTEGER,    mw_b2_return_pct REAL,
                -- PP因子
                pp_v1_vol_ratio REAL,    pp_v1_rps_250 INTEGER,   pp_v1_gain_pct REAL,
                pp_v2_vol_ratio REAL,    pp_v2_rps_250 INTEGER,   pp_v2_gain_pct REAL,
                -- BO因子
                bo_v2_vol_ratio REAL,    bo_v2_decline_pct REAL,  bo_v2_ind_rs250 INTEGER,
                bo_v2_gain_pct REAL,
                PRIMARY KEY (stock_code, date)
            );
            CREATE INDEX IF NOT EXISTS idx_se_date ON signal_events(date);
            CREATE INDEX IF NOT EXISTS idx_se_mask ON signal_events(signal_mask);
        """)
        
        # ── 收集6个子查询的结果到Python内存 ──
        # 用 Polars 做 GROUP BY 聚合，避免在SQLite里做复杂JOIN
        rows_by_key = defaultdict(lambda: {
            'stock_name': '', 'mask': 0,
            'mw_b1_d': None, 'mw_b1_rs': None, 'mw_b1_vr': None,
            'mw_b2_sc': None, 'mw_b2_gap': None, 'mw_b2_ret': None,
            'pp_v1_vr': None, 'pp_v1_rs': None, 'pp_v1_gain': None,
            'pp_v2_vr': None, 'pp_v2_rs': None, 'pp_v2_gain': None,
            'bo_v2_vr': None, 'bo_v2_dd': None, 'bo_v2_irs': None, 'bo_v2_gain': None,
        })
        
        date_range = f"date >= '{self.start_date}' AND date <= '{self.end_date}'"
        
        # MW B1
        print('  加载 MW B1...', end=' ', flush=True)
        t = time.time()
        for r in db.execute(f"""
            SELECT stock_code, b1_date as date, stock_name, decline_pct, h_rs250, b1_vol_ratio
            FROM mw_signal_daily WHERE b1_date IS NOT NULL AND b1_date >= ? AND b1_date <= ?
              AND stock_code != '_sentinel_'
        """, (self.start_date, self.end_date)):
            key = (r['stock_code'], r['date'])
            d = rows_by_key[key]
            d['mask'] |= (1 << SIGNAL_BITS['MW_B1'])
            d['stock_name'] = r['stock_name'] or ''
            d['mw_b1_d'] = r['decline_pct']
            d['mw_b1_rs'] = r['h_rs250']
            d['mw_b1_vr'] = r['b1_vol_ratio']
        print(f'{time.time()-t:.1f}s')
        
        # MW B2
        print('  加载 MW B2...', end=' ', flush=True)
        t = time.time()
        for r in db.execute(f"""
            SELECT stock_code, b2_date as date, stock_name, score, b2_is_gap, b2_return_pct
            FROM mw_signal_daily WHERE b2_date IS NOT NULL AND b2_date >= ? AND b2_date <= ?
              AND stock_code != '_sentinel_'
        """, (self.start_date, self.end_date)):
            key = (r['stock_code'], r['date'])
            d = rows_by_key[key]
            d['mask'] |= (1 << SIGNAL_BITS['MW_B2'])
            if not d['stock_name']:
                d['stock_name'] = r['stock_name'] or ''
            d['mw_b2_sc'] = r['score']
            d['mw_b2_gap'] = r['b2_is_gap']
            d['mw_b2_ret'] = r['b2_return_pct']
        print(f'{time.time()-t:.1f}s')
        
        # MW PLUS
        print('  加载 MW PLUS...', end=' ', flush=True)
        t = time.time()
        for r in db.execute(f"""
            SELECT stock_code, b2_date as date, stock_name, score, b2_is_gap, b2_return_pct
            FROM mw_signal_daily WHERE is_plus=1 AND b2_date >= ? AND b2_date <= ?
              AND stock_code != '_sentinel_'
        """, (self.start_date, self.end_date)):
            key = (r['stock_code'], r['date'])
            d = rows_by_key[key]
            d['mask'] |= (1 << SIGNAL_BITS['MW_PLUS'])
            if not d['stock_name']:
                d['stock_name'] = r['stock_name'] or ''
        print(f'{time.time()-t:.1f}s')
        
        # PP V1
        print('  加载 PP V1...', end=' ', flush=True)
        t = time.time()
        for r in db.execute(f"""
            SELECT stock_code, date, stock_name, vol_ratio, rps_250, gain_pct
            FROM pocket_pivot_daily WHERE {date_range} AND engine_version='V1'
        """):
            key = (r['stock_code'], r['date'])
            d = rows_by_key[key]
            d['mask'] |= (1 << SIGNAL_BITS['PP_V1'])
            if not d['stock_name']:
                d['stock_name'] = r['stock_name'] or ''
            d['pp_v1_vr'] = r['vol_ratio']
            d['pp_v1_rs'] = r['rps_250']
            d['pp_v1_gain'] = r['gain_pct']
        print(f'{time.time()-t:.1f}s')
        
        # PP V2
        print('  加载 PP V2...', end=' ', flush=True)
        t = time.time()
        for r in db.execute(f"""
            SELECT stock_code, date, stock_name, vol_ratio, rps_250, gain_pct
            FROM pocket_pivot_daily WHERE {date_range} AND engine_version='V2'
        """):
            key = (r['stock_code'], r['date'])
            d = rows_by_key[key]
            d['mask'] |= (1 << SIGNAL_BITS['PP_V2'])
            if not d['stock_name']:
                d['stock_name'] = r['stock_name'] or ''
            d['pp_v2_vr'] = r['vol_ratio']
            d['pp_v2_rs'] = r['rps_250']
            d['pp_v2_gain'] = r['gain_pct']
        print(f'{time.time()-t:.1f}s')
        
        # BO V2
        print('  加载 BO V2...', end=' ', flush=True)
        t = time.time()
        for r in db.execute(f"""
            SELECT stock_code, date, stock_name, vol_ratio, decline_pct, ind_rs250, gain_pct
            FROM market_breakout_v2_daily WHERE {date_range} AND engine_version='V2'
        """):
            key = (r['stock_code'], r['date'])
            d = rows_by_key[key]
            d['mask'] |= (1 << SIGNAL_BITS['BO_V2'])
            if not d['stock_name']:
                d['stock_name'] = r['stock_name'] or ''
            d['bo_v2_vr'] = r['vol_ratio']
            d['bo_v2_dd'] = r['decline_pct']
            d['bo_v2_irs'] = r['ind_rs250']
            d['bo_v2_gain'] = r['gain_pct']
        print(f'{time.time()-t:.1f}s')
        
        # ── 批量写入 ──
        print(f'  写入 signal_events ({len(rows_by_key)} 个事件)...', end=' ', flush=True)
        t = time.time()
        cur = db.cursor()
        batch = []
        for (code, date), d in rows_by_key.items():
            mask = d['mask']
            # 生成combo_label
            parts = []
            for i in range(6):
                if mask & (1 << i):
                    parts.append(SIGNAL_NAMES[i])
            combo_label = '+'.join(parts)
            signal_count = len(parts)
            
            batch.append((
                code, date, d['stock_name'], mask, combo_label, signal_count,
                d['mw_b1_d'], d['mw_b1_rs'], d['mw_b1_vr'],
                d['mw_b2_sc'], d['mw_b2_gap'], d['mw_b2_ret'],
                d['pp_v1_vr'], d['pp_v1_rs'], d['pp_v1_gain'],
                d['pp_v2_vr'], d['pp_v2_rs'], d['pp_v2_gain'],
                d['bo_v2_vr'], d['bo_v2_dd'], d['bo_v2_irs'], d['bo_v2_gain'],
            ))
            if len(batch) >= 5000:
                cur.executemany("""
                    INSERT OR REPLACE INTO signal_events 
                    (stock_code,date,stock_name,signal_mask,combo_label,signal_count,
                     mw_b1_decline_pct,mw_b1_h_rs250,mw_b1_vol_ratio,
                     mw_b2_score,mw_b2_is_gap,mw_b2_return_pct,
                     pp_v1_vol_ratio,pp_v1_rps_250,pp_v1_gain_pct,
                     pp_v2_vol_ratio,pp_v2_rps_250,pp_v2_gain_pct,
                     bo_v2_vol_ratio,bo_v2_decline_pct,bo_v2_ind_rs250,bo_v2_gain_pct)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, batch)
                batch = []
        if batch:
            cur.executemany("""
                INSERT OR REPLACE INTO signal_events 
                (stock_code,date,stock_name,signal_mask,combo_label,signal_count,
                 mw_b1_decline_pct,mw_b1_h_rs250,mw_b1_vol_ratio,
                 mw_b2_score,mw_b2_is_gap,mw_b2_return_pct,
                 pp_v1_vol_ratio,pp_v1_rps_250,pp_v1_gain_pct,
                 pp_v2_vol_ratio,pp_v2_rps_250,pp_v2_gain_pct,
                 bo_v2_vol_ratio,bo_v2_decline_pct,bo_v2_ind_rs250,bo_v2_gain_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, batch)
        db.commit()
        db.close()
        
        self.event_count = len(rows_by_key)
        
        # 统计
        print(f'{time.time()-t:.1f}s')
        print(f'  事件总数: {self.event_count}')
        for i in range(6):
            cnt = sum(1 for d in rows_by_key.values() if d['mask'] & (1 << i))
            print(f'    {SIGNAL_NAMES[i]}: {cnt}')
        
        # combo分布Top10
        combo_counts = defaultdict(int)
        for d in rows_by_key.values():
            parts = []
            for i in range(6):
                if d['mask'] & (1 << i):
                    parts.append(SIGNAL_NAMES[i])
            combo_counts['+'.join(parts)] += 1
        print(f'  Top10组合:')
        for combo, cnt in sorted(combo_counts.items(), key=lambda x: -x[1])[:10]:
            print(f'    {combo}: {cnt}')
        
        print(f'  [1/7] 完成 ({time.time()-t0:.0f}s)')
        return self.event_count
    
    # ── 步骤 2: 预加载K线 + 市场环境 ──
    
    def step2_preload(self):
        """预加载全量K线到内存 + 计算市场环境"""
        t0 = time.time()
        print('[2/7] 预加载K线 + 市场环境...')
        
        db = get_db()
        
        # ── 2a. ST股票池 ──
        rows = db.execute("""
            SELECT stock_code FROM stock_basic 
            WHERE listing_status IN ('special_treatment', 'delisting_risk_warning')
        """).fetchall()
        self.st_stocks = {r['stock_code'] for r in rows}
        print(f'  ST/风险警示: {len(self.st_stocks)} 只')
        
        # ── 2b. 全量K线（前复权价）──
        # 加载范围：start_date前120天～end_date后70天（覆盖最长持有期+MA计算）
        load_start = (datetime.strptime(self.start_date, '%Y-%m-%d') - timedelta(days=200)).strftime('%Y-%m-%d')
        load_end = (datetime.strptime(self.end_date, '%Y-%m-%d') + timedelta(days=70)).strftime('%Y-%m-%d')
        
        print(f'  加载K线 {load_start}~{load_end}...', end=' ', flush=True)
        t = time.time()
        rows = db.execute("""
            SELECT stock_code, date, open, high, low, close, volume, amount,
                   adj_close, adj_open, adj_high, adj_low
            FROM daily_kline WHERE date >= ? AND date <= ? ORDER BY stock_code, date
        """, (load_start, load_end)).fetchall()
        
        for r in rows:
            self.kline_idx[(r['stock_code'], r['date'])] = {
                'open': r['open'], 'high': r['high'], 'low': r['low'],
                'close': r['close'], 'volume': r['volume'], 'amount': r['amount'],
                'adj_close': r['adj_close'], 'adj_open': r['adj_open'],
                'adj_high': r['adj_high'], 'adj_low': r['adj_low'],
            }
        print(f'{len(rows)} 行 ({time.time()-t:.1f}s)')
        
        # ── 2c. 交易日历 ──
        self.trading_dates = sorted(set(
            r['date'] for r in db.execute("""
                SELECT DISTINCT date FROM daily_kline 
                WHERE date >= ? AND date <= ? ORDER BY date
            """, (load_start, self.end_date)).fetchall()
        ))
        self.date_to_idx = {d: i for i, d in enumerate(self.trading_dates)}
        print(f'  交易日: {len(self.trading_dates)} 天')
        
        # ── 2d. 基准指数K线 ──
        rows = db.execute("""
            SELECT date, close FROM index_daily_kline 
            WHERE stock_code = ? AND date >= ? AND date <= ?
            ORDER BY date
        """, (BENCHMARK_INDEX, load_start, load_end)).fetchall()
        self.benchmark_klines = [(r['date'], r['close']) for r in rows]
        self.benchmark_dict = dict(self.benchmark_klines)
        print(f'  基准指数: {len(self.benchmark_klines)} 天')
        
        # ── 2e. 市场环境计算（中证全指MA50/MA200）──
        print('  计算市场环境...', end=' ', flush=True)
        t = time.time()
        closes = [c for _, c in self.benchmark_klines]
        dates = [d for d, _ in self.benchmark_klines]
        
        # 用numpy向量化计算MA
        closes_arr = np.array(closes, dtype=np.float64)
        ma50 = np.full(len(closes_arr), np.nan)
        ma200 = np.full(len(closes_arr), np.nan)
        
        # 简单移动平均
        for i in range(49, len(closes_arr)):
            ma50[i] = np.mean(closes_arr[i-49:i+1])
        for i in range(199, len(closes_arr)):
            ma200[i] = np.mean(closes_arr[i-199:i+1])
        
        for i in range(len(dates)):
            if np.isnan(ma50[i]) or np.isnan(ma200[i]):
                self.regime_cache[dates[i]] = 'ranging'
                continue
            above_ma200 = closes_arr[i] > ma200[i]
            # MA50斜率: 当前MA50 vs 20天前MA50
            if i >= 20 and not np.isnan(ma50[i-20]):
                slope = (ma50[i] - ma50[i-20]) / ma50[i-20]
            else:
                slope = 0
            
            if slope > 0.005 and above_ma200:     # MA50上升且指数在MA200上方
                self.regime_cache[dates[i]] = 'bull'
            elif slope < -0.005 and not above_ma200:  # MA50下降且指数在MA200下方
                self.regime_cache[dates[i]] = 'bear'
            else:
                self.regime_cache[dates[i]] = 'ranging'
        
        # 统计分布
        from collections import Counter
        dist = Counter(self.regime_cache.values())
        total = sum(dist.values())
        print(f'{time.time()-t:.1f}s')
        print(f'  环境分布: bull={dist["bull"]}({dist["bull"]/total*100:.0f}%) '
              f'bear={dist["bear"]}({dist["bear"]/total*100:.0f}%) '
              f'ranging={dist["ranging"]}({dist["ranging"]/total*100:.0f}%)')
        
        db.close()
        print(f'  [2/7] 完成 ({time.time()-t0:.0f}s)')
    
    # ── 步骤 3: 固定持有期回测 ──
    
    def step3_backtest(self):
        """核心回测：对每条signal_event计算3×4×4种场景的收益"""
        t0 = time.time()
        print('[3/7] 固定持有期回测...')
        
        # 自动检查前置步骤
        if not self.kline_idx:
            print('  ⚠ 未预加载K线，自动执行步骤2...')
            self.step2_preload()
        
        db = get_db()
        
        # 建结果表
        db.executescript("""
            DROP TABLE IF EXISTS backtest_results;
            CREATE TABLE backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                signal_date TEXT NOT NULL,
                signal_mask INTEGER NOT NULL,
                combo_label TEXT NOT NULL,
                signal_count INTEGER NOT NULL,
                entry_method TEXT NOT NULL,
                hold_days INTEGER NOT NULL,
                market_regime TEXT NOT NULL,
                pool_mode TEXT NOT NULL DEFAULT 'full',
                entry_price REAL,
                exit_price REAL,
                ret_pct REAL,
                net_ret_pct REAL,
                is_win INTEGER,
                peak_ret_pct REAL,
                trough_ret_pct REAL,
                index_ret_pct REAL,
                excess_ret_pct REAL,
                skipped_reason TEXT,
                UNIQUE(stock_code, signal_date, entry_method, hold_days, pool_mode)
            );
            CREATE INDEX IF NOT EXISTS idx_br_combo ON backtest_results(combo_label);
            CREATE INDEX IF NOT EXISTS idx_br_hold ON backtest_results(hold_days);
            CREATE INDEX IF NOT EXISTS idx_br_regime ON backtest_results(market_regime);
        """)
        
        # 加载所有signal_events
        rows = db.execute("""
            SELECT * FROM signal_events WHERE date >= ? AND date <= ?
        """, (self.start_date, self.end_date)).fetchall()
        
        events = []
        for r in rows:
            # 过滤池模式：检查ST
            if self.pool_mode == 'filtered' and r['stock_code'] in self.st_stocks:
                continue
            # 过滤池模式：检查成交额（信号日当天）
            if self.pool_mode == 'filtered':
                kl = self.kline_idx.get((r['stock_code'], r['date']))
                if kl and kl['amount'] < 50_000_000:  # <5000万
                    continue
            events.append(dict(r))
        
        print(f'  信号事件: {len(events)}')
        
        # 统计变量
        total_trades = 0
        skipped = defaultdict(int)
        results_batch = []
        batch_size = 5000
        
        for ei, ev in enumerate(events):
            if ei % 5000 == 0 and ei > 0:
                print(f'  进度: {ei}/{len(events)} ({total_trades}笔交易)...', flush=True)
            
            code = ev['stock_code']
            sig_date = ev['date']
            mask = ev['signal_mask']
            combo = ev['combo_label']
            sc = ev['signal_count']
            
            # 确定市场环境
            regime = self.regime_cache.get(sig_date, 'ranging')
            
            for entry_method in ENTRY_METHODS:
                for hold_days in HOLD_PERIODS:
                    result = self._eval_one_trade(
                        code, sig_date, mask, combo, sc,
                        entry_method, hold_days, regime
                    )
                    if result is None:
                        continue  # 无有效数据
                    
                    if result.get('skipped'):
                        skipped[result['skipped_reason']] += 1
                        continue
                    
                    # 全市场模式写一条
                    result['pool_mode'] = 'full'
                    results_batch.append(result)
                    total_trades += 1
                    
                    # 过滤池模式也需要写（但过滤已经在events加载时做了）
                    if self.pool_mode == 'filtered':
                        r2 = dict(result)
                        r2['pool_mode'] = 'filtered'
                        results_batch.append(r2)
                        total_trades += 1
                    
                    if len(results_batch) >= batch_size:
                        self._flush_results(db, results_batch)
                        results_batch = []
        
        if results_batch:
            self._flush_results(db, results_batch)
        
        db.commit()
        db.close()
        
        print(f'  总交易: {total_trades}')
        if skipped:
            print(f'  跳过: {dict(skipped)}')
        print(f'  [3/7] 完成 ({time.time()-t0:.0f}s)')
        return total_trades
    
    def _eval_one_trade(self, code, sig_date, mask, combo, sc,
                         entry_method, hold_days, regime):
        """评估单笔交易，返回dict或None（无数据）/ skipped标记"""
        
        # ── 找入场日（T+0/T+1/T+2）──
        sig_idx = self.date_to_idx.get(sig_date)
        if sig_idx is None:
            return None
        
        offset = {'T+0_C': 0, 'T+1_O': 1, 'T+2_O': 2}[entry_method]
        entry_date_idx = sig_idx + offset
        
        if entry_date_idx >= len(self.trading_dates):
            return {'skipped': True, 'skipped_reason': 'entry_out_of_range'}
        
        entry_date = self.trading_dates[entry_date_idx]
        entry_kl = self.kline_idx.get((code, entry_date))
        
        # 停牌检查：连续无K线
        if entry_kl is None:
            # 尝试顺延（涨停/停牌延迟）
            for delay in range(1, MAX_LIMIT_DELAY + 1):
                delayed_idx = entry_date_idx + delay
                if delayed_idx >= len(self.trading_dates):
                    break
                delayed_date = self.trading_dates[delayed_idx]
                delayed_kl = self.kline_idx.get((code, delayed_date))
                if delayed_kl:
                    entry_kl = delayed_kl
                    entry_date = delayed_date
                    break
            if entry_kl is None:
                return {'skipped': True, 'skipped_reason': 'suspended'}
        
        # 使用前复权价
        if entry_method.endswith('_C'):
            entry_price = entry_kl['adj_close']
        else:  # _O
            entry_price = entry_kl['adj_open']
        
        if entry_price is None or entry_price <= 0:
            return {'skipped': True, 'skipped_reason': 'invalid_entry_price'}
        
        # ── 涨停检查（T+1_O/T+2_O入场时）──
        if entry_method.endswith('_O'):
            # 查前一日的收盘价
            prev_idx = entry_date_idx - 1
            if prev_idx >= 0:
                prev_date = self.trading_dates[prev_idx]
                prev_kl = self.kline_idx.get((code, prev_date))
                if prev_kl and prev_kl['adj_close'] and prev_kl['adj_close'] > 0:
                    gap_pct = (entry_price - prev_kl['adj_close']) / prev_kl['adj_close'] * 100
                    if gap_pct >= LIMIT_UP_PCT:
                        # 涨停延迟：顺延到次日
                        for delay in range(1, MAX_LIMIT_DELAY + 1):
                            next_idx = entry_date_idx + delay
                            if next_idx >= len(self.trading_dates):
                                break
                            next_date = self.trading_dates[next_idx]
                            next_kl = self.kline_idx.get((code, next_date))
                            if next_kl is None or next_kl.get('adj_open') is None:
                                continue
                            next_prev = self.trading_dates[next_idx - 1]
                            next_prev_kl = self.kline_idx.get((code, next_prev))
                            if next_prev_kl and next_prev_kl.get('adj_close') and next_prev_kl['adj_close'] > 0:
                                next_gap = (next_kl['adj_open'] - next_prev_kl['adj_close']) / next_prev_kl['adj_close'] * 100
                                if next_gap < LIMIT_UP_PCT:
                                    entry_price = next_kl['adj_open']
                                    entry_date = next_date
                                    break
                        else:
                            return {'skipped': True, 'skipped_reason': 'limit_up_blocked'}
        
        # ── 找退出日 ──
        entry_idx_in_td = self.date_to_idx.get(entry_date)
        if entry_idx_in_td is None:
            return None
        
        exit_idx = entry_idx_in_td + hold_days - 1  # H5=持有5日(含入场日=第1日，第5日退出)
        if exit_idx >= len(self.trading_dates):
            return None  # 超出范围
        
        exit_date = self.trading_dates[exit_idx]
        exit_kl = self.kline_idx.get((code, exit_date))
        
        if exit_kl is None:
            # 停牌：顺延找下一个有K线的日期
            for delay in range(1, MAX_LIMIT_DELAY + 1):
                delayed_idx = exit_idx + delay
                if delayed_idx >= len(self.trading_dates):
                    break
                delayed_date = self.trading_dates[delayed_idx]
                delayed_kl = self.kline_idx.get((code, delayed_date))
                if delayed_kl:
                    exit_kl = delayed_kl
                    exit_date = delayed_date
                    break
            if exit_kl is None:
                return {'skipped': True, 'skipped_reason': 'exit_suspended'}
        
        exit_price = exit_kl['adj_close']
        if exit_price is None or exit_price <= 0:
            return {'skipped': True, 'skipped_reason': 'invalid_exit_price'}
        
        # ── 跌停检查（卖出时）──
        prev_exit_idx = exit_idx - 1
        if prev_exit_idx >= 0:
            prev_exit_date = self.trading_dates[prev_exit_idx]
            prev_exit_kl = self.kline_idx.get((code, prev_exit_date))
            if prev_exit_kl and prev_exit_kl['adj_close'] and prev_exit_kl['adj_close'] > 0:
                gap_pct = (exit_kl['adj_open'] - prev_exit_kl['adj_close']) / prev_exit_kl['adj_close'] * 100
                if gap_pct <= LIMIT_DOWN_PCT:
                    # 跌停顺延
                    for delay in range(1, MAX_LIMIT_DELAY + 1):
                        next_i = exit_idx + delay
                        if next_i >= len(self.trading_dates):
                            break
                        next_d = self.trading_dates[next_i]
                        next_kl = self.kline_idx.get((code, next_d))
                        if next_kl is None or next_kl.get('adj_open') is None:
                            continue
                        next_prev_d = self.trading_dates[next_i - 1]
                        next_prev_kl = self.kline_idx.get((code, next_prev_d))
                        if next_prev_kl and next_prev_kl['adj_close'] and next_prev_kl['adj_close'] > 0:
                            next_gap = (next_kl['adj_open'] - next_prev_kl['adj_close']) / next_prev_kl['adj_close'] * 100
                            if next_gap > LIMIT_DOWN_PCT:
                                exit_price = next_kl['adj_close']
                                exit_date = next_d
                                break
                    else:
                        return {'skipped': True, 'skipped_reason': 'limit_down_blocked'}
        
        # ── 计算收益 ──
        gross_ret = (exit_price - entry_price) / entry_price
        net_ret = gross_ret - self.cost_total
        is_win = 1 if net_ret > 0 else 0
        
        # ── 持有期内峰谷值 ──
        peak_ret = 0.0
        trough_ret = 0.0
        for di in range(entry_idx_in_td, exit_idx + 1):
            td = self.trading_dates[di]
            tkl = self.kline_idx.get((code, td))
            if tkl and tkl['adj_close'] and entry_price > 0:
                ret_at = (tkl['adj_close'] - entry_price) / entry_price
                peak_ret = max(peak_ret, ret_at)
                trough_ret = min(trough_ret, ret_at)
        
        # ── 基准同期收益 ──
        index_ret = 0.0
        entry_bm = self.benchmark_dict.get(entry_date)
        exit_bm = self.benchmark_dict.get(exit_date)
        if entry_bm and exit_bm and entry_bm > 0:
            index_ret = (exit_bm - entry_bm) / entry_bm
        
        excess_ret = net_ret - index_ret
        
        return {
            'stock_code': code, 'signal_date': sig_date,
            'signal_mask': mask, 'combo_label': combo, 'signal_count': sc,
            'entry_method': entry_method, 'hold_days': hold_days,
            'market_regime': regime,
            'entry_price': round(entry_price, 2), 'exit_price': round(exit_price, 2),
            'ret_pct': round(gross_ret * 100, 2),
            'net_ret_pct': round(net_ret * 100, 2),
            'is_win': is_win,
            'peak_ret_pct': round(peak_ret * 100, 2),
            'trough_ret_pct': round(trough_ret * 100, 2),
            'index_ret_pct': round(index_ret * 100, 2),
            'excess_ret_pct': round(excess_ret * 100, 2),
        }
    
    def _flush_results(self, db, batch):
        cur = db.cursor()
        cur.executemany("""
            INSERT OR REPLACE INTO backtest_results 
            (stock_code, signal_date, signal_mask, combo_label, signal_count,
             entry_method, hold_days, market_regime, pool_mode,
             entry_price, exit_price, ret_pct, net_ret_pct, is_win,
             peak_ret_pct, trough_ret_pct, index_ret_pct, excess_ret_pct)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            (r['stock_code'], r['signal_date'], r['signal_mask'], r['combo_label'],
             r['signal_count'], r['entry_method'], r['hold_days'], r['market_regime'],
             r['pool_mode'], r['entry_price'], r['exit_price'], r['ret_pct'],
             r['net_ret_pct'], r['is_win'], r['peak_ret_pct'], r['trough_ret_pct'],
             r['index_ret_pct'], r['excess_ret_pct'])
            for r in batch
        ])
        db.commit()
    
    # ── 步骤 4: 统计分析 ──
    
    def step4_statistics(self):
        """聚合backtest_results，计算全部绩效指标"""
        t0 = time.time()
        print('[4/7] 统计分析...')
        
        db = get_db()
        
        # 用Polars从SQLite读取
        df = pl.read_database(
            """SELECT combo_label, signal_mask, signal_count, entry_method, hold_days,
                      market_regime, net_ret_pct, is_win, peak_ret_pct, trough_ret_pct,
                      index_ret_pct, excess_ret_pct
               FROM backtest_results WHERE pool_mode='full'""",
            db
        )
        db.close()
        
        print(f'  加载 {len(df):,} 行到 Polars ({time.time()-t0:.1f}s)')
        
        # ── 分组聚合 ──
        stats_list = []
        
        # 按信号+入场+持有期+环境 四维分组
        group_cols = ['combo_label', 'entry_method', 'hold_days', 'market_regime']
        groups = df.group_by(group_cols)
        
        # 先算全局 win_rate 用于 JS 收缩
        global_win = float(df['is_win'].mean())
        
        total_groups = len(groups.agg(pl.len()))
        print(f'  分组数: {total_groups}')
        
        for name, gdf in iter_groups(df, group_cols):
            combo, entry, hold, regime = name
            n = len(gdf)
            if n < 5:  # 样本太少跳过
                continue
            
            rets = gdf['net_ret_pct'].to_numpy()
            wins = gdf['is_win'].to_numpy()
            
            win_rate = wins.mean()
            mean_ret = rets.mean()
            median_ret = np.median(rets)
            std_ret = rets.std()
            
            # 盈亏比
            pos_rets = rets[rets > 0]
            neg_rets = rets[rets < 0]
            avg_win = pos_rets.mean() if len(pos_rets) > 0 else 0
            avg_loss = abs(neg_rets.mean()) if len(neg_rets) > 0 else 0
            profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')
            
            # 尾部风险
            sorted_rets = np.sort(rets)
            worst_1pct = sorted_rets[:max(1, int(n*0.01))].mean()
            var_95 = np.percentile(sorted_rets, 5)
            
            # 亏损分布
            loss_buckets = {
                '0-3%': int(((rets <= 0) & (rets >= -3)).sum()),
                '3-7%': int(((rets < -3) & (rets >= -7)).sum()),
                '7-15%': int(((rets < -7) & (rets >= -15)).sum()),
                '15%+': int((rets < -15).sum()),
            }
            
            # 连胜连败
            max_win_streak = max_consecutive(wins, 1)
            max_lose_streak = max_consecutive(wins, 0)
            
            # James-Stein 收缩
            js_win_rate = win_rate if n >= 100 else james_stein_shrink(
                win_rate, n, np.array([global_win, win_rate]))
            
            # 凯利
            kelly = compute_kelly(win_rate, avg_win, abs(neg_rets.mean()) if len(neg_rets) > 0 else avg_win)
            
            # 超额收益
            excess_mean = gdf['excess_ret_pct'].to_numpy().mean()
            
            stats_list.append({
                'combo_label': combo,
                'signal_mask': int(gdf['signal_mask'][0]),
                'signal_count': int(gdf['signal_count'][0]),
                'entry_method': entry,
                'hold_days': int(hold),
                'market_regime': regime,
                'samples': n,
                'win_rate': round(float(win_rate), 4),
                'win_rate_js': round(float(js_win_rate), 4),
                'mean_ret': round(float(mean_ret), 2),
                'median_ret': round(float(median_ret), 2),
                'std_ret': round(float(std_ret), 2),
                'profit_loss_ratio': round(float(profit_loss_ratio), 2),
                'worst_1pct_mean': round(float(worst_1pct), 2),
                'var_95': round(float(var_95), 2),
                'loss_buckets': loss_buckets,
                'max_win_streak': max_win_streak,
                'max_lose_streak': max_lose_streak,
                'kelly': round(float(kelly), 4),
                'excess_ret': round(float(excess_mean), 2),
            })
        
        # 存入实例变量供后续步骤使用
        self.stats = stats_list
        self.stats_df = pl.DataFrame(stats_list)
        
        print(f'  有效统计组合: {len(stats_list)}')
        
        # ── 快速汇总 ──
        # Top5 单信号组合（全环境混合，按胜率排序）
        single = self.stats_df.filter(pl.col('signal_count') == 1)
        single_sorted = single.sort('win_rate', descending=True)
        print('\n  Top5 单信号×持有期组合（按胜率）:')
        for row in single_sorted.head(5).iter_rows(named=True):
            reliability = '⚠<30' if row['samples'] < 30 else ('~' if row['samples'] < 100 else '')
            print(f'    {row["combo_label"]:12s} H{row["hold_days"]:<3d} {row["entry_method"]:6s} {row["market_regime"]:8s} '
                  f'win={row["win_rate"]*100:.1f}% JS={row["win_rate_js"]*100:.1f}% '
                  f'avg={row["mean_ret"]:.2f}% n={row["samples"]}{reliability}')
        
        print(f'  [4/7] 完成 ({time.time()-t0:.0f}s)')
    
    # ── 步骤 5: 质量分层 ──
    
    def step5_quality_tiers(self):
        """对每个信号的核心因子做10等频分桶，检测拐点确定质量分层阈值"""
        t0 = time.time()
        print('[5/7] 信号质量分层...')
        
        db = get_db()
        
        # 加载 signal_events（含因子列）
        events_df = pl.read_database(
            """SELECT stock_code, date, signal_mask, combo_label,
                      mw_b1_decline_pct, mw_b1_h_rs250, mw_b1_vol_ratio,
                      mw_b2_score, mw_b2_is_gap,
                      pp_v1_vol_ratio, pp_v1_rps_250,
                      pp_v2_vol_ratio, pp_v2_rps_250,
                      bo_v2_vol_ratio, bo_v2_decline_pct, bo_v2_ind_rs250
               FROM signal_events""",
            db,
            schema_overrides={
                'mw_b1_decline_pct': pl.Float64, 'mw_b1_h_rs250': pl.Float64,
                'mw_b1_vol_ratio': pl.Float64, 'mw_b2_score': pl.Float64,
                'mw_b2_is_gap': pl.Float64, 'pp_v1_vol_ratio': pl.Float64,
                'pp_v1_rps_250': pl.Float64, 'pp_v2_vol_ratio': pl.Float64,
                'pp_v2_rps_250': pl.Float64, 'bo_v2_vol_ratio': pl.Float64,
                'bo_v2_decline_pct': pl.Float64, 'bo_v2_ind_rs250': pl.Float64,
            }
        )
        
        # 加载 backtest_results 的胜率
        results_df = pl.read_database(
            """SELECT stock_code, signal_date, combo_label, net_ret_pct, is_win
               FROM backtest_results WHERE pool_mode='full'""",
            db
        )
        db.close()
        
        # 因子定义
        factor_defs = {
            'PP_V1': [
                ('pp_v1_vol_ratio', 'vol_ratio', '量比'),
                ('pp_v1_rps_250', 'rps_250', 'RS强度'),
            ],
            'PP_V2': [
                ('pp_v2_vol_ratio', 'vol_ratio', '量比'),
                ('pp_v2_rps_250', 'rps_250', 'RS强度'),
            ],
            'BO_V2': [
                ('bo_v2_vol_ratio', 'vol_ratio', '量比'),
                ('bo_v2_decline_pct', 'decline_pct', '基部深度'),
            ],
            'MW_B1': [
                ('mw_b1_decline_pct', 'decline_pct', '调整深度'),
                ('mw_b1_h_rs250', 'h_rs250', '前高RS'),
            ],
            'MW_B2': [
                ('mw_b2_score', 'score', '总分'),
                ('mw_b2_is_gap', 'is_gap', '跳空'),
            ],
        }
        
        self.quality_tiers = {}
        
        for signal_name, factors in factor_defs.items():
            bit = SIGNAL_BITS[signal_name]
            mask = 1 << bit
            
            # 筛出该信号的事件
            sig_events = events_df.filter(pl.col('signal_mask') & mask > 0)
            if len(sig_events) < 30:
                print(f'  {signal_name}: 样本不足 ({len(sig_events)}), 跳过')
                continue
            
            # JOIN results（通过stock_code+date）
            sig_results = results_df.join(
                sig_events.select(['stock_code', 'date']).rename({'date': 'signal_date'}),
                on=['stock_code', 'signal_date'], how='inner'
            )
            
            tier_info = {'signal': signal_name, 'factors': {}}
            
            for col, short_name, label in factors:
                # 过滤有该因子值的行
                valid = sig_events.filter(pl.col(col).is_not_null())
                if len(valid) < 30:
                    continue
                
                # 10等频分桶
                try:
                    bucketed = valid.with_columns(
                        pl.col(col).qcut(10, labels=False).alias('bucket')
                    )
                except Exception:
                    continue
                
                # JOIN with results to get win_rate per bucket
                bucket_stats = bucketed.join(
                    results_df.select(['stock_code', 'signal_date', 'is_win']),
                    left_on=['stock_code', 'date'], right_on=['stock_code', 'signal_date'],
                    how='inner'
                ).group_by('bucket').agg([
                    pl.col('is_win').mean().alias('win_rate'),
                    pl.len().alias('samples'),
                    pl.col(col).mean().alias(f'{short_name}_mean'),
                ]).sort('bucket')
                
                # 检测拐点：胜率跃升>3%的位置
                wr = bucket_stats['win_rate'].to_list()
                samples = bucket_stats['samples'].to_list()
                thresholds = []
                for i in range(1, len(wr)):
                    if wr[i] - wr[i-1] > 0.03 and samples[i] >= 10:
                        mean_val = bucket_stats[f'{short_name}_mean'][i]
                        thresholds.append({
                            'bucket': i,
                            'value': round(float(mean_val), 2),
                            'win_rate_jump': round(float(wr[i] - wr[i-1]), 3),
                        })
                
                tier_info['factors'][short_name] = {
                    'label': label,
                    'buckets': [
                        {'bucket': int(r['bucket']), 'win_rate': round(float(r['win_rate']), 3),
                         'samples': int(r['samples']), 'mean': round(float(r[f'{short_name}_mean']), 2)}
                        for r in bucket_stats.iter_rows(named=True)
                    ],
                    'thresholds': thresholds,
                }
            
            self.quality_tiers[signal_name] = tier_info
            
            # 摘要
            for short_name, info in tier_info['factors'].items():
                if info['thresholds']:
                    t = info['thresholds'][0]
                    print(f'  {signal_name}.{short_name}: 拐点 bucket={t["bucket"]} value≈{t["value"]} wr+{t["win_rate_jump"]*100:.0f}%')
                else:
                    print(f'  {signal_name}.{short_name}: 无明显拐点（因子线性）')
        
        print(f'  [5/7] 完成 ({time.time()-t0:.0f}s)')
    
    # ── 步骤 6: YAML 输出 ──
    
    def step6_yaml_output(self):
        """输出凯利矩阵和各信号YAML到 config/strategy/"""
        t0 = time.time()
        print('[6/7] YAML 输出...')
        
        if not hasattr(self, 'stats_df') or self.stats_df is None:
            print('  ⚠ 无内存统计数据，自动执行步骤4...')
            self.step4_statistics()
        
        os.makedirs(CONFIG_DIR, exist_ok=True)
        
        # ── 6a. 凯利矩阵 ──
        kelly_rows = []
        for signal_name in SIGNAL_BITS:
            for regime in MARKET_REGIMES:
                subset = self.stats_df.filter(
                    (pl.col('combo_label') == signal_name) &
                    (pl.col('market_regime') == regime)
                )
                if len(subset) == 0:
                    continue
                # 取H10/T+1_O作为标准配置
                std = subset.filter(
                    (pl.col('hold_days') == 10) & (pl.col('entry_method') == 'T+1_O')
                )
                if len(std) == 0:
                    std = subset  # fallback
                
                row = std.sort('samples', descending=True).head(1)
                if len(row) > 0:
                    r = next(row.iter_rows(named=True))
                    kelly_rows.append({
                        'signal': signal_name,
                        'regime': regime,
                        'kelly': r['kelly'],
                        'win_rate': r['win_rate'],
                        'profit_loss_ratio': r['profit_loss_ratio'],
                        'samples': r['samples'],
                        'note': '' if r['samples'] >= 100 else ('⚠<100' if r['samples'] >= 30 else '⛔<30'),
                    })
        
        kelly_matrix = {
            'version': '1.0',
            'date': datetime.now().strftime('%Y-%m-%d'),
            'period': f'{self.start_date}~{self.end_date}',
            'cost_total': self.cost_total,
            'notes': [
                '凯利比例 = win_rate - (1-win_rate) / profit_loss_ratio',
                '取H10持有期+T+1_O入场作为标准配置',
                '⚠样本<100需谨慎，⛔<30不可靠',
                '仓位管理模块应按当前市场环境选择对应列的凯利值',
            ],
            'matrix': kelly_rows,
        }
        
        with open(os.path.join(CONFIG_DIR, 'kelly_matrix.yaml'), 'w', encoding='utf-8') as f:
            yaml.dump(kelly_matrix, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f'  kelly_matrix.yaml ({len(kelly_rows)} 条)')
        
        # ── 6b. 各信号详细统计 ──
        for signal_name in SIGNAL_BITS:
            subset = self.stats_df.filter(pl.col('combo_label') == signal_name)
            if len(subset) == 0:
                continue
            
            # 只在全环境下输出（不同环境的分组在矩阵里已有）
            all_env = subset.filter(pl.col('market_regime') == 'all')
            if len(all_env) == 0:
                all_env = subset
            
            summary = {
                'signal': signal_name,
                'period': f'{self.start_date}~{self.end_date}',
                'total_trades': int(subset['samples'].sum()),
                'by_hold_days': {},
                'by_entry_method': {},
            }
            
            for hd in HOLD_PERIODS:
                hd_data = all_env.filter(pl.col('hold_days') == hd)
                if len(hd_data) > 0:
                    r = hd_data.sort('samples', descending=True).head(1)
                    rr = next(r.iter_rows(named=True))
                    summary['by_hold_days'][f'H{hd}'] = {
                        'win_rate': round(rr['win_rate'], 4),
                        'win_rate_js': round(rr['win_rate_js'], 4),
                        'mean_ret': rr['mean_ret'],
                        'median_ret': rr['median_ret'],
                        'profit_loss_ratio': rr['profit_loss_ratio'],
                        'kelly': rr['kelly'],
                        'samples': rr['samples'],
                    }
            
            for em in ENTRY_METHODS:
                em_data = all_env.filter(pl.col('entry_method') == em)
                if len(em_data) > 0:
                    r = em_data.sort('samples', descending=True).head(1)
                    rr = next(r.iter_rows(named=True))
                    summary['by_entry_method'][em] = {
                        'win_rate': round(rr['win_rate'], 4),
                        'mean_ret': rr['mean_ret'],
                        'samples': rr['samples'],
                    }
            
            slug = signal_name.lower().replace('_', '-')
            path = os.path.join(CONFIG_DIR, f'{slug}.yaml')
            with open(path, 'w', encoding='utf-8') as f:
                yaml.dump(summary, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            print(f'  {slug}.yaml')
        
        # ── 6c. 质量分层阈值 ──
        if hasattr(self, 'quality_tiers') and self.quality_tiers:
            tiers_output = {
                'version': '1.0',
                'date': datetime.now().strftime('%Y-%m-%d'),
                'method': '10等频分桶 + 拐点检测（胜率跃升>3%）',
                'signals': self.quality_tiers,
            }
            with open(os.path.join(CONFIG_DIR, 'quality_tiers.yaml'), 'w', encoding='utf-8') as f:
                yaml.dump(tiers_output, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            print(f'  quality_tiers.yaml')
        
        print(f'  [6/7] 完成 ({time.time()-t0:.0f}s)')
    
    # ── 步骤 7: 前视偏差审计 ──
    
    def step7_audit(self):
        """随机偏移信号日期±5天，对比真实信号vs随机日期的胜率差异"""
        t0 = time.time()
        print('[7/7] 前视偏差审计...')
        
        # 确保K线已加载
        if not self.kline_idx:
            print('  ⚠ 未预加载K线，自动执行步骤2...')
            self.step2_preload()
        
        db = get_db()
        
        # 取50,000条随机样本
        cur = db.execute("""
            SELECT stock_code, signal_date, signal_mask, combo_label, signal_count
            FROM backtest_results WHERE pool_mode='full'
            ORDER BY RANDOM() LIMIT 50000
        """)
        samples = cur.fetchall()
        db.close()
        
        if len(samples) < 100:
            print('  样本不足')
            return
        
        print(f'  审计样本: {len(samples)} 条')
        
        # 真实信号的净收益
        real_rets = []
        fake_rets = []
        
        # 生成随机偏移日期
        np.random.seed(42)
        offsets = np.random.choice([-5, -4, -3, -2, -1, 1, 2, 3, 4, 5], size=len(samples))
        
        # 取H10+T+1_O作为标准统计（减少噪声）
        for i, row in enumerate(samples):
            code = row['stock_code']
            sig_date = row['signal_date']
            mask = row['signal_mask']
            combo = row['combo_label']
            sc = row['signal_count']
            
            # 真实信号
            real = self._eval_one_trade(code, sig_date, mask, combo, sc,
                                        'T+1_O', 10, self.regime_cache.get(sig_date, 'ranging'))
            if real and not real.get('skipped') and real.get('net_ret_pct') is not None:
                real_rets.append(real['net_ret_pct'])
            
            # 随机偏移日期
            sig_idx = self.date_to_idx.get(sig_date)
            if sig_idx is None:
                continue
            fake_idx = sig_idx + offsets[i]
            if fake_idx < 0 or fake_idx >= len(self.trading_dates):
                continue
            fake_date = self.trading_dates[fake_idx]
            fake = self._eval_one_trade(code, fake_date, mask, combo, sc,
                                        'T+1_O', 10, self.regime_cache.get(fake_date, 'ranging'))
            if fake and not fake.get('skipped') and fake.get('net_ret_pct') is not None:
                fake_rets.append(fake['net_ret_pct'])
        
        # t 检验
        from scipy import stats as scipy_stats
        real_arr = np.array(real_rets)
        fake_arr = np.array(fake_rets)
        
        t_stat, p_value = scipy_stats.ttest_ind(real_arr, fake_arr, equal_var=False)
        
        diff = real_arr.mean() - fake_arr.mean()
        real_win = (real_arr > 0).mean()
        fake_win = (fake_arr > 0).mean()
        
        self.audit_result = {
            'real_samples': len(real_arr),
            'fake_samples': len(fake_arr),
            'real_mean_ret': round(float(real_arr.mean()), 2),
            'fake_mean_ret': round(float(fake_arr.mean()), 2),
            'diff': round(float(diff), 2),
            'real_win_rate': round(float(real_win), 4),
            'fake_win_rate': round(float(fake_win), 4),
            't_statistic': round(float(t_stat), 4),
            'p_value': round(float(p_value), 6),
            'passed': p_value < 0.01 and diff > 0,
            'verdict': '',
        }
        
        if self.audit_result['passed']:
            self.audit_result['verdict'] = (
                f'✅ 通过：真实信号显著优于随机偏移（p={p_value:.4f}，差值+{diff:.2f}%）。'
                f'信号有独立预测力，非市场beta噪声。'
            )
        elif diff > 0:
            self.audit_result['verdict'] = (
                f'⚠ 边缘：真实信号优于随机但未达显著水平（p={p_value:.4f}）。'
                f'建议扩大样本或延长回测周期后重新检验。'
            )
        else:
            self.audit_result['verdict'] = (
                f'❌ 未通过：真实信号不优于随机偏移（p={p_value:.4f}）。'
                f'信号可能仅反映市场beta或存在前视偏差。'
            )
        
        # 输出
        print(f'\n  真实信号: avg={self.audit_result["real_mean_ret"]}% win={self.audit_result["real_win_rate"]*100:.1f}% (n={self.audit_result["real_samples"]})')
        print(f'  随机偏移: avg={self.audit_result["fake_mean_ret"]}% win={self.audit_result["fake_win_rate"]*100:.1f}% (n={self.audit_result["fake_samples"]})')
        print(f'  差值: {self.audit_result["diff"]}% | t={self.audit_result["t_statistic"]} p={self.audit_result["p_value"]}')
        print(f'  {self.audit_result["verdict"]}')
        
        # 保存
        with open(os.path.join(CONFIG_DIR, 'audit.yaml'), 'w', encoding='utf-8') as f:
            yaml.dump(self.audit_result, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        
        print(f'  [7/7] 完成 ({time.time()-t0:.0f}s)')
    
    # ── 主入口 ──
    
    def run(self, steps=None):
        """按步骤执行回测"""
        if steps is None:
            steps = [1, 2, 3, 4, 5, 6, 7]
        
        for step in steps:
            if step == 1:
                self.step1_build_events()
            elif step == 2:
                self.step2_preload()
            elif step == 3:
                self.step3_backtest()
            elif step == 4:
                self.step4_statistics()
            elif step == 5:
                self.step5_quality_tiers()
            elif step == 6:
                self.step6_yaml_output()
            elif step == 7:
                self.step7_audit()
        
        print('\n=== 回测完成 ===')


# ═══════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='全信号回测引擎 M0')
    parser.add_argument('--start', default='2023-01-01')
    parser.add_argument('--end', default='2026-06-22')
    parser.add_argument('--step', type=int, default=0, help='只跑指定步骤(1-7), 0=全部')
    parser.add_argument('--pool', choices=['full', 'filtered'], default='full',
                        help='股票池模式: full=全市场, filtered=50亿+5000万+非ST')
    parser.add_argument('--skip-cost', action='store_true', help='调试: 跳过交易成本')
    args = parser.parse_args()
    
    engine = BacktestEngine(
        start_date=args.start,
        end_date=args.end,
        pool_mode=args.pool,
        skip_cost=args.skip_cost,
    )
    
    if args.step > 0:
        engine.run(steps=[args.step])
    else:
        engine.run()
