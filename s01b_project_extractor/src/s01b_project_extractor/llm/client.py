"""配置化统一 LLM client：一处配 provider+model+key，各阶段经 complete() 调用；处理重试/退避/JSON 容错。"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM 调用配置；provider∈{gemini,deepseek}，可被各阶段单独覆盖 model。"""
    provider: str = "gemini"
    model: str = "gemini-2.5-flash-lite"
    temperature: float = 0.14
    max_retries: int = 3
    backoff_base: float = 2.0
    base_url: str = ""        # deepseek 用（OpenAI 兼容）
    _api_key: str = field(default="", repr=False)


def config_from_env(stage: str | None = None) -> LLMConfig:
    """从环境变量构建配置：S01B_PROVIDER / S01B_MODEL，缺省 gemini-2.5-flash-lite。stage 预留按阶段覆盖。"""
    provider = os.getenv("S01B_PROVIDER", "gemini").strip().lower()
    if provider == "deepseek":
        return LLMConfig(
            provider="deepseek",
            model=os.getenv("DEEPSEEK_MODEL", "").strip(),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip(),
            _api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
        )
    return LLMConfig(
        provider="gemini",
        model=os.getenv("S01B_MODEL", "gemini-2.5-flash-lite").strip(),
        _api_key=os.getenv("GEMINI_API_KEY", "").strip(),
    )


def _build_provider_client(cfg: LLMConfig):
    """按 provider 构建底层 SDK client；缺 key 立即抛错（入口校验）。"""
    if not cfg._api_key:
        raise RuntimeError(f"LLM provider={cfg.provider} 缺少 API key（请检查 .env）")
    if not cfg.model:
        raise RuntimeError(f"LLM provider={cfg.provider} 缺少 model 名（请检查 .env，如 DEEPSEEK_MODEL）")
    if cfg.provider == "deepseek":
        from openai import OpenAI
        return OpenAI(api_key=cfg._api_key, base_url=cfg.base_url or None)
    from google import genai
    return genai.Client(api_key=cfg._api_key)


@lru_cache(maxsize=1)
def _retryable_errors() -> tuple[type[BaseException], ...]:
    """返回可重试的异常类型元组（缓存）：JSON/取值/类型错误 + 网络/IO + 各 provider SDK 基类（懒加载，缺失则跳过）。"""
    errs: list[type[BaseException]] = [ValueError, TypeError, OSError, ConnectionError, TimeoutError]
    try:
        from google.genai import errors as genai_errors
        errs.append(genai_errors.APIError)
    except (ImportError, AttributeError):
        pass
    try:
        from openai import OpenAIError
        errs.append(OpenAIError)
    except (ImportError, AttributeError):
        pass
    return tuple(errs)


class LLMClient:
    """统一调用入口：complete(prompt, *, stage, json_mode) -> dict|str；按 provider 分派 + 重试退避。"""

    def __init__(self, cfg: LLMConfig, _client=None):
        self.cfg = cfg
        self._client = _client if _client is not None else _build_provider_client(cfg)

    def _raw_call(self, prompt: str, json_mode: bool) -> str:
        """单次底层调用，返回响应文本。"""
        if self.cfg.provider == "deepseek":
            resp = self._client.chat.completions.create(
                model=self.cfg.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.cfg.temperature,
                response_format={"type": "json_object"} if json_mode else None,
            )
            return resp.choices[0].message.content
        from google.genai import types
        cfg_kwargs = {"temperature": self.cfg.temperature}
        if json_mode:
            cfg_kwargs["response_mime_type"] = "application/json"
        resp = self._client.models.generate_content(
            model=self.cfg.model, contents=prompt,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
        return resp.text

    def complete(self, prompt: str, *, stage: str, json_mode: bool = True):
        """跑一次补全：失败按 max_retries 指数退避；json_mode 时解析为 dict；全失败抛 RuntimeError（不静默）。"""
        last_error = ""
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                text = self._raw_call(prompt, json_mode)
                return json.loads(text) if json_mode else text
            except _retryable_errors() as e:  # 指定可重试异常类型（解析/网络/SDK），并记录上下文
                last_error = f"{type(e).__name__}: {e}"
                logger.error("LLM complete 失败 stage=%s attempt=%d/%d error=%s",
                             stage, attempt, self.cfg.max_retries, last_error)
                if attempt < self.cfg.max_retries and self.cfg.backoff_base:
                    time.sleep(self.cfg.backoff_base ** attempt)
        raise RuntimeError(f"LLM complete stage={stage} 失败(重试{self.cfg.max_retries}次): {last_error}")
