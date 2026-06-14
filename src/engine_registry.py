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

    import src.scanners as pkg

    engines = {}
    # 排除的模块名（辅助模块、私有模块、非引擎模块）
    EXCLUDE = {
        'recommend',          # 建议合成层，不是引擎
        'base_detector',      # 基础工具模块，被其他引擎消费
        '__init__',
        'distribution_day',   # 大盘指数级别引擎，不参与个股扫描
    }

    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        # 跳过私有模块和排除模块
        if name.startswith('_') or name in EXCLUDE:
            continue

        try:
            mod = importlib.import_module(f'src.scanners.{name}')
        except Exception as e:
            print(f"[engine_registry] 跳过 {name}: 导入失败 ({e})")
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
            'version': eng['meta'].get('version', '0.0'),
            'description': eng['meta'].get('description', ''),
        }
        for eng in engines.values()
    ]


def run_all_engines(klines, indicators=None, silent=False, whitelist=None):
    """
    运行全部已发现的引擎。

    Args:
        klines: 用 OHLCV 列构建的 dict 列表或 pd.DataFrame
        indicators: TA-Lib 指标 dict（由框架统一计算后传入）
        silent: True 时不打印错误日志（批量模式）
        whitelist: 可选，只运行指定名称的引擎列表

    Returns:
        all_signals: List[dict]，每条信号已自动注入 source 字段
    """
    import inspect

    engines = discover_engines()
    all_signals = []

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
                # 尝试加载引擎自己的默认参数
                try:
                    mod_params = eng['module'].load_params()
                    kwargs['params'] = mod_params
                except Exception:
                    pass

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
            if not silent:
                print(f"[engine_registry] 引擎 {name} 执行失败: {e}")

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
