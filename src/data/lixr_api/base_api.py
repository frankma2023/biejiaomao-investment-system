"""
理杏仁 API 基类

所有 API 管理器继承此类，获得统一的请求、重试、错误处理能力。
"""

import time
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
import requests

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class LixingerBase:
    """理杏仁 API 基类"""

    # 子类必须定义
    API_PATH: str = ""  # 例如 "/company"
    API_NAME: str = ""  # 中文名，用于日志

    def __init__(self, config_path: Optional[str] = None):
        self._load_config(config_path)
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "InvestmentAgent/1.0"
        })

    def _load_config(self, config_path: Optional[str] = None):
        """加载配置文件"""
        if config_path is None:
            config_path = PROJECT_ROOT / "config" / "config.yaml"
        else:
            config_path = Path(config_path)

        with open(config_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        self.token = self.config["LIXINGER_TOKEN"]
        self.base_url = self.config["base_url"]
        self.timeout = self.config["request"]["timeout"]
        self.retry_count = self.config["request"]["retry_count"]
        self.retry_delay = self.config["request"]["retry_delay"]

    def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送 POST 请求，带重试机制。

        Args:
            payload: 请求体（不含 token，自动注入）

        Returns:
            API 返回的 JSON 数据

        Raises:
            Exception: 请求失败或 API 返回错误
        """
        payload["token"] = self.token
        url = f"{self.base_url}{self.API_PATH}"

        for attempt in range(1, self.retry_count + 1):
            try:
                logger.debug(f"[{self.API_NAME}] 请求 {url} (第{attempt}次)")
                resp = self.session.post(url, json=payload, timeout=self.timeout)

                if resp.status_code != 200:
                    raise Exception(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )

                result = resp.json()

                if result.get("code") != 1:
                    raise Exception(
                        f"API 错误: code={result.get('code')}, "
                        f"message={result.get('message')}"
                    )

                logger.debug(
                    f"[{self.API_NAME}] 成功，"
                    f"返回 {len(result.get('data', []))} 条数据"
                )
                return result

            except requests.exceptions.Timeout:
                logger.warning(
                    f"[{self.API_NAME}] 超时 (第{attempt}次)，"
                    f"{self.retry_delay}秒后重试"
                )
            except requests.exceptions.ConnectionError as e:
                logger.warning(
                    f"[{self.API_NAME}] 连接错误 (第{attempt}次): {e}"
                )
            except Exception as e:
                # API 业务错误不重试，直接抛出
                if "API 错误" in str(e):
                    raise
                logger.warning(
                    f"[{self.API_NAME}] 请求异常 (第{attempt}次): {e}"
                )

            if attempt < self.retry_count:
                time.sleep(self.retry_delay)

        raise Exception(
            f"[{self.API_NAME}] 请求失败，已重试 {self.retry_count} 次"
        )

    def _save_raw(self, data: Any, filename: str):
        """保存原始响应到 data/raw/ 目录"""
        raw_dir = PROJECT_ROOT / self.config["paths"]["raw"]
        raw_dir.mkdir(parents=True, exist_ok=True)

        filepath = raw_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.debug(f"[{self.API_NAME}] 原始数据已保存: {filepath}")
