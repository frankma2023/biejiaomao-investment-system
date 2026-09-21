#!/usr/bin/env python3
"""
大盘扫描快照计算脚本

每日盘后运行，计算核心信号、指数维度、个股维度数据存入 market_snapshot_daily，
供 market-scan 看板直接读取。所有计算均使用对应 yaml 配置文件中的参数。

用法：python scripts/compute_market_snapshot.py --date 2026-05-12
"""

import sys, os, argparse, sqlite3, json, yaml
from datetime import datetime, date as dt_date

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, "src"))
os.chdir(PROJECT_DIR)

from scripts.common import log as logger
from detectors.distribution_day import detect as detect_dist
from detectors.follow_through_day import detect as detect_ftd
from detectors.accumulation_day import detect as detect_acc
from server import enrich_klines as server_enrich

DB_PATH = os.path.join(PROJECT_DIR, "data", "lixinger.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_tables():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_snapshot_daily (
            date              TEXT PRIMARY KEY,
            dist_30d_count    INTEGER,  dist_30d_dates  TEXT,  dist_detail     TEXT,
            ftd_30d_count     INTEGER,  ftd_30d_dates   TEXT,  ftd_detail      TEXT,
            acc_30d_count     INTEGER,  acc_30d_dates   TEXT,  acc_detail      TEXT,
            crowd_high_count  INTEGER,  crowd_total     INTEGER,
            ad_positive_count INTEGER,  ad_total        INTEGER,  ad_detail    TEXT,
            diverge_count     INTEGER,  diverge_detail  TEXT,
            double_strong     INTEGER,  steady_leader   INTEGER,  burst         INTEGER,
            created_at        TEXT DEFAULT (datetime('now','localtime'))
        );
    """)
    conn.commit()
    conn.close()


def load_yaml_config(signal_type):
    cfg_path = os.path.join(PROJECT_DIR, 'config', 'market', f'{signal_type}.yaml')
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    return {}


def compute(target_date):
    conn = get_db()
    logger.info(f"大盘扫描快照计算 — {target_date}")

    snapshot = {'date': target_date}

    # ── 1. 抛盘日：distribution_day.yaml 配置 ──
    dist_info = _compute_distribution(conn, target_date)
    snapshot.update(dist_info)
    logger.info(f"  抛盘日(30天): {snapshot['dist_30d_count']}个 {snapshot.get('dist_30d_dates','')}")

    # ── 2. 追盘日：follow_through_day.yaml 配置 ──
    snapshot.update(_compute_signal(conn, target_date, 'follow_through_day', 'ftd', detect_ftd))
    logger.info(f"  追盘日(30天): {snapshot['ftd_30d_count']}个 {snapshot.get('ftd_30d_dates','')}")

    # ── 3. 吸筹日：accumulation_day.yaml 配置 ──
    snapshot.update(_compute_signal(conn, target_date, 'accumulation_day', 'acc', detect_acc))
    logger.info(f"  吸筹日(30天): {snapshot['acc_30d_count']}个 {snapshot.get('acc_30d_dates','')}")

    # ── 4. 指数拥挤度 ──
    cdate_row = conn.execute("SELECT MAX(date) as d FROM index_crowding_daily WHERE date<=?", (target_date,)).fetchone()
    if cdate_row and cdate_row['d']:
        cdate = cdate_row['d']
        cr = conn.execute("SELECT COUNT(*) as c FROM index_crowding_daily WHERE date=? AND composite_score>=70", (cdate,)).fetchone()
        ct = conn.execute("SELECT COUNT(*) as c FROM index_crowding_daily WHERE date=?", (cdate,)).fetchone()
        snapshot['crowd_high_count'] = cr['c'] if cr else 0
        snapshot['crowd_total'] = ct['c'] if ct else 0
    else:
        snapshot['crowd_high_count'] = snapshot['crowd_total'] = 0
    logger.info(f"  拥挤度≥70: {snapshot['crowd_high_count']}/{snapshot['crowd_total']}")

    # ── 5. 机构吸筹出货 ──
    ad_info = _compute_ad(conn, target_date)
    snapshot.update(ad_info)
    logger.info(f"  机构AD: 正向{snapshot['ad_positive_count']}/{snapshot['ad_total']}")

    # ── 6. 指数背离 ──
    div_info = _compute_divergence(conn, target_date)
    snapshot.update(div_info)
    logger.info(f"  指数背离: {snapshot['diverge_count']}个")

    # ── 5. 个股RS ──
    rd = conn.execute("SELECT MAX(date) as d FROM stock_rs_daily WHERE date<=?", (target_date,)).fetchone()
    rs_date = rd['d'] if rd else ''
    if rs_date:
        dbl = conn.execute("SELECT COUNT(*) as c FROM stock_rs_daily WHERE date=? AND rps_250>=90 AND rps_20>=95", (rs_date,)).fetchone()
        steady = conn.execute("SELECT COUNT(*) as c FROM stock_rs_daily WHERE date=? AND rps_250>=95", (rs_date,)).fetchone()
        burst = conn.execute("SELECT COUNT(*) as c FROM stock_rs_daily WHERE date=? AND rps_20>=95 AND rps_250<95", (rs_date,)).fetchone()
        snapshot['double_strong'] = dbl['c'] if dbl else 0
        snapshot['steady_leader'] = steady['c'] if steady else 0
        snapshot['burst'] = burst['c'] if burst else 0
    else:
        snapshot['double_strong'] = snapshot['steady_leader'] = snapshot['burst'] = 0
    logger.info(f"  个股: 双强{snapshot['double_strong']} 龙头{snapshot['steady_leader']} 爆发{snapshot['burst']}")

    # ── 写入 ──
    conn.execute("""
        INSERT OR REPLACE INTO market_snapshot_daily
        (date, dist_30d_count, dist_30d_dates, dist_detail,
         ftd_30d_count, ftd_30d_dates, ftd_detail,
         acc_30d_count, acc_30d_dates, acc_detail,
         crowd_high_count, crowd_total,
         ad_positive_count, ad_total, ad_detail,
         diverge_count, diverge_detail,
         double_strong, steady_leader, burst)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        target_date,
        snapshot['dist_30d_count'], snapshot.get('dist_30d_dates',''), snapshot.get('dist_detail',''),
        snapshot['ftd_30d_count'], snapshot.get('ftd_30d_dates',''), snapshot.get('ftd_detail',''),
        snapshot['acc_30d_count'], snapshot.get('acc_30d_dates',''), snapshot.get('acc_detail',''),
        snapshot['crowd_high_count'], snapshot['crowd_total'],
        snapshot['ad_positive_count'], snapshot['ad_total'], snapshot.get('ad_detail',''),
        snapshot['diverge_count'], snapshot.get('diverge_detail',''),
        snapshot['double_strong'], snapshot['steady_leader'], snapshot['burst'],
    ))
    conn.commit()
    conn.close()
    logger.info(f"  [OK] 快照已保存")


