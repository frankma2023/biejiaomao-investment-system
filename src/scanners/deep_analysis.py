#!/usr/bin/env python3
"""
深度分析引擎 —— stock-health-report Skill 的服务端实现（页面化）

■ 来源
  .agents/skills/stock-health-report/SKILL.md（十步工作流 + 八大师奇数投票 + 主持人裁决）
  本模块是它的**服务端流水线实现**：数据确定性取自本地库，判断层由 LLM 完成。

■ 与 Skill 的对应
  Skill 步骤 0  市场闸门      → _market_gate()
  Skill 步骤 1  数据拉取      → build_snapshot()
  Skill 步骤 2~8 否决/认识/行业/评估/定价/择时/情景 → 第 1 次 LLM 调用（整合分析）
  Skill 步骤 9  八大师投票    → 7 次并行 LLM 调用（每人一副眼镜）
  Skill 步骤 10 主持人裁决    → 第 9 次 LLM 调用（拿机器分 + 七票 + 证据）

■ 铁律（与 Skill 一致，写进 prompt）
  1. 裁决协议是输出结构：五档评级 + 动作清单
  2. 八大师每人必须投「买/等/回避」单向票 + 一句基于数据的理由
  3. 数据不可编造：缺的明说「未获取」，不伪装
  4. 红涨绿跌（前端负责）
  5. 禁止目标价、禁止收益预测

■ 为什么是 9 次调用而不是 1 次
  八大师的价值来自**彼此独立的视角**。一次调用让模型扮演七个角色，它会自我一致化
  （先给出结论，再让七个角色为这个结论找理由），失去"分歧"这个最有用的信号。
  实测代价可接受：7 次并行，墙钟时间约等于 1 次。

■ 用法
  from scanners.deep_analysis import start_job, get_job
  job_id = start_job('600309')       # 立即返回，后台线程跑
  get_job(job_id)                     # {'state': 'running', 'elapsed': 42, ...}
"""
import os, sys, json, time, uuid, sqlite3, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'lixinger.db')


# ═══════════════════════════════════════════════
# 配置（与 src/cockpit/oneil_deep.py 同源）
# ═══════════════════════════════════════════════
def _load_env():
    env_path = os.path.join(os.path.dirname(PROJECT_ROOT), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
DEEPSEEK_KEY = os.environ.get('DEEPSEEK_KEY', '')
DEEPSEEK_BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
LLM_MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')

# MoYu 平台备用模型
MOYU_KEY = os.environ.get('MOYU_KEY', '')
MOYU_BASE_URL = os.environ.get('MOYU_BASE_URL', 'https://www.moyu.info/v1')
MOYU_MODEL = os.environ.get('MOYU_MODEL', 'deepseek-chat')

PROVIDERS = {
    'deepseek': {'key': DEEPSEEK_KEY, 'base_url': DEEPSEEK_BASE_URL, 'model': LLM_MODEL},
    'moyu':    {'key': MOYU_KEY,      'base_url': MOYU_BASE_URL,      'model': MOYU_MODEL},
}
DEFAULT_PROVIDER = 'deepseek'


# ═══════════════════════════════════════════════
# 表
# ═══════════════════════════════════════════════
DDL = """
CREATE TABLE IF NOT EXISTS deep_analysis_jobs (
    job_id      TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    code_name   TEXT,
    data_date   TEXT,
    state       TEXT NOT NULL,          -- pending / running / done / error
    stage       TEXT,                   -- 当前阶段（供前端展示进度）
    elapsed_sec REAL DEFAULT 0,
    created_at  TEXT,
    finished_at TEXT,
    result_md   TEXT,
    votes_json  TEXT,                   -- 七票原始结果（可追溯）
    error       TEXT
)
"""


def ensure_table(conn):
    conn.executescript(DDL)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daj_code ON deep_analysis_jobs(code, data_date)")
    conn.commit()


# ═══════════════════════════════════════════════
# LLM（OpenAI 兼容）
# ═══════════════════════════════════════════════
def _llm(system, user, temperature=0.3, timeout=900, retries=2, provider=None):
    """单次调用。provider = 'deepseek' | 'moyu'，默认使用 DEFAULT_PROVIDER。"""
    prov = provider or DEFAULT_PROVIDER
    cfg = PROVIDERS.get(prov)
    if not cfg or not cfg['key']:
        raise RuntimeError(f'{prov} 未配置（检查 .env 中的 {prov.upper()}_KEY）')
    payload = json.dumps({
        'model': cfg['model'],
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'stream': False,
    }).encode('utf-8')
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                cfg['base_url'].rstrip('/') + '/chat/completions',
                data=payload,
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {cfg["key"]}'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode('utf-8'))
            return d['choices'][0]['message']['content']
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f'LLM 调用失败 ({prov}): {type(last).__name__}: {str(last)[:200]}')


