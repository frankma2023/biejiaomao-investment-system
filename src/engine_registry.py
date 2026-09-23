"""
引擎自动发现模块

约定优于配置：扫描 src/scanners/ 目录，凡是实现了 detect() 函数
且声明了 ENGINE_META 字典的 .py 文件，即为合法引擎。

用法:
    from src.engine_registry import discover_engines
    engines = discover_engines()
    for name, eng in engines.items():
        signals = eng['detect'](klines=df, indicators=indicators)
"""

import importlib
import pkgutil
import sys
import os

# 确保项目根在 path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# 缓存：进程生命周期内引擎列表不变
_engine_cache = None

# 引擎失败记录。静默跳过会让「整类信号消失」表现为「今天没有信号」，
# 因此每次失败都留痕，并可由调用方通过 get_import_failures() / get_run_failures() 读走。
_import_failures = []
_run_failures = []

# 已告警过的 (引擎名, 阶段)。批量模式下同一失败只打印一次以免逐股刷屏，
# 同时保证失败本身不会因为 silent=True 而被完全吞掉。
_warned_signatures = set()


def _warn_once(signature, message):
    """
    按 signature 在进程内只打印一次告警，避免批量模式下逐股刷屏。

    Args:
        signature: 去重键，通常为 (引擎名, 阶段)。
        message: 告警正文。

    Returns:
        None
    """
    if signature not in _warned_signatures:
        _warned_signatures.add(signature)
        print(message)


def _record_failure(store, name, stage, error):
    """
    记录一次引擎失败，并对其首次出现打印告警。

    Args:
        store: 接收记录的列表。
        name: 引擎名。
        stage: 失败阶段，import / load_params / detect 之一。
        error: 异常文本。

    Returns:
        None
    """
    store.append({'name': name, 'stage': stage, 'error': error})
    # 前缀保持 ASCII：控制台为 GBK 时 emoji 会抛 UnicodeEncodeError
    _warn_once((name, stage),
               f"[engine_registry] [FAIL] 引擎 {name} 在 {stage} 阶段失败，本轮不产出信号"
               f"（同类告警不再重复）: {error}")


def discover_engines(force_reload=False):
    """
    扫描 src/scanners/ 目录，返回所有合法引擎。

    Args:
        force_reload: 强制重新扫描（默认使用缓存）

    Returns:
        dict[name] = {
            'module': module,
            'meta': ENGINE_META dict,
            'detect': detect 函数引用
        }
    """
    global _engine_cache
    if _engine_cache is not None and not force_reload:
        return _engine_cache

    _import_failures.clear()
    import src.scanners as pkg

    engines = {}
    # 排除的模块名（辅助模块、私有模块、非引擎模块）
    EXCLUDE = {
        'recommend',          # 建议合成层，不是引擎
        'base_detector',      # 基础工具模块，被其他引擎消费
        '__init__',
        'distribution_day',   # 大盘指数级别引擎，不参与个股扫描
        # v1 杯柄：买点取自包含检测日自身的窗口最大值，而突破判定又要求检测日收盘
        # 超过该值（x >= x + 0.01），数学上不可能产出信号。已由 cup_handle_v2 取代。
        'cup_handle',
    }

    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        # 跳过私有模块和排除模块
        if name.startswith('_') or name in EXCLUDE:
            continue

        try:
            mod = importlib.import_module(f'src.scanners.{name}')
        except Exception as e:
            # 引擎文件存在却导入失败：静默排除会让该类信号整体消失，
            # 上游只看到「今天没信号」，因此留痕并始终告警。
            _record_failure(_import_failures, name, 'import', repr(e))
            continue

        # 检查引擎契约：必须有 detect() 函数
        if not hasattr(mod, 'detect'):
            continue

        # 获取元信息（缺失时给默认值，不拒绝）
        meta = getattr(mod, 'ENGINE_META', {
            'name': name,
            'display_name': name,
            'category': 'misc',
            'version': '0.0',
            'description': ''
        })

        engines[meta['name']] = {
            'module': mod,
            'meta': meta,
            'detect': mod.detect,
        }

    _engine_cache = engines
    return engines