def _compute_distribution(conn, target_date):
    """用 distribution_day.yaml 配置检测近30天抛盘日"""
    config = load_yaml_config('distribution_day')
    rows = conn.execute("""
        SELECT date, open, high, low, close, volume, amount, change
        FROM index_daily_kline WHERE stock_code='000985' AND kline_type='normal'
        AND date >= date(?, '-60 days') AND date <= ? ORDER BY date
    """, (target_date, target_date)).fetchall()

    if len(rows) < 10:
        return {'dist_30d_count': 0, 'dist_30d_dates': '', 'dist_detail': ''}

    klines_raw = [dict(r) for r in rows]
    for k in klines_raw: k['stock_code'] = '000985'
    klines = server_enrich(klines_raw)
    sigs = detect_dist(klines, config)

    recent = [s for s in sigs if s['date'] >= klines[-30]['date']] if len(klines) > 30 else sigs
    dates = sorted(list({s['date'] for s in recent}))
    cnt = len(dates)
    dates_str = ', '.join(dates) if dates else ''

    if cnt >= 5:
        level = 'danger'; desc = f'近30天{cnt}个抛盘日，市场面临持续抛压。建议大幅降低仓位，暂停新开仓。'
    elif cnt >= 3:
        level = 'warning'; desc = f'近30天{cnt}个抛盘日，市场存在一定抛压。注意持仓品种是否放量破位。'
    elif cnt >= 1:
        level = 'ok'; desc = f'近30天{cnt}个抛盘日，属于正常范围，可继续操作。'
    else:
        level = 'ok'; desc = '近30天无抛盘日信号，市场抛压极轻，环境健康。'

    return {'dist_30d_count': cnt, 'dist_30d_dates': dates_str, 'dist_detail': desc, 'dist_level': level}