# ═══════════════════════════════════════════════
# 数据快照（Skill 步骤 1 的确定性部分）
# ═══════════════════════════════════════════════
# 查询错误必须留痕（2026-09-15）：本模块多处查库，曾经因列名写错而被静默吞掉，
# 导致上层拿到 None 却不知道是“没数据”还是“查询错了”。
# 这正是 2026-09-14 行业分组 null 事故的同一类病，故此处不再静默。
_SNAP_ERRORS = []


def _rows(conn, sql, args=()):
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as e:
        _SNAP_ERRORS.append(f'{type(e).__name__}: {str(e)[:160]} | SQL: {" ".join(sql.split())[:90]}')
        return []


def _one(conn, sql, args=()):
    r = _rows(conn, sql, args)
    return r[0] if r else None


def build_snapshot(conn, code, data_date):
    """按 Skill 的数据清单组织快照。缺失的显式标 None，不推算。"""
    _SNAP_ERRORS.clear()
    snap = {'code': code, 'data_date': data_date}

    # 名称：stock_basic 的列是 stock_code/name（无 industry，行业从观察池取）
    snap['basic'] = _one(conn, "SELECT stock_code, name FROM stock_basic WHERE stock_code=?", (code,))
    if not snap['basic']:
        snap['basic'] = _one(conn, "SELECT stock_code, stock_name AS name FROM watchlist WHERE stock_code=? LIMIT 1", (code,))
    if not snap['basic']:
        snap['basic'] = {'stock_code': code, 'name': None}

    # 日线（近 250 日）
    snap['kline'] = _rows(conn, """SELECT date, open, high, low, close, volume, change_pct
        FROM daily_kline_adj WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 250""", (code, data_date))
    snap['kline'].reverse()
    if snap['kline']:
        last = snap['kline'][-1]
        snap['quote'] = {'date': last['date'], 'close': last['close'],
                         'change_pct': last.get('change_pct')}
        closes = [k['close'] for k in snap['kline'] if k.get('close')]
        if closes:
            snap['quote']['high_250'] = max(closes)
            snap['quote']['low_250'] = min(closes)
            snap['quote']['pos_250'] = round((closes[-1] - min(closes)) / (max(closes) - min(closes)), 3) \
                if max(closes) > min(closes) else None
            for n in (5, 10, 20, 50, 120, 250):
                if len(closes) >= n:
                    snap.setdefault('ma', {})[f'ma{n}'] = round(sum(closes[-n:]) / n, 2)
        # 距高低点
        snap['quote']['from_high_pct'] = round(closes[-1] / max(closes) - 1, 4) if closes else None

    # 财务（季度 + 年度）
    snap['fin_quarterly'] = _rows(conn, """SELECT * FROM stock_financials_quarterly
        WHERE stock_code=? ORDER BY report_date DESC LIMIT 12""", (code,))
    snap['fin_annual'] = _rows(conn, """SELECT * FROM stock_financials_annual
        WHERE stock_code=? ORDER BY report_date DESC LIMIT 10""", (code,))

    # 估值：从 fundamental_indicator（EAV 模型 metric_code+value）取 PE/PB/PS/股息率/市值
    # 计算历史分位，逻辑与 /api/stock-valuation 一致
    snap['valuation'] = []
    FI_METRICS = ('pe_ttm', 'pb', 'ps_ttm', 'dyr', 'mc')
    fi_rows = conn.execute('''
        SELECT date, metric_code, value FROM fundamental_indicator
        WHERE stock_code=? AND date<=? AND metric_code IN (?,?,?,?,?)
        ORDER BY date, metric_code
    ''', (code, data_date, *FI_METRICS)).fetchall()
    by_date = {}
    for r in fi_rows:
        d = r['date']
        if d not in by_date:
            by_date[d] = {}
        by_date[d][r['metric_code']] = r['value']
    if by_date:
        sorted_dates = sorted(by_date.keys())
        latest = by_date[sorted_dates[-1]]
        # 收集全历史用于分位计算
        hist = {m: [] for m in FI_METRICS}
        for d in sorted_dates:
            for m in FI_METRICS:
                v = by_date[d].get(m)
                if v is not None:
                    hist[m].append(v)
        # 计算最新分位（PE/PB/PS 越小越便宜 ascending=True；股息率越高越好 ascending=False）
        def _pct(vals, cv, ascending):
            if len(vals) < 2 or cv is None:
                return None
            sv = sorted(vals, reverse=not ascending)
            try:
                rank = sv.index(cv)
                return round(rank / (len(sv) - 1), 4)
            except ValueError:
                return None
        entry = {'date': sorted_dates[-1]}
        entry['pe_ttm'] = latest.get('pe_ttm')
        entry['pb'] = latest.get('pb')
        entry['ps_ttm'] = latest.get('ps_ttm')
        entry['dyr'] = latest.get('dyr')
        entry['mc'] = latest.get('mc')
        entry['pe_pct'] = _pct(hist['pe_ttm'], latest.get('pe_ttm'), True)
        entry['pb_pct'] = _pct(hist['pb'], latest.get('pb'), True)
        entry['ps_pct'] = _pct(hist['ps_ttm'], latest.get('ps_ttm'), True)
        entry['dyr_pct'] = _pct(hist['dyr'], latest.get('dyr'), False)
        snap['valuation'] = [entry]

    # RS
    snap['rs'] = _one(conn, """SELECT * FROM stock_rs_daily WHERE stock_code=? AND date<=?
        ORDER BY date DESC LIMIT 1""", (code, data_date))

    # CPA 阶段（本项目新增的处境层）
    cpa = _one(conn, """SELECT date, stage, action, days_in_stage, structure_support,
                               invalid_level, metrics_json FROM cpa_stage_daily
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1""", (code, data_date))
    if cpa:
        try:
            m = json.loads(cpa.get('metrics_json') or '{}')
        except Exception:
            m = {}
        cpa.pop('metrics_json', None)
        cpa['six_dir'] = m.get('six_dir')
        cpa['warn_from'] = m.get('warn_from')
        cpa['transition_zone'] = m.get('transition_zone')
        if cpa.get('warn_from'):                  # 还原底层，避免把"预警"演成"已进 ⑥"
            cpa['stage_base'] = cpa['warn_from']
    snap['cpa'] = cpa

    # 近期信号（MW / 口袋支点 / 基部突破）
    snap['signals'] = _rows(conn, """SELECT date, signal_mask, signal_count, combo_label
        FROM signal_events WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 20""", (code, data_date))

    # 形态信号（卖出形态）
    snap['patterns'] = []
    pr = _one(conn, "SELECT signals_json FROM pattern_scan_signals WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1",
              (code, data_date))
    if pr and pr.get('signals_json'):
        try:
            snap['patterns'] = json.loads(pr['signals_json'])[-8:]
        except Exception:
            pass

    # 观察池（CANSLIM 等）
    snap['observation'] = _one(conn, """SELECT * FROM discipline_observation_pool
        WHERE stock_code=? AND date<=? ORDER BY date DESC LIMIT 1""", (code, data_date))
    snap['market'] = _one(conn, "SELECT * FROM market_health_daily ORDER BY date DESC LIMIT 1")
    return snap


