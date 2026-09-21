# -*- coding: utf-8 -*-
"""T4 周线 CPA 全量重算薄壳（交付用户人工执行，交接文档 §7 T4）

前置：chanlun_weekly_bi_json 必须先全市场回填（数据层）：
  python scripts/backfill_chanlun_weekly.py --start 2016-01-01 --workers 8 --purge
本脚本预检覆盖率，不足时拒绝执行（防 CPA 在缺笔顶快照的状态下跑出半残数据）。

用法:
  python scripts/backfill_cpa_weekly.py                # 预检 + 全量（purge）
  python scripts/backfill_cpa_weekly.py --workers 8    # 指定进程数
  python scripts/backfill_cpa_weekly.py --start 2018-01-01
"""
import sys, os, sqlite3, argparse

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT, 'src'))
sys.path.insert(0, os.path.join(PROJECT, 'src', 'scanners'))

DB = os.path.join(PROJECT, 'data', 'lixinger.db')


def precheck(conn, min_ratio=0.9):
    """周线笔快照覆盖率预检：股票池中有周线快照的占比。

    池与数据层回填同源（POOL_SQL 流动性池）——日线引擎大池（5971）里
    有 1800+ 只无流动性/已退市，数据层根本不给它们回填快照，
    分母用大池会永远拦住合法的全量重算。
    """
    import scanners.cpa_stage_weekly as wk
    from backfill_chanlun_weekly import POOL_SQL
    codes = [r[0] for r in conn.execute(POOL_SQL).fetchall()]
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM chanlun_weekly_bi_json")}
    covered = [c for c in codes if c in have]
    ratio = len(covered) / len(codes) if codes else 0
    print('预检: 流动性池 %d 只 | 已有周线笔快照 %d 只 | 覆盖率 %.1f%%' % (
        len(codes), len(covered), ratio * 100))
    if ratio < min_ratio:
        print('覆盖率不足 %d%%，拒绝执行。先跑数据层回填：' % (min_ratio * 100))
        print('  python scripts/backfill_chanlun_weekly.py --start 2016-01-01 --workers 8')
        return None
    # 只重算有快照的股票（无快照的写了也是半残数据）
    return covered


def main():
    ap = argparse.ArgumentParser(description='周线 CPA 全量重算（T4 薄壳）')
    ap.add_argument('--start', default='2016-01-01')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--no-purge', action='store_true', help='追加模式（默认 purge 全量重建）')
    ap.add_argument('--skip-precheck', action='store_true', help='跳过覆盖率预检（调试用）')
    a = ap.parse_args()

    import scanners.cpa_stage_weekly as wk
    conn = sqlite3.connect(DB, timeout=30)
    if not a.skip_precheck:
        codes = precheck(conn)
        conn.close()
        if codes is None:
            sys.exit(1)
        wk.backfill(start=a.start, workers=a.workers, purge=not a.no_purge, codes=codes)
    else:
        conn.close()
        wk.backfill(start=a.start, workers=a.workers, purge=not a.no_purge)


if __name__ == '__main__':
    main()
