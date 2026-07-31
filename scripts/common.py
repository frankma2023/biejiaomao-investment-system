"""
scripts/common.py — 数据拉取脚本共享模块

提供：Token 加载、数据库连接、API 请求、限流器、股票列表。
"""

import os
import re
import time
import sqlite3
import threading
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── 路径 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "lixinger.db"
ENV_PATH = Path.home() / ".hermes" / ".env"

# ── 日志 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scripts")

# ════════════════════════════════════════════════════════
# Token 加载
# ════════════════════════════════════════════════════════

def load_token() -> str:
    """从 ~/.hermes/.env 读取 LIXINGER_TOKEN"""
    if not ENV_PATH.exists():
        raise FileNotFoundError(f"未找到 .env 文件: {ENV_PATH}")
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("LIXINGER_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ValueError(f"LIXINGER_TOKEN 未在 {ENV_PATH} 中配置")


# ════════════════════════════════════════════════════════
# API 配置
# ════════════════════════════════════════════════════════

BASE_URL = "https://open.lixinger.com/api/cn"
DEFAULT_TIMEOUT = 60  # 秒，10年33指标查询偶尔超过30s


# ════════════════════════════════════════════════════════
# 限流器
# ════════════════════════════════════════════════════════

class RateLimiter:
    """线程安全的请求频率控制 — 默认每 2 秒最多 5 次"""

    def __init__(self, max_requests: int = 5, window: float = 2.0):
        self.max_requests = max_requests
        self.window = window
        self.timestamps: List[float] = []
        self.total_requests = 0
        self._lock = threading.Lock()

    def wait(self):
        """等待直到可以发送下一个请求（线程安全）"""
        with self._lock:
            now = time.time()
            # 清理窗口外的记录
            self.timestamps = [t for t in self.timestamps if now - t < self.window]
            if len(self.timestamps) >= self.max_requests:
                sleep_time = self.window - (now - self.timestamps[0]) + 0.1
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    now = time.time()
                    self.timestamps = [t for t in self.timestamps if now - t < self.window]
            self.timestamps.append(now)
            self.total_requests += 1


# ════════════════════════════════════════════════════════
# API 请求
# ════════════════════════════════════════════════════════

# 全局共享（模块级，保证所有脚本共用一个 limiter 时行为一致）
_token: Optional[str] = None
_limiter = RateLimiter(max_requests=10, window=2.0)
_session: Optional[requests.Session] = None
_session_lock = threading.Lock()

# 超时退避序列（秒）：第1次超时等5s，第2次等10s，第3次等15s
TIMEOUT_BACKOFF = [5, 10, 15]


def get_token() -> str:
    global _token
    if _token is None:
        _token = load_token()
    return _token


def get_session() -> requests.Session:
    """获取或创建带连接池的 HTTP Session（线程安全）"""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                _session = requests.Session()
                # 连接池：20 连接，适配高并发
                adapter = HTTPAdapter(
                    pool_connections=20,
                    pool_maxsize=20,
                    max_retries=Retry(total=0),  # 我们自己控制重试
                )
                _session.mount("https://", adapter)
                _session.mount("http://", adapter)
    return _session


def api_post(path: str, payload: Dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    发送 POST 请求到理杏仁 API，自动注入 token 和限流。

    Args:
        path: API 路径，如 "/company/candlestick"
        payload: 请求体（不含 token）
        timeout: 超时秒数（默认 60s）

    Returns:
        API 响应的 data 字段（列表）

    Raises:
        RuntimeError: 请求失败或 API 返回错误
    """
    _limiter.wait()

    payload["token"] = get_token()
    url = f"{BASE_URL}{path}"
    session = get_session()

    for attempt in range(3):
        try:
            resp = session.post(url, json=payload, timeout=timeout)
            if resp.status_code == 429:
                wait_s = [5, 15, 30][attempt] if attempt < 3 else 60
                log.warning(f"触发 429 限流，等待 {wait_s} 秒后重试...")
                time.sleep(wait_s)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            result = resp.json()
            if result.get("code") != 1:
                raise RuntimeError(f"API 错误: code={result.get('code')}, msg={result.get('message')}")
            return result.get("data", [])
        except requests.exceptions.Timeout:
            if attempt < len(TIMEOUT_BACKOFF):
                wait_s = TIMEOUT_BACKOFF[attempt]
                log.warning(f"请求超时 (第 {attempt+1} 次)，等待 {wait_s}s 后重试...")
                time.sleep(wait_s)
            else:
                log.warning(f"请求超时 (第 {attempt+1} 次，已达上限)")
        except requests.exceptions.ConnectionError as e:
            wait_s = 2 * (attempt + 1)  # 2s, 4s, 6s
            log.warning(f"连接错误 (第 {attempt+1} 次): {e}，等待 {wait_s}s...")
            time.sleep(wait_s)
        except RuntimeError:
            raise

    raise RuntimeError(f"API 请求失败（3次重试均失败）: {url}")


# ════════════════════════════════════════════════════════
# 数据库
# ════════════════════════════════════════════════════════

def get_db() -> sqlite3.Connection:
    """获取 lixinger.db 连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def get_all_stock_codes(conn: sqlite3.Connection) -> List[str]:
    """从 stock_basic 表获取所有正常交易状态的股票代码"""
    rows = conn.execute(
        "SELECT stock_code FROM stock_basic "
        "WHERE listing_status IN ('normally_listed', 'special_treatment', 'delisting_risk_warning') "
        "ORDER BY stock_code"
    ).fetchall()
    return [r["stock_code"] for r in rows]


def get_latest_date(conn: sqlite3.Connection, table: str, stock_code: Optional[str] = None) -> Optional[str]:
    """获取某表的最新数据日期"""
    if stock_code:
        row = conn.execute(
            f"SELECT MAX(date) as d FROM {table} WHERE stock_code = ?", (stock_code,)
        ).fetchone()
    else:
        row = conn.execute(f"SELECT MAX(date) as d FROM {table}").fetchone()
    return row["d"] if row and row["d"] else None


def ensure_tables(conn: sqlite3.Connection, schema_sql: str):
    """执行建表语句"""
    conn.executescript(schema_sql)
    conn.commit()