def _compute_signal(conn, target_date, signal_type, prefix, detector):
    """通用信号检测：加载对应 yaml 配置，检测30天内信号"""
    config = load_yaml_config(signal_type)
    if not config:
        return {f'{prefix}_30d_count': 0, f'{prefix}_30d_dates': '', f'{prefix}_detail': ''}

    rows = conn.execute("""
        SELECT date, open, high, low, close, volume, amount, change
        FROM index_daily_kline WHERE stock_code='000985' AND kline_type='normal'
        AND date >= date(?, '-120 days') AND date <= ? ORDER BY date
    """, (target_date, target_date)).fetchall()

    if len(rows) < 60:
        return {f'{prefix}_30d_count': 0, f'{prefix}_30d_dates': '', f'{prefix}_detail': ''}

    klines_raw = [dict(r) for r in rows]
    for k in klines_raw: k['stock_code'] = '000985'
    klines = server_enrich(klines_raw)

    klines_60d = [k for k in klines if k['date'] >= klines[-60]['date']]
    try:
        dist_sigs = detect_dist(klines_60d, config) if klines_60d else []
    except:
        dist_sigs = []

    if prefix == 'ftd':
        _, signals, _ = detector(klines, config, dist_sigs)
    else:
        _, signals = detector(klines, config, dist_sigs)

    recent = [s for s in signals if s['date'] >= klines[-30]['date']] if len(klines) > 30 else []
    dates = sorted(list({s['date'] for s in recent}))
    cnt = len(dates)
    dates_str = ', '.join(dates) if dates else ''

    if prefix == 'ftd':
        detail = f'近30天{cnt}个追盘日({dates_str})。追盘日确认反弹，可入场。' if cnt else '近30天无追盘日。等待反弹确认再增加仓位。'
    else:
        detail = f'近30天{cnt}个吸筹日({dates_str})。机构强力介入，可积极操作。' if cnt else '近30天无吸筹日。条件比追盘日更严苛，出现时信号更强。'

    return {f'{prefix}_30d_count': cnt, f'{prefix}_30d_dates': dates_str, f'{prefix}_detail': detail}


def _compute_ad(conn, target_date):
    """机构吸筹/出货评级：使用 index_style.yaml 池定义"""
    try:
        from detectors.index_ad import detect as detect_index_ad

        # 读取 index_style.yaml 的 L1 池
        with open(os.path.join(PROJECT_DIR, 'config', 'index_style.yaml'), encoding='utf-8') as f:
            idx_cfg = yaml.safe_load(f)
        l1_codes = [item['code'] for item in idx_cfg.get('categories', {}).get('sector_l1', [])]
        l2_codes = [item['code'] for item in idx_cfg.get('categories', {}).get('sector_l2', [])]
        all_codes = l1_codes + l2_codes[:10]  # L1全部 + L2前10个

        # 加载 ad 配置
        ad_cfg = load_yaml_config('index_ad') or {}
        method = 'raw'  # 快照用 raw 方法，与回测看板默认一致
        window_days = ad_cfg.get('window_days', 65)
        lookback = 500 if method == 'zscore' else window_days * 2 + 30

        # 查询K线
        ph = ','.join(['?' for _ in all_codes])
        rows = conn.execute(f"""
            SELECT stock_code, date, open, high, low, close, volume, amount, change
            FROM index_daily_kline WHERE kline_type='normal'
            AND stock_code IN ({ph})
            AND date >= date(?, '-{lookback} days')
            ORDER BY stock_code, date
        """, all_codes + [target_date]).fetchall()

        pool_klines = {}
        for r in rows:
            code = r['stock_code']
            if code not in pool_klines:
                pool_klines[code] = []
            pool_klines[code].append(dict(r))

        pools = {'sector_l1': l1_codes, 'sector_l2_select': l2_codes[:10]}
        result = detect_index_ad(pool_klines, pools, target_date, window_days, method)

        positive = 0; total = 0
        for pdata in result.get('pools', {}).values():
            for item in pdata.get('rankings', []):
                r = item.get('rating', '')
                if r:
                    total += 1
                    if r[0] in ('A', 'B'):
                        positive += 1

        if total == 0:
            return {'ad_positive_count': 0, 'ad_total': 0, 'ad_detail': '暂无AD评级数据'}

        pct = round(positive / total * 100)
        desc = f'{positive}/{total}个指数AD评级为正(A/B)，' + (
            '机构资金积极流入，市场支撑强。' if pct >= 60 else
            '机构资金中性偏多，可适度参与。' if pct >= 30 else
            '机构资金偏向流出，谨慎操作。')
        return {'ad_positive_count': positive, 'ad_total': total, 'ad_detail': desc}

    except Exception as e:
        logger.info(f"  AD计算异常: {e}")
        return {'ad_positive_count': -1, 'ad_total': -1,
                'ad_detail': f'AD计算异常: {str(e)[:60]}'}