def snapshot_to_text(snap):
    """把快照压成给 LLM 的文本。**只列有值的项**，缺的明确写「未获取」。"""
    L = []
    b = snap.get('basic') or {}
    q = snap.get('quote') or {}
    L.append(f"# 数据快照 — {b.get('name') or ''}({snap['code']})  数据日期 {snap.get('data_date')}")
    L.append(f"\n## 行情\n最新 {q.get('date')} 收盘 {q.get('close')} 涨跌 {q.get('change_pct')}")
    L.append(f"250日高/低 {q.get('high_250')} / {q.get('low_250')} · 距高点 {q.get('from_high_pct')} · 250日区间位置 {q.get('pos_250')}")
    if snap.get('ma'):
        L.append("均线 " + " ".join(f"{k.upper()}={v}" for k, v in sorted(snap['ma'].items())))

    cpa = snap.get('cpa')
    if cpa:
        L.append(f"\n## CPA 阶段（处境层）\n{cpa.get('date')} 阶段 {cpa.get('stage')} · 动作「{cpa.get('action')}」"
                 f" · 已 {cpa.get('days_in_stage')} 个交易日"
                 + (f" · 方向 {'反弹' if cpa.get('six_dir') == 'rebound' else '续跌'}" if cpa.get('six_dir') else '')
                 + (f" · 预警中（底层 {cpa.get('warn_from')}）" if cpa.get('warn_from') else ''))
        L.append(f"结构支撑 {cpa.get('structure_support')} · 失效位 {cpa.get('invalid_level')}")
    else:
        L.append("\n## CPA 阶段\n未获取")

    rs = snap.get('rs')
    L.append("\n## 相对强度")
    if rs:
        L.append(f"RPS20 {rs.get('rps_20')} / RPS60 {rs.get('rps_60')} / RPS120 {rs.get('rps_120')} / RPS250 {rs.get('rps_250')}")
    else:
        L.append("未获取")

    if snap.get('signals'):
        L.append("\n## 近期信号（近 20 条）")
        for s in snap['signals'][:10]:
            L.append(f"  {s.get('date')} mask={s.get('signal_mask')} count={s.get('signal_count')} {s.get('combo_label') or ''}")

    if snap.get('patterns'):
        L.append("\n## 形态信号（最近）")
        for p in snap['patterns']:
            L.append(f"  {p.get('signal_date') or p.get('date')} {p.get('pattern')} {p.get('type')}")

    if snap.get('fin_quarterly'):
        L.append("\n## 财务·季度（近 12 期，原始字段名）")
        # 真实列名：revenue_single / net_profit_single / net_profit_margin / gross_margin_single /
        #           roe_single / asset_liability_ratio / interest_bearing_debt_ratio / free_cash_flow ...
        keep_cols = ('report_date', 'revenue_single', 'revenue_yoy', 'net_profit_single',
                     'net_profit_yoy', 'net_profit_adj_single', 'net_profit_margin',
                     'gross_margin_single', 'roe_single', 'free_cash_flow',
                     'asset_liability_ratio', 'interest_bearing_debt_ratio')
        for f in snap['fin_quarterly'][:10]:
            keep = {k: f.get(k) for k in keep_cols if f.get(k) is not None}
            if keep:
                L.append("  " + json.dumps(keep, ensure_ascii=False))
    else:
        L.append("\n## 财务·季度\n未获取")

    if snap.get('fin_annual'):
        L.append("\n## 财务·年度（近 10 期，字段原样）")
        for f in snap['fin_annual'][:6]:
            keep = {k: v for k, v in f.items() if v is not None}
            L.append("  " + json.dumps({k: keep[k] for k in list(keep)[:14]}, ensure_ascii=False))

    if snap.get('valuation') and snap['valuation'][0].get('pe_ttm') is not None:
        L.append("\n## 估值")
        for v in snap['valuation']:
            L.append(f"  {v.get('date')} PE_TTM={v.get('pe_ttm')} PE分位={v.get('pe_pct')} "
                     f"PB={v.get('pb')} PB分位={v.get('pb_pct')} "
                     f"PS={v.get('ps_ttm')} PS分位={v.get('ps_pct')} "
                     f"股息率={v.get('dyr')} DY分位={v.get('dyr_pct')} "
                     f"市值={v.get('mc')}")
    else:
        L.append("\n## 估值\n未获取（fundamental_indicator 无此股票数据）")

    o = snap.get('observation')
    if o:
        L.append("\n## 观察池指标")
        keep = {k: v for k, v in o.items() if v is not None and
                (k.startswith('canslim') or k.startswith('rps') or k in ('rs_category', 'industry_name'))}
        L.append("  " + json.dumps(keep, ensure_ascii=False))

    m = snap.get('market')
    if m:
        L.append(f"\n## 大盘环境\n{m.get('date')} 健康分 {m.get('total_score')} 评级 {m.get('rating')}")

    L.append("\n---\n以上是本系统能提供的**全部**数据。凡未列出的维度，请直接写「未获取」，"
             "禁止用行业均值、同类公司或其他字段推算填充。")
    if _SNAP_ERRORS:
        L.append("\n⚠ 数据层本次有 %d 条查询失败（已注明，非数据缺失）：" % len(_SNAP_ERRORS))
        for e in _SNAP_ERRORS[:5]:
            L.append("  " + e)
    return "\n".join(L)


