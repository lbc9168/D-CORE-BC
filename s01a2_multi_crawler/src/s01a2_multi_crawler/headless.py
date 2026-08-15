# headless.py：Playwright 隐身渲染 HTTP 层（过瑞数类 JS 反爬墙），接口(.get/.close)兼容 Fetcher
import logging
import time
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from .fetcher import SiteBlockedError

logger = logging.getLogger(__name__)

# 隐身脚本：抹掉 navigator.webdriver 等自动化破绽，让瑞数把我们当真实 Chrome
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
window.chrome = {runtime: {}};
"""

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"


@dataclass
class RenderedResponse:
    """模拟 requests.Response 的最小接口（.text/.status_code），供 discover/article 复用"""

    text: str
    status_code: int


class HeadlessFetcher:
    """Playwright 隐身 headless 渲染；一个实例持有一个浏览器，接口兼容 Fetcher（.get/.close）"""

    def __init__(self, site_name: str, rate_limit_seconds: float = 3.0, timeout: int = 30, render_wait_ms: int = 4000):
        self.site_name = site_name
        self.rate_limit = rate_limit_seconds
        self.timeout_ms = timeout * 1000
        self.render_wait = render_wait_ms
        self._last_ts = 0.0
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        self._ctx = self._browser.new_context(
            user_agent=_UA, locale="zh-CN", viewport={"width": 1280, "height": 800}
        )
        self._ctx.add_init_script(_STEALTH_JS)

    def _throttle(self) -> None:
        """同站请求最小间隔"""
        elapsed = time.monotonic() - self._last_ts
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_ts = time.monotonic()

    def get(self, url: str, headers: dict | None = None, params: dict | None = None) -> RenderedResponse | None:
        """渲染一个 URL 返回 RenderedResponse(200) 或 None；403 抛 SiteBlockedError，未过验证墙记 warning 返回 None"""
        self._throttle()
        page = self._ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(self.render_wait)  # 给瑞数 JS 挑战与内容渲染时间
            status = resp.status if resp else 200
            if status == 403:
                logger.error("[%s] 403 被拒，终止该站: %s", self.site_name, url)
                raise SiteBlockedError(url)
            if status == 404:
                logger.warning("[%s] 404 跳过: %s", self.site_name, url)
                return None
            title = page.title().strip()
            if "验证" in title or title == "Verification":  # 瑞数墙标题变体：访问验证/滑动验证页面/Verification
                logger.warning("[%s] 未过反爬验证墙(%s)——列表可过但详情页触发滑块: %s", self.site_name, title, url)
                return None
            return RenderedResponse(text=page.content(), status_code=status)
        except PlaywrightTimeout:
            logger.warning("[%s] 渲染超时: %s", self.site_name, url)
            return None
        except PlaywrightError as e:
            logger.error("[%s] 渲染错误: %s (%s)", self.site_name, url, e)
            return None
        finally:
            page.close()

    def close(self) -> None:
        """关闭浏览器与 Playwright 运行时"""
        self._browser.close()
        self._pw.stop()