def get_engine_list():
    """返回引擎元信息列表，供 API 响应的 engines 字段使用"""
    engines = discover_engines()
    return [
        {
            'name': eng['meta']['name'],
            'display_name': eng['meta']['display_name'],
            'category': eng['meta'].get('category', 'misc'),
            'type': eng['meta'].get('type', ''),
            'pattern': eng['meta'].get('pattern', ''),
            'version': eng['meta'].get('version', '0.0'),
            'description': eng['meta'].get('description', ''),
        }
        for eng in engines.values()
    ]


def get_import_failures():
    """
    返回上一次引擎扫描中导入失败的引擎记录。

    Returns:
        List[dict]，每项含 name 与 error；空列表表示全部引擎导入成功。
    """
    return list(_import_failures)


def get_run_failures():
    """
    返回最近一次 run_all_engines() 中未产出信号的引擎记录。

    Returns:
        List[dict]，每项含 name、stage、error；空列表表示本轮全部引擎正常。
    """
    return list(_run_failures)


def run_all_engines(klines, indicators=None, silent=False, whitelist=None):
    """
    运行全部已发现的引擎。

    Args:
        klines: 用 OHLCV 列构建的 dict 列表或 pd.DataFrame
        indicators: TA-Lib 指标 dict（由框架统一计算后传入）
        silent: True 时抑制逐次调用的失败明细。引擎失败不会被完全静默：
            每个 (引擎名, 阶段) 在进程内首次失败时必定告警一次，
            完整记录另可通过 get_run_failures() 读取。
        whitelist: 可选，只运行指定名称的引擎列表

    Returns:
        all_signals: List[dict]，每条信号已自动注入 source 字段
    """
    import inspect

    engines = discover_engines()
    all_signals = []
    _run_failures.clear()

    for name, eng in engines.items():
        if whitelist and name not in whitelist:
            continue
        try:
            # 根据函数签名智能传参
            sig = inspect.signature(eng['detect'])
            params = sig.parameters
            kwargs = {}

            if 'klines' in params:
                kwargs['klines'] = klines
            if 'daily' in params:
                kwargs['daily'] = klines
            if 'daily_klines' in params:
                kwargs['daily_klines'] = klines
            if 'indicators' in params and indicators is not None:
                kwargs['indicators'] = indicators
            if 'params' in params:
                # 引擎未提供 load_params()（或它抛错）时分两种情形：
                #   params 有默认值 → 省略即可，由引擎自身的默认逻辑接管；
                #   params 无默认值 → 少传会让 detect() 抛 TypeError，并被下方
                #   宽 except 吞成「该引擎没有信号」，必须显式跳过并留痕。
                try:
                    kwargs['params'] = eng['module'].load_params()
                except Exception as e:
                    if params['params'].default is inspect.Parameter.empty:
                        _record_failure(_run_failures, name, 'load_params', repr(e))
                        continue
                    _warn_once((name, 'load_params-optional'),
                               f"[engine_registry] [WARN] 引擎 {name} 未取得 load_params()，"
                               f"沿用 detect() 默认参数（引擎规范要求提供 load_params()）: {e}")

            if 'stock_code' in params and klines and isinstance(klines[0], dict):
                sc = klines[0].get('stock_code')
                if sc:
                    kwargs['stock_code'] = sc
                else:
                    kwargs['stock_code'] = ''

            raw_signals = eng['detect'](**kwargs)
            # 引擎可能返回 dict（含 signals 键）或直接返回 list
            if isinstance(raw_signals, dict):
                raw_signals = raw_signals.get('signals', raw_signals.get('daily', []))
            if not raw_signals:
                continue
            for sig_item in raw_signals:
                sig_item['source'] = name  # 框架自动注入 source
                # 归一化日期字段：有的引擎用 signal_date，统一补 date
                if 'date' not in sig_item and 'signal_date' in sig_item:
                    sig_item['date'] = sig_item['signal_date']
                all_signals.append(sig_item)
        except Exception as e:
            _record_failure(_run_failures, name, 'detect', repr(e))

    return all_signals


if __name__ == '__main__':
    # 快速自检
    engines = discover_engines()
    print(f"发现 {len(engines)} 个引擎:")
    for name, eng in engines.items():
        meta = eng['meta']
        print(f"  [{meta['category']:12s}] {name:20s} → {meta['display_name']}")
    print(f"\n引擎列表 (供 API):")
    for e in get_engine_list():
        print(f"  {e['name']}: {e['category']} v{e['version']}")
