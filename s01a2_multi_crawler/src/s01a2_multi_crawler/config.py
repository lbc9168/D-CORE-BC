# config.py：站点 YAML 配置的 Pydantic 模型与加载器（非法配置在入口立即抛错）
import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl, field_validator

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "sites"


class ArticleRules(BaseModel):
    """文章页提取规则：正文选择器候选、日期正则、标题来源/清理（按站覆盖）"""

    content_selectors: list[str] = Field(default_factory=list)
    date_pattern: str = r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})"
    url_date_pattern: str | None = None  # 从文章 URL 提日期的正则（3 个捕获组 y/m/d），HTML 无日期时兜底
    title_source: str = "auto"  # auto=og:title>h1>title标签；title_tag=只用<title>（对 h1 是导航的站）
    title_strip: str | None = None  # 从标题末尾剥离站名后缀的正则

    @field_validator("title_source")
    @classmethod
    def _known_title_source(cls, v: str) -> str:
        if v not in {"auto", "title_tag"}:
            raise ValueError(f"未知 title_source: {v}")
        return v


class SiteConfig(BaseModel):
    """单个站点的适配器配置（一站一份 YAML）"""

    name: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    parser: str  # html_list / jpaas / sitemap_daily
    listing_urls: list[HttpUrl] = Field(default_factory=list)
    page_param: str | None = None  # 分页 URL 后缀模板，如 "?page={n}"，null=只抓第1页
    max_pages: int = Field(default=1, ge=1, le=20)
    article_url_pattern: str  # 识别文章链接的正则（match 到即视为文章 URL）
    sitemap_index: HttpUrl | None = None  # sitemap_daily 专用
    article: ArticleRules = Field(default_factory=ArticleRules)
    rate_limit_seconds: float = Field(default=3.0, ge=1.0)

    @field_validator("parser")
    @classmethod
    def _known_parser(cls, v: str) -> str:
        if v not in {"html_list", "jpaas", "sitemap_daily"}:
            raise ValueError(f"未知 parser 类型: {v}")
        return v


def load_site_configs(config_dir: Path = CONFIG_DIR, only: str | None = None) -> list[SiteConfig]:
    """加载 config/sites/ 下全部（或指定名称的）站点 YAML，校验失败立即抛错"""
    if not config_dir.is_dir():
        raise FileNotFoundError(f"站点配置目录不存在: {config_dir}")
    configs: list[SiteConfig] = []
    for path in sorted(config_dir.glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        cfg = SiteConfig(**raw)  # Pydantic 校验，非法即抛
        if only is None or cfg.name == only:
            configs.append(cfg)
    if only is not None and not configs:
        raise ValueError(f"未找到站点配置: {only}（可用: {[p.stem for p in config_dir.glob('*.yaml')]}）")
    logger.info("加载站点配置 %d 份: %s", len(configs), [c.name for c in configs])
    return configs
