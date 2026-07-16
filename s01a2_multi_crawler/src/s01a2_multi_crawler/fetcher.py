# fetcher.py：统一 HTTP 层——curl_cffi Chrome 指纹、按站限速、状态码分级策略（CLAUDE.md 规则5）
import logging
import time

from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)


class SiteBlockedError(Exception):
    """站点返回 403：记录并终止该站本轮抓取（防止触发对方封禁升级）"""


class Fetcher:
    """单站点的限速 HTTP 客户端；同站请求间隔 >= rate_limit_seconds"""

    def __init__(self, site_name: str, rate_limit_seconds: float = 3.0, timeout: int = 25):
        self.site_name = site_name
        self.rate_limit = rate_limit_seconds
        self.timeout = timeout
        self._last_request_ts = 0.0
        self._session = cffi_requests.Session(impersonate="chrome")

    def _throttle(self) -> None:
        """保证与上一次请求的最小间隔"""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_ts = time.monotonic()

    def get(self, url: str, headers: dict | None = None, params: dict | None = None) -> cffi_requests.Response | None:
        """GET 一个 URL。返回 Response（200）或 None（404/重试耗尽）；403 抛 SiteBlockedError"""
        backoffs = [5, 25]  # 5xx/连接错误的指数退避
        attempt = 0
        while True:
            self._throttle()
            try:
                resp = self._session.get(url, headers=headers or {}, params=params, timeout=self.timeout)
            except cffi_requests.exceptions.RequestException as e:
                if attempt < len(backoffs):
                    logger.warning("[%s] 连接错误 %s，%ds 后重试: %s", self.site_name, e, backoffs[attempt], url)
                    time.sleep(backoffs[attempt])
                    attempt += 1
                    continue
                logger.error("[%s] 连接错误重试耗尽: %s (%s)", self.site_name, url, e)
                return None

            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                logger.warning("[%s] 404 跳过: %s", self.site_name, url)
                return None
            if resp.status_code == 403:
                logger.error("[%s] 403 被拒，终止该站本轮抓取: %s", self.site_name, url)
                raise SiteBlockedError(url)
            if resp.status_code == 429:
                logger.warning("[%s] 429 限流，等待 30s 重试: %s", self.site_name, url)
                time.sleep(30)
                if attempt < 1:  # 429 只重试一次
                    attempt += 1
                    continue
                return None
            if 500 <= resp.status_code < 600 and attempt < len(backoffs):
                logger.warning("[%s] %d，%ds 后重试: %s", self.site_name, resp.status_code, backoffs[attempt], url)
                time.sleep(backoffs[attempt])
                attempt += 1
                continue
            logger.error("[%s] 未处理状态码 %d: %s", self.site_name, resp.status_code, url)
            return None

    def close(self) -> None:
        """接口占位，与 HeadlessFetcher 对齐（curl_cffi session 无需显式关闭）"""
        pass