# ═══════════════════════════════════════════════
# 八大师
# ═══════════════════════════════════════════════
MASTERS = [
    ('欧奈尔', '看买点质量、相对强度(RPS)、量价配合、机构行为(CANSLIM)'),
    ('威科夫', '判断当前处于吸筹/派发/拉升/下跌阶段，并用量价关系验证'),
    ('利弗莫尔', '趋势是否成立、关键点是否突破或失效、是否在顺势方向'),
    ('段永平', '生意是否看得懂、商业模式是否简单可持续、管理层是否本分、能力圈内外'),
    ('巴菲特', '护城河宽窄、估值是否有安全边际、资本配置是否理性'),
    ('芒格', '最大的风险是什么、我可能错在哪里、有无致命缺陷（逆向思考）'),
    ('索罗斯', '趋势自我强化处于早期/中段/后段、共识叙事是否已被价格透支、反身性转向风险'),
    ('Oliver Kell', 'CPA 价格行为循环位置判定（反转/楔形突破/EMA回踩/基部突破/衰竭/破位）+ EMA10/EMA20 多周期关系 + 量价验证 + 风险收益比评估'),
]

MASTER_SYS = """你是一位资深 A 股分析师，本轮只负责以「{name}」的视角独立判断。
纪律（违反即不合格）：
1. 必须给出单向票：「买」/「等」/「回避」三选一，不得弃权、不得给两可答案。
2. 必须附一句**基于数据**的理由，引用具体数字或信号名。
3. 只依据用户提供的快照判断，缺的维度不要猜。
4. 不要给目标价、不要预测收益率。
5. 输出格式严格为两行：第一行只有票名，第二行是理由（80 字以内）。不要任何其他内容。"""