def _compute_divergence(conn, target_date):
    """指数背离检测：量价/RSI/MACD背离共振"""
    try:
        from detectors.divergence import (detect_volume_price_divergence,
            detect_rsi_divergence, detect_macd_divergence,
            confirm_divergence, compute_resonance)

        with open(os.path.join(PROJECT_DIR, 'config', 'index_style.yaml'), encoding='utf-8') as f:
            idx_cfg = yaml.safe_load(f)
        codes = [item['code'] for item in idx_cfg.get('categories', {}).get('sector_l1', [])]

        cfg = load_yaml_config('divergence') or {}
        vp_cfg = cfg.get('volume_price', {})
        rsi_cfg = cfg.get('rsi', {})
        macd_cfg = cfg.get('macd', {})
        confirm_window = cfg.get('confirm_window', 20)

        count = 0
        for code in codes:
            rows = conn.execute("""
                SELECT date, open, high, low, close, volume, amount
                FROM index_daily_kline WHERE stock_code=? AND kline_type='normal'
                AND date <= ? ORDER BY date DESC LIMIT 300
            """, (code, target_date)).fetchall()
            if len(rows) < 100:
                continue

            klines = [dict(r) for r in reversed(rows)]
            # 找到 target_date 对应的索引
            as_of_idx = None
            for i, k in enumerate(klines):
                if k['date'] <= target_date:
                    as_of_idx = i
            if as_of_idx is None or as_of_idx < 50:
                continue

            div_vp = detect_volume_price_divergence(klines, vp_cfg, as_of_idx)
            div_rsi = detect_rsi_divergence(klines, rsi_cfg, as_of_idx)
            div_macd = detect_macd_divergence(klines, macd_cfg, as_of_idx)

            div_vp = confirm_divergence(klines, as_of_idx, div_vp, confirm_window)
            div_rsi = confirm_divergence(klines, as_of_idx, div_rsi, confirm_window)
            div_macd = confirm_divergence(klines, as_of_idx, div_macd, confirm_window)

            divergences = {'vp': div_vp, 'rsi': div_rsi, 'macd': div_macd, 'breadth': None}
            level, _ = compute_resonance(divergences, None, None, None)

            if level and level not in ('无信号',):
                count += 1

        if count == 0:
            desc = '当前无指数出现背离共振，市场趋势一致性强。'
        elif count <= 2:
            desc = f'{count}个指数出现背离共振，关注趋势转折可能。'
        else:
            desc = f'{count}个指数出现背离共振，多项指标预警，建议降低仓位。'
        return {'diverge_count': count, 'diverge_detail': desc}

    except Exception as e:
        logger.info(f"  背离计算异常: {e}")
        return {'diverge_count': -1, 'diverge_detail': f'计算异常: {str(e)[:60]}'}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大盘扫描快照计算")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()
    target = args.date or dt_date.today().strftime("%Y-%m-%d")
    ensure_tables()
    compute(target)