# ═══════════════════════════════════════════════
# 流水线
# ═══════════════════════════════════════════════
ANALYST_SYS = """你是资深 A 股分析师，按「四层体检」框架工作。
铁律：
1. 每个判断必须带数据（具体数字或信号名），禁止「基本面良好」式空话。
2. 数据缺失时明说「未获取」，不许用行业均值或其他字段推算填充。
3. 禁止给目标价、禁止预测收益率。
4. 理解优先：若写不出「一句话业务定位」，直接说明"能力圈外"，不要硬写。
输出 Markdown，章节固定如下，每节简洁但必须带数字：
## 一、否决筛查
## 二、认识它（一句话业务定位 + 收入/毛利结构 + 商业模式类型）
## 三、行业位置（生命周期 + 竞争格局 + 驱动因素）
## 四、财务验证（季度与年度趋势、二阶导、盈利质量、财务健康并对照行业校准）
## 五、定价（多周期分位 + 估值构成拆解，说明现价隐含了什么预期）
## 六、择时（趋势状态、均线排列、关键位、支撑压力）
## 七、情景前瞻（基准/乐观/悲观三情景 + 证伪条件）
"""

HOST_SYS = """你是投资决策的主持人。你拿到：客观机器数据、七位大师的独立投票、以及一份整合分析。
你的任务是**终裁**，不是复述。
铁律：
1. 必须落到「五档评级」（强烈推荐/谨慎推荐/中性/观望/回避）+ 动作清单。
2. 七票清点必须显式写出：买 n / 等 n / 回避 n，并指出分歧集中在哪（技术派 vs 价值派 vs 周期派）。
3. 机器分是底线（基本面差的不得给高评级）；大师票决定上限（非共识买入不得给「强烈推荐」）。
4. 分歧时你要基于证据裁决并**写明理由**，不许和稀泥。
5. 禁止目标价、禁止收益预测。
6. 数据缺口必须标注。
输出 Markdown，章节固定：
## 终裁
**评级**：xxx
**一句话理由**：xxx
**动作清单**：建仓区 / 止损位 / 失效条件 / 下次体检触发点
## 七票清点
## 分歧与裁决理由
## 风险提示与数据缺口
## 时间戳
"""


def _run_job(job_id, code, provider=None):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    t0 = time.time()

    def _stage(s):
        conn.execute("UPDATE deep_analysis_jobs SET stage=?, elapsed_sec=?", (s, round(time.time() - t0, 1)))
        conn.commit()

    try:
        ensure_table(conn)
        data_date = conn.execute("SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]
        conn.execute("UPDATE deep_analysis_jobs SET state='running', data_date=? WHERE job_id=?",
                     (data_date, job_id))
        conn.commit()

        _stage('拉取数据快照')
        snap = build_snapshot(conn, code, data_date)
        ctx = snapshot_to_text(snap)
        name = (snap.get('basic') or {}).get('name')
        conn.execute("UPDATE deep_analysis_jobs SET code_name=? WHERE job_id=?", (name, job_id))
        conn.commit()

        _stage('整合分析（步骤 2-8）')
        analysis = _llm(ANALYST_SYS, ctx, temperature=0.3, timeout=900, provider=provider)

        _stage('八大师独立投票')
        votes = []
        with ThreadPoolExecutor(max_workers=7) as pool:
            futs = {}
            for mname, lens in MASTERS:
                sys_msg = MASTER_SYS.format(name=mname)
                user_msg = f"你的视角重点：{lens}\n\n{ctx}"
                futs[pool.submit(_llm, sys_msg, user_msg, 0.5, 600)] = mname
            for fut in as_completed(futs):
                mname = futs[fut]
                try:
                    txt = (fut.result() or '').strip()
                except Exception as e:
                    txt = f'等\n（该视角调用失败：{type(e).__name__}）'
                lines = [l.strip() for l in txt.split('\n') if l.strip()]
                vote = next((l for l in lines if l in ('买', '等', '回避')), '等')
                reason = next((l for l in lines if l not in ('买', '等', '回避')), '')
                votes.append({'master': mname, 'vote': vote, 'reason': reason[:120]})
        votes.sort(key=lambda v: [m[0] for m in MASTERS].index(v['master']))
        conn.execute("UPDATE deep_analysis_jobs SET votes_json=? WHERE job_id=?",
                     (json.dumps(votes, ensure_ascii=False), job_id))
        conn.commit()

        _stage('主持人裁决')
        tally = {'买': 0, '等': 0, '回避': 0}
        for v in votes:
            tally[v['vote']] = tally.get(v['vote'], 0) + 1
        host_user = (f"## 七票\n" + "\n".join(f"- {v['master']}：{v['vote']} —— {v['reason']}" for v in votes)
                     + f"\n\n票数统计：买 {tally['买']} / 等 {tally['等']} / 回避 {tally['回避']}"
                     + f"\n\n## 整合分析\n{analysis}\n\n## 原始数据快照\n{ctx}")
        verdict = _llm(HOST_SYS, host_user, temperature=0.2, timeout=900)

        # 组装成稿
        md = []
        md.append(f"# {name or ''}（{code}）深度分析")
        md.append(f"\n> 数据日期 {data_date} · 生成于 {time.strftime('%Y-%m-%d %H:%M')} · "
                  f"框架：stock-health-report（十步工作流 + 八大师奇数投票 + 主持人裁决）")
        md.append("\n---\n")
        md.append(verdict)
        md.append("\n---\n## 八大师投票明细\n")
        md.append("| 大师 | 票 | 理由 |\n|---|---|---|")
        for v in votes:
            md.append(f"| {v['master']} | **{v['vote']}** | {v['reason']} |")
        md.append("\n---\n")
        md.append(analysis)
        md.append("\n---\n*本报告由本地数据 + LLM 生成，不构成投资建议。所有数值来自本系统数据库，"
                  "未获取的维度已在文中标注。*")
        result = "\n".join(md)

        conn.execute("""UPDATE deep_analysis_jobs SET state='done', stage='完成', result_md=?,
                        elapsed_sec=?, finished_at=? WHERE job_id=?""",
                     (result, round(time.time() - t0, 1), time.strftime('%Y-%m-%d %H:%M:%S'), job_id))
        conn.commit()
    except Exception as e:
        import traceback
        conn.execute("""UPDATE deep_analysis_jobs SET state='error', error=?,
                        elapsed_sec=?, finished_at=? WHERE job_id=?""",
                     (f'{type(e).__name__}: {str(e)[:300]}\n{traceback.format_exc()[-600:]}',
                      round(time.time() - t0, 1), time.strftime('%Y-%m-%d %H:%M:%S'), job_id))
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════
# 对外接口
# ═══════════════════════════════════════════════
def start_job(code):
    """建 job + 起后台线程。每次都重新生成，不缓存复用。"""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    data_date = conn.execute("SELECT MAX(date) FROM daily_kline_adj").fetchone()[0]

    job_id = uuid.uuid4().hex[:16]
    conn.execute("""INSERT INTO deep_analysis_jobs
        (job_id, code, data_date, state, stage, created_at) VALUES (?,?,?,?,?,?)""",
                 (job_id, code, data_date, 'pending', '排队中', time.strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()

    th = threading.Thread(target=_run_job, args=(job_id, code), daemon=True)
    th.start()
    return {'job_id': job_id}


def get_job(job_id):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)
    r = conn.execute("SELECT * FROM deep_analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
    conn.close()
    if not r:
        return None
    d = dict(r)
    if d.get('votes_json'):
        try:
            d['votes'] = json.loads(d['votes_json'])
        except Exception:
            pass
    d.pop('votes_json', None)
    return d


if __name__ == '__main__':
    code = sys.argv[1] if len(sys.argv) > 1 else '600309'
    res = start_job(code, force=True)
    print('job_id =', res['job_id'])
    jid = res['job_id']
    while True:
        j = get_job(jid)
        print(f"  [{j['state']}] {j.get('stage')}  {j.get('elapsed_sec')}s")
        if j['state'] in ('done', 'error'):
            if j['state'] == 'error':
                print(j.get('error'))
            else:
                print('\n' + (j.get('result_md') or '')[:2000])
            break
        time.sleep(5)
